"""Reed adapter.

Reed's API authenticates with HTTP Basic using the API key as the username and
an empty password, which is unusual enough to be worth stating here rather than
leaving as a puzzle in the code.

Reed's search endpoint returns a truncated description. The full text lives on a
per-job detail endpoint, and the description is what the scorer and the letter
drafter both work from, so this adapter fetches detail for each result. That
costs one request per job, which is why `max_results` matters more here than it
does for Adzuna.

API reference: https://www.reed.co.uk/developers
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

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
DETAIL_URL = "https://www.reed.co.uk/api/1.0/jobs"

#: Concurrent detail fetches. Kept small on purpose: this is somebody's free
#: API and the difference between 4 and 40 is imperceptible to the user.
DETAIL_CONCURRENCY = 4


class ReedSource(JobSource):
    name = "reed"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.api_key = os.getenv("REED_API_KEY")

    async def fetch(self, client: httpx.AsyncClient) -> list[Job]:
        if not self.api_key:
            raise SourceError("reed: REED_API_KEY is not set")

        auth = httpx.BasicAuth(self.api_key, "")
        jobs: list[Job] = []

        for query in self.config.queries:
            try:
                results = await self._search(client, auth, query)
            except httpx.HTTPError as exc:
                log.warning("reed: query %r failed: %s", query, exc)
                continue
            jobs.extend(await self._with_descriptions(client, auth, results))

        return jobs

    async def _search(
        self, client: httpx.AsyncClient, auth: httpx.BasicAuth, query: str
    ) -> list[dict]:
        params = {
            "keywords": query,
            "locationName": self.config.location,
            "distanceFromLocation": self.config.radius_km,
            "resultsToTake": min(self.config.max_results, 100),
            "postedByDirectEmployer": "false",
        }
        response = await client.get(
            SEARCH_URL,
            params=params,
            auth=auth,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 401:
            raise SourceError("reed: credentials rejected (401)")
        response.raise_for_status()
        return response.json().get("results", [])

    async def _with_descriptions(
        self, client: httpx.AsyncClient, auth: httpx.BasicAuth, results: list[dict]
    ) -> list[Job]:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def hydrate(item: dict) -> Job:
            description = item.get("jobDescription") or ""
            async with semaphore:
                try:
                    detail = await client.get(
                        f"{DETAIL_URL}/{item['jobId']}",
                        auth=auth,
                        headers={"User-Agent": USER_AGENT},
                        timeout=DEFAULT_TIMEOUT,
                    )
                    if detail.status_code == 200:
                        description = detail.json().get("jobDescription") or description
                except httpx.HTTPError as exc:
                    # Falling back to the truncated description is better than
                    # dropping the posting: a short description still scores.
                    log.debug("reed: detail fetch failed for %s: %s", item.get("jobId"), exc)
            return self._to_job(item, description)

        return list(await asyncio.gather(*(hydrate(item) for item in results)))

    def _to_job(self, item: dict, description: str) -> Job:
        location = item.get("locationName")
        title = (item.get("jobTitle") or "").strip()

        return Job(
            source=self.name,
            external_id=str(item["jobId"]),
            title=title,
            company=(item.get("employerName") or "Unknown").strip(),
            description=description,
            url=item.get("jobUrl", ""),
            location=location,
            remote=self._looks_remote(description, location, title),
            salary_min=item.get("minimumSalary"),
            salary_max=item.get("maximumSalary"),
            currency=item.get("currency") or "GBP",
            contract_type="contract" if item.get("contractType") == "Contract" else "permanent",
            posted_at=self._parse_date(item.get("date") or item.get("datePosted")),
        )
