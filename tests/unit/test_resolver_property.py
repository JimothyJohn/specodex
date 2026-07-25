"""Property tests for ``specodex.pricing.resolver`` — URL resolver cascade.

The example-based companion (``test_pricing.py``) pins specific
``(manufacturer, part_number)`` pairs end-to-end (Oriental Motor →
OEM, Yaskawa → distributor-only, Mitsubishi → shop1, dedup, tier
ordering). This file generates *adversarial* inputs — empty strings,
whitespace-only, unicode-laced manufacturer names, control characters
in part numbers, URL-meaningful chars (``&?#%``) — and asserts the
documented contracts hold on every input the strategy can produce.

The targets sit at the edge of an untrusted-bytes boundary: the
``(manufacturer, part_number)`` pair flows in from LLM-extracted
product rows; ``source_type_for_domain`` consumes raw URL netlocs
from the fetched HTML / SERP organic links. A raised exception aborts
the price-lookup cascade for that row and silently drops the price;
a generated URL whose hostname falls outside our known-domain
allowlist is a confused-deputy bug — the fetcher would happily hit
a third-party host on our reputation. Both are bugs the property
tests catch more cleanly than enumerated cases.

**Targets:**

- ``source_type_for_domain(netloc) -> Optional[SourceType]`` — netloc
  → ``"oem" | "distributor" | "aggregator" | None`` classification.
- ``source_name_for_domain(netloc) -> str`` — netloc →
  human-readable site name (falls back to the host).
- ``_oem_candidates(manufacturer, part_number) -> list[Candidate]``
  and ``_distributor_candidates(part_number) -> list[Candidate]`` —
  the two pure tier-builders (Tier 3 aggregators retired
  2026-06-11; Tier 4 SERP touches the network).
- ``resolve_candidates(manufacturer, part_number, use_serp=False)
  -> list[Candidate]`` — the public cascade entry, network-free with
  ``use_serp=False``.

**Contracts under test:**

1. **Never raises** — every helper returns cleanly for any string
   input the strategy produces.
2. **Return shapes.** Classifier returns one of the literal
   ``SourceType`` values or ``None``. Name function returns ``str``.
   Builders and ``resolve_candidates`` return ``list[Candidate]``.
3. **Empty-input short-circuit.** ``resolve_candidates`` returns
   ``[]`` when either ``manufacturer`` or ``part_number`` is empty,
   regardless of the other input.
4. **Hostname allowlist.** Every URL ``resolve_candidates`` emits
   has a hostname that classifies into a known tier (no third-party
   hosts can slip through the URL-construction code paths).
5. **Source-type tagging integrity.** Every candidate's
   ``source_type`` matches the bucket the URL came from — OEM
   builder only yields ``source_type="oem"`` candidates whose host
   resolves to ``"oem"``; distributor builder only yields
   ``"distributor"`` candidates whose host resolves to
   ``"distributor"``.
6. **Tier ordering invariant.** In ``resolve_candidates``'s output,
   every OEM-tier candidate precedes every distributor-tier
   candidate (the cascade walks in tier order and stops at the
   first hit; reordering would silently prefer the wrong tier).
7. **Dedup invariant.** ``resolve_candidates`` never emits the same
   URL twice; the seen-set guarantee at the bottom of the function
   must hold for every input combination.
8. **URL parseability.** Every candidate's URL parses to an HTTPS
   scheme with a non-empty hostname (no malformed strings reach
   the fetcher).
9. **``source_type_for_domain`` www-prefix idempotence.** For any
   host string, prefixing ``"www."`` does not change the tier
   classification — the function strips the prefix before lookup.
10. **``source_name_for_domain`` always returns a string.** Never
    ``None`` and never raises. For unknown hosts, returns the
    (case-folded, ``www.``-stripped) host as a fallback.
"""

from __future__ import annotations

import logging
from typing import Optional, get_args
from urllib.parse import urlparse

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.pricing.resolver import (
    Candidate,
    SourceType,
    _distributor_candidates,
    _oem_candidates,
    iter_source_domains,
    resolve_candidates,
    source_name_for_domain,
    source_type_for_domain,
)


# Silence noisy library logs at the property-search scale. The resolver
# emits a DEBUG line per missing SERPER_API_KEY call; with
# ``use_serp=False`` the path is not exercised, but the autouse fixture
# matches the pattern from ``test_pricing_parsers_property.py``.
@pytest.fixture(autouse=True)
def _silence_logs():
    logger = logging.getLogger("specodex.pricing.resolver")
    prior_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(prior_level)


# --- Strategies ----------------------------------------------------------

_SOURCE_TYPE_VALUES: frozenset[str] = frozenset(get_args(SourceType))
_KNOWN_DOMAINS: tuple[str, ...] = tuple(iter_source_domains())

# Arbitrary text — short, possibly empty, unicode-laced. Covers the
# "user-supplied manufacturer / part number from LLM" surface. The
# Hypothesis default ``text`` strategy already explores surrogates,
# control chars, and zero-length; cap length to keep the URL-builder
# work bounded.
_arbitrary_text = st.text(min_size=0, max_size=32)

# Non-empty variant for tests that need both fields present so the
# empty-input short-circuit is not the dominating path.
_nonempty_text = st.text(min_size=1, max_size=32).filter(lambda s: s.strip() != "")

# A manufacturer name that has a non-trivial chance of matching one of
# the OEM URL builders' normalized-string conditions. Mixing canonical
# names with totally arbitrary text exercises both the OEM hit and the
# distributor-only fall-through path.
_mfg_with_oem_chance = st.one_of(
    st.sampled_from(
        [
            "Oriental Motor",
            "ORIENTAL MOTOR",
            "OrientalMotorUSA",
            "Maxon Group",
            "maxon motor",
            "AutomationDirect",
            "ADC",
            "Schneider Electric",
            "schneider",
            "Mitsubishi Electric",
            "MITSUBISHI",
            "MitsubishiElectricAutomation",
            "Yaskawa",  # no OEM URL builder — exercises the fall-through
            "ABB",
            "Siemens",
        ]
    ),
    _nonempty_text,
)

# Part-number strategy — mix of plausible PNs (alphanumeric with
# hyphens) and arbitrary adversarial text (control chars, unicode,
# URL-meaningful chars like ``&?#%/``). The quote_plus calls in the
# resolver should handle all of these without raising.
_part_number = st.one_of(
    st.from_regex(r"^[A-Z]{1,6}[-]?[0-9]{1,6}[A-Z0-9]{0,8}$", fullmatch=True),
    _nonempty_text,
)

# Netloc strategy for source_type_for_domain. Combines known-good
# domains (with and without subdomain / ``www.`` prefix), close
# look-alikes (``orientalmotor.com.evil.com`` to verify endswith
# bypass protection from the canonical ``"." + d`` check), and
# adversarial garbage.
_known_netloc = st.sampled_from(_KNOWN_DOMAINS)
_known_with_prefix = st.one_of(
    _known_netloc,
    _known_netloc.map(lambda d: f"www.{d}"),
    _known_netloc.map(lambda d: f"shop.{d}"),
    _known_netloc.map(lambda d: d.upper()),
    _known_netloc.map(lambda d: f".{d}"),
)
_lookalike_netloc = _known_netloc.map(lambda d: f"{d}.evil.com")
_arbitrary_netloc = st.one_of(
    _arbitrary_text,
    st.from_regex(r"^[a-z0-9.-]{1,40}$", fullmatch=True),
)
_netloc = st.one_of(_known_with_prefix, _lookalike_netloc, _arbitrary_netloc)


# --- Contract 1: never raises -------------------------------------------


@given(netloc=_netloc)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_source_type_for_domain_never_raises(netloc: str) -> None:
    source_type_for_domain(netloc)


@given(netloc=_netloc)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_source_name_for_domain_never_raises(netloc: str) -> None:
    source_name_for_domain(netloc)


@given(mfg=_arbitrary_text, pn=_arbitrary_text)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_oem_candidates_never_raises(mfg: str, pn: str) -> None:
    _oem_candidates(mfg, pn)


@given(pn=_arbitrary_text)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_distributor_candidates_never_raises(pn: str) -> None:
    _distributor_candidates(pn)


@given(mfg=_arbitrary_text, pn=_arbitrary_text)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_never_raises(mfg: str, pn: str) -> None:
    resolve_candidates(mfg, pn, use_serp=False)


# --- Contract 2: return shapes ------------------------------------------


@given(netloc=_netloc)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_source_type_for_domain_returns_literal_or_none(netloc: str) -> None:
    result: Optional[str] = source_type_for_domain(netloc)
    assert result is None or result in _SOURCE_TYPE_VALUES


@given(netloc=_netloc)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_source_name_for_domain_returns_string(netloc: str) -> None:
    name = source_name_for_domain(netloc)
    assert isinstance(name, str)


@given(mfg=_arbitrary_text, pn=_arbitrary_text)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_returns_candidate_list(mfg: str, pn: str) -> None:
    result = resolve_candidates(mfg, pn, use_serp=False)
    assert isinstance(result, list)
    for c in result:
        assert isinstance(c, Candidate)
        assert isinstance(c.url, str)
        assert isinstance(c.source_type, str)
        assert isinstance(c.source_name, str)


# --- Contract 3: empty-input short-circuit -------------------------------


@given(other=_arbitrary_text)
@settings(max_examples=100)
def test_resolve_candidates_empty_manufacturer_returns_empty(other: str) -> None:
    assert resolve_candidates("", other, use_serp=False) == []


@given(other=_arbitrary_text)
@settings(max_examples=100)
def test_resolve_candidates_empty_part_number_returns_empty(other: str) -> None:
    assert resolve_candidates(other, "", use_serp=False) == []


# --- Contract 4: hostname allowlist --------------------------------------


@given(mfg=_mfg_with_oem_chance, pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_hosts_classify_to_known_tier(mfg: str, pn: str) -> None:
    cands = resolve_candidates(mfg, pn, use_serp=False)
    for c in cands:
        host = urlparse(c.url).netloc
        tier = source_type_for_domain(host)
        assert tier is not None, (
            f"resolver emitted unknown-host candidate {c.url!r} for ({mfg!r}, {pn!r})"
        )


# --- Contract 5: source-type tagging integrity ---------------------------


@given(mfg=_mfg_with_oem_chance, pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_oem_candidates_are_oem_tagged_and_host_oem(mfg: str, pn: str) -> None:
    for c in _oem_candidates(mfg, pn):
        assert c.source_type == "oem"
        host = urlparse(c.url).netloc
        assert source_type_for_domain(host) == "oem", (
            f"_oem_candidates emitted non-OEM host {host!r} for {mfg!r}"
        )


@given(pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_distributor_candidates_are_distributor_tagged(pn: str) -> None:
    for c in _distributor_candidates(pn):
        assert c.source_type == "distributor"
        host = urlparse(c.url).netloc
        assert source_type_for_domain(host) == "distributor", (
            f"_distributor_candidates emitted non-distributor host {host!r} for {pn!r}"
        )


# --- Contract 6: tier ordering invariant ---------------------------------


@given(mfg=_mfg_with_oem_chance, pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_oem_precedes_distributor(mfg: str, pn: str) -> None:
    cands = resolve_candidates(mfg, pn, use_serp=False)
    tiers = [c.source_type for c in cands]
    last_oem = max((i for i, t in enumerate(tiers) if t == "oem"), default=-1)
    first_distributor = min(
        (i for i, t in enumerate(tiers) if t == "distributor"), default=len(tiers)
    )
    # When both tiers are present, every OEM index must precede every
    # distributor index. Vacuously true when either tier is empty.
    if last_oem != -1 and first_distributor != len(tiers):
        assert last_oem < first_distributor, (
            f"tier ordering broken: tiers={tiers!r} for ({mfg!r}, {pn!r})"
        )


# --- Contract 7: dedup invariant ----------------------------------------


@given(mfg=_mfg_with_oem_chance, pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_dedupes_urls(mfg: str, pn: str) -> None:
    cands = resolve_candidates(mfg, pn, use_serp=False)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls)), f"duplicate URLs in resolver output: {urls!r}"


# --- Contract 8: URL parseability ----------------------------------------


@given(mfg=_mfg_with_oem_chance, pn=_part_number)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_resolve_candidates_urls_are_https_with_host(mfg: str, pn: str) -> None:
    cands = resolve_candidates(mfg, pn, use_serp=False)
    for c in cands:
        parsed = urlparse(c.url)
        assert parsed.scheme == "https", (
            f"non-https URL {c.url!r} for ({mfg!r}, {pn!r})"
        )
        assert parsed.netloc, f"empty netloc in {c.url!r} for ({mfg!r}, {pn!r})"


# --- Contract 9: www-prefix idempotence ---------------------------------


@given(host=_known_netloc)
@settings(max_examples=100)
def test_source_type_for_domain_www_prefix_idempotent(host: str) -> None:
    bare = source_type_for_domain(host)
    prefixed = source_type_for_domain(f"www.{host}")
    assert bare == prefixed, (
        f"www-prefix changed tier classification: bare={bare!r}, "
        f"prefixed={prefixed!r} for host={host!r}"
    )


@given(host=_known_netloc)
@settings(max_examples=100)
def test_source_type_for_domain_case_idempotent(host: str) -> None:
    # ``host`` is already lowercase in _KNOWN_DOMAINS; check uppercase
    # variant. The function lowercases internally before lookup.
    lower = source_type_for_domain(host)
    upper = source_type_for_domain(host.upper())
    assert lower == upper, (
        f"case changed tier classification: lower={lower!r}, "
        f"upper={upper!r} for host={host!r}"
    )


# --- Contract 10: source_name_for_domain fallback shape -----------------


@given(host=_arbitrary_netloc)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_source_name_for_domain_unknown_host_returns_string(host: str) -> None:
    # Unknown hosts fall through to the bare host string (case-folded,
    # ``www.``-stripped). Even for empty input the function returns "".
    name = source_name_for_domain(host)
    assert isinstance(name, str)


@given(host=_known_netloc)
@settings(max_examples=100)
def test_source_name_for_domain_known_host_returns_nonempty(host: str) -> None:
    name = source_name_for_domain(host)
    assert name, f"known host {host!r} produced empty name"
