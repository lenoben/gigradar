"""SQLite store for seen jobs: dedup across scheduled runs, plus per-search state and
(from Phase 2) scores, embedding cache and labels.

A job is identified by the `~cipher` at the end of its URL (Job has no id field).
Jobs are only marked seen after they were handled (e.g. alerted), so a failed
alert run re-sends them next time instead of silently dropping them.

Schema v2 (PRAGMA user_version): v1's `seen_jobs` unchanged, plus
- search_runs: searches that have run at least once ("established"; a search without a
  row is on its first run and seeds silently);
- job_search: which search found which job;
- scores / embeddings / labels: used by the scoring steps. No foreign keys to seen_jobs:
  a job is scored before it is marked seen.
v1 -> v2 migrates in one transaction after a backup copy of the file.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from array import array
from collections.abc import Sequence
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from gigradar.score import Score
from upwork_search import Job

SCHEMA_VERSION = 2

_V1_TABLES = (
    """CREATE TABLE seen_jobs (
        job_id     TEXT PRIMARY KEY,
        first_seen TEXT NOT NULL,
        url        TEXT NOT NULL,
        title      TEXT NOT NULL,
        published  TEXT,
        payload    TEXT NOT NULL
    )""",
)

# No IF NOT EXISTS: an unexpected existing table must fail the migration, not be reused.
_V2_TABLES = (
    """CREATE TABLE search_runs (
        search_name TEXT PRIMARY KEY,
        first_run   TEXT NOT NULL,
        last_run    TEXT NOT NULL
    )""",
    """CREATE TABLE job_search (
        job_id      TEXT NOT NULL,
        search_name TEXT NOT NULL,
        first_found TEXT NOT NULL,
        PRIMARY KEY (job_id, search_name)
    )""",
    """CREATE TABLE scores (
        job_id    TEXT NOT NULL,
        scorer    TEXT NOT NULL,
        version   TEXT NOT NULL,
        value     INTEGER NOT NULL CHECK (value BETWEEN 0 AND 100),
        reason    TEXT NOT NULL,
        scored_at TEXT NOT NULL,
        PRIMARY KEY (job_id, scorer, version)
    )""",
    """CREATE TABLE embeddings (
        job_id     TEXT NOT NULL,
        model      TEXT NOT NULL,
        dim        INTEGER NOT NULL,
        vector     BLOB NOT NULL,  -- float32 little-endian
        created_at TEXT NOT NULL,
        PRIMARY KEY (job_id, model)
    )""",
    """CREATE TABLE labels (
        job_id     TEXT PRIMARY KEY,
        label      INTEGER NOT NULL CHECK (label IN (-1, 1)),
        labeled_at TEXT NOT NULL
    )""",
)


class StoreError(RuntimeError):
    """The store can't be opened or migrated (newer schema, backup failed, ...)."""


def open_store(path: Path, adopt: Sequence[str], now: datetime) -> sqlite3.Connection:
    """Open the store at `path`, creating or migrating it to the current schema.
    ":memory:" works for tests.

    `adopt`: search names to register as established when migrating a v1 store (v1 ran
    every configured search but didn't record which), so they keep alerting instead of
    silently seeding once. Ignored for new and already-current stores."""
    in_memory = str(path) == ":memory:"
    if not in_memory:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        version = _version(conn)
        if version > SCHEMA_VERSION:
            raise StoreError(f"store schema v{version} is newer than this code (v{SCHEMA_VERSION})")
        if version == 1 and not in_memory:
            backup(conn, path, now)
        if version < SCHEMA_VERSION:
            _migrate(conn, adopt, now)
    except BaseException:
        conn.close()
        raise
    return conn


def backup(conn: sqlite3.Connection, path: Path, now: datetime) -> Path:
    """Copy the store next to itself (gigradar.db.v1-<UTC time>.bak) via SQLite's backup API."""
    target = path.with_name(f"{path.name}.v{_version(conn)}-{now:%Y%m%dT%H%M%SZ}.bak")
    if target.exists():
        raise StoreError(f"backup {target} already exists; not overwriting it")
    dst = sqlite3.connect(str(target))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return target


def _version(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seen_jobs'").fetchone():
        return 1  # v1 always set user_version; be safe with a hand-made/older file anyway
    return version


def _migrate(conn: sqlite3.Connection, adopt: Sequence[str], now: datetime) -> None:
    """0 -> 2 (new store) or 1 -> 2, all in one transaction: on any error nothing changes."""
    conn.execute("BEGIN IMMEDIATE")  # write lock first, then re-check: another run may have migrated
    try:
        version = _version(conn)
        if version == 0:
            for ddl in _V1_TABLES + _V2_TABLES:
                conn.execute(ddl)
        elif version == 1:
            for ddl in _V2_TABLES:
                conn.execute(ddl)
            stamp = now.isoformat()
            conn.executemany("INSERT INTO search_runs VALUES (?, ?, ?)",
                             [(name, stamp, stamp) for name in dict.fromkeys(adopt)])
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


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
    with conn:
        return _insert_seen(conn, jobs, now)


def seen_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0]


def established_searches(conn: sqlite3.Connection) -> set[str]:
    """Searches that have run before; a configured search not in here is on its first run."""
    return {row[0] for row in conn.execute("SELECT search_name FROM search_runs")}


def record_searches(conn: sqlite3.Connection, results: Sequence[tuple[str, list[Job]]],
                    seed: list[Job], now: datetime) -> None:
    """In one transaction: mark `seed` seen silently, register every search in `results`
    as run, and remember which search found which job."""
    stamp = now.isoformat()
    with conn:
        _insert_seen(conn, seed, now)
        conn.executemany(
            "INSERT INTO search_runs VALUES (?, ?, ?) "
            "ON CONFLICT (search_name) DO UPDATE SET last_run = excluded.last_run",
            [(name, stamp, stamp) for name, _ in results])
        conn.executemany(
            "INSERT OR IGNORE INTO job_search VALUES (?, ?, ?)",
            [(jid, name, stamp) for name, jobs in results for job in jobs
             if (jid := job_id(job)) is not None])


def save_scores(conn: sqlite3.Connection, jobs: Sequence[Job], scores: Sequence[Score], now: datetime) -> int:
    """Store one score per job (same order); the newest per (job, scorer, version) wins.
    Jobs without an id are skipped. Returns rows written."""
    if len(jobs) != len(scores):
        raise ValueError(f"{len(jobs)} jobs but {len(scores)} scores")
    rows = [(jid, s.scorer, s.version, s.value, s.reason, now.isoformat())
            for job, s in zip(jobs, scores) if (jid := job_id(job)) is not None]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def stored_jobs(conn: sqlite3.Connection) -> list[Job]:
    """Every job in the store, oldest first, rebuilt from its saved payload."""
    names = {f.name for f in fields(Job)}
    return [Job(**{k: v for k, v in json.loads(payload).items() if k in names})
            for (payload,) in conn.execute("SELECT payload FROM seen_jobs ORDER BY first_seen, job_id")]


def save_label(conn: sqlite3.Connection, jid: str, label: int, now: datetime) -> None:
    """My verdict on a job: +1 (would apply) or -1 (not for me). Replaces an earlier one."""
    if label not in (1, -1):
        raise ValueError(f"label must be +1 or -1, got {label}")
    with conn:
        conn.execute("INSERT OR REPLACE INTO labels VALUES (?, ?, ?)", (jid, label, now.isoformat()))


def delete_label(conn: sqlite3.Connection, jid: str) -> None:
    with conn:
        conn.execute("DELETE FROM labels WHERE job_id = ?", (jid,))


def load_labels(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT job_id, label FROM labels"))


class SqliteEmbeddingCache:
    """Embeddings in the `embeddings` table, keyed by (key, model): a job (key = its ~cipher) is
    embedded once per model, never again on re-runs or re-scoring. Profile sections are cached
    too, under score.section_key() ("profile:<hash of the text>"), so an edited section is
    re-embedded and an unchanged one is not; the job_id column holds that key."""

    def __init__(self, conn: sqlite3.Connection, now: datetime) -> None:
        self.conn = conn
        self.now = now

    def get(self, job_ids: Sequence[str], model: str) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for jid in dict.fromkeys(job_ids):
            row = self.conn.execute("SELECT dim, vector FROM embeddings WHERE job_id = ? AND model = ?",
                                    (jid, model)).fetchone()
            if row is not None:
                found[jid] = decode_vector(row[1], row[0])
        return found

    def put(self, vectors: dict[str, list[float]], model: str) -> None:
        rows = [(jid, model, len(vec), encode_vector(vec), self.now.isoformat()) for jid, vec in vectors.items()]
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?, ?)", rows)


def encode_vector(vector: Sequence[float]) -> bytes:
    """float32 little-endian, independent of the machine's byte order."""
    values = array("f", vector)
    if sys.byteorder == "big":
        values.byteswap()
    return values.tobytes()


def decode_vector(blob: bytes, dim: int) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    if sys.byteorder == "big":
        values.byteswap()
    if len(values) != dim:
        raise StoreError(f"embedding has {len(values)} values, expected {dim}")
    return values.tolist()


def _insert_seen(conn: sqlite3.Connection, jobs: list[Job], now: datetime) -> int:
    rows = [
        (jid, now.isoformat(), job.url, job.title, job.published, json.dumps(asdict(job)))
        for job in jobs
        if (jid := job_id(job)) is not None
    ]
    cur = conn.executemany("INSERT OR IGNORE INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?)", rows)
    return cur.rowcount
