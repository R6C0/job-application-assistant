"""Skill detection tests.

Every case here is a false positive or false negative that substring matching
produces, and each one would end up quoted in a cover letter.
"""

from __future__ import annotations

from jobhunt.matching.skills import canonicalise, count_mentions, find_skills


class TestFalsePositives:
    def test_r_does_not_match_every_word(self):
        assert find_skills("We are a growing retail brand", ["R"]) == set()

    def test_go_does_not_match_going(self):
        assert find_skills("Going forward we will grow", ["Go"]) == set()

    def test_sql_does_not_match_inside_a_word(self):
        assert find_skills("nosqlish", ["SQL"]) == set()

    def test_ml_alone_is_ignored(self):
        assert find_skills("The HTML template", ["ML"]) == set()


class TestFalseNegatives:
    def test_powerbi_without_a_space(self):
        assert find_skills("Experience with PowerBI dashboards", ["Power BI"]) == {"power bi"}

    def test_aws_spelled_out(self):
        assert find_skills("Built on Amazon Web Services", ["AWS"]) == {"aws"}

    def test_case_insensitive(self):
        assert find_skills("PYTHON and python", ["Python"]) == {"python"}

    def test_punctuation_boundaries(self):
        assert find_skills("Skills: Python, SQL; ETL.", ["Python", "SQL", "ETL"]) == {
            "python",
            "sql",
            "etl",
        }

    def test_ci_cd_with_a_slash(self):
        assert find_skills("Own the CI/CD pipeline", ["CI/CD"]) == {"ci/cd"}


class TestCanonicalise:
    def test_maps_aliases_to_one_name(self):
        assert canonicalise("PowerBI") == "power bi"
        assert canonicalise("power-bi") == "power bi"
        assert canonicalise("Amazon Web Services") == "aws"

    def test_unknown_skill_is_lowercased(self):
        assert canonicalise("Fortran") == "fortran"


class TestEmphasis:
    def test_counts_repeats(self):
        text = "Python is required. Strong Python. Did we mention Python?"
        assert count_mentions(text, "Python") == 3

    def test_counts_across_aliases(self):
        assert count_mentions("Power BI and PowerBI", "Power BI") == 2


def test_empty_text_finds_nothing():
    assert find_skills("", ["Python"]) == set()


def test_unknown_skills_still_match_literally():
    assert find_skills("We use Fortran here", ["Fortran"]) == {"fortran"}
