"""One scheduled watch run: search -> dedup -> notify -> mark seen.

    python -m gigradar.watch [--config PATH]

Exit codes: 0 ok · 75 stopped early (no token / blocked; try at the next scheduled run,
no retries) · 2 config error · 1 anything else.

A search's first run seeds silently: what only it found is marked seen, no notification
blast (also for a search added later). Jobs are marked seen only after the notification
went out, so a failed notification re-sends them next run.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from gigradar.config import Config, ConfigError, SearchSpec, default_paths, load_config, load_dotenv
from gigradar.notify import Notifier, build_notifier
from gigradar.search import UPSTREAM_SEARCH, CurlSearcher, Searcher, SearchFn
from gigradar.store import established_searches, filter_new, job_id, mark_seen, open_store, record_searches
from gigradar.tokens import StopRun, build_provider
from upwork_search import Job

log = logging.getLogger("gigradar")

EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_STOPPED = 0, 1, 2, 75

Results = list[tuple[SearchSpec, list[Job]]]


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


def process(conn, results: Results, notifier: Notifier, now: datetime) -> int:
    """Dedup against the store, seed first-run searches, notify, then mark seen.
    Returns the number of jobs notified.

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
    if alert:
        notifier.notify_jobs(alert)  # raises on failure -> not marked seen -> re-sent next run
        mark_seen(conn, alert, now)
    log.info("%d new of %d found", len(alert), len(jobs))
    return len(alert)


def run_watch(cfg: Config, searcher: Searcher, notifier: Notifier, conn, now: datetime) -> int:
    """One run; returns an exit code. `work` may run on another thread, so the store is
    only touched here, after searcher.run() returned."""
    try:
        results, stopped = searcher.run(lambda search: collect(search, cfg.searches))
    except StopRun as exc:
        log.warning("run stopped before searching: %s", exc)
        results, stopped = [], exc
    process(conn, results, notifier, now)
    return EXIT_STOPPED if stopped else EXIT_OK


def build_searcher(cfg: Config, notifier: Notifier) -> Searcher:
    if cfg.backend == "webview":
        from gigradar.webview_search import WebViewSearcher  # optional pywebview dependency
        return WebViewSearcher(cfg.webview_profile, notifier, len(cfg.searches))
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
    default_toml, env_path = default_paths()
    parser = argparse.ArgumentParser(prog="python -m gigradar.watch", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=default_toml, help="gigradar.toml path")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="append logs here (rotating, 1 MB x 3) instead of stderr; needed under pythonw.exe")
    args = parser.parse_args(argv)
    setup_logging(args.log_file)
    log.info("run start")  # vs. Task Scheduler's start time: shows interpreter startup lag

    try:
        load_dotenv(env_path, os.environ)
        cfg = load_config(args.config, os.environ)
        notifier = build_notifier(cfg)
        searcher = build_searcher(cfg, notifier)
    except ConfigError as exc:
        log.error("config: %s", exc)
        return EXIT_CONFIG

    now = datetime.now(timezone.utc)
    try:
        # adopt: on a v1 -> v2 migration, the configured searches count as established
        conn = open_store(cfg.db_path, [s.name for s in cfg.searches], now)
    except Exception:  # noqa: BLE001  migration/open errors must reach the log under pythonw
        log.exception("store %s could not be opened", cfg.db_path)
        return EXIT_ERROR
    try:
        return run_watch(cfg, searcher, notifier, conn, now)
    except Exception:  # noqa: BLE001  log for the scheduler's history, then fail the run
        log.exception("run failed")
        return EXIT_ERROR
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
