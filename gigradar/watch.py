"""One scheduled watch run: search -> dedup -> notify -> mark seen.

    python -m gigradar.watch [--config PATH]

Exit codes: 0 ok · 75 stopped early (no token / blocked; try at the next scheduled run,
no retries) · 2 config error · 1 anything else.

The very first run (empty store) seeds silently: everything found is marked seen, no
notification blast. Jobs are marked seen only after the notification went out, so a
failed notification re-sends them next run.
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
from gigradar.store import filter_new, mark_seen, open_store, seen_count
from gigradar.tokens import StopRun, build_provider
from upwork_search import Job

log = logging.getLogger("gigradar")

EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_STOPPED = 0, 1, 2, 75
MAX_TITLES = 3

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
    """Dedup against the store, notify, then mark seen. Returns the number of new jobs."""
    jobs = [job for _, found in results for job in found]
    seeding = seen_count(conn) == 0
    new = filter_new(conn, jobs)
    if seeding:
        mark_seen(conn, new, now)
        log.info("first run: seeded %d jobs silently", len(new))
        return 0
    if new:
        titles = "\n".join(f"• {job.title}" for job in new[:MAX_TITLES])
        more = f"\n…and {len(new) - MAX_TITLES} more" if len(new) > MAX_TITLES else ""
        notifier.notify(f"gigradar: {len(new)} new job{'s' if len(new) != 1 else ''}", titles + more)
    mark_seen(conn, new, now)
    log.info("%d new of %d found", len(new), len(jobs))
    return len(new)


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
    """Log to a rotating file (scheduled runs under pythonw.exe have no stderr) or stderr."""
    if log_file is None:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


def main(argv: Sequence[str]) -> int:
    default_toml, env_path = default_paths()
    parser = argparse.ArgumentParser(prog="python -m gigradar.watch", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=default_toml, help="gigradar.toml path")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="append logs here (rotating, 1 MB x 3) instead of stderr; needed under pythonw.exe")
    args = parser.parse_args(argv)
    setup_logging(args.log_file)

    try:
        load_dotenv(env_path, os.environ)
        cfg = load_config(args.config, os.environ)
        notifier = build_notifier(cfg.notify_channels)
        searcher = build_searcher(cfg, notifier)
    except ConfigError as exc:
        log.error("config: %s", exc)
        return EXIT_CONFIG

    conn = open_store(cfg.db_path)
    try:
        return run_watch(cfg, searcher, notifier, conn, datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001  log for the scheduler's history, then fail the run
        log.exception("run failed")
        return EXIT_ERROR
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
