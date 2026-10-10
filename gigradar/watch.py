"""One scheduled watch run: search -> dedup -> notify -> mark seen.

    python -m gigradar.watch [--config PATH]

Exit codes: 0 ok · 75 stopped early (no token / blocked; try at the next scheduled run,
no retries) · 2 config error · 1 anything else.

A search's first run seeds silently: what only it found is marked seen, no notification
blast (also for a search added later). Jobs are marked seen only after the notification
went out, so a failed notification re-sends them next run.

Upwork access (recheck.py): a run that cannot get through (the Cloudflare clearance expired) tells the user ONCE
and leaves a marker; later blocked runs log one line and stop at once, no window opens by itself. A run that gets
through removes the marker.

Quiet hours ([schedule] quiet_start/quiet_end): a scheduled run in that window does nothing (jobs wait for the
next run; `--ignore-quiet-hours` is for a check started by hand).

Crash detection (runstate.py): each run marks start/completion in run_state.json next to the store;
a run that finds the previous one unfinished sends one warning (log + Telegram, toast if no Telegram).

Every stored job gets a score: jobs seeded silently (a search's first run) are scored after the alerts, and any job
still unscored (a run ran out of time, or scoring failed) is picked up by the next runs, newest first.

Retention ([retention] in gigradar.toml, written by the app): jobs older than keep_days are pruned at the end of a run,
keeping only a marker so they never alert again. Without the table nothing is deleted.

With [profile] in gigradar.toml, new jobs are scored before alerting (shadow mode: shown
and sorted, never filtered); a scoring failure alerts them unscored ("Score n/a").
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from gigradar import branding, recheck
from gigradar.config import Config, ConfigError, SearchSpec, default_paths, load_config, load_dotenv
from gigradar.embed import preload_runtime
from gigradar.notify import Alert, Notifier, build_notifier, score_text, warning_notifier
from gigradar.score import EmbeddingScorer, Score, scorer_from_config
from gigradar.runstate import begin_run, end_run, pid_alive, state_path
from gigradar.search import UPSTREAM_SEARCH, CurlSearcher, Searcher, SearchBlocked, SearchFn
from gigradar.store import (SqliteEmbeddingCache, established_searches, filter_new, job_id, mark_seen, open_store,
                            prune, record_searches, save_scores, unscored_jobs)
from gigradar.tokens import StopRun, TokenUnavailable, build_provider
from upwork_search import Job

log = logging.getLogger("gigradar")

EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_STOPPED = 0, 1, 2, 75
# Scoring runs after the search, outside the WebView watchdog. Worst case per run (2 searches):
# WebView hard limit 440 s + scoring 60 s + one chunk overrun ~7 s + Telegram 5 briefs at the
# 15 s timeout ~80 s + startup ~5 s = ~592 s, under 10 min (task limit: 15 min, gigradar-task.ps1,
# so a run is never killed mid-notification). 60 s still scores ~60 new
# jobs (2 searches x 30) at ~0.6 s each plus the ~4 s model load; any rest goes out unscored.
SCORING_BUDGET_S = 60
SCORING_CHUNK = 10  # jobs per scoring call; the budget is checked between chunks
BACKLOG_BUDGET_S = 120   # scoring stored jobs that have no score yet (seeded silently); the worst case above grows to ~712 s
BACKLOG_LIMIT = 300      # at most this many per run, newest first; the rest follows in the next runs

Results = list[tuple[SearchSpec, list[Job]]]
ScoreFn = Callable[[list[Job]], list[Score]]


def collect(search: SearchFn, specs: Sequence[SearchSpec]) -> tuple[Results, StopRun | None]:
    """Run each search once. On StopRun, keep what we have and make no further requests."""
    results: Results = []
    for spec in specs:
        try:
            jobs = search(spec)
        except StopRun as exc:
            log.warning("search %r stopped the run: %s", spec.name, exc)
            return results, exc
        log.info("search %r: %d jobs", spec.name, len(jobs))
        results.append((spec, jobs))
    return results, None


def process(conn, results: Results, notifier: Notifier, now: datetime,
            score_fn: ScoreFn | None, clock: Callable[[], float], min_score: int) -> int:
    """Dedup against the store, seed first-run searches, score + notify, then mark seen.
    Returns the number of jobs notified, best first. min_score 0 = shadow mode: everything is
    alerted. Otherwise scored jobs below min_score are stored, scored and marked seen, but not
    alerted (logged as filtered); unscored jobs ("Score n/a") are never filtered.

    A new job is alerted if at least one established search found it; a job found only by
    searches on their first run is seeded silently (no blast when a search is added).
    Seeding and search registration are committed BEFORE notifying: if the notification
    fails, the alert jobs stay unseen (re-sent next run) and new searches aren't re-seeded."""
    jobs = [job for _, found in results for job in found]
    established = established_searches(conn)
    alertable = {job_id(job) for spec, found in results if spec.name in established for job in found}
    new = filter_new(conn, jobs)
    alert = [job for job in new if job_id(job) in alertable]
    seed = [job for job in new if job_id(job) not in alertable]

    record_searches(conn, [(spec.name, found) for spec, found in results], seed, now)
    for spec, _ in results:
        if spec.name not in established:
            log.info("search %r: first run, seeded silently", spec.name)
    if seed:
        log.info("seeded %d jobs silently", len(seed))
    notified: list[Alert] = []
    if alert:
        alerts = score_alerts(alert, score_fn, clock)
        scored = [a for a in alerts if a.score is not None]
        save_scores(conn, [a.job for a in scored], [a.score for a in scored], now)
        notified = [a for a in alerts if a.score is None or a.score.value >= min_score]
        for a in alerts:
            if a not in notified:
                log.info("filtered (below min_score %d): %s | %s", min_score, score_text(a.score), a.job.title)
        if notified:   # the others are counted in the message, not listed
            notifier.notify_jobs(notified, now.isoformat(), len(alerts) - len(notified))  # raises on failure -> re-sent next run
        mark_seen(conn, alert, now)  # filtered ones too: decided, never alerted later
    filtered = f", {len(alert) - len(notified)} below min_score {min_score}" if min_score else ""
    log.info("%d new of %d found%s", len(alert), len(jobs), filtered)
    return len(notified)


def score_backlog(conn, score_fn: ScoreFn | None, clock: Callable[[], float], now: datetime) -> int:
    """Score stored jobs that have no score (seeded silently, or never scored), newest first, saving after every chunk
    so a stop loses nothing. Within BACKLOG_BUDGET_S; a failure is logged and the next run tries again.
    Returns the number of jobs scored."""
    if score_fn is None:
        return 0
    jobs = unscored_jobs(conn, "embed", BACKLOG_LIMIT)
    if not jobs:
        return 0
    deadline, started, done = clock() + BACKLOG_BUDGET_S, clock(), 0
    try:
        for start in range(0, len(jobs), SCORING_CHUNK):
            if clock() >= deadline:
                break
            chunk = jobs[start:start + SCORING_CHUNK]
            save_scores(conn, chunk, score_fn(chunk), now)
            done += len(chunk)
    except Exception:  # noqa: BLE001  scoring the backlog must never fail the run; logged with traceback
        log.exception("scoring the stored jobs failed after %d of %d", done, len(jobs))
    log.info("scored %d of %d stored jobs that had no score, in %.1fs", done, len(jobs), clock() - started)
    return done


def score_alerts(jobs: list[Job], score_fn: ScoreFn | None, clock: Callable[[], float]) -> list[Alert]:
    """Score the jobs to alert, best first; unscored ones last. Never loses a job: if scoring
    is off, fails, or runs out of SCORING_BUDGET_S, the rest are alerted unscored ("Score n/a").
    Scored in chunks so the budget is checked between them (the model loads on the first)."""
    if score_fn is None:
        return [Alert(job, None) for job in jobs]
    deadline = clock() + SCORING_BUDGET_S
    started = clock()
    scores: list[Score | None] = []
    try:
        for start in range(0, len(jobs), SCORING_CHUNK):
            if clock() >= deadline:
                log.warning("scoring budget of %ss used up: %d jobs alerted unscored",
                            SCORING_BUDGET_S, len(jobs) - len(scores))
                break
            scores.extend(score_fn(jobs[start:start + SCORING_CHUNK]))
    except Exception:  # noqa: BLE001  scoring must never cost an alert; logged with traceback
        log.exception("scoring failed: %d jobs alerted unscored", len(jobs) - len(scores))
    scores.extend([None] * (len(jobs) - len(scores)))
    log.info("scored %d of %d jobs in %.1fs", sum(s is not None for s in scores), len(jobs), clock() - started)
    alerts = [Alert(job, score) for job, score in zip(jobs, scores, strict=True)]
    return sorted(alerts, key=lambda a: (a.score is None, -(a.score.value if a.score else 0)))


def build_score_fn(cfg: Config, conn, now: datetime) -> ScoreFn | None:
    """None without [profile]. The model is loaded lazily on the first call, i.e. only in runs
    that have new jobs to alert (most runs have none), and offline (no network)."""
    if cfg.profile is None:
        return None
    profile, s = cfg.profile, cfg.scoring
    scorer: list[EmbeddingScorer] = []

    def score(jobs: list[Job]) -> list[Score]:
        if not scorer:
            from gigradar.embed import FastEmbedder  # optional dependency: requirements-scoring.txt

            started = time.monotonic()
            embedder = FastEmbedder(s.model, s.model_dir, offline=True)
            log.info("scoring model %s loaded in %.1fs", s.model, time.monotonic() - started)
            scorer.append(scorer_from_config(embedder, s, SqliteEmbeddingCache(conn, now)))
        return scorer[0].score(jobs, profile)

    return score


def run_watch(cfg: Config, searcher: Searcher, notifier: Notifier, conn, now: datetime,
              score_fn: ScoreFn | None, clock: Callable[[], float]) -> int:
    """One run; returns an exit code. `work` may run on another thread, so the store is
    only touched here, after searcher.run() returned. Scoring happens in process(), i.e.
    after the searcher (and its WebView watchdog) is done: it never eats the search budget."""
    try:
        results, stopped = searcher.run(lambda search: collect(search, cfg.searches))
    except StopRun as exc:
        log.warning("run stopped before searching: %s", exc)
        results, stopped = [], exc
    process(conn, results, notifier, now, score_fn, clock, cfg.scoring.min_score)
    note_upwork_access(recheck.marker_path(cfg.db_path), stopped, notifier, now)
    score_backlog(conn, score_fn, clock, now)
    if cfg.retention is not None and cfg.retention.keep_days > 0:
        pruned = prune(conn, cfg.retention.keep_days, cfg.retention.keep_rated, now)
        if pruned.jobs or pruned.markers:
            log.info("pruned %d jobs older than %d days (%d old markers dropped)",
                     pruned.jobs, cfg.retention.keep_days, pruned.markers)
    return EXIT_STOPPED if stopped else EXIT_OK


def needs_quick_check(stopped: StopRun | None) -> bool:
    """Did Upwork itself turn this run away (no token, rejected token, challenge page)? Not: a timeout or a network
    error, which fix themselves."""
    if isinstance(stopped, TokenUnavailable):
        return True
    return isinstance(stopped, SearchBlocked) and str(stopped).startswith("HTTP ")


def note_upwork_access(marker: Path, stopped: StopRun | None, notifier: Notifier, now: datetime) -> None:
    """Keep the marker in step with the run: set it (and tell the user, once) when Upwork turned the run away,
    remove it when a run got through. Never raises: this must not change the run's exit code."""
    try:
        if stopped is None:
            if recheck.clear(marker):
                log.info("Upwork lets the checks through again; the quick check is no longer needed")
            return
        if not needs_quick_check(stopped):
            return
        state = recheck.read(marker)
        if state.needed:
            log.warning("Upwork still needs a quick check (since %s): open %s and click Check Upwork",
                        state.since or "earlier", branding.APP_DISPLAY_NAME)
            return
        notifier.notify(f"{branding.APP_DISPLAY_NAME}: Upwork needs a quick check",
                        f"Open {branding.APP_DISPLAY_NAME} and click Check Upwork.", branding.STATUS_LINK)
        recheck.mark_needed(marker, now)   # after the notification: if it failed, the next run tries again
        log.warning("Upwork needs a quick check: told the user once; no window opens until it is done in the app")
    except Exception:  # noqa: BLE001  bookkeeping and a failed notification must not fail the run
        log.exception("could not record or announce that Upwork needs a quick check")


def build_searcher(cfg: Config) -> Searcher:
    if cfg.backend == "webview":
        from gigradar.webview_search import WebViewSearcher  # optional pywebview dependency
        return WebViewSearcher(cfg.webview_profile, len(cfg.searches), visible=False)
    return CurlSearcher(build_provider(cfg.token_sources, cfg.proxy), cfg.proxy, UPSTREAM_SEARCH)


def setup_logging(log_file: Path | None) -> None:
    """Log to a rotating file (scheduled runs under pythonw.exe have no stderr) or stderr.
    pywebview's own logger goes to the same place: it reports WebView2 startup failures
    there, and under pythonw its default stderr handler silently drops them. Must run
    before `import webview`, whose setup skips adding a handler if one exists."""
    if log_file is None:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    for name in ("gigradar", "pywebview"):
        logger = logging.getLogger(name)
        logger.handlers[:] = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False


def main(argv: Sequence[str]) -> int:
    default_toml, _ = default_paths()
    parser = argparse.ArgumentParser(prog="python -m gigradar.watch", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=default_toml, help="gigradar.toml path")
    parser.add_argument("--ignore-quiet-hours", action="store_true",
                        help="run even inside [schedule] quiet hours (a check started by hand)")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="append logs here (rotating, 1 MB x 3) instead of stderr; needed under pythonw.exe")
    args = parser.parse_args(argv)
    setup_logging(args.log_file)
    log.info("run start")  # vs. Task Scheduler's start time: shows interpreter startup lag

    try:
        load_dotenv(args.config.parent / ".env", os.environ)  # secrets live next to the config
        cfg = load_config(args.config, os.environ)
        if not args.ignore_quiet_hours and cfg.schedule.in_quiet_hours(datetime.now().time()):
            log.info("quiet hours (%s to %s): no check this time", cfg.schedule.quiet_start, cfg.schedule.quiet_end)
            return EXIT_OK
        if cfg.profile is not None:
            preload_runtime()  # before build_notifier loads winrt: see its docstring
        notifier = build_notifier(cfg)
        searcher = build_searcher(cfg)
    except ConfigError as exc:
        log.error("config: %s", exc)
        return EXIT_CONFIG

    now = datetime.now(timezone.utc)
    marker = state_path(cfg.db_path)
    state = begin_run(marker, warning_notifier(notifier), now, os.getpid(), pid_alive)
    try:
        return run_once(cfg, searcher, notifier, now)
    finally:
        end_run(marker, state)  # only a hard death (crash, kill, shutdown) skips this


def run_once(cfg: Config, searcher: Searcher, notifier: Notifier, now: datetime) -> int:
    try:
        # adopt: on a v1 -> v2 migration, the configured searches count as established
        conn = open_store(cfg.db_path, [s.name for s in cfg.searches], now)
    except Exception:  # noqa: BLE001  migration/open errors must reach the log under pythonw
        log.exception("store %s could not be opened", cfg.db_path)
        return EXIT_ERROR
    try:
        return run_watch(cfg, searcher, notifier, conn, now, build_score_fn(cfg, conn, now), time.monotonic)
    except Exception:  # noqa: BLE001  log for the scheduler's history, then fail the run
        log.exception("run failed")
        return EXIT_ERROR
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
