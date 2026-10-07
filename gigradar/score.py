"""Job scoring against my profile: a Scorer gives each job a 0-100 score plus a short reason.

RuleScorer ("rules") is the explainable layer:
- hard rules: an excluded keyword, a disallowed tier, or pay below my minimum scores 0.
  Missing data passes (no rate/budget/tier given is not a bad job);
- skill overlap: job skills that are also in [profile].skills, saturating at N matches.
It's a usable baseline on its own and the rule/skill input for the embedding scorer (step 4).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from gigradar.jobfields import format_money, parse_amount
from gigradar.profile import Profile
from upwork_search import TIERS, Job

NEUTRAL = 50  # standalone score when the job lists no skills: unknown, not bad
_TIER_NAMES = {api: name for name, api in TIERS.items()}  # "ExpertLevel" -> "expert"


@dataclass(frozen=True)
class Score:
    value: int      # 0-100
    reason: str     # "matched: Rust, PostgreSQL" | "✗ fixed $300 < min $1,000"
    scorer: str
    version: str    # bump when the logic changes, so old and new scores stay comparable


class Scorer(Protocol):
    name: str
    version: str

    def score(self, jobs: Sequence[Job], profile: Profile) -> list[Score]: ...


@dataclass(frozen=True)
class RuleResult:
    rejected: str | None        # reason of the hard rule that fired, else None
    matched: list[str]          # job skills (as the job lists them) that are in my profile
    overlap: float | None       # 0..1; None = the job lists no skills


def normalize_skill(skill: str) -> str:
    """Next.js = nextjs = Next JS; C#, C++ and C stay distinct."""
    return re.sub(r"[\s.\-_]", "", skill.casefold())


def job_skills(job: Job) -> list[str]:
    return [s.strip() for s in job.skills.split(",") if s.strip()]


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Case-insensitive whole-term match; works for C++/.NET (\\b doesn't) and multi-word
    keywords with any whitespace in between."""
    body = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


class RuleScorer:
    name = "rules"
    version = "1"

    def __init__(self, skill_saturation: int) -> None:
        if skill_saturation < 1:
            raise ValueError(f"skill_saturation must be >= 1, got {skill_saturation}")
        self.skill_saturation = skill_saturation

    def score(self, jobs: Sequence[Job], profile: Profile) -> list[Score]:
        rules = _CompiledProfile(profile)
        return [self._to_score(self._evaluate(rules, job)) for job in jobs]

    def evaluate(self, jobs: Sequence[Job], profile: Profile) -> list[RuleResult]:
        rules = _CompiledProfile(profile)
        return [self._evaluate(rules, job) for job in jobs]

    def _evaluate(self, rules: _CompiledProfile, job: Job) -> RuleResult:
        skills = job_skills(job)
        seen: set[str] = set()
        matched = []
        for skill in skills:
            key = normalize_skill(skill)
            if key in rules.skills and key not in seen:
                seen.add(key)
                matched.append(skill)
        overlap = min(len(matched), self.skill_saturation) / self.skill_saturation if skills else None
        return RuleResult(rejected=rules.reject_reason(job), matched=matched, overlap=overlap)

    def _to_score(self, result: RuleResult) -> Score:
        if result.rejected:
            return Score(0, result.rejected, self.name, self.version)
        if result.overlap is None:
            return Score(NEUTRAL, "no skills listed", self.name, self.version)
        reason = f"matched: {', '.join(result.matched)}" if result.matched else "no skill match"
        return Score(round(100 * result.overlap), reason, self.name, self.version)


class _CompiledProfile:
    """The profile's rule settings, prepared once per scoring batch."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.skills = {normalize_skill(s) for s in profile.skills}
        self.keywords = [(kw, keyword_pattern(kw)) for kw in profile.exclude_keywords]
        self.tiers = {TIERS[t] for t in profile.tiers}

    def reject_reason(self, job: Job) -> str | None:
        """The first hard rule that fires, in order: keyword, tier, hourly, fixed."""
        text = f"{job.title}\n{job.description}"
        for keyword, pattern in self.keywords:
            if pattern.search(text):
                return f'✗ excluded keyword "{keyword}"'
        if self.tiers and job.tier and job.tier not in self.tiers:
            return f"✗ tier {_TIER_NAMES.get(job.tier, job.tier)} not wanted"
        kind = (job.job_type or "").upper()
        minimum = self.profile.min_hourly
        if kind == "HOURLY" and minimum is not None:
            top = parse_amount(job.hourly_max) or parse_amount(job.hourly_min)  # highest rate offered
            if top is not None and top < minimum:
                return f"✗ hourly up to {format_money(top)} < min {format_money(minimum)}"
        minimum = self.profile.min_fixed
        if kind == "FIXED" and minimum is not None:
            budget = parse_amount(job.fixed_budget)
            if budget is not None and budget < minimum:
                return f"✗ fixed {format_money(budget)} < min {format_money(minimum)}"
        return None
