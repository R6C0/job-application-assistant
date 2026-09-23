"""Your CV, as structured data.

The profile is deliberately not a parsed PDF. Parsing a CV gives you a pile of
text that a model then has to interpret on every single job, which is both
expensive and a licence to invent. Writing the CV out once, by hand, as skills
and evidence bullets means the scorer has something exact to match on and the
letter drafter has a closed set of facts it is allowed to use.

`Evidence` is the important type. Each one is a claim you can actually defend in
an interview, tagged with the skills it demonstrates. The drafter may only cite
these, which is what stops it inventing an achievement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    skills: list[str] = field(default_factory=list)
    metric: str | None = None      # the number, if there is one
    context: str | None = None     # where it happened, for the letter's framing

    def mentions_any(self, skills: set[str]) -> bool:
        return bool(skills.intersection(s.lower() for s in self.skills))


@dataclass
class Preferences:
    min_salary: float | None = None
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True
    onsite_ok: bool = True
    contract_ok: bool = False
    exclude_companies: list[str] = field(default_factory=list)
    exclude_title_terms: list[str] = field(default_factory=list)


@dataclass
class Profile:
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    headline: str | None = None
    target_roles: list[str] = field(default_factory=list)
    seniority: str = "mid"           # junior | mid | senior
    must_have_skills: list[str] = field(default_factory=list)
    nice_to_have_skills: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    preferences: Preferences = field(default_factory=Preferences)
    voice_sample: str | None = None

    @property
    def all_skills(self) -> list[str]:
        return [*self.must_have_skills, *self.nice_to_have_skills]

    def evidence_for(self, skills: list[str]) -> list[Evidence]:
        """Evidence demonstrating any of `skills`, strongest first.

        "Strongest" means covering the most requested skills, then having a
        number in it. A bullet with a measured outcome beats one without, every
        time, and that ordering is the entire trick to a letter that reads as
        specific rather than enthusiastic.
        """
        wanted = {s.lower() for s in skills}
        scored: list[tuple[int, int, Evidence]] = []
        for item in self.evidence:
            overlap = len(wanted.intersection(s.lower() for s in item.skills))
            if overlap:
                scored.append((overlap, 1 if item.metric else 0, item))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [item for _, _, item in scored]


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    if not path.exists():
        raise ProfileError(
            f"no profile at {path}. Copy profile.example.yaml to {path} and fill it in."
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    for required in ("name", "email"):
        if not raw.get(required):
            raise ProfileError(f"profile is missing required field {required!r}")

    evidence = [Evidence(**item) for item in raw.get("evidence", [])]
    if not evidence:
        raise ProfileError(
            "profile has no evidence entries. Without them the letter drafter has "
            "nothing it is allowed to say about you."
        )

    seen_ids = [item.id for item in evidence]
    duplicates = {i for i in seen_ids if seen_ids.count(i) > 1}
    if duplicates:
        raise ProfileError(f"duplicate evidence ids: {', '.join(sorted(duplicates))}")

    return Profile(
        name=raw["name"],
        email=raw["email"],
        phone=raw.get("phone"),
        location=raw.get("location"),
        headline=raw.get("headline"),
        target_roles=raw.get("target_roles", []),
        seniority=raw.get("seniority", "mid"),
        must_have_skills=raw.get("must_have_skills", []),
        nice_to_have_skills=raw.get("nice_to_have_skills", []),
        evidence=evidence,
        preferences=Preferences(**(raw.get("preferences") or {})),
        voice_sample=raw.get("voice_sample"),
    )
