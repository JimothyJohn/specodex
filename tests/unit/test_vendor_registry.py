"""Tests for the citation-required vendor-facts registry.

Covers the loader contract (a fact without a valid citation must never
load), slug normalization against real DB manufacturer strings, the
gen_vendor_data output shape, and the seeded live profiles under
data/vendors/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from specodex.vendors.registry import (
    VendorFact,
    VendorProfile,
    VendorRegistryError,
    load_vendor_profiles,
    profile_for_manufacturer,
    slug_for_manufacturer,
)


def _load_gen_module():
    """Load scripts/gen_vendor_data.py by path (scripts/ is not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "gen_vendor_data.py"
    spec = importlib.util.spec_from_file_location("gen_vendor_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_SOURCE = {
    "url": "https://example.com/warranty",
    "kind": "oem",
    "retrieved_at": "2026-07-21T00:00:00Z",
    "excerpt": "warranted for a period of one (1) year",
}

VALID_FACT = {
    "label": "Standard warranty",
    "value": "1 year from shipment",
    "kind": "warranty",
    "sources": [VALID_SOURCE],
}


def _write_profile(directory: Path, slug: str, **overrides) -> Path:
    profile = {
        "slug": slug,
        "display_name": overrides.pop("display_name", slug.title()),
        "facts": overrides.pop("facts", [dict(VALID_FACT)]),
    }
    assert not overrides, f"unused overrides: {overrides}"
    path = directory / f"{slug}.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


class TestLoaderAcceptsValid:
    def test_valid_profile_loads(self, tmp_path):
        _write_profile(tmp_path, "acme")
        profiles = load_vendor_profiles(tmp_path)
        assert set(profiles) == {"acme"}
        fact = profiles["acme"].facts[0]
        assert fact.kind == "warranty"
        assert fact.sources[0].url == VALID_SOURCE["url"]

    def test_empty_facts_list_is_honest_not_invalid(self, tmp_path):
        # "An empty registry renders honestly" — a profile with zero
        # facts is legal; only uncited facts are rejected.
        _write_profile(tmp_path, "acme", facts=[])
        assert load_vendor_profiles(tmp_path)["acme"].facts == []

    def test_missing_directory_returns_empty(self, tmp_path):
        assert load_vendor_profiles(tmp_path / "nope") == {}


class TestLoaderRejects:
    def test_fact_without_sources(self, tmp_path):
        fact = dict(VALID_FACT, sources=[])
        _write_profile(tmp_path, "acme", facts=[fact])
        with pytest.raises(VendorRegistryError):
            load_vendor_profiles(tmp_path)

    def test_citation_without_url(self, tmp_path):
        bad_source = dict(VALID_SOURCE)
        del bad_source["url"]
        fact = dict(VALID_FACT, sources=[bad_source])
        _write_profile(tmp_path, "acme", facts=[fact])
        with pytest.raises(VendorRegistryError):
            load_vendor_profiles(tmp_path)

    def test_citation_without_excerpt(self, tmp_path):
        bad_source = dict(VALID_SOURCE)
        del bad_source["excerpt"]
        fact = dict(VALID_FACT, sources=[bad_source])
        _write_profile(tmp_path, "acme", facts=[fact])
        with pytest.raises(VendorRegistryError):
            load_vendor_profiles(tmp_path)

    def test_db_inference_citation_rejected(self, tmp_path):
        # A vendor fact is a published statement — it can never cite a
        # DB inference, even a structurally valid one.
        inference = {
            "kind": "db_inference",
            "retrieved_at": "2026-07-21T00:00:00Z",
            "excerpt": "n/a",
            "comparable_ids": ["a", "b"],
            "method_version": "v1",
        }
        fact = dict(VALID_FACT, sources=[inference])
        _write_profile(tmp_path, "acme", facts=[fact])
        with pytest.raises(VendorRegistryError):
            load_vendor_profiles(tmp_path)

    def test_unknown_fact_kind(self, tmp_path):
        fact = dict(VALID_FACT, kind="reputation")
        _write_profile(tmp_path, "acme", facts=[fact])
        with pytest.raises(VendorRegistryError):
            load_vendor_profiles(tmp_path)

    def test_slug_filename_mismatch(self, tmp_path):
        path = _write_profile(tmp_path, "acme")
        path.rename(tmp_path / "other.json")
        with pytest.raises(VendorRegistryError, match="does not match"):
            load_vendor_profiles(tmp_path)

    def test_unreadable_json(self, tmp_path):
        (tmp_path / "acme.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(VendorRegistryError, match="unreadable"):
            load_vendor_profiles(tmp_path)

    def test_denormalized_slug_rejected(self):
        with pytest.raises(ValidationError, match="not normalized"):
            VendorProfile(slug="Mitsubishi Electric", display_name="x", facts=[])

    def test_excerpt_over_280_chars_rejected(self):
        source = dict(VALID_SOURCE, excerpt="x" * 281)
        with pytest.raises(ValidationError):
            VendorFact(**dict(VALID_FACT, sources=[source]))


class TestSlugNormalization:
    @pytest.mark.parametrize(
        ("manufacturer", "expected"),
        [
            ("Yaskawa", "yaskawa"),
            ("Mitsubishi Electric", "mitsubishi-electric"),
            ("ABB", "abb"),
            (" ABB ", "abb"),
            ("OMRON", "omron"),
            ("Oriental Motor", "oriental-motor"),
            ("Oriental Motor U.S.A. Corp.", "oriental-motor-u-s-a-corp"),
            ("Allen-Bradley", "allen-bradley"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_slug_for_manufacturer(self, manufacturer, expected):
        assert slug_for_manufacturer(manufacturer) == expected

    def test_profile_lookup_by_db_manufacturer_string(self, tmp_path):
        _write_profile(tmp_path, "mitsubishi-electric")
        profiles = load_vendor_profiles(tmp_path)
        hit = profile_for_manufacturer("Mitsubishi Electric", profiles)
        assert hit is not None and hit.slug == "mitsubishi-electric"
        assert profile_for_manufacturer("Nidec", profiles) is None


class TestGenVendorData:
    def test_output_shape(self, tmp_path):
        gen = _load_gen_module()

        data_dir = tmp_path / "vendors"
        data_dir.mkdir()
        _write_profile(data_dir, "acme", display_name="Acme Corp")
        out = tmp_path / "out" / "vendors.json"

        payload = gen.generate(data_dir, out)

        assert out.is_file()
        on_disk = json.loads(out.read_text(encoding="utf-8"))
        assert on_disk == payload
        assert set(on_disk) == {"acme"}
        assert on_disk["acme"]["display_name"] == "Acme Corp"
        facts = on_disk["acme"]["facts"]
        assert len(facts) == 1
        assert facts[0]["kind"] == "warranty"
        assert facts[0]["sources"][0]["url"] == VALID_SOURCE["url"]

    def test_uncited_fact_cannot_reach_output(self, tmp_path):
        gen = _load_gen_module()

        data_dir = tmp_path / "vendors"
        data_dir.mkdir()
        _write_profile(data_dir, "acme", facts=[dict(VALID_FACT, sources=[])])
        with pytest.raises(VendorRegistryError):
            gen.generate(data_dir, tmp_path / "out.json")

    def test_committed_output_matches_live_data(self):
        # Drift gate: the committed frontend bundle must equal a fresh
        # compile of data/vendors/ (same contract as generated.ts).
        committed = (
            Path(__file__).resolve().parents[2]
            / "app"
            / "frontend"
            / "src"
            / "data"
            / "vendors.json"
        )
        assert committed.is_file(), "run scripts/gen_vendor_data.py and commit"
        on_disk = json.loads(committed.read_text(encoding="utf-8"))
        profiles = load_vendor_profiles()
        assert set(on_disk) == set(profiles)
        for slug, entry in on_disk.items():
            assert entry["display_name"] == profiles[slug].display_name
            assert len(entry["facts"]) == len(profiles[slug].facts)


class TestSeededProfiles:
    """The four researched vendor files ship with the repo and must load."""

    def test_seeded_files_load(self):
        profiles = load_vendor_profiles()
        assert {"yaskawa", "mitsubishi-electric", "oriental-motor", "abb"} <= set(
            profiles
        )

    def test_every_seeded_fact_is_fully_cited(self):
        for profile in load_vendor_profiles().values():
            assert profile.facts, f"{profile.slug}: seeded profile with no facts"
            for fact in profile.facts:
                assert fact.sources, f"{profile.slug}/{fact.label}: no sources"
                for src in fact.sources:
                    ctx = f"{profile.slug}/{fact.label}"
                    assert src.url, f"{ctx}: citation without url"
                    assert src.excerpt and src.excerpt.strip(), (
                        f"{ctx}: citation without excerpt"
                    )
                    assert src.retrieved_at, f"{ctx}: citation without retrieved_at"
                    assert src.url.startswith("https://"), (
                        f"{ctx}: non-https source {src.url}"
                    )

    def test_seeded_slugs_match_manufacturer_normalization(self):
        profiles = load_vendor_profiles()
        for slug, profile in profiles.items():
            assert slug_for_manufacturer(profile.display_name) == slug
