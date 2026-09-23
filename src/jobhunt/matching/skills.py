"""Skill detection.

Substring matching on a job description is wrong in both directions. "R" matches
every word containing it; "Power BI" is missed when the posting writes "PowerBI";
"Go" matches "going". This module is the small amount of care that turns skill
matching from a coin flip into something you can trust enough to put in a letter.

Three rules:

1. Match on word boundaries, not substrings.
2. Carry an alias list, because the same skill has several spellings.
3. Require a longer form for names that are also ordinary words.
"""

from __future__ import annotations

import re

#: Canonical name -> spellings seen in the wild. Lowercase throughout.
ALIASES: dict[str, list[str]] = {
    "python": ["python", "python3"],
    "sql": ["sql", "t-sql", "tsql", "ansi sql"],
    "pyspark": ["pyspark", "py-spark"],
    "spark": ["apache spark", "spark"],
    "aws": ["aws", "amazon web services"],
    "s3": ["amazon s3", "aws s3", "s3 bucket"],
    "glue": ["aws glue", "glue etl", "glue crawler"],
    "lambda": ["aws lambda"],
    "athena": ["amazon athena", "aws athena"],
    "redshift": ["amazon redshift", "redshift"],
    "power bi": ["power bi", "powerbi", "power-bi"],
    "dax": ["dax"],
    "power query": ["power query", "power query m", "powerquery"],
    "power automate": ["power automate", "microsoft flow", "ms flow"],
    "azure devops": ["azure devops", "ado", "vsts"],
    "azure": ["microsoft azure", "azure"],
    "sharepoint": ["sharepoint"],
    "etl": ["etl", "elt", "etl/elt"],
    "data pipeline": ["data pipeline", "data pipelines", "pipeline development"],
    "data modelling": ["data modelling", "data modeling", "dimensional modelling"],
    "parquet": ["parquet"],
    "airflow": ["apache airflow", "airflow"],
    "dbt": ["dbt"],
    "snowflake": ["snowflake"],
    "databricks": ["databricks"],
    "kafka": ["apache kafka", "kafka"],
    "docker": ["docker", "containerisation", "containerization"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "git": ["git", "github", "version control"],
    "ci/cd": ["ci/cd", "cicd", "continuous integration"],
    "rest api": ["rest api", "restful api", "rest apis", "api integration"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "machine learning": ["machine learning", "ml", "mlops"],
    "llm": ["llm", "llms", "large language model", "generative ai", "genai"],
    "rag": ["rag", "retrieval augmented generation"],
    "prompt engineering": ["prompt engineering"],
    "luau": ["luau"],
    "lua": ["lua"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "excel": ["excel", "advanced excel"],
    "jupyter": ["jupyter", "jupyter notebook"],
    "linux": ["linux", "unix"],
}

#: Skills whose names are also common English words or too short to match on
#: their own. These are only detected via a longer alias.
AMBIGUOUS = {"r", "go", "c", "ts", "js", "ml", "ado", "dbt", "rag", "lambda"}


def _pattern(alias: str) -> re.Pattern[str]:
    # \b does the wrong thing next to "+" and "#", so the boundary is asserted
    # with lookarounds on "word-ish" characters instead.
    escaped = re.escape(alias)
    return re.compile(rf"(?<![a-z0-9+#]){escaped}(?![a-z0-9+#])", re.IGNORECASE)


_COMPILED: dict[str, list[re.Pattern[str]]] = {
    canonical: [_pattern(a) for a in aliases] for canonical, aliases in ALIASES.items()
}


def canonicalise(skill: str) -> str:
    """Map a spelling to its canonical name, or return it lowercased."""
    lowered = skill.strip().lower()
    for canonical, aliases in ALIASES.items():
        if lowered == canonical or lowered in aliases:
            return canonical
    return lowered


def find_skills(text: str, skills: list[str]) -> set[str]:
    """Which of `skills` appear in `text`, by canonical name."""
    if not text:
        return set()

    found: set[str] = set()
    for skill in skills:
        canonical = canonicalise(skill)
        patterns = _COMPILED.get(canonical)

        if patterns is None:
            # Unknown skill: match the literal, unless it is too short or a
            # common word, where a false positive is worse than a miss.
            if canonical in AMBIGUOUS or len(canonical) < 3:
                continue
            patterns = [_pattern(canonical)]

        if any(p.search(text) for p in patterns):
            found.add(canonical)

    return found


def count_mentions(text: str, skill: str) -> int:
    """How often a skill appears. A requirement repeated is a requirement meant."""
    canonical = canonicalise(skill)
    patterns = _COMPILED.get(canonical) or [_pattern(canonical)]
    return sum(len(p.findall(text)) for p in patterns)
