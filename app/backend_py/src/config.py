"""Runtime configuration for the FastAPI backend.

Mirrors the env-var contract used by ``app/backend/src/config/`` so
the two stacks can run side-by-side without divergent config
surfaces. Defaults match the Express service's defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

AppMode = Literal["public", "admin"]


@dataclass(frozen=True)
class Settings:
    app_mode: AppMode
    node_env: str
    dynamodb_table_name: str
    aws_region: str
    cors_origins: list[str]


def _parse_origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


def load() -> Settings:
    """Read settings from the environment at request time.

    Kept as a function rather than a module-level constant so tests
    can override env vars via ``monkeypatch.setenv`` before importing
    the FastAPI app.
    """

    mode_raw = os.environ.get("APP_MODE", "public").lower()
    mode: AppMode = "admin" if mode_raw == "admin" else "public"

    return Settings(
        app_mode=mode,
        node_env=os.environ.get("NODE_ENV", "development"),
        dynamodb_table_name=os.environ.get("DYNAMODB_TABLE_NAME", "products"),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        cors_origins=_parse_origins(os.environ.get("CORS_ORIGINS", "*")),
    )
