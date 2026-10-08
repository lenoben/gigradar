"""Maintenance: delete the "claude" scores, for a clean re-score. Not an MCP tool.

    python -m gigradar.scores_reset [--config PATH] [--version V] [--yes]

Deletes the scores of scorer "claude" (every rubric version, or only --version V). It never touches
other scorers (embedding, rules), labels or seen jobs. The deleted rows are first written to
claude-scores-backup-<UTC time>.json next to the store; if that fails, nothing is deleted.
Without --yes it asks for confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from gigradar.config import ConfigError, default_paths, load_config
from gigradar.mcp_server import config_environ
from gigradar.mcp_tools import BUSY_TIMEOUT
from gigradar.rubric import SCORER
from gigradar.store import StoreError, delete_scores, open_existing, score_rows

BACKUP_COLUMNS = ("job_id", "scorer", "version", "value", "reason", "scored_at")


def reset(conn, db_path: Path, version: str | None, assume_yes: bool, ask: Callable[[str], str],
          now: datetime, out: TextIO) -> int:
    """Back up, confirm, delete. Returns the number of scores deleted."""
    rows = score_rows(conn, SCORER, version)
    counts = Counter(row[2] for row in rows)
    total = len(rows)
    if not total:
        print(f"no '{SCORER}' scores to delete", file=out)
        return 0
    summary = ", ".join(f"version {v}: {n}" for v, n in sorted(counts.items()))
    print(f"'{SCORER}' scores: {total} ({summary})", file=out)
    if not assume_yes and ask(f"Delete {total} '{SCORER}' scores? [y/N] ").strip().lower() != "y":
        print("nothing deleted", file=out)
        return 0
    backup = db_path.with_name(f"claude-scores-backup-{now:%Y%m%dT%H%M%SZ}.json")
    if backup.exists():
        raise StoreError(f"backup {backup} already exists; not overwriting it")
    backup.write_text(json.dumps([dict(zip(BACKUP_COLUMNS, row)) for row in rows], indent=1), encoding="utf-8")
    deleted = delete_scores(conn, SCORER, version)
    print(f"backup: {backup}\ndeleted {deleted} '{SCORER}' scores", file=out)
    return deleted


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m gigradar.scores_reset", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=default_paths()[0], help="gigradar.toml path")
    parser.add_argument("--version", default=None, help="only this rubric version (default: all versions)")
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config, config_environ(dict(os.environ)))  # no secrets needed here either
        conn = open_existing(cfg.db_path, BUSY_TIMEOUT)
    except (ConfigError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        reset(conn, cfg.db_path, args.version, args.yes, input, datetime.now(timezone.utc), sys.stdout)
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
