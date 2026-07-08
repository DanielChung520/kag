"""OpenAI-compatible LLM client (dllm-first).

Wraps the official ``openai.AsyncOpenAI`` SDK for chat, embedding,
vision-language captioning, and provider health checks.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from kag.config import get_settings

log = structlog.get_logger(__name__)


class LLMHealth(BaseModel):
    """Health status of the LLM provider connection."""

    ok: bool
    provider: str = "dllm"
    base_url: str
    models_available: list[str]
    error: str | None = None


class LLMClient:
    """OpenAI-compatible LLM client configured for the team's dllm server.

    Wraps the official :class:`openai.AsyncOpenAI` SDK. Every public
    method is async — there are no synchronous variants. Timeouts are
    set per-method via the ``timeout`` parameter passed through to the
    underlying ``httpx.AsyncClient``.

    Usage::

        client = LLMClient()
        reply = await client.chat("gpt-4o", [...])
        embedding = await client.embed("bge-m3", ["hello"])
        caption = await client.vl_caption("qwen2.5-vl-8b", image_bytes, "Describe")
        health = await client.health()
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.LLM_BASE_URL
        self._api_key = settings.LLM_API_KEY
        self._json_retry = settings.LLM_JSON_RETRY

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.LLM_TIMEOUT, connect=10.0),
        )
        self._client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            http_client=http_client,
        )

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message.

        When *json_mode* is ``True``, sets ``response_format={"type": "json_object"}``
        and automatically retries up to ``Settings.LLM_JSON_RETRY`` times if the
        response body fails to parse as valid JSON.

        Args:
            model: Model name (e.g. ``"qwen3-30b-a3b-4bit"``).
            messages: List of message dicts in OpenAI chat format.
            json_mode: Request structured JSON output.
            temperature: Sampling temperature (overrides provider default).
            max_tokens: Maximum tokens in the response.

        Returns:
            The assistant message content as a plain string.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": 120.0,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        attempts = (self._json_retry + 1) if json_mode else 1

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""

                if json_mode:
                    # Validate JSON before returning the raw string.
                    json.loads(content)

                if response.usage is not None:
                    log.info(
                        "chat.completion",
                        model=model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                        attempt=attempt,
                    )
                else:
                    log.info("chat.completion", model=model, attempt=attempt)

                return content

            except json.JSONDecodeError:
                if attempt == attempts:
                    raise
                log.warning(
                    "chat.json_decode_error",
                    model=model,
                    attempt=attempt,
                )

        # This line is never reached because either we return or raise above.
        raise RuntimeError("unreachable")  # pragma: no cover

    async def embed(
        self,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        """Create embeddings for a batch of texts.

        Args:
            model: Embedding model name (e.g. ``"bge-m3"``).
            texts: List of strings to embed.

        Returns:
            Embeddings in the same order as *texts*.
        """
        response = await self._client.embeddings.create(
            model=model,
            input=texts,
            timeout=30.0,
        )
        log.info(
            "embed.batch",
            model=model,
            batch_size=len(texts),
        )
        return [item.embedding for item in response.data]

    async def vl_caption(
        self,
        model: str,
        image_bytes: bytes,
        prompt: str,
    ) -> str:
        """Generate a caption for an image using a vision-language model.

        Args:
            model: VLM model name (e.g. ``"qwen2.5-vl-8b"``).
            image_bytes: Raw image bytes (PNG, JPEG, etc.).
            prompt: Text prompt describing what to caption.

        Returns:
            The generated caption as a string.
        """
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{encoded}"

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ]

        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]  # dicts are structurally compatible with OpenAI message params
            timeout=60.0,
        )

        log.info("vl_caption", model=model)
        return response.choices[0].message.content or ""

    async def health(self) -> LLMHealth:
        """Check LLM provider health by fetching the model list.

        Uses a short 5-second timeout so a non-responsive provider does not
        block the health check endpoint for long.

        Returns:
            :class:`LLMHealth` with ``ok=True`` on success, or ``ok=False``
            with an error description on failure.
        """
        try:
            models_page = await self._client.models.list(timeout=5.0)
            model_ids = [m.id for m in models_page.data]
            log.info("health.ok", models_available=len(model_ids))
            return LLMHealth(
                ok=True,
                provider="dllm",
                base_url=self._base_url,
                models_available=model_ids,
            )
        except Exception as exc:
            log.warning("health.fail", error=str(exc))
            return LLMHealth(
                ok=False,
                provider="dllm",
                base_url=self._base_url,
                models_available=[],
                error=str(exc),
            )
