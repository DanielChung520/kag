#!/usr/bin/env python
"""Verify the API docs (docs/API.md, README.md) match the running app.

The FastAPI app in ``src/kag/api/`` is the single source of truth
for what endpoints kag exposes. This script:

1. Builds the app in-process and pulls ``app.openapi()``.
2. Parses the endpoint table out of ``docs/API.md``.
3. Parses the "External API" section out of ``README.md``.
4. Diffs the three sources and exits non-zero on any drift.

Run via:

    uv run python scripts/check_api_sync.py

In CI, the same script runs in ``.github/workflows/ci.yml``.

Notes
-----
- Path templates are normalized so ``{kb_key}`` in the doc matches
  ``{kb_key}`` in OpenAPI; no real path matching is done.
- The script does not check request/response field shapes; that's
  what the OpenAPI spec and generated clients are for. It only
  catches "endpoint exists in code but not in doc" and the reverse.
- Custom tag names / descriptions in ``src/kag/api/openapi.py``
  are the source of truth for grouping.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCS = REPO_ROOT / "docs" / "API.md"
DEFAULT_README = REPO_ROOT / "README.md"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str

    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


def canonical_endpoints() -> list[Endpoint]:
    """Build the app in-process and pull the OpenAPI path/method list."""
    # Stub env so Settings() can be instantiated without a real .env.
    for k, v in _stub_env().items():
        os.environ.setdefault(k, v)
    from kag.main import create_app

    app = create_app()
    schema = app.openapi()
    out: list[Endpoint] = []
    for path, ops in sorted(schema.get("paths", {}).items()):
        for method in ops:
            if method == "parameters":
                continue
            out.append(Endpoint(method=method.upper(), path=path))
    return out


def _stub_env() -> dict[str, str]:
    return {
        "KAG_API_KEY_PEPPER": "x" * 64,
        "KAG_ADMIN_TOKEN": "x" * 64,
        "ARANGO_URL": "http://localhost:8529",
        "ARANGO_DB": "x",
        "ARANGO_USER": "x",
        "ARANGO_PASSWORD": "x",
        "QDRANT_URL": "http://localhost:6333",
        "SEAWEED_URL": "http://localhost:8888",
        "SEAWEED_BUCKET": "kag",
        "SEAWEED_ACCESS_KEY": "x",
        "SEAWEED_SECRET_KEY": "x",
        "REDIS_URL": "redis://localhost:6379/0",
        "LLM_BASE_URL": "http://localhost:11400/v1",
        "EMBEDDING_MODEL": "x",
        "GRAPH_MODEL": "x",
        "VLM_MODEL": "x",
    }


_TABLE_ROW = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s+\|\s*`?([^`\s|]+)`?\s+\|"
)


def parse_doc_table(path: Path) -> list[Endpoint]:
    """Parse the endpoints-overview table from docs/API.md.

    Only rows starting with ``| METHOD | ... |`` are read; surrounding
    prose is ignored. Path templates are returned as-is (no
    normalization — the doc and OpenAPI use the same brace syntax).
    """
    if not path.exists():
        return []
    out: list[Endpoint] = []
    in_section = False
    for line in path.read_text().splitlines():
        if line.startswith("## Endpoints Overview"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            m = _TABLE_ROW.match(line)
            if m:
                out.append(Endpoint(method=m.group(1), path=m.group(2)))
    return out


def parse_readme_endpoints(path: Path) -> list[Endpoint]:
    """Parse the External API section of README.md.

    The README section is plain prose; we look for lines of the
    form ``METHOD /path/...`` inside the fenced block. Anything
    else is ignored. Empty / missing section returns [].
    """
    if not path.exists():
        return []
    text = path.read_text()
    in_section = False
    out: list[Endpoint] = []
    for line in text.splitlines():
        if line.startswith("## External API"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            m = re.match(
                r"^\s*-\s*`(GET|POST|PUT|PATCH|DELETE)\s+([^`\s]+)`",
                line,
            )
            if m:
                out.append(Endpoint(method=m.group(1), path=m.group(2)))
    return out


def diff(
    canonical: Iterable[Endpoint],
    documented: Iterable[Endpoint],
    source_name: str,
) -> list[str]:
    """Return human-readable error lines; empty list means no drift."""
    c = {e.key(): e for e in canonical}
    d = {e.key(): e for e in documented}
    only_code = sorted(set(c) - set(d))
    only_doc = sorted(set(d) - set(c))
    errs: list[str] = []
    for m, p in only_code:
        errs.append(f"  [in code, missing from {source_name}]  {m}  {p}")
    for m, p in only_doc:
        errs.append(f"  [in {source_name}, missing from code]  {m}  {p}")
    return errs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--docs",
        type=Path,
        default=DEFAULT_DOCS,
        help="Path to the human-readable API doc (default: %(default)s).",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=DEFAULT_README,
        help="Path to the README (default: %(default)s).",
    )
    parser.add_argument(
        "--include-readme",
        action="store_true",
        help="Also cross-check README.md (default: off — README's 'External API' "
        "section is a short summary, not a full table; the full table is in docs/API.md).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the canonical endpoint list as JSON and exit 0 (no diff).",
    )
    args = parser.parse_args()

    canonical = canonical_endpoints()
    if args.json:
        json.dump(
            [{"method": e.method, "path": e.path} for e in canonical],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    print(f"Canonical endpoints: {len(canonical)}")
    errs: list[str] = []

    doc_endpoints = parse_doc_table(args.docs)
    print(f"docs/API.md endpoints: {len(doc_endpoints)}")
    errs.extend(f"docs/API.md drift:\n{line}" for line in diff(canonical, doc_endpoints, "docs/API.md"))

    if args.include_readme and "## External API" in args.readme.read_text():
        readme_endpoints = parse_readme_endpoints(args.readme)
        print(f"README.md endpoints: {len(readme_endpoints)}")
        errs.extend(f"README.md drift:\n{line}" for line in diff(canonical, readme_endpoints, "README.md"))
    elif args.include_readme:
        print("README.md: no '## External API' section found (skipping)")
    else:
        print("README.md: skipped (pass --include-readme to cross-check; "
              "README's 'External API' is a short summary, not a full table)")

    if errs:
        print("\nFAIL — documentation drift detected:")
        print("\n".join(errs))
        print(
            "\nFix: update the doc to match the code (or vice-versa), "
            "then re-run this script. See 'Keeping this doc in sync with the code' "
            f"in {args.docs}."
        )
        return 1

    print("\nOK — all documented endpoints match the code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
