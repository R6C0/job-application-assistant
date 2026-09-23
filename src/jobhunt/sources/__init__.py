"""Source registry.

Adding a board is: write the adapter, add one line here, enable it in
config.yaml. Nothing else in the codebase learns its name.
"""

from __future__ import annotations

from ..config import SourceConfig
from .adzuna import AdzunaSource
from .base import JobSource, SourceError
from .reed import ReedSource

REGISTRY: dict[str, type[JobSource]] = {
    AdzunaSource.name: AdzunaSource,
    ReedSource.name: ReedSource,
}


def build_sources(configs: dict[str, SourceConfig]) -> list[JobSource]:
    """Instantiate every enabled, known source.

    An unknown name in config is a typo, and a typo that silently disables your
    only job source is worth an exception rather than a shrug.
    """
    sources: list[JobSource] = []
    for name, config in configs.items():
        if not config.enabled:
            continue
        if name not in REGISTRY:
            raise SourceError(
                f"unknown source {name!r}. Known sources: {', '.join(sorted(REGISTRY))}"
            )
        sources.append(REGISTRY[name](config))
    return sources


__all__ = ["REGISTRY", "JobSource", "SourceError", "build_sources"]
