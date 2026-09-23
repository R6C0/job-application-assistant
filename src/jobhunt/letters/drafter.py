"""Cover letter drafting.

The goal is a letter that reads as yours because it substantially is: built from
evidence bullets you wrote, about a posting you are actually suited to, and
edited by you before it goes anywhere. That is also the only honest way to get
one, and it happens to produce better letters than asking a model to be
impressive about a CV it has just met.

Four things do most of the work:

**A closed set of facts.** The drafter may only use `Evidence` entries from your
profile. The prompt says so, and `_validate` checks the output for numbers that
do not appear in the supplied evidence. A model that cannot invent an
achievement will use a real one.

**Your own phrasing.** `voice_sample` from the profile is given as the style to
match. Without it, everything regresses to the same register.

**A ban list.** The stock phrases that make a letter read as generated are
rejected outright, and a failed draft is retried once with the offending phrases
named.

**Structural variety.** The opening move is chosen deterministically from the
job id, so consecutive applications do not all begin the same way. Deterministic
rather than random so that regenerating a letter gives the same shape, which
makes editing predictable.
"""

from __future__ import annotations

import hashlib
import logging
import re

from ..matching.profile import Evidence, Profile
from ..matching.skills import canonicalise
from ..models import CompanyBrief, Job, Letter, Match

log = logging.getLogger(__name__)

#: Phrases that mark a letter as machine-written, or as saying nothing.
BANNED_PHRASES = [
    "i am writing to express",
    "i am excited to apply",
    "i was thrilled to see",
    "perfect fit",
    "perfect candidate",
    "i am passionate about",
    "passionate about leveraging",
    "leverage my skills",
    "dynamic environment",
    "fast-paced environment",
    "wealth of experience",
    "proven track record",
    "delve into",
    "tapestry",
    "in today's ever-evolving",
    "as a highly motivated",
    "team player with excellent communication",
    "i believe i would be a great asset",
    "thank you for considering my application",
]

#: Openings, picked by job id. Each forces a different first sentence shape.
OPENING_MOVES = [
    "Open by naming the single most relevant thing you have done, with its number, "
    "and connect it to the specific problem the posting describes.",
    "Open with the concrete detail of the role that matches your experience most "
    "closely, then say what you have done that maps onto it.",
    "Open with the scale or constraint you have worked under that is comparable to "
    "theirs, stated plainly, then what came of it.",
]

_NUMBER_RE = re.compile(r"\b\d[\d,.]*\s*(?:%|k|m|bn)?\b", re.IGNORECASE)

#: Words that keep their capital when a sentence is spliced in after a colon.
#: Without this, "I wrote the PySpark layer" becomes "i wrote the PySpark layer".
_KEEP_CAPITAL = {"I", "I'm", "I've", "AWS", "ETL", "SQL", "API", "UK", "CSV", "BI"}


def _decapitalise(text: str) -> str:
    """Lowercase a sentence's first word so it can follow a colon."""
    first, separator, rest = text.partition(" ")
    if first in _KEEP_CAPITAL or (first.isupper() and len(first) > 1):
        return text
    return first.lower() + separator + rest


SYSTEM_PROMPT = """You draft cover letters for a specific person applying to a \
specific job.

Hard rules:
- Use ONLY the achievements in EVIDENCE. Never invent a project, employer, \
number, date or technology.
- Every factual claim must trace to an evidence item. If evidence is thin, write \
a shorter letter rather than padding it.
- Match the VOICE SAMPLE's register: sentence length, directness, vocabulary.
- British English.
- No bullet points. Three or four short paragraphs.
- Do not restate the job title back at them or flatter the company.
- Do not use any phrase from BANNED PHRASES, or anything close to one.
- Plain sign-off. No "I look forward to hearing from you".

You are writing a first draft that the applicant will edit. Make it specific \
enough to be worth editing."""


class LetterDrafter:
    """Drafts with Claude when a key is present, falls back to a template.

    The fallback is not a placeholder. It produces a usable letter offline, and
    it exists so the pipeline degrades instead of stopping when the API is down
    or the key runs out.
    """

    def __init__(
        self,
        profile: Profile,
        backend: str = "auto",
        model: str = "claude-sonnet-5",
        max_words: int = 320,
        api_key: str | None = None,
    ) -> None:
        self.profile = profile
        self.backend = backend
        self.model = model
        self.max_words = max_words
        self.api_key = api_key

    @property
    def _can_use_claude(self) -> bool:
        if self.backend == "template":
            return False
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            log.info("anthropic package not installed; using template drafter")
            return False
        return True

    # ------------------------------------------------------------------
    def draft(self, job: Job, match: Match, brief: CompanyBrief | None = None) -> Letter:
        evidence = self._select_evidence(match)

        if self._can_use_claude:
            try:
                body = self._draft_with_claude(job, match, brief, evidence)
                return Letter(
                    body=body,
                    evidence_used=[e.id for e in evidence],
                    generator="claude",
                )
            except Exception as exc:
                log.warning("claude drafting failed (%s); falling back to template", exc)

        return Letter(
            body=self._draft_from_template(job, match, evidence),
            evidence_used=[e.id for e in evidence],
            generator="template",
        )

    # ------------------------------------------------------------------
    def _select_evidence(self, match: Match, limit: int = 4) -> list[Evidence]:
        """The evidence the posting actually asked for, best first."""
        chosen = self.profile.evidence_for(match.matched_skills)[:limit]
        if chosen:
            return chosen
        # Nothing matched on skills: fall back to whatever is most quantified,
        # so the letter still says something concrete.
        return sorted(self.profile.evidence, key=lambda e: bool(e.metric), reverse=True)[:2]

    def _opening_move(self, job: Job) -> str:
        digest = hashlib.sha256(job.id.encode()).digest()
        return OPENING_MOVES[digest[0] % len(OPENING_MOVES)]

    def _draft_with_claude(
        self,
        job: Job,
        match: Match,
        brief: CompanyBrief | None,
        evidence: list[Evidence],
    ) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = self._build_prompt(job, match, brief, evidence)

        body = self._call(client, prompt)
        problems = self._validate(body, evidence)
        if problems:
            log.info("redrafting: %s", "; ".join(problems))
            body = self._call(
                client,
                prompt
                + "\n\nYour previous draft was rejected for: "
                + "; ".join(problems)
                + "\nWrite it again without those problems.",
            )
            remaining = self._validate(body, evidence)
            if remaining:
                # Two strikes. A template letter is better than one that
                # confidently states something untrue about you.
                raise ValueError(f"draft still invalid: {'; '.join(remaining)}")
        return body.strip()

    def _call(self, client, prompt: str) -> str:  # noqa: ANN001 - SDK type
        response = client.messages.create(
            model=self.model,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def _build_prompt(
        self,
        job: Job,
        match: Match,
        brief: CompanyBrief | None,
        evidence: list[Evidence],
    ) -> str:
        evidence_block = "\n".join(
            f"- [{e.id}] {e.text}"
            + (f" (metric: {e.metric})" if e.metric else "")
            + (f" (context: {e.context})" if e.context else "")
            for e in evidence
        )
        reasons = "\n".join(f"- {r.component}: {r.detail}" for r in match.top_reasons(4))

        company_block = ""
        if brief and brief.is_useful:
            facts = "; ".join(f"{k}: {v}" for k, v in brief.facts.items())
            company_block = (
                f"\nCOMPANY (public record, confidence {brief.confidence}):\n{facts}\n"
                "Use at most one of these, only if it genuinely bears on why you are "
                "applying. Do not use it to flatter them."
            )

        voice = self.profile.voice_sample or "(no sample given: write plainly and directly)"
        skills_line = ", ".join(match.matched_skills) or "none detected"

        return f"""JOB
Title: {job.title}
Company: {job.company}
Location: {job.location or 'not stated'}

DESCRIPTION
{job.description[:3500]}
{company_block}

WHY THIS WAS FLAGGED AS A MATCH
{reasons}
Skills the posting asks for that the applicant has: {skills_line}

EVIDENCE (the only facts you may use)
{evidence_block}

VOICE SAMPLE (match this register)
{voice}

BANNED PHRASES
{chr(10).join('- ' + p for p in BANNED_PHRASES)}

INSTRUCTIONS
{self._opening_move(job)}
Address it to the hiring team. Sign off as {self.profile.name}.
Maximum {self.max_words} words. Return the letter only, no preamble or commentary."""

    # ------------------------------------------------------------------
    def _validate(self, body: str, evidence: list[Evidence]) -> list[str]:
        """Reject drafts that are generic, over-long, or making things up."""
        problems: list[str] = []
        lowered = body.lower()

        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                problems.append(f"uses banned phrase {phrase!r}")

        words = len(body.split())
        if words > self.max_words * 1.15:
            problems.append(f"{words} words, limit is {self.max_words}")

        # Any number in the letter should be traceable to the evidence given.
        # This is the cheap version of a fabrication check and it catches the
        # common failure, which is a model rounding "380,000" up to "half a
        # million" or inventing a percentage.
        allowed = " ".join(f"{e.text} {e.metric or ''}" for e in evidence)
        allowed_numbers = {n.strip().lower() for n in _NUMBER_RE.findall(allowed)}
        for number in _NUMBER_RE.findall(body):
            token = number.strip().lower()
            if len(token) < 2:          # single digits: "3 years", too noisy
                continue
            if token not in allowed_numbers:
                problems.append(f"number {token!r} is not in the supplied evidence")

        return problems

    # ------------------------------------------------------------------
    def _headline_skills(self, match: Match, limit: int = 4) -> list[str]:
        """The skills worth naming, most important first.

        `Match.matched_skills` is sorted alphabetically for stable storage, which
        is the wrong order to quote: it put "airflow, databricks, dbt" ahead of
        Python and SQL on a posting that listed the first three as nice-to-have.
        Core skills lead, in the order you declared them.
        """
        matched = set(match.matched_skills)
        core = [canonicalise(s) for s in self.profile.must_have_skills]
        secondary = [canonicalise(s) for s in self.profile.nice_to_have_skills]

        ordered = [s for s in core if s in matched]
        ordered += [s for s in secondary if s in matched and s not in ordered]
        ordered += [s for s in match.matched_skills if s not in ordered]
        return ordered[:limit]

    def _draft_from_template(
        self, job: Job, match: Match, evidence: list[Evidence]
    ) -> str:
        """Deterministic fallback. Plain, specific, and obviously a draft."""
        lead = evidence[0] if evidence else None
        others = evidence[1:3]

        skills = ", ".join(self._headline_skills(match)) or "the areas listed"

        paragraphs = [
            "Dear hiring team,",
            (
                f"I am applying for the {job.title} role. "
                + (
                    f"{lead.text}"
                    + (f" ({lead.metric})" if lead and lead.metric else "")
                    + "."
                    if lead
                    else f"My background is in {skills}."
                )
            ),
        ]

        if others:
            paragraphs.append(
                "Alongside that: " + "; ".join(_decapitalise(e.text) for e in others) + "."
            )

        paragraphs.append(
            f"The posting asks for {skills}, which is where most of my work has been. "
            "I would welcome the chance to talk it through."
        )
        paragraphs.append(f"Regards,\n{self.profile.name}")

        return "\n\n".join(paragraphs)
