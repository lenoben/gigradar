"""Label stored jobs 👍/👎 (for calibration), and evaluate scorer variants against those labels.

    python -m gigradar.label [--config PATH] [--limit 50] [--show-score]
    python -m gigradar.label --eval [--config PATH] [--show-misses]

Labeling shows one job at a time (title, pay, skills with my matches marked ✓, description
start, link) and asks: y = 👍 would apply, n = 👎 not for me, s = skip, u = undo, q = quit.
Each answer is saved at once (labels table), so a session can stop and resume any time;
labeled jobs are never asked again. The score is hidden unless --show-score, so it doesn't
sway the verdict. Jobs come in mixed score order (top, middle, bottom third in turn), so
~50 labels cover the whole range. Writes only the labels table and the embedding cache.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from gigradar.notify import score_text
from gigradar.score import Score, job_skills, normalize_skill
from gigradar.store import delete_label, job_id, load_labels, save_label
from gigradar.telegram import pay_line
from upwork_search import Job

DESCRIPTION_CHARS = 800
KEYS = {"y": 1, "n": -1}


def mixed_order(jobs: Sequence[Job], scores: dict[str, Score] | None) -> list[Job]:
    """Best, middle, worst third in turn, so a short session still spans the score range.
    Without scores: newest first."""
    if not scores:
        return list(reversed(jobs))
    ranked = sorted(jobs, key=lambda j: -scores[job_id(j)].value)
    third = -(-len(ranked) // 3)
    parts = [ranked[:third], ranked[third:2 * third], ranked[2 * third:]]
    mixed = []
    for i in range(third):
        mixed.extend(part[i] for part in parts if i < len(part))
    return mixed


def render(job: Job, my_skills: set[str], score: Score | None, position: str) -> str:
    skills = ", ".join(("✓" if normalize_skill(s) in my_skills else "") + s for s in job_skills(job))
    description = " ".join(job.description.split())
    if len(description) > DESCRIPTION_CHARS:
        description = description[:DESCRIPTION_CHARS].rsplit(" ", 1)[0] + " …"
    lines = ["", "=" * 80, f"{position}  {job.title}", pay_line(job) or "(no pay info)",
             f"Skills: {skills or '(none listed)'}", "", description, "", job.url]
    if score is not None:
        lines.append(f"[score {score_text(score)}]")
    return "\n".join(lines)


def session(conn, jobs: Sequence[Job], scores: dict[str, Score] | None, my_skills: set[str], show_score: bool,
            limit: int, ask: Callable[[str], str], out: TextIO, now: Callable[[], datetime]) -> int:
    """Ask about up to `limit` unlabeled jobs; returns how many labels were saved this session."""
    labeled = load_labels(conn)
    queue = [j for j in mixed_order(jobs, scores) if job_id(j) is not None and job_id(j) not in labeled][:limit]
    done: list[str] = []   # job ids labeled this session (for undo)
    position = 0
    while position < len(queue):
        job = queue[position]
        jid = job_id(job)
        shown = scores.get(jid) if (scores and show_score) else None
        print(render(job, my_skills, shown, f"[{len(done) + 1}/{len(queue)}]"), file=out)
        answer = ask("y = 👍  n = 👎  s = skip  u = undo  q = quit > ").strip().lower()
        if answer in KEYS:
            save_label(conn, jid, KEYS[answer], now())
            done.append(jid)
            position += 1
        elif answer == "s":
            position += 1
        elif answer == "u":
            if not done:
                print("(nothing to undo)", file=out)
                continue
            undone = done.pop()
            delete_label(conn, undone)
            position = next(i for i, j in enumerate(queue) if job_id(j) == undone)
            print("(undone: asking that job again)", file=out)
        elif answer == "q":
            break
        else:
            print("(unknown key)", file=out)
    total = load_labels(conn)
    good, bad = sum(v > 0 for v in total.values()), sum(v < 0 for v in total.values())
    print(f"\nsaved {len(done)} labels this session; total {good} 👍 / {bad} 👎", file=out)
    return len(done)


def main(argv: Sequence[str]) -> int:
    from gigradar.config import ConfigError, default_paths, load_config, load_dotenv
    from gigradar.embed import EmbedderError, FastEmbedder
    from gigradar.evaluate import build_variants, claude_variant, report
    from gigradar.rubric import RUBRIC_VERSION, SCORER
    from gigradar.score import scorer_from_config
    from gigradar.store import SqliteEmbeddingCache, load_scores, open_store, stored_jobs

    parser = argparse.ArgumentParser(prog="python -m gigradar.label", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=default_paths()[0], help="gigradar.toml path")
    parser.add_argument("--limit", type=int, default=50, help="jobs to ask about in this session")
    parser.add_argument("--show-score", action="store_true", help="show the current score while labeling")
    parser.add_argument("--eval", action="store_true", help="compare scorer variants on the labels")
    parser.add_argument("--show-misses", action="store_true", help="with --eval: list the worst disagreements")
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
    now = datetime.now(timezone.utc)
    conn = open_store(cfg.db_path, [s.name for s in cfg.searches], now)
    try:
        jobs = stored_jobs(conn)
        cache = SqliteEmbeddingCache(conn, now)
        try:
            embedder = FastEmbedder(cfg.scoring.model, cfg.scoring.model_dir, offline=True)
        except EmbedderError as exc:
            if args.eval:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"warning: no scores ({exc}); jobs come newest first", file=sys.stderr)
            embedder = None
        if args.eval:
            labels = load_labels(conn)
            labeled = [j for j in jobs if job_id(j) in labels]
            values = [labels[job_id(j)] for j in labeled]
            variants = build_variants(labeled, cfg.profile, cfg.scoring, embedder, cache) if labeled else []
            if labeled:
                extra, status = claude_variant(labeled, load_scores(conn, SCORER, RUBRIC_VERSION))
                print(status)
                variants += [extra] if extra else []
            report(labeled, values, variants, len(jobs), args.show_misses, sys.stdout)
            return 0
        scores = None
        if embedder is not None:
            print(f"scoring {len(jobs)} stored jobs (first time: ~0.5 s per job, then cached)...")
            scored = scorer_from_config(embedder, cfg.scoring, cache).score(jobs, cfg.profile)
            scores = {job_id(j): s for j, s in zip(jobs, scored, strict=True) if job_id(j) is not None}
        my_skills = {normalize_skill(s) for s in cfg.profile.skills}
        session(conn, jobs, scores, my_skills, args.show_score, args.limit, input, sys.stdout,
                lambda: datetime.now(timezone.utc))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    # UTF-8 so 👍/👎/✓ survive in Git Bash (a pipe, cp1252 by default); a real console is fine either way
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
