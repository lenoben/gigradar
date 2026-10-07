"""Job scoring against my profile: a Scorer gives each job a 0-100 score plus a short reason.

RuleScorer ("rules") is the explainable layer:
- hard rules: an excluded keyword, a disallowed tier, or pay below my minimum scores 0.
  Missing data passes (no rate/budget/tier given is not a bad job);
- skill overlap: job skills that are also in [profile].skills, saturating at N matches.
It's a usable baseline on its own and the rule/skill input for the embedding scorer.

EmbeddingScorer ("embed") adds the semantic match: the job is compared with every profile
section, the best one (max cosine) wins and names the reason. Score = 0 if a hard rule
fired, else the weighted mean of semantic (cosine scaled from [cos_low, cos_high] to 0..1)
and skill overlap; a job without skills is scored on the semantic part alone.

    python -m gigradar.score --dry-run [--config PATH] [--limit N]

scores the stored jobs read-only (nothing is written) and prints the cosine distribution
plus the best and worst jobs, for calibrating cos_low/cos_high.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Protocol

from gigradar.embed import Embedder, Vector, cosine
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


class EmbeddingCache(Protocol):
    def get(self, job_ids: Sequence[str], model: str) -> dict[str, Vector]: ...

    def put(self, vectors: dict[str, Vector], model: str) -> None: ...


class MemoryEmbeddingCache:
    """Per-process cache (dry runs, tests); the store's SqliteEmbeddingCache persists."""

    def __init__(self) -> None:
        self.vectors: dict[tuple[str, str], Vector] = {}

    def get(self, job_ids: Sequence[str], model: str) -> dict[str, Vector]:
        return {jid: self.vectors[(jid, model)] for jid in job_ids if (jid, model) in self.vectors}

    def put(self, vectors: dict[str, Vector], model: str) -> None:
        self.vectors.update({(jid, model): vec for jid, vec in vectors.items()})


@dataclass(frozen=True)
class EmbedResult:
    score: Score
    cosine: float           # best section's raw cosine (for calibration)
    section: str            # its heading
    rule: RuleResult


def job_text(job: Job) -> str:
    """Title and skills first: the model truncates long descriptions (~512 tokens)."""
    return f"{job.title}\nSkills: {job.skills}\n{job.description}"


def section_key(text: str) -> str:
    """Cache key of a profile section's embedding: changes whenever its text does (the model is
    the cache's second key). Never collides with job ids, which start with "~"."""
    return "profile:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def scale(value: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (value - low) / (high - low)))


class EmbeddingScorer:
    name = "embed"
    version = "1"

    def __init__(self, embedder: Embedder, rules: RuleScorer, cache: EmbeddingCache,
                 weight_semantic: float, weight_skills: float, cos_low: float, cos_high: float) -> None:
        self.embedder = embedder
        self.rules = rules
        self.cache = cache
        self.weight_semantic = weight_semantic
        self.weight_skills = weight_skills
        self.cos_low = cos_low
        self.cos_high = cos_high

    def score(self, jobs: Sequence[Job], profile: Profile) -> list[Score]:
        return [r.score for r in self.evaluate(jobs, profile)]

    def evaluate(self, jobs: Sequence[Job], profile: Profile) -> list[EmbedResult]:
        from gigradar.store import job_id  # store imports this module

        sections = profile.sections
        section_texts = [f"{s.heading}\n{s.text}" for s in sections]
        section_vecs = self._vectors(section_texts, [section_key(t) for t in section_texts])
        job_vecs = self._vectors([job_text(job) for job in jobs], [job_id(job) for job in jobs])
        results = []
        for job, vec, rule in zip(jobs, job_vecs, self.rules.evaluate(jobs, profile), strict=True):
            sims = [cosine(vec, s) for s in section_vecs]
            best = max(range(len(sims)), key=sims.__getitem__)
            heading = sections[best].heading
            semantic = scale(sims[best], self.cos_low, self.cos_high)
            results.append(EmbedResult(self._combine(rule, semantic, heading), sims[best], heading, rule))
        return results

    def _vectors(self, texts: list[str], keys: list[str | None]) -> list[Vector]:
        """Cached vectors where possible; embed the rest in one batch and cache those with a key.
        Keys: a job's ~cipher, or section_key() for profile sections."""
        model = self.embedder.model_id
        cached = self.cache.get([key for key in keys if key is not None], model)
        missing = [k for k, key in enumerate(keys) if key is None or key not in cached]
        fresh = dict(zip(missing, self.embedder.embed([texts[k] for k in missing]), strict=True))
        new_entries = {keys[k]: vec for k, vec in fresh.items() if keys[k] is not None}
        if new_entries:
            self.cache.put(new_entries, model)
        return [fresh[k] if k in fresh else cached[keys[k]] for k in range(len(texts))]

    def _combine(self, rule: RuleResult, semantic: float, heading: str) -> Score:
        if rule.rejected:
            return Score(0, rule.rejected, self.name, self.version)
        total, weights = self.weight_semantic * semantic, self.weight_semantic
        if rule.overlap is not None:  # no skills listed: semantic alone
            total += self.weight_skills * rule.overlap
            weights += self.weight_skills
        value = round(100 * total / weights) if weights > 0 else NEUTRAL
        reason = heading + (f" · matched: {', '.join(rule.matched)}" if rule.matched else "")
        return Score(value, reason, self.name, self.version)


def load_stored_jobs(db_path: Path) -> list[Job]:
    """All stored jobs, oldest first, via a read-only connection (SQLite refuses writes)."""
    import json
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        names = {f.name for f in fields(Job)}
        return [Job(**{k: v for k, v in json.loads(payload).items() if k in names})
                for (payload,) in conn.execute("SELECT payload FROM seen_jobs ORDER BY first_seen, job_id")]
    finally:
        conn.close()


def dry_run(jobs: Sequence[Job], profile: Profile, scorer: EmbeddingScorer, limit: int, out) -> None:
    """Score `jobs` and print calibration numbers: cosine distribution, best/worst, rejects."""
    if not jobs:
        print("no stored jobs", file=out)
        return
    started = time.perf_counter()
    results = scorer.evaluate(jobs, profile)
    elapsed = time.perf_counter() - started
    print(f"{len(jobs)} jobs scored in {elapsed:.2f} s ({1000 * elapsed / len(jobs):.0f} ms/job), "
          f"model {scorer.embedder.model_id}", file=out)

    cosines = sorted(r.cosine for r in results)
    q1, median, q3 = statistics.quantiles(cosines, n=4) if len(cosines) > 1 else cosines * 3
    print(f"best-section cosine: min {cosines[0]:.3f}  p25 {q1:.3f}  median {median:.3f}  p75 {q3:.3f}  "
          f"max {cosines[-1]:.3f}  (cos_low {scorer.cos_low}, cos_high {scorer.cos_high})", file=out)
    counts = {s.heading: sum(r.section == s.heading for r in results) for s in profile.sections}
    print(f"best section per job: {counts}", file=out)

    rejected = [(job, r) for job, r in zip(jobs, results) if r.rule.rejected]
    ranked = sorted(((job, r) for job, r in zip(jobs, results) if not r.rule.rejected),
                    key=lambda pair: pair[1].score.value, reverse=True)
    print(f"rejected by hard rules: {len(rejected)} of {len(jobs)}", file=out)
    for title, chunk in (("top", ranked[:limit]), ("bottom", ranked[::-1][:limit])):
        print(f"\n{title} {len(chunk)}:", file=out)
        for job, r in chunk:
            print(f"  {r.score.value:3d}  cos {r.cosine:.3f}  {r.score.reason[:55]:<55}  {job.title[:55]}", file=out)
    if rejected:
        print(f"\nrejected (first {min(limit, len(rejected))}):", file=out)
        for job, r in rejected[:limit]:
            print(f"    0  {r.rule.rejected[:55]:<55}  {job.title[:55]}", file=out)


def main(argv: Sequence[str]) -> int:
    from gigradar.config import ConfigError, default_paths, load_config, load_dotenv
    from gigradar.embed import EmbedderError, FastEmbedder

    parser = argparse.ArgumentParser(prog="python -m gigradar.score", description="score stored jobs, read-only")
    parser.add_argument("--dry-run", action="store_true", required=True, help="read-only: writes nothing")
    parser.add_argument("--config", type=Path, default=default_paths()[0], help="gigradar.toml path")
    parser.add_argument("--limit", type=int, default=10, help="jobs listed at the top and bottom")
    args = parser.parse_args(argv)

    environ = dict(os.environ)
    try:
        load_dotenv(args.config.parent / ".env", environ)
        cfg = load_config(args.config, environ)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    if cfg.profile is None:
        print("config: no [profile] table in gigradar.toml (and it needs profile.md)", file=sys.stderr)
        return 2
    s = cfg.scoring
    try:
        started = time.perf_counter()
        embedder = FastEmbedder(s.model, s.model_dir, offline=True)  # exactly as scheduled runs will load it
    except EmbedderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"model loaded offline in {time.perf_counter() - started:.2f} s")
    scorer = EmbeddingScorer(embedder, RuleScorer(s.skill_saturation), MemoryEmbeddingCache(),
                             s.weight_semantic, s.weight_skills, s.cos_low, s.cos_high)
    dry_run(load_stored_jobs(cfg.db_path), cfg.profile, scorer, args.limit, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # job titles with characters cp1252 can't print
    sys.exit(main(sys.argv[1:]))
