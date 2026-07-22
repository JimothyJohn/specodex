"""Citation-required vendor-facts registry (todo/COMMERCIAL.md Phase 4).

One JSON file per manufacturer under ``data/vendors/<slug>.json``. Every
fact is a ``{label, value, kind, sources[]}`` record where each source is
a :class:`specodex.models.commercial.SourceCitation` — URL + retrieval
timestamp + a verbatim excerpt the reader (and the ``vendor-facts
verify`` audit) can find on the page.

The loader **rejects** any fact without at least one valid citation.
Facts are limited to the explicit-and-published (see ``FactKind``): no
review scores, no sentiment, no "known for good service". If a vendor
doesn't publish it, we don't have it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from specodex.models.commercial import SourceCitation

# Repo root: specodex/vendors/registry.py → parents[2]
_ROOT = Path(__file__).resolve().parents[2]
VENDOR_DATA_DIR = _ROOT / "data" / "vendors"

# The only fact categories allowed in the registry — all of them
# explicit, published, and re-checkable against a public source.
FactKind = Literal[
    "warranty",
    "support",
    "certification",
    "on_time_delivery",
    "location",
    "lead_time_statement",
]

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class VendorRegistryError(ValueError):
    """Raised when a vendor profile file is malformed or uncited."""


def slug_for_manufacturer(name: Optional[str]) -> str:
    """Normalize a DB ``manufacturer`` string to a registry slug.

    Lowercase, strip, collapse every run of non-alphanumerics to a
    single hyphen: ``"Mitsubishi Electric"`` → ``"mitsubishi-electric"``,
    ``"OMRON"`` → ``"omron"``, ``" ABB "`` → ``"abb"``. Empty/None input
    returns ``""`` (no profile can match it).
    """
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


class VendorFact(BaseModel):
    """One published, citable fact about a vendor.

    Invariants (enforced):
    - ``sources`` carries at least one citation.
    - Every citation carries a ``url`` and a verbatim ``excerpt`` —
      ``db_inference`` citations are not allowed here; a vendor fact is
      a published statement, not a statistic.
    """

    label: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    kind: FactKind
    sources: List[SourceCitation] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _require_replicable_citations(self) -> "VendorFact":
        for src in self.sources:
            if src.kind == "db_inference":
                raise ValueError(
                    f"fact {self.label!r}: db_inference citations are not "
                    "allowed on vendor facts — a vendor fact must cite a "
                    "published source"
                )
            if not src.url:
                raise ValueError(
                    f"fact {self.label!r}: citation without a url is not replicable"
                )
            if not src.excerpt or not src.excerpt.strip():
                raise ValueError(
                    f"fact {self.label!r}: citation for {src.url} carries no "
                    "verbatim excerpt — the verify audit would have nothing "
                    "to re-check"
                )
        return self


class VendorProfile(BaseModel):
    """All registry facts for one manufacturer."""

    slug: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    facts: List[VendorFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_slug_shape(self) -> "VendorProfile":
        if not _SLUG_RE.match(self.slug):
            raise ValueError(
                f"slug {self.slug!r} is not normalized — expected lowercase "
                "alphanumerics separated by single hyphens "
                "(see slug_for_manufacturer)"
            )
        return self


def load_vendor_profiles(
    data_dir: Optional[Path] = None,
) -> Dict[str, VendorProfile]:
    """Load every ``data/vendors/*.json`` profile, keyed by slug.

    Raises :class:`VendorRegistryError` on any schema or citation
    violation — a fact without a valid citation must never load. Also
    raises when a file's name doesn't match its ``slug`` field or when
    two files claim the same slug.
    """
    directory = VENDOR_DATA_DIR if data_dir is None else data_dir
    profiles: Dict[str, VendorProfile] = {}
    if not directory.is_dir():
        return profiles

    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise VendorRegistryError(f"{path.name}: unreadable JSON: {e}") from e
        try:
            profile = VendorProfile.model_validate(raw)
        except ValidationError as e:
            raise VendorRegistryError(f"{path.name}: {e}") from e
        if profile.slug != path.stem:
            raise VendorRegistryError(
                f"{path.name}: slug {profile.slug!r} does not match the "
                f"filename stem {path.stem!r}"
            )
        if profile.slug in profiles:
            raise VendorRegistryError(f"duplicate vendor slug {profile.slug!r}")
        profiles[profile.slug] = profile

    return profiles


def profile_for_manufacturer(
    manufacturer: Optional[str],
    profiles: Dict[str, VendorProfile],
) -> Optional[VendorProfile]:
    """Look up the profile matching a DB ``manufacturer`` string, if any."""
    return profiles.get(slug_for_manufacturer(manufacturer))
