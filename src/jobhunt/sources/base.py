"""The contract every job source implements.

A source's only job is to turn somebody's API into `Job` objects. It does not
score, deduplicate, store or notify. That boundary is why adding a third board
later is one file and one registry line.
"""

from __future__ import annotations

import abc
import logging
from datetime import UTC, datetime

import httpx

from ..config import SourceConfig
from ..models import Job

log = logging.getLogger(__name__)

#: Every source sends this. Free tiers are a favour, and an identifiable client
#: is the least you can do in return. It also means that if this tool ever
#: misbehaves, the operator can see who to contact.
USER_AGENT = "jobhunt/0.1 (personal job search tool; +https://github.com/R6C0)"

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class SourceError(RuntimeError):
    """A source failed in a way the pipeline should log and step over."""


class JobSource(abc.ABC):
    name: str = "base"

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @abc.abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[Job]:
        """Return postings for every configured query.

        Implementations should raise `SourceError` for anything the caller can
        usefully log, and let genuine bugs propagate.
        """

    # -- helpers shared by implementations --------------------------------

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            log.debug("%s: unparseable date %r", value, value)
            return None

    @staticmethod
    def _looks_remote(*fields: str | None) -> bool | None:
        haystack = " ".join(f.lower() for f in fields if f)
        if not haystack:
            return None
        if any(term in haystack for term in ("remote", "work from home", "wfh")):
            return True
        if any(term in haystack for term in ("on-site", "onsite", "on site")):
            return False
        return None
