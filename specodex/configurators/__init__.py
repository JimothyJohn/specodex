"""Harvest structured product data from vendors' online JS configurators.

See ``todo/CONFIGURATOR_HARVEST.md`` for the design and rationale. Public
surface: :class:`~specodex.configurators.base.ConfiguratorClient` (the
vendor-agnostic browser session) and per-vendor adapters (e.g.
:class:`~specodex.configurators.stober.StoberAdapter`).
"""

from specodex.configurators.base import (
    ApiResponse,
    ConfiguratorClient,
    ConfiguratorError,
    RobotsDisallowedError,
)

__all__ = [
    "ApiResponse",
    "ConfiguratorClient",
    "ConfiguratorError",
    "RobotsDisallowedError",
]
