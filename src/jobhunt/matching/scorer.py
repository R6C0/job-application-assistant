"""Scoring, with its reasoning attached.

Two rules shape this module.

**Blockers are separate from points.** A job that requires 10 years when you
have 3, or sits outside your salary floor, is not "a low score". It is out, and
mixing the two lets a job clear a threshold on the strength of unrelated
keyword matches. `Match.blockers` is a hard filter; `Match.score` only ranks
what survived it.

**Every point carries a `Reason`.** Not for display: the reasons are what the
letter drafter uses to decide which of your evidence bullets to cite. A scorer
that returned a bare number would leave the drafter guessing, and a guessing
drafter writes "I am passionate about data".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ..config import ScoringConfig
from ..models import Job, Match, Reason
from .profile import Profile
from .skills import canonicalise, count_mentions, find_skills

SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "principal"]

_SENIORITY_TERMS = {
    "junior": ["junior", "graduate", "entry level", "entry-level", "trainee", "apprentice"],
    "mid": ["mid-level", "mid level", "midweight"],
    "senior": ["senior", "snr"],
    "lead": ["lead", "team lead", "tech lead"],
    "principal": ["principal", "staff engineer", "head of"],
}

_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years?", re.IGNORECASE)


def _detect_seniority(title: str, description: str) -> str | None:
    """Prefer the title: a description often mentions the whole ladder."""
    for level, terms in _SENIORITY_TERMS.items():
        if any(t in title.lower() for t in terms):
            return level
    head = description[:600].lower()
    for level, terms in _SENIORITY_TERMS.items():
        if any(t in head for t in terms):
            return level
    return None


def _required_years(description: str) -> int | None:
    """The largest plausible "N years" in the text.

    Largest, not first, because postings routinely say "3 years of Python, 5
    years of SQL" and the binding constraint is the biggest one. Anything above
    15 is treated as noise: it is nearly always a company age or a salary.
    """
    years = [int(m.group(1)) for m in _YEARS_RE.finditer(description)]
    plausible = [y for y in years if 0 < y <= 15]
    return max(plausible) if plausible else None


class Scorer:
    def __init__(self, profile: Profile, config: ScoringConfig) -> None:
        self.profile = profile
        self.config = config

    def score(self, job: Job) -> Match:
        text = f"{job.title}\n{job.description}"
        weights = self.config.weights
        reasons: list[Reason] = []

        blockers = self._blockers(job, text)

        matched, missing, skill_reason = self._score_skills(text, weights.get("skills", 40.0))
        reasons.append(skill_reason)
        reasons.append(self._score_title(job, weights.get("title", 20.0)))
        reasons.append(self._score_seniority(job, text, weights.get("seniority", 15.0)))
        reasons.append(self._score_location(job, weights.get("location", 10.0)))
        reasons.append(self._score_salary(job, weights.get("salary", 10.0)))
        reasons.append(self._score_recency(job, weights.get("recency", 5.0)))

        total_possible = sum(weights.values()) or 1.0
        raw = sum(r.points for r in reasons)
        score = max(0.0, min(100.0, raw / total_possible * 100.0))

        return Match(
            score=round(score, 1),
            reasons=reasons,
            matched_skills=sorted(matched),
            missing_skills=sorted(missing),
            blockers=blockers,
        )

    # ------------------------------------------------------------------
    # Hard filters
    # ------------------------------------------------------------------
    def _blockers(self, job: Job, text: str) -> list[str]:
        prefs = self.profile.preferences
        blockers: list[str] = []

        for company in prefs.exclude_companies:
            if company.strip().lower() in job.company.lower():
                blockers.append(f"excluded company: {job.company}")

        for term in prefs.exclude_title_terms:
            if term.strip().lower() in job.title.lower():
                blockers.append(f"excluded title term: {term!r}")

        if job.contract_type == "contract" and not prefs.contract_ok:
            blockers.append("contract role, and contract_ok is false")

        # Salary: only block when the posting's own ceiling is below the floor.
        # A missing salary is not evidence of a low one, and blocking on absent
        # data would discard most of the market.
        if prefs.min_salary and job.salary_max and job.salary_max < prefs.min_salary:
            blockers.append(
                f"salary max {job.salary_max:,.0f} below floor {prefs.min_salary:,.0f}"
            )

        required = _required_years(job.description)
        if required is not None:
            ceiling = {"junior": 3, "mid": 6, "senior": 10}.get(self.profile.seniority, 10)
            if required > ceiling:
                blockers.append(f"asks for {required} years experience")

        if job.remote is True and not prefs.remote_ok:
            blockers.append("remote role, and remote_ok is false")
        if job.remote is False and not prefs.onsite_ok:
            blockers.append("onsite role, and onsite_ok is false")

        return blockers

    # ------------------------------------------------------------------
    # Components
    # ------------------------------------------------------------------
    def _score_skills(self, text: str, weight: float) -> tuple[set[str], set[str], Reason]:
        must = {canonicalise(s) for s in self.profile.must_have_skills}
        nice = {canonicalise(s) for s in self.profile.nice_to_have_skills}

        found_must = find_skills(text, list(must))
        found_nice = find_skills(text, list(nice))
        matched = found_must | found_nice

        # Weighted by how often the posting repeats a skill, capped so that one
        # skill mentioned nine times cannot outvote four mentioned once.
        def emphasis(skill: str) -> float:
            return min(count_mentions(text, skill), 3) / 3.0

        must_score = sum(0.6 + 0.4 * emphasis(s) for s in found_must)
        nice_score = sum(0.3 + 0.2 * emphasis(s) for s in found_nice)

        denominator = max(len(must), 1)
        ratio = min((must_score + nice_score) / denominator, 1.0)
        points = weight * ratio

        # "Missing" means required by you and absent from the posting, which is
        # a signal the role is pointed elsewhere, not a gap in your CV.
        missing = must - found_must

        detail = (
            f"{len(found_must)}/{len(must)} core skills present"
            + (f", plus {len(found_nice)} secondary" if found_nice else "")
        )
        return (
            matched,
            missing,
            Reason("skills", round(points, 2), detail, sorted(matched)),
        )

    def _score_title(self, job: Job, weight: float) -> Reason:
        title = job.title.lower()
        best = 0.0
        matched_role = None

        for role in self.profile.target_roles:
            words = [w for w in role.lower().split() if len(w) > 2]
            if not words:
                continue
            overlap = sum(1 for w in words if w in title) / len(words)
            if overlap > best:
                best, matched_role = overlap, role

        detail = (
            f"title matches target role {matched_role!r}"
            if best >= 0.5
            else f"title {job.title!r} is not a close match to any target role"
        )
        return Reason("title", round(weight * best, 2), detail, [job.title])

    def _score_seniority(self, job: Job, text: str, weight: float) -> Reason:
        detected = _detect_seniority(job.title, job.description)
        if detected is None:
            # Unstated seniority is genuinely unknown. Award the midpoint rather
            # than punishing a posting for being terse.
            return Reason("seniority", round(weight * 0.5, 2), "seniority not stated")

        mine = SENIORITY_ORDER.index(self.profile.seniority)
        theirs = SENIORITY_ORDER.index(detected) if detected in SENIORITY_ORDER else mine
        gap = theirs - mine

        if gap == 0:
            points, detail = weight, f"{detected} matches your level"
        elif gap == 1:
            points, detail = weight * 0.7, f"{detected}: one step up, a stretch"
        elif gap == -1:
            points, detail = weight * 0.4, f"{detected}: one step down"
        else:
            points, detail = weight * 0.1, f"{detected}: {abs(gap)} levels away"

        return Reason("seniority", round(points, 2), detail)

    def _score_location(self, job: Job, weight: float) -> Reason:
        prefs = self.profile.preferences

        if job.remote and prefs.remote_ok:
            return Reason("location", weight, "remote, which you accept")

        location = (job.location or "").lower()
        for wanted in prefs.locations:
            if wanted.strip().lower() in location:
                return Reason("location", weight, f"in {job.location}")

        if not location:
            return Reason("location", round(weight * 0.5, 2), "location not stated")

        return Reason("location", round(weight * 0.2, 2), f"{job.location} is outside your list")

    def _score_salary(self, job: Job, weight: float) -> Reason:
        floor = self.profile.preferences.min_salary
        if not floor:
            return Reason("salary", round(weight * 0.5, 2), "no salary preference set")
        if not (job.salary_min or job.salary_max):
            # Most UK postings hide salary. Neutral, not negative.
            return Reason("salary", round(weight * 0.5, 2), "salary not advertised")

        top = job.salary_max or job.salary_min or 0
        if top >= floor * 1.2:
            return Reason("salary", weight, f"up to {top:,.0f}, comfortably above your floor")
        if top >= floor:
            return Reason("salary", round(weight * 0.7, 2), f"up to {top:,.0f}, above your floor")
        return Reason("salary", 0.0, f"up to {top:,.0f}, below your floor")

    def _score_recency(self, job: Job, weight: float) -> Reason:
        if not job.posted_at:
            return Reason("recency", round(weight * 0.5, 2), "posting date unknown")

        posted = job.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        days = (datetime.now(UTC) - posted).days

        if days <= 2:
            return Reason("recency", weight, "posted in the last 2 days")
        if days <= 7:
            return Reason("recency", round(weight * 0.7, 2), f"posted {days} days ago")
        if days <= 21:
            return Reason("recency", round(weight * 0.3, 2), f"posted {days} days ago")
        return Reason("recency", 0.0, f"posted {days} days ago, likely filled")
