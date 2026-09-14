"""Unit tests for the backend Lambda bundle manifest (cli/quickstart.py)."""

import copy

from cli.quickstart import lambda_bundle_manifest


def _pkg() -> dict:
    return {
        "name": "specodex-backend",
        "version": "1.0.0",
        "main": "dist/index.js",
        "scripts": {"build": "tsc"},
        "dependencies": {"express": "^5.0.0", "zod": "^3.0.0"},
        "devDependencies": {
            "typescript": "^7.0.2",
            "@typescript-eslint/parser": "^8.18.2",
        },
    }


def test_drops_dev_dependencies_only():
    out = lambda_bundle_manifest(_pkg())
    assert "devDependencies" not in out
    assert out["dependencies"] == {"express": "^5.0.0", "zod": "^3.0.0"}
    assert out["name"] == "specodex-backend"
    assert out["version"] == "1.0.0"
    assert out["main"] == "dist/index.js"


def test_does_not_mutate_input():
    src = _pkg()
    snapshot = copy.deepcopy(src)
    lambda_bundle_manifest(src)
    assert src == snapshot


def test_tolerates_missing_dev_dependencies():
    src = _pkg()
    del src["devDependencies"]
    assert lambda_bundle_manifest(src) == src
