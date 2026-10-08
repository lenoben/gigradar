# Claude scoring via a local MCP server (optional)

`python -m gigradar.mcp_server` lets Claude (Claude Code, Claude Desktop) read your stored jobs and
profile and write its own 0–100 scores into the same SQLite store, as the scorer `claude`. Nothing
else in gigradar depends on it: `watch.py` never imports `mcp`.

## Install and register

```bash
pip install -r requirements-mcp.txt          # mcp 2.x (pulls in pydantic, starlette, uvicorn, pywin32 on Windows)
python -m gigradar.mcp_server --print-config # prints the exact `claude mcp add ...` command for this machine
```

Run the printed command once. It registers the server at **user scope** (never project scope, which
would write a `.mcp.json` with your paths into the repo); the paths are computed on your machine.
The server needs `gigradar.toml` with a `[profile]` and an existing store (run the watcher once first).

## Scoring

In a Claude session, **start with the slash command `/mcp__gigradar__score_jobs`** (Claude Code).
Do not ask in plain language: Claude Code only loads an MCP prompt through its slash command, so
a plain request scores jobs *without the rubric* while storing them under the rubric version.
Claude then calls `get_profile`, then `get_unscored_jobs` in batches,
and `set_score` once per job. The rubric (`gigradar/rubric.py`) is generic; it reads your profile via
`get_profile`.

| Tool | Does |
|---|---|
| `get_profile` | skill areas, skills, hard rules from your profile. Never any secret. |
| `get_unscored_jobs(limit)` | newest-first jobs without a `claude` score for the current rubric version. Use limit 10; the cap is 20 (50 overflowed one tool result) |
| `get_job(job_id, include_scores)` | one job, full description. `include_scores=true` also shows your label and every stored score: **inspection only** |
| `set_score(job_id, value, reason)` | value 0–100, reason one line ≤ 200 characters |

Scores are stored as scorer `claude`, version = the rubric version. **Changing the rubric text means
bumping `RUBRIC_VERSION`** (a test pins both); old scores stay, and jobs are offered again for the new
version.

## Blind scoring

**Run the scoring session from a folder outside the repo.** A session started inside the repo loads
`CLAUDE.md`/`CLAUDE.local.md` (your notes, evaluation results, job titles), which would break blindness.
Any empty folder works; the user-scope server is available from everywhere.

So that `python -m gigradar.label --eval` can fairly compare Claude with the embedding scorer on your
labels, the scoring tools never return your labels or any other scorer's score or reason, and the
rubric tells Claude not to look for them. `--eval` adds a "claude (stored scores)" row once every
labeled job has a Claude score (a partial row would compare different jobs).

## Safety

- The only write is `set_score` → `scores`. The server's SQLite connection enforces it (authorizer):
  writes to any other table are refused by SQLite itself.
- It opens an existing store and never migrates or creates one. It reads no `.env`: no Telegram
  token or proxy URL enters the process. No network access.
- Job descriptions are untrusted text from strangers. Tools label them as such, the rubric says to
  treat them as data, and the worst a prompt injection can do is skew one capped score.
- stdout carries only the protocol; logs go to stderr (a test checks this).

## Who can reach it

Claude Code and Claude Desktop on the same machine. Desktop *local* scheduled tasks can too (they run
on your machine while the app is open); cloud routines cannot reach a local stdio server.

## Resetting Claude's scores

`python -m gigradar.scores_reset [--version V] [--yes]` deletes the `claude` scores (all versions, or one),
e.g. before re-scoring. It is a maintenance command, not an MCP tool, and it only ever touches scorer
`claude`: never the embedding scores or your labels. Before deleting it writes the rows to
`claude-scores-backup-<time>.json` next to the store, and without `--yes` it asks first.
`--eval` also adds a "combined: mean(embedding, claude)" row once every labeled job has both scores.

Changing the rubric bumps its version, and `--eval` compares the current version's scores. To compare an
older run (say, scores made without the rubric) with a newer one without deleting anything, use
`python -m gigradar.label --eval --claude-version 1`.

To see *which* jobs two rubric versions disagree on, run `python -m gigradar.label --eval --claude-diff 1 2`.
For the labeled jobs scored in both versions it lists those whose scores differ by 15 or more (label,
both scores, difference, the embedding score, title), biggest difference first, then prints per version
the mean Claude score of your 👍 and 👎 jobs and its Spearman correlation with the embedding scores.
It is read-only: it never writes to the store, not even embeddings.
