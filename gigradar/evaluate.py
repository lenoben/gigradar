"""Evaluate scorer variants against my labels (+1 = would apply, -1 = not for me). Stdlib only.

Metrics, per variant, on the labeled jobs:
- AUC: probability that a random 👍 job scores higher than a random 👎 one (0.5 = coin toss);
- precision@5 / @10: share of 👍 among the top-scored jobs;
- a bootstrap 95% interval for AUC, and for the AUC difference against the current config
  (paired: same resampled jobs), so a "better" variant has to be better beyond the noise.
With ~50 labels these numbers are rough: the report leads with the 👍/👎 counts and, if
either class has fewer than MIN_PER_CLASS labels, marks every metric unreliable and makes
no recommendation.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TextIO

from gigradar.config import ScoringConfig
from gigradar.embed import Embedder
from gigradar.profile import Profile
from gigradar.score import EmbeddingCache, RuleScorer, Score, job_text, scorer_from_config
from gigradar.store import job_id
from upwork_search import Job

MIN_PER_CLASS = 10
BOOTSTRAP_ROUNDS = 1000
SEED = 7                      # fixed: the same labels always give the same report
KEEP_GOOD = 0.95              # suggested min_score keeps at least this share of 👍 jobs
CLEAR_GAIN = 0.05             # a variant must beat the current AUC by this much ...
BM25_K1, BM25_B = 1.5, 0.75   # ... and its paired interval must exclude 0


def auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Mann-Whitney AUC with ties counted half (via average ranks, O(n log n));
    None if a class is missing."""
    n_pos = sum(label > 0 for label in labels)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return None
    order = sorted(range(len(scores)), key=scores.__getitem__)
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1  # average rank of the tie group (1-based)
        i = j + 1
    rank_sum = sum(r for r, label in zip(ranks, labels, strict=True) if label > 0)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def precision_at(scores: Sequence[float], labels: Sequence[int], k: int) -> tuple[int, int]:
    """(👍 among the top k, k actually used). Ties keep input order (stable sort)."""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
    return sum(labels[i] > 0 for i in order), len(order)


def bootstrap_auc(scores: Sequence[float], labels: Sequence[int], rng: random.Random,
                  rounds: int) -> tuple[float, float] | None:
    values = []
    n = len(scores)
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        value = auc([scores[i] for i in idx], [labels[i] for i in idx])
        if value is not None:
            values.append(value)
    return _interval(values)


def bootstrap_diff(base: Sequence[float], other: Sequence[float], labels: Sequence[int],
                   rng: random.Random, rounds: int) -> tuple[float, float] | None:
    """Interval of AUC(other) - AUC(base) on the same resampled jobs."""
    values = []
    n = len(labels)
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        lab = [labels[i] for i in idx]
        a, b = auc([base[i] for i in idx], lab), auc([other[i] for i in idx], lab)
        if a is not None and b is not None:
            values.append(b - a)
    return _interval(values)


def _interval(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def threshold_rows(scores: Sequence[float], labels: Sequence[int],
                   thresholds: Sequence[int]) -> list[tuple[int, float, float]]:
    """(min_score, share of 👍 kept, share of 👎 filtered out) per threshold."""
    pos = [s for s, label in zip(scores, labels, strict=True) if label > 0]
    neg = [s for s, label in zip(scores, labels, strict=True) if label < 0]
    return [(t, sum(s >= t for s in pos) / len(pos), sum(s < t for s in neg) / len(neg)) for t in thresholds]


def suggest_min_score(scores: Sequence[float], labels: Sequence[int], keep: float) -> int:
    """Highest multiple of 5 that still keeps >= `keep` of the 👍 jobs."""
    best = 0
    for t, kept, _ in threshold_rows(scores, labels, range(0, 101, 5)):
        if kept >= keep:
            best = t
    return best


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9+#.]*", text.lower())


def bm25(documents: Sequence[str], query: str, k1: float, b: float) -> list[float]:
    """Okapi BM25 of each document against the query (the keyword baseline)."""
    docs = [Counter(tokenize(d)) for d in documents]
    lengths = [sum(d.values()) for d in docs]
    avg = sum(lengths) / len(docs) if docs else 0.0
    df = Counter(term for d in docs for term in d)
    n = len(docs)
    terms = set(tokenize(query))
    scores = []
    for d, length in zip(docs, lengths, strict=True):
        total = 0.0
        for term in terms:
            tf = d.get(term, 0)
            if tf:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                total += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        scores.append(total)
    return scores


@dataclass(frozen=True)
class Variant:
    name: str
    scores: list[float]
    reasons: list[str]


def build_variants(jobs: Sequence[Job], profile: Profile, current: ScoringConfig, embedder: Embedder,
                   cache: EmbeddingCache) -> list[Variant]:
    """The fixed comparison list (no grid search: ~50 labels can't support one)."""
    flip = "unknown" if current.zero_skill_match == "zero" else "zero"
    configs = [
        ("current config", current),
        (f"zero skill match = {flip}", replace(current, zero_skill_match=flip)),
        ("semantic only", replace(current, weight_semantic=1.0, weight_skills=0.0)),
        ("skills only (+semantic if none listed)", replace(current, weight_semantic=0.0, weight_skills=1.0)),
        ("weights 0.5 / 0.5", replace(current, weight_semantic=0.5, weight_skills=0.5)),
        ("weights 0.85 / 0.15", replace(current, weight_semantic=0.85, weight_skills=0.15)),
        ("first 200 words only", replace(current, max_words=200)),
    ]
    variants = []
    for name, cfg in configs:
        scored = scorer_from_config(embedder, cfg, cache).score(jobs, profile)
        variants.append(Variant(name, [s.value for s in scored], [s.reason for s in scored]))
    rules = RuleScorer(current.skill_saturation).score(jobs, profile)
    variants.append(Variant("rules only (skill overlap)", [s.value for s in rules], [s.reason for s in rules]))
    query = "\n".join([*(f"{s.heading}\n{s.text}" for s in profile.sections), " ".join(profile.skills)])
    keyword = bm25([job_text(j) for j in jobs], query, BM25_K1, BM25_B)
    variants.append(Variant("BM25 keywords (baseline)", keyword, ["keyword match"] * len(jobs)))
    return variants


def claude_variant(jobs: Sequence[Job], stored: dict[str, Score]) -> tuple[Variant | None, str]:
    """The "claude" row: Claude's stored scores (MCP server) for the labeled `jobs`. Returned only
    when every labeled job has one, so its AUC is on the same jobs as the other rows; otherwise
    None and a one-line status explaining why."""
    have = [job_id(j) in stored for j in jobs]
    if not any(have):
        return None, "claude: no stored scores yet (score the jobs through the MCP server first)"
    if not all(have):
        return None, (f"claude: scored {sum(have)} of {len(jobs)} labeled jobs; the row appears "
                      "once all are scored (a partial row would compare different jobs)")
    scores = [stored[job_id(j)] for j in jobs]
    variant = Variant("claude (stored scores)", [s.value for s in scores], [s.reason for s in scores])
    return variant, f"claude: using {len(jobs)} stored scores"


def report(jobs: Sequence[Job], labels: Sequence[int], variants: Sequence[Variant], stored: int,
           show_misses: bool, out: TextIO) -> None:
    good, bad = sum(label > 0 for label in labels), sum(label < 0 for label in labels)

    def say(line: str) -> None:
        print(line, file=out)

    say(f"LABELS: {good} 👍 good / {bad} 👎 bad   ({len(labels)} labeled of {stored} stored jobs)")
    if not good or not bad:
        say("Need at least one 👍 and one 👎 to compare anything. Label more: python -m gigradar.label")
        return
    reliable = good >= MIN_PER_CLASS and bad >= MIN_PER_CLASS
    if not reliable:
        say(f"⚠ UNRELIABLE: fewer than {MIN_PER_CLASS} labels in a class. The numbers below are shown for "
            f"orientation only; no recommendation. Label more: python -m gigradar.label")
    rng = random.Random(SEED)
    base = variants[0]
    say("")
    say(f"{'variant':<40} {'AUC':>5}  {'95% CI':<11}  {'P@5':>4}  {'P@10':>5}  vs current (95% CI)")
    winners = []
    for v in variants:
        value = auc(v.scores, labels)
        ci = bootstrap_auc(v.scores, labels, rng, BOOTSTRAP_ROUNDS)
        p5, n5 = precision_at(v.scores, labels, 5)
        p10, n10 = precision_at(v.scores, labels, 10)
        diff = ""
        if v is not base:
            delta = value - auc(base.scores, labels)
            dci = bootstrap_diff(base.scores, v.scores, labels, rng, BOOTSTRAP_ROUNDS)
            diff = f"{delta:+.2f}" + (f" ({dci[0]:+.2f}..{dci[1]:+.2f})" if dci else "")
            if dci and delta >= CLEAR_GAIN and dci[0] > 0:
                winners.append((delta, v))
        ci_text = f"{ci[0]:.2f}–{ci[1]:.2f}" if ci else "n/a"
        say(f"{v.name:<40} {value:5.2f}  {ci_text:<11}  {p5:>2}/{n5:<1}  {p10:>2}/{n10:<2}  {diff}")
    if not reliable:
        return

    chosen = max(winners, key=lambda w: w[0])[1] if winners else base
    say("")
    if chosen is base:
        say(f"RECOMMENDATION: keep the current config (no variant beats it by >= {CLEAR_GAIN} AUC with a "
            "paired interval above 0).")
    else:
        say(f"RECOMMENDATION: '{chosen.name}' (clearly better than the current config).")
    say(f"\nmin_score for '{chosen.name}':  min_score | 👍 kept | 👎 filtered out")
    for t, kept, filtered in threshold_rows(chosen.scores, labels, range(10, 81, 10)):
        say(f"    {t:3d}      | {kept:5.0%}  | {filtered:5.0%}")
    t = suggest_min_score(chosen.scores, labels, KEEP_GOOD)
    [(_, kept, filtered)] = threshold_rows(chosen.scores, labels, [t])
    say(f"SUGGESTED min_score = {t}: keeps {kept:.0%} of 👍, filters {filtered:.0%} of 👎.")
    if show_misses:
        _misses(jobs, labels, chosen, out)


def _misses(jobs: Sequence[Job], labels: Sequence[int], v: Variant, out: TextIO) -> None:
    """The worst disagreements: 👍 scored lowest, 👎 scored highest."""
    good = sorted((i for i, label in enumerate(labels) if label > 0), key=lambda i: v.scores[i])[:5]
    bad = sorted((i for i, label in enumerate(labels) if label < 0), key=lambda i: -v.scores[i])[:5]
    for title, idx in (("👍 jobs it scored lowest", good), ("👎 jobs it scored highest", bad)):
        print(f"\n{title}:", file=out)
        for i in idx:
            print(f"  {v.scores[i]:5.0f}  {jobs[i].title[:80]}\n         {v.reasons[i][:100]}", file=out)
