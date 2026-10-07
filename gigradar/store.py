"""SQLite store for seen jobs: dedup across scheduled runs.

A job is identified by the `~cipher` at the end of its URL (Job has no id field).
Jobs are only marked seen after they were handled (e.g. alerted), so a failed
alert run re-sends them next time instead of silently dropping them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from upwork_search import Job

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_id     TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    url        TEXT NOT NULL,
    title      TEXT NOT NULL,
    published  TEXT,
    payload    TEXT NOT NULL
);
"""


def open_store(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the store at `path`; ":memory:" works for tests."""
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"store schema v{version} is newer than this code (v{SCHEMA_VERSION})")
    with conn:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def job_id(job: Job) -> str | None:
    """The job's `~cipher` from its URL, or None when the URL is missing."""
    tail = job.url.rstrip("/").rsplit("/", 1)[-1] if job.url else ""
    return tail if tail.startswith("~") else None


def filter_new(conn: sqlite3.Connection, jobs: list[Job]) -> list[Job]:
    """Jobs not yet in the store, in input order, deduplicated within the batch.
    Jobs without an id are dropped (they can't be tracked or linked)."""
    new: list[Job] = []
    batch: set[str] = set()
    for job in jobs:
        jid = job_id(job)
        if jid is None or jid in batch:
            continue
        batch.add(jid)
        if conn.execute("SELECT 1 FROM seen_jobs WHERE job_id = ?", (jid,)).fetchone() is None:
            new.append(job)
    return new


def mark_seen(conn: sqlite3.Connection, jobs: list[Job], now: datetime) -> int:
    """Record jobs as seen; already-seen jobs are left untouched. Returns rows inserted."""
    rows = [
        (jid, now.isoformat(), job.url, job.title, job.published, json.dumps(asdict(job)))
        for job in jobs
        if (jid := job_id(job)) is not None
    ]
    with conn:
        cur = conn.executemany("INSERT OR IGNORE INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?)", rows)
    return cur.rowcount


def seen_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]
