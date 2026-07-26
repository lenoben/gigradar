# Upwork Job Search (no account)

Search Upwork jobs from the command line with full filters — **no login, no browser, no API key**.
A single Python script that talks to Upwork's public *visitor* GraphQL API and returns clean
JSON or CSV.

```bash
python upwork_search.py -q AI --job-type hourly --tier expert --limit 20
```

There's also a **mobile-first web UI** (Next.js, in [`web/`](web/)) with a filter panel, "load more"
pagination, and email job alerts — see [Web UI](#web-ui).

> ### ⚠️ You need a residential IP (or a proxy)
>
> Upwork's Cloudflare blocks by **IP reputation**, not just TLS fingerprint. This works from a
> normal **residential** connection, but **datacenter / VPS / cloud IPs get `403`'d** — and even a
> single residential IP gets rate-limited under sustained scraping. To run it on a server, point it
> at a **rotating residential proxy** (e.g. [Webshare](https://www.webshare.io), which has a free
> tier) via `--proxy` (CLI) or the `UPWORK_PROXY` env (web). See [Caveats](#caveats).

## How it works

Upwork's public job search is backed by a GraphQL endpoint (`/api/graphql/v1`) protected by
Cloudflare. This tool:

1. **Gets a visitor token.** It requests `upwork.com` with a real **Chrome TLS fingerprint**
   (via [`curl_cffi`](https://curl-cffi.readthedocs.io/)). That fingerprint passes Cloudflare's
   passive check where plain `curl`/`requests` get a `403`. Upwork hands back an anonymous
   `visitor_gql_token` — no account involved.
2. **Runs the search.** It calls the `visitorJobSearchV1` query with that token and your filters.
3. **Returns results** as JSON (default) or CSV, paginating up to Upwork's ~5000-result ceiling.

No account is needed because it uses the **visitor** query, which is public. (The logged-in
`userJobSearchV1` query exists too but requires an account — this tool deliberately avoids it.)

## Setup

Requires Python 3.11+. One dependency: `curl_cffi`.

```bash
cd ~/claude_projects/upwork
python3 -m venv .venv
source .venv/bin/activate
pip install curl_cffi
```

(A `.venv` is already set up in this folder.)

Verify it without hitting the network:

```bash
python upwork_search.py --self-check      # -> "self-check OK"
```

## Usage

```bash
python upwork_search.py [filters] [--limit N] [--format json|csv] [--out FILE] [--proxy URL]
```

### Examples

```bash
# Keyword + experience + type, first 20, JSON to stdout
python upwork_search.py -q AI --job-type hourly --tier expert --limit 20

# Fixed-price $1k–5k "next.js" jobs -> CSV file
python upwork_search.py -q "next.js" --fixed-budget 1000-4999 --format csv --out jobs.csv

# Hourly $50-100/hr, full-time, expert
python upwork_search.py -q "machine learning" --hourly-rate 50-100 --workload full_time --tier expert

# Everything recent (no keyword), 200 jobs
python upwork_search.py --limit 200

# Established clients only (10+ hires), ongoing work
python upwork_search.py -q automation --client-hires 10- --duration ongoing

# US-based clients only, expert hourly
python upwork_search.py -q "AI agent" --location "United States" --job-type hourly --tier expert
```

## Web UI

A mobile-first web app in [`web/`](web/) wraps the same search with a visual filter UI and
email job alerts. It's a Next.js app (shadcn/ui) whose API route shells out to this Python tool.

```bash
cd web
npm install          # first time only
npm run dev          # http://localhost:3000
```

- **Search** — query bar + a filters sheet (job type, tier, workload, length, client hires,
  client location, hourly/fixed budget, contract-to-hire). Results show budget, skills, full
  description, and link to the Upwork post. Backed by `POST /api/search`.
- **Email alerts** — "Get email alerts" saves your current filters + email to
  `web/data/subscriptions.jsonl` and (optionally) emails via [Resend](https://resend.com).
  Add your key to `web/.env.local` (`RESEND_API_KEY=…`); without it, signups are still stored
  but no email is sent.
- **New-job watcher** — `node web/scripts/notify.mjs` (run via cron/launchd) re-runs each
  subscription's search and emails only jobs posted since last run. The first run per
  subscription seeds silently (no backfill blast).

Repeated searches are fast because the Python tool caches the visitor token (`~/claude_projects/upwork/.token_cache.json`, 20-min TTL).

## Filters

| Flag | Values | Notes |
|---|---|---|
| `-q`, `--query` | any text | keyword; omit for all recent jobs |
| `--job-type` | `hourly` `fixed` `weekly_retainer` | |
| `--tier` | `entry` `intermediate` `expert` | experience level |
| `--workload` | `part_time` `full_time` `as_needed` | |
| `--duration` | `ongoing` `week` `month` `semester` | project length |
| `--client-hires` | `0` `1-9` `10-` | client's past hires |
| `--location` | exact country name, e.g. `"United States"` | client location (`"US"`/`"USA"` don't work) |
| `--contract-to-hire` | (flag) | only contract-to-hire jobs |
| `--hourly-rate` | `MIN-MAX` e.g. `25-50` | auto-applies `--job-type hourly` |
| `--fixed-budget` | `MIN-MAX` e.g. `1000-4999`, or `5000-` | auto-applies `--job-type fixed` |
| `--sort` | `recency` (default) | reorders only |
| `--limit` | integer (default 50) | ceiling ~5000 |
| `--format` | `json` (default) `csv` | |
| `--out` | file path | default: stdout |
| `--proxy` | `http://user:pass@host:port` | recommended (see Caveats) |

> **Budget/rate note:** on Upwork, `--fixed-budget` filters *fixed-price* jobs and `--hourly-rate`
> filters *hourly* jobs — so the tool automatically pins the matching job type. Don't combine
> `--fixed-budget` with `--job-type hourly` (or vice-versa); it will refuse.

## Output

Each job:

```json
{
  "title": "AI Automation Engineer for Lead Management",
  "url": "https://www.upwork.com/jobs/~021...",
  "job_type": "HOURLY",
  "published": "2026-07-24T...",
  "hourly_min": "10.0",
  "hourly_max": "80.0",
  "fixed_budget": null,
  "tier": "ExpertLevel",
  "skills": "Artificial Intelligence, API, Automation",
  "description": "..."
}
```

CSV has the same columns.

## What you get without an account (and what you don't)

The visitor endpoint returns the **full** job posting — the complete description (not truncated),
skills, budget/rate, job type, experience tier, and post time. That's enough to read and evaluate
a job end to end.

These fields exist on the schema but are **oauth-scope-gated for the visitor token** (they require a
logged-in account): number of **applicants/proposals**, **connects** required to apply, **client
details** (country, total spent, hires, rating, payment-verified), whether it's **premium/enterprise**,
and **freelancer-location targeting**. Requesting any of them makes the API reject the whole query,
so the tool doesn't. The job **detail page** (`/jobs/~…`) is also hard-blocked by Cloudflare, so
there's no HTML fallback for that extra data — it genuinely needs an account.

> Note: `--location` filters by client location even though the client's country can't be *read back*
> without an account. The filter works server-side; you just don't see the country field in results.

## Caveats

- **Token fetch is occasionally blocked.** Cloudflare sometimes returns `403` to the homepage
  request that mints the token (common from datacenter/VPN IPs). The tool retries up to 5×; if it
  still fails from your network, pass a **residential `--proxy`**. The search itself is reliable
  once a token is obtained.
- **Passive protection only.** This clears TLS fingerprinting, not JavaScript challenges. If
  Upwork/Cloudflare escalates to an interactive "checking your browser" challenge, this approach
  won't get through on its own.
- **~5000-result ceiling.** Upwork caps pagination around offset 5000. To go wider, split by
  filters (per category, per budget bucket) and merge.
- **Be reasonable / Terms of Service.** Scraping may conflict with Upwork's ToS. This uses only the
  public visitor endpoint with no account, but keep it to personal/research volumes and don't
  hammer the API.

## Roadmap / possible upgrades

All optional — the tool is complete as-is. Ordered by value (adoption verified Jul-2026, GitHub ★ / PyPI monthly downloads):

1. **Concurrent pagination — no new dependency.** `curl_cffi` already ships `AsyncSession`; pulling
   5000 jobs is 100 sequential requests today. `asyncio.gather` over the page offsets would cut
   wall-clock ~10×. `AsyncSession` also has a built-in `retry=`/`RetryStrategy`, so you likely
   don't need a separate retry lib at all.
2. **[`apprise`](https://github.com/caronc/apprise) — job alerts.** (16.9k★, 11.8M/mo — the
   dominant option.) Universal notifications (Telegram, Discord, email, Slack…). Run on a schedule,
   diff new jobs against seen IDs, and get pinged when a matching job appears. Lead-gen watcher.
3. **`sqlite3` (stdlib) — persistence.** A local store + dedup for the `--watch`/cron mode above; no
   dependency needed.
4. **Analysis at volume — [DuckDB](https://duckdb.org/) (39.7k★) or [`polars`](https://pola.rs/)
   (39.1k★, 64M/mo).** DuckDB can run SQL *directly over the output file*
   (`SELECT ... FROM 'jobs.jsonl'`) with no load step — best for dedup/filter/export. `polars` if you
   prefer DataFrames. Both lighter/faster than pandas.
5. **Schema safety — [`msgspec`](https://github.com/jcrist/msgspec) (recommended, 3.9k★) or
   [`pydantic`](https://docs.pydantic.dev/) (28.4k★, 1.03B/mo).** Validate the GraphQL response and
   fail loudly if Upwork changes shape, instead of silently returning `null`. `msgspec` is faster
   and lighter (C-speed validate + JSON decode in one) — the better fit for a lean scraper;
   `pydantic` if you want the bigger ecosystem.
6. **[`rich`](https://github.com/Textualize/rich) — pretty terminal output.** Tables and progress
   bars for interactive use.
7. **Retries, if you skip async's built-in:** [`tenacity`](https://github.com/jd/tenacity) (8.7k★,
   407M/mo — the standard) or [`stamina`](https://github.com/hynek/stamina) (nicer production
   defaults, wraps tenacity). Avoid `backoff` — unmaintained since 2024.
8. **[`primp`](https://github.com/deedy5/primp) — alternative impersonation engine.** Rust-based
   (11.3M/mo); a hedge if you become throughput-bound or Cloudflare tightens and you want a
   different fingerprint stack than `curl_cffi`. (`tls-client`/`hrequests` are both stale — not
   worth it.)

**Deliberately *not* recommended:** Scrapy (overkill for one endpoint), Selenium/Playwright
(defeats the no-browser design), captcha solvers (not needed for passive TLS bypass), `httpx`
(no TLS impersonation, so it can't replace `curl_cffi` here).

## Credits

The no-account mechanic (Chrome-impersonated token fetch → `visitorJobSearchV1`) is based on
[asaniczka/Upwork-Job-Scraper](https://github.com/asaniczka/Upwork-Job-Scraper). This tool keeps
that core and adds the full, reverse-engineered search-filter set with a CLI.

## License & disclaimer

[MIT](LICENSE). **Not affiliated with, endorsed by, or connected to Upwork.** Provided for personal
and educational use — scraping may conflict with Upwork's Terms of Service, so use it responsibly
and at your own risk.
