"""Adzuna adapter.

Adzuna aggregates most UK boards, which makes it the highest-coverage source
that has an official API and terms permitting this use. Free tier is rate
limited, so queries are issued in sequence with a small delay rather than
fanned out.

API reference: https://developer.adzuna.com/
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from ..config import SourceConfig
from ..models import Job
from .base import DEFAULT_TIMEOUT, USER_AGENT, JobSource, SourceError

log = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search"

#: Adzuna's free tier is generous but not unlimited. One query per second is
#: well inside it and keeps this a good citizen.
QUERY_DELAY_SECONDS = 1.0


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.app_id = os.getenv("ADZUNA_APP_ID")
        self.app_key = os.getenv("ADZUNA_APP_KEY")

    async def fetch(self, client: httpx.AsyncClient) -> list[Job]:
        if not (self.app_id and self.app_key):
            raise SourceError("adzuna: ADZUNA_APP_ID and ADZUNA_APP_KEY are not set")

        jobs: list[Job] = []
        for index, query in enumerate(self.config.queries):
            if index:
                await asyncio.sleep(QUERY_DELAY_SECONDS)
            try:
                jobs.extend(await self._search(client, query))
            except httpx.HTTPError as exc:
                # One bad query should not lose the other five.
                log.warning("adzuna: query %r failed: %s", query, exc)
        return jobs

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[Job]:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query,
            "where": self.config.location,
            "distance": self.config.radius_km,
            "results_per_page": min(self.config.max_results, 50),
            "max_days_old": self.config.max_days_old,
            "content-type": "application/json",
            "sort_by": "date",
        }

        response = await client.get(
            f"{BASE_URL}/1",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 401:
            raise SourceError("adzuna: credentials rejected (401)")
        if response.status_code == 429:
            raise SourceError("adzuna: rate limited (429)")
        response.raise_for_status()

        payload = response.json()
        return [self._to_job(item) for item in payload.get("results", []) if item.get("id")]

    def _to_job(self, item: dict) -> Job:
        company = (item.get("company") or {}).get("display_name") or "Unknown"
        location = (item.get("location") or {}).get("display_name")
        description = item.get("description") or ""

        return Job(
            source=self.name,
            external_id=str(item["id"]),
            title=item.get("title", "").strip(),
            company=company.strip(),
            description=description,
            url=item.get("redirect_url", ""),
            location=location,
            remote=self._looks_remote(description, location, item.get("title")),
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            currency="GBP",
            contract_type=item.get("contract_time") or item.get("contract_type"),
            posted_at=self._parse_date(item.get("created")),
        )
