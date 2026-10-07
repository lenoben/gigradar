# Phase 2: job scoring against my profile

Status: **plan approved (2026-10-07); step 1 built.** Branch: `feat/scoring`.
Start here in a new session, together with `CLAUDE.local.md` (Phase 1 architecture + repo rules).

## Goal

Every job a watch run finds gets a **score 0–100 plus a short reason**
(e.g. `82 · Rust backend · matched: Rust, PostgreSQL`), stored in SQLite and shown in alerts.
Local by default (no API key); LLM scoring optional; Claude via MCP later (Phase 3).

## Decisions (agreed)

| Topic | Decision |
|---|---|
| Profile | `profile.md` (free text, **gitignored**; committed template `profile.example.md`) for the semantic match, plus a `[profile]` table in `gigradar.toml` for rates, tiers, skills, keywords |
| Profile import | One-time `python -m gigradar.profile_import` turns my documents into `profile_sources/*.md` (**gitignored**) as raw material for writing `profile.md` by hand. Converter: **anydoc** (`firecrawl-anydoc`, MIT, local Rust, optional `requirements-profile.txt`). markitdown is **not** used |
| Default scorer | Local embeddings: **fastembed** (ONNX, no PyTorch) with a small English model |
| Alert policy | **Shadow mode first**: scores shown in alerts, digests sorted by score, nothing filtered. `min_score` filtering only after calibration |
| Calibration | Label ~50 stored jobs 👍/👎 with a CLI, then tune weights/threshold on that data |
| Missing fields | Hard rules **pass** jobs whose budget/rate is missing; they never score 0 for that |
| Client data | None: the visitor API exposes no client fields, so **no client-based rules** |
| LLM provider | Decided at step 7 (Anthropic API or OpenRouter) |
| Later | Telegram 👍/👎 buttons for labeling (follow-up, not Phase 2) |

## Scoring model: three layers

1. **Hard rules (instant, explainable).** Score 0 + reason, never alerted:
   - hourly max below `min_hourly`, or fixed budget below `min_fixed`;
     **a missing rate/budget passes** (no data ≠ bad job);
   - tier not in `tiers` (if configured);
   - any `exclude_keywords` in title/description (e.g. "WordPress", "unpaid test").
2. **Semantic match (local embeddings).** Embed the job (title + skills + description) and the
   profile split into **chunks per skill area** (sections of `profile.md`, e.g. "Rust/Go backend",
   "Next.js/SvelteKit frontend", "Postgres/data"). Use the **best-matching chunk** (max cosine),
   not one averaged profile vector: a job usually matches one area, not all.
3. **Skill overlap.** Bonus per job skill that is in `[profile].skills` (case-insensitive,
   simple normalization like `Next.js` = `nextjs`).

Combined: weighted sum mapped to 0–100 (weights in `[profile]`/`[scoring]`, defaults tuned in
step 6). Reason = best chunk heading + matched skills (+ rule that fired, if any).

## Local embedding options (decided: fastembed)

| Option | Pros | Cons |
|---|---|---|
| **fastembed** (ONNX runtime) | small install, no PyTorch, fast CPU, ~1–2 s cold start; good small English models (bge-small/base, e5) | fewer models than sentence-transformers; one-time model download |
| sentence-transformers | most models, reference implementation | PyTorch (multi-GB on Windows), slow cold start every 30 min |
| Ollama (e.g. nomic-embed-text) | GPU, shared with other tools | separate always-running service; runs fail if it's down |
| model2vec (static) | tiny, instant | clearly weaker semantics |
| BM25 / keywords | no model | no semantics; useful as an **evaluation baseline** |

Practicalities:
- Optional dependency `requirements-scoring.txt` (like pywebview / windows-toasts).
- Model downloaded once by a setup command (e.g. `python -m gigradar.score --download`) into
  `%LOCALAPPDATA%\gigradar\models` (pathlib; `~/.local/share/gigradar/models` elsewhere),
  never into the repo. Scheduled runs then work offline.
- ~60 jobs/run embeds in well under a second on CPU; runs stay ~5 s.
- **Verified 2026-10-07 (PyPI + fastembed source)**: fastembed **0.8.1** (2026-09-22),
  pure-Python wheel, Python >=3.10. Its compiled deps all have `cp312-win_amd64` (or abi3)
  wheels: onnxruntime 1.30.0, tokenizers 0.23.2, numpy, pillow, mmh3, py-rust-stemmers.
  huggingface-hub is pinned `<2.0` by fastembed (latest is 2.1.1; pip picks 1.x).
  `TextEmbedding(model_name, cache_dir=...)`; `local_files_only=True` kwarg for offline runs.
  Candidate models (fastembed's own `size_in_GB`):

  | Model | Dim | Download | License |
  |---|---|---|---|
  | `BAAI/bge-small-en-v1.5` (fastembed default) | 384 | 0.067 GB | MIT |
  | `snowflake/snowflake-arctic-embed-xs` | 384 | 0.09 GB | Apache-2.0 |
  | `BAAI/bge-base-en-v1.5` | 768 | 0.21 GB | MIT |
  | `nomic-ai/nomic-embed-text-v1.5-Q` | 768 | 0.13 GB | Apache-2.0 |

  Start with bge-small; compare bge-base at step 6 on the labeled set. Re-check the
  version right before step 4 in case it moved.

## Profile import (step 1, one-time, optional)

`python -m gigradar.profile_import CV.pdf portfolio/ [--out profile_sources] [--force]`
converts my documents into `profile_sources/<file name>.md` (gitignored) to copy from when
writing `profile.md`. **Scheduled runs never convert; scoring only reads `profile.md`.**

- `.pdf/.docx/.pptx/.xlsx/.odt/.rtf` → anydoc; `.txt/.md` → copied as-is (byte copy).
  Other types are listed as ignored. Folders: top level only.
- anydoc verified 2026-10-07: `firecrawl-anydoc` **0.2.4** (2026-08-27), MIT, Python >=3.10,
  no Python deps, `cp310-abi3-win_amd64` wheel. Imported lazily, only when a document
  needs converting.
- Image-only/scanned PDFs: anydoc raises **`NeedsOcrError`** (with `.pages`/`.page_count`;
  not `UnsupportedError`, which is for unknown formats). The file is reported as SKIPPED
  with the page count and the run continues; no OCR. Encrypted and other `ConvertError`s
  are reported and skipped the same way. Any skip → exit 1.
- **Privacy:** anydoc's `to_markdown(..., ocr="hosted")` would upload the whole document
  to Firecrawl's API (keyless). We pass `ocr="reject"` explicitly; nothing leaves the PC.
- Outputs are `cv.pdf.md` (so `cv.pdf` and `cv.docx` don't collide); existing outputs are
  kept unless `--force`, so hand edits in `profile_sources/` survive a re-run.

## LLM scorer (optional, step 7)

`LLMScorer`: job + profile → score + reason. Catches what embeddings miss (red flags,
"fix my existing codebase" vs. greenfield, scope vs. budget).
- Cost ~1–2k tokens per job → cents per day at a few dozen jobs.
- Network dependence + latency; the profile goes to the provider (job text is public).
- Key in `.env`, never in the TOML. Provider decided at step 7.
- Run it **only on jobs that pass the local score** (a handful per day, not all 60).
- Same no-retry policy as everything else: a failure leaves the job unscored-by-LLM, not lost.

## Phase 2.5: shareable setup (after Phase 2, not now; no code yet)

Goal: someone else can install gigradar and get their own alerts without editing files by hand.

- **One instance per user.** Own profile, searches, scoring, store, and their **own Telegram
  bot** sending to their **own private chat**. No shared group, no shared or bundled token.
- **Setup wizard** `python -m gigradar.setup` (first run): notification channels (telegram,
  toast, or both) → searches → profile documents import (`gigradar.profile_import`) →
  writes `gigradar.toml` and `.env`. The written TOML must pass `load_config` (tested), so
  the wizard can never produce a config the runs reject.
- **Telegram step:** the user creates a bot with @BotFather and pastes the token; then sends
  the bot any message; the wizard calls `getUpdates` and reads the chat ID automatically
  (private chat only; ask if several). Validate with one test message (as
  `python -m gigradar.telegram --test` does). The token goes only into `.env`, never echoed or logged.
- **install.ps1:** creates the venv, installs the deps for the chosen channels/backends, runs
  the wizard, registers the scheduled task (`scripts/windows/gigradar-task.ps1 -Register`).
- **Later:** a packaged .exe (PyInstaller); Phase 4's Tauri installer replaces it.

What this means for Phase 2 now:
- **No hardcoded paths.** Every path (profile, `profile_sources`, model cache, store, logs)
  comes from config, relative to `gigradar.toml`, or from a platform default via pathlib
  (`%LOCALAPPDATA%\gigradar\...` / `~/.local/share/gigradar/...`). Nothing assumes my
  machine or my repo checkout.
- **Every setting via config:** model name, weights and `min_score` live in `[scoring]` with
  defaults, so the wizard only writes what the user chose. Personal data stays in
  gitignored files (`gigradar.toml`, `.env`, `profile.md`, `profile_sources/`, `data/`).
- Known gap for the .exe only: `config.default_paths()` and `profile_import.DEFAULT_OUT`
  default to the repo root via `__file__`, which won't exist in a PyInstaller bundle.
  Fine for venv installs; the .exe step adds a data-dir default (e.g. `%APPDATA%\gigradar`).
- The import command and the config loader stay usable as library functions (the wizard
  calls them; no logic only reachable via `argparse`).

## Claude via MCP (Phase 3, not now)

Claude Code / a scheduled Claude task pulls unscored jobs, scores them, writes scores back
(tools like `get_new_jobs`, `set_score`, `mark_seen`). Requirement for Phase 2: **scores live in
SQLite** with a scorer name, so an MCP-written score is just another scorer.

## Design

- `Scorer` protocol: `score(jobs, profile) -> list[Score]` where
  `Score = (value 0–100, reason, scorer name+version)`. Implementations: `RuleScorer` layers
  (rules + overlap), `EmbeddingScorer` (fastembed), later `LLMScorer`. `watch.py` stays
  agnostic, as with `Searcher` / `Notifier`.
- `Embedder` protocol behind `EmbeddingScorer` → **fake embedder** (deterministic vectors) in
  tests; fastembed only in the real implementation, imported lazily.
- **Store schema v2** (`PRAGMA user_version` 1 → 2, one migration), also fixing the
  per-search seeding follow-up:
  - which search found each job (`job_search` table or column) → seed per search on its
    first run, so a newly added search doesn't alert on 50 old jobs;
  - scores: job_id, scorer, version, value, reason, scored_at;
  - embedding cache: job_id, model, vector (BLOB) so re-runs/re-scoring don't re-embed;
  - labels: job_id, label (+1/−1), labeled_at.
  Migration tested on a v1 database fixture.
- Profile loading in `config.py`: `[profile]` validated like the rest (unknown keys rejected,
  types checked); `profile.md` path configurable, default next to `gigradar.toml`; missing
  `profile.md` with scoring enabled → `ConfigError`.
- Notifiers: Telegram brief gets a score line (`Score 82 · Rust backend · Rust, PostgreSQL`);
  digest sorted by score descending; toast summary lists the top-scored titles first.
- Shadow mode: `[scoring] min_score` absent/0 → no filtering. When set, jobs below it are
  stored + scored + marked seen but not notified.
- Failure policy: a scorer error must not lose jobs. If scoring fails, the run still alerts
  (unscored, marked as such) rather than dropping or re-sending everything. To be detailed in
  step 5.

## Calibration (step 6)

- CLI, e.g. `python -m gigradar.label`: shows stored jobs (title, pay, skills, current
  score) one at a time; keys 👍 `y` / 👎 `n` / skip `s`; writes to the labels table.
- `python -m gigradar.label --eval`: for each scorer config, precision@k (how many top picks
  are 👍) and a ranking metric (e.g. AUC) on the labeled set; compares against the BM25 baseline.
- Tune weights and pick `min_score` from that data, then leave shadow mode.

## Build order (branch `feat/scoring`, small Conventional Commits, offline tests each step)

1. Profile: `profile.example.md`, `[profile]` table in `gigradar.example.toml`, loader + validation;
   `gigradar.profile_import` (anydoc) for the one-time document import. **Built.**
   Format: each `## Heading` in `profile.md` = one skill-area section; text above the first
   `##` and `<!-- comments -->` are ignored; empty/duplicate sections are errors.
   `[profile]` present = scoring enabled (`Config.profile`), absent = `None`.
2. Store schema v2 migration (job_search, scores, embeddings, labels) + per-search seeding.
3. `RuleScorer`: hard rules (missing fields pass) + skill overlap, with reasons.
4. `EmbeddingScorer` with fastembed (verify versions first) + `--download` setup command.
5. Wire scoring into `watch.py` + notifier output (score line, sorted digest), shadow mode.
6. Labeling CLI + `--eval`; tune weights/threshold on ~50 labels.
7. Optional `LLMScorer` (own branch; provider decided then).

Live checks (user runs them): step 4 model download + one scoring dry run on stored jobs;
step 5 one scheduled run with scores in Telegram; step 6 labeling session.

## Constraints (unchanged)

No auto-submitting proposals, no per-minute polling, personal volume only. No secrets in the
TOML. Never commit `profile.md`, `profile_sources/`, `gigradar.toml`, `.env`, `data/`, models or logs.
