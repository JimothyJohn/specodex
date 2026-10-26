"""Replay the committed atheris corpus through the page-finder contract.

``tests/fuzz/fuzz_page_finder.py`` is the coverage-guided fuzzer
(opt-in ``fuzz`` dependency group). Its ``check_one_input`` is plain
Python, so every input under ``tests/fuzz/corpus/`` — the seeds plus
each minimised crash — runs here in the normal unit suite without
atheris. Committing a crash input to the corpus pins it forever.
"""

from __future__ import annotations

import logging

import pytest

from tests.fuzz.fuzz_page_finder import CORPUS_DIR, check_one_input, make_seed_corpus

_CORPUS = sorted(CORPUS_DIR.iterdir())


@pytest.fixture(autouse=True)
def _silence_page_finder_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def test_corpus_is_populated() -> None:
    names = {p.name for p in _CORPUS}
    assert set(make_seed_corpus()) <= names
    assert any(n.startswith("crash_") for n in names)


@pytest.mark.parametrize("path", _CORPUS, ids=[p.name for p in _CORPUS])
def test_corpus_input_honours_contract(path) -> None:
    check_one_input(path.read_bytes())
