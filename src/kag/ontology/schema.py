"""Pydantic schemas for ontology payload validation.

Three layers per the design (see ``docs/ARCHITECTURE.md``):

- **basic** — universal 5W1H metadata
- **domain** — entity classes + object properties (inherits from a basic)
- **major** — domain-specific classes + properties (inherits from a domain)

Use :func:`validate_payload` for runtime validation. The
:class:`OntologyPayload` discriminated union narrows the model
based on the ``layer`` field.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Building blocks ─────────────────────────────────────────────────────


class FiveWOneH(BaseModel):
    """The 5W1H metadata block for the **basic** layer.

    Only ``what`` is required; the other five are optional to keep
    the basic layer lightweight while still capturing the universal
    who/when/where/why/how context.
    """

    model_config = ConfigDict(extra="forbid")

    what: str = Field(min_length=1, max_length=500)
    why: str | None = Field(default=None, max_length=1000)
    who: str | None = Field(default=None, max_length=200)
    when: str | None = Field(default=None, max_length=200)
    where: str | None = Field(default=None, max_length=200)
    how: str | None = Field(default=None, max_length=1000)


class EntityClass(BaseModel):
    """An entity class in a domain or major ontology."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    description: str | None = Field(default=None, max_length=500)
    parent: str | None = Field(
        default=None,
        max_length=100,
        description="Parent entity class in a major-layer ontology (inherits_from).",
    )


class ObjectProperty(BaseModel):
    """An object property: a binary relation between two entity classes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    domain: str = Field(min_length=1, max_length=100)
    range: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


# ── Per-layer payloads ──────────────────────────────────────────────────


class BasicPayload(BaseModel):
    """Basic layer: 5W1H metadata only."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["basic"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    metadata_5w1h: FiveWOneH


class DomainPayload(BaseModel):
    """Domain layer: entity classes + object properties; optionally inherits a basic."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["domain"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    inherits_from: str | None = Field(
        default=None,
        max_length=200,
        description="Name of a basic-layer ontology this domain extends.",
    )
    entity_classes: list[EntityClass] = Field(default_factory=list)
    object_properties: list[ObjectProperty] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_internals(self) -> DomainPayload:
        dup = _check_unique_names(self.entity_classes, "name")
        if dup:
            raise ValueError(dup)
        dup = _check_unique_names(self.object_properties, "name")
        if dup:
            raise ValueError(dup)
        ref_err = _check_object_properties_reference_known_classes(
            self.object_properties, self.entity_classes
        )
        if ref_err:
            raise ValueError(ref_err)
        return self


class MajorPayload(BaseModel):
    """Major layer: domain-specific specialization. ``inherits_from`` required."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["major"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    inherits_from: str = Field(
        min_length=1,
        max_length=200,
        description="Name of a domain-layer ontology this major extends. Required.",
    )
    entity_classes: list[EntityClass] = Field(default_factory=list)
    object_properties: list[ObjectProperty] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_internals(self) -> MajorPayload:
        dup = _check_unique_names(self.entity_classes, "name")
        if dup:
            raise ValueError(dup)
        dup = _check_unique_names(self.object_properties, "name")
        if dup:
            raise ValueError(dup)
        ref_err = _check_object_properties_reference_known_classes(
            self.object_properties, self.entity_classes
        )
        if ref_err:
            raise ValueError(ref_err)
        return self


OntologyPayload = Annotated[
    BasicPayload | DomainPayload | MajorPayload,
    Field(discriminator="layer"),
]


# ── Cross-field validation ─────────────────────────────────────────────


def _check_unique_names(
    items: list[BasicPayload] | list[EntityClass] | list[ObjectProperty],
    field: str,
) -> str | None:
    """Return an error string if any item's ``field`` collides with another."""
    seen: set[str] = set()
    for it in items:
        value = getattr(it, field, None)
        if value is None:
            continue
        if value in seen:
            return f"Duplicate {field}={value!r}"
        seen.add(value)
    return None


def _check_object_properties_reference_known_classes(
    properties: list[ObjectProperty],
    classes: list[EntityClass],
) -> str | None:
    """Every object property's domain/range must name a known class (or parent)."""
    known: set[str] = set()
    for c in classes:
        known.add(c.name)
        if c.parent is not None:
            known.add(c.parent)
    for p in properties:
        if p.domain not in known:
            return f"Object property {p.name!r} references unknown domain class {p.domain!r}"
        if p.range not in known:
            return f"Object property {p.name!r} references unknown range class {p.range!r}"
    return None


@model_validator(mode="after")
def _check_internals(
    self: BasicPayload | DomainPayload | MajorPayload,
) -> BasicPayload | DomainPayload | MajorPayload:
    """Cross-field checks applied uniformly; the per-layer branch decides
    which relationships to enforce."""
    if isinstance(self, (DomainPayload, MajorPayload)):
        dup = _check_unique_names(self.entity_classes, "name")
        if dup:
            raise ValueError(dup)
        dup = _check_unique_names(self.object_properties, "name")
        if dup:
            raise ValueError(dup)
        ref_err = _check_object_properties_reference_known_classes(
            self.object_properties, self.entity_classes
        )
        if ref_err:
            raise ValueError(ref_err)
    return self


# ── Public entry point ─────────────────────────────────────────────────


def validate_payload(data: dict[str, object]) -> BasicPayload | DomainPayload | MajorPayload:
    """Validate a raw ontology JSON dict and return the typed payload.

    Raises ``pydantic.ValidationError`` with a list of structured
    errors on failure. Discriminates on the ``layer`` field.
    """
    layer = data.get("layer")
    if layer not in {"basic", "domain", "major"}:
        raise ValueError(
            f"Ontology payload must have layer in {{'basic', 'domain', 'major'}}, got {layer!r}"
        )
    model: type[BasicPayload] | type[DomainPayload] | type[MajorPayload]
    if layer == "basic":
        model = BasicPayload
    elif layer == "domain":
        model = DomainPayload
    else:
        model = MajorPayload
    return model.model_validate(data)
