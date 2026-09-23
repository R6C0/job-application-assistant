"""Company research, from public records only.

Scope is deliberately narrow. This answers "is this a real, solvent company, how
big, and how long has it existed", which is what changes whether you apply and
gives a letter something concrete to open with. It does not attempt culture,
glassdoor sentiment or funding rounds, because those need scraping sites that
forbid it and the output would be unciteable anyway.

Source is the UK Companies House API: official, free, and authoritative for
exactly these facts.

The hard part is not the request, it is deciding whether the company you found
is the company in the job advert. `_confidence` is that judgement, and it is
reported rather than hidden, so a letter never asserts a fact drawn from the
wrong "Acme Ltd".
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date

import httpx

from ..models import CompanyBrief

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"
PROFILE_URL = "https://api.company-information.service.gov.uk/company"

#: Suffixes stripped before comparing names. "Monzo" in an advert is "MONZO
#: BANK LIMITED" at Companies House.
_SUFFIXES = re.compile(
    r"\b(limited|ltd|llp|plc|uk|group|holdings|services|technologies|technology|inc)\b",
    re.IGNORECASE,
)


def _normalise(name: str) -> str:
    name = _SUFFIXES.sub("", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    return " ".join(name.split())


def _confidence(advert_name: str, record_name: str) -> str:
    a, b = _normalise(advert_name), _normalise(record_name)
    if not a or not b:
        return "low"
    if a == b:
        return "high"
    if a in b or b in a:
        return "medium"
    shared = set(a.split()) & set(b.split())
    return "medium" if len(shared) >= 2 else "low"


class CompanyResearcher:
    """Looks a company up, or says honestly that it could not."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("COMPANIES_HOUSE_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def research(self, client: httpx.AsyncClient, company_name: str) -> CompanyBrief:
        if not self.available:
            return CompanyBrief(
                name=company_name,
                summary="No company research: COMPANIES_HOUSE_API_KEY is not set.",
                confidence="low",
            )

        try:
            record = await self._search(client, company_name)
        except httpx.HTTPError as exc:
            log.warning("companies house: lookup failed for %r: %s", company_name, exc)
            return CompanyBrief(
                name=company_name,
                summary="Company lookup failed; treat this posting on its own terms.",
                confidence="low",
            )

        if not record:
            return CompanyBrief(
                name=company_name,
                summary=(
                    "No Companies House match. Common for overseas employers, "
                    "trading names and recruitment agencies advertising on behalf "
                    "of a client."
                ),
                confidence="low",
            )

        confidence = _confidence(company_name, record.get("title", ""))
        detail = await self._profile(client, record["company_number"])
        facts = self._facts(record, detail)

        return CompanyBrief(
            name=record.get("title", company_name),
            summary=self._summary(record, facts, confidence),
            facts=facts,
            sources=[f"{PROFILE_URL}/{record['company_number']}"],
            confidence=confidence,
        )

    async def _search(self, client: httpx.AsyncClient, name: str) -> dict | None:
        response = await client.get(
            SEARCH_URL,
            params={"q": name, "items_per_page": 5},
            auth=httpx.BasicAuth(self.api_key or "", ""),
            timeout=15.0,
        )
        if response.status_code == 401:
            log.warning("companies house: key rejected")
            return None
        response.raise_for_status()

        items = [
            item
            for item in response.json().get("items", [])
            if item.get("company_status") == "active" and item.get("company_number")
        ]
        if not items:
            return None

        # Best name match, not first result: Companies House ranks by its own
        # relevance and "Apple" returns a lot of orchards.
        ranking = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda i: ranking[_confidence(name, i.get("title", ""))])
        return items[0]

    async def _profile(self, client: httpx.AsyncClient, number: str) -> dict:
        try:
            response = await client.get(
                f"{PROFILE_URL}/{number}",
                auth=httpx.BasicAuth(self.api_key or "", ""),
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return {}

    def _facts(self, record: dict, detail: dict) -> dict[str, str]:
        facts: dict[str, str] = {}

        incorporated = detail.get("date_of_creation") or record.get("date_of_creation")
        if incorporated:
            facts["Incorporated"] = incorporated
            try:
                years = date.today().year - int(incorporated[:4])
                facts["Age"] = f"about {years} years"
            except ValueError:
                pass

        if detail.get("company_status"):
            facts["Status"] = detail["company_status"]
        if detail.get("type"):
            facts["Type"] = detail["type"]

        sic = detail.get("sic_codes") or []
        if sic:
            facts["SIC codes"] = ", ".join(sic[:3])

        accounts = (detail.get("accounts") or {}).get("last_accounts") or {}
        if accounts.get("type"):
            # A useful size proxy: micro-entity and small-company filings have
            # statutory thresholds, so the filing type bounds the headcount.
            facts["Last accounts"] = accounts["type"]

        address = detail.get("registered_office_address") or {}
        locality = address.get("locality") or address.get("region")
        if locality:
            facts["Registered office"] = locality

        return facts

    def _summary(self, record: dict, facts: dict[str, str], confidence: str) -> str:
        name = record.get("title", "The company")
        parts = [f"{name} is an active UK-registered company"]
        if "Age" in facts:
            parts.append(f"incorporated {facts['Age']} ago")
        if "Registered office" in facts:
            parts.append(f"registered in {facts['Registered office']}")

        summary = ", ".join(parts) + "."
        if confidence != "high":
            summary += (
                " Name match is approximate, so confirm this is the same"
                " organisation before quoting any of it."
            )
        return summary
