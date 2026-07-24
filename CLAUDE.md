# CLAUDE.md — Upwork Job Search

No-account Upwork job search. One self-contained script, `upwork_search.py`, that queries
Upwork's **public visitor GraphQL API** with full search filters — no login, no browser, no
Postgres. `README.md` is the user-facing guide; this file captures the non-obvious, hard-won
facts a future session needs and cannot get from the code alone.

## How it works (the mechanic)

1. `curl_cffi` does a `GET https://www.upwork.com/` with `impersonate="chrome"`. The Chrome
   **TLS/JA3 fingerprint** clears Cloudflare's passive check that a plain `requests`/`curl` fails
   (they get a 403). The response sets a `visitor_gql_token` cookie.
2. That token is sent as `Authorization: Bearer <token>` to `POST /api/graphql/v1`, running the
   `visitorJobSearchV1` query (again with `impersonate="chrome"`).
3. Results are paginated (50/page, offset capped ~5000) and flattened into `Job` rows.
4. Output as JSON or CSV.

## Non-obvious facts (verified live — do not re-derive by guessing)

- **`visitorJobSearchV1` is public; `userJobSearchV1` is NOT.** The `visitor*` query needs no
  account. The `user*` query (what hashiromer's Go repo used) requires a **logged-in** token —
  a visitor token is rejected with *"not enough oauth2 permissions/scopes"*. This is the whole
  reason this tool needs no account.
- **Use the homepage `visitor_gql_token`, not the search-page cookie.** The search app also sets
  `UniversalSearchNuxt_vt`, but it is narrowly scoped and rejects most fields
  (`ciphertext`, `amount`, `publishTime`, …). The homepage `visitor_gql_token` has broad public
  scope and resolves every field in `GRAPHQL_QUERY`.
- **GraphQL introspection is disabled** (`"Introspection query is disabled for this client"`).
  The parameter set below was reverse-engineered by field-probing (unknown fields error with
  *"not defined for input object type 'VisitorJobSearchV1Request'"*) and cross-checked against
  the response `facets{…}` counts. If you need to discover more, that's the method.
- **`budget` and `hourlyRate` are job-type-scoped predicates that do NOT restrict job type.**
  Send `budget` without `jobType:"fixed"` and every hourly job passes through unfiltered (they
  have no fixed budget to test). `build_request_variables()` enforces the pairing:
  `budget → jobType=fixed`, `hourlyRate → jobType=hourly`. Verified:
  `budget=1000-4999` alone = 3444 (= 305 in-bucket fixed + 3139 all hourly); with
  `jobType=fixed` = 305 (exact facet match).
- **`contractorTier` is a PascalCase enum**, `JobSearchContractorTier`:
  `EntryLevel` / `IntermediateLevel` / `ExpertLevel`. NOT `1/2/3`, NOT `EXPERT`, NOT `EXPERT_LEVEL`.
- **`sort` only reorders, never changes the count.** Repo default `recency`.
- **Token fetch is the fragile step**, not the search. Upwork often 403s the homepage GET from a
  bare datacenter IP (the token mint), so `fetch_visitor_token()` retries `MAX_RETRIES`×. The
  search POST is reliable once the token is in hand. Pass `--proxy` (residential) for production.
- **TLS impersonation ≠ JS-challenge bypass.** This clears passive TLS fingerprinting only. If
  Cloudflare escalates to an active "checking your browser" / Turnstile challenge, curl_cffi
  cannot solve it — you'd need a real browser or a proxy that clears the challenge.
- **Full job info: the search result IS the full posting for a visitor.** `description` is the
  complete text (the tool no longer truncates it), plus skills/budget/tier/type/times. But these
  result fields are **scope-gated** (login required — requesting any aborts the whole query, so
  don't add them): `totalApplicants`, `connectPrice`, `premium`, `enterpriseJob`,
  `preferredFreelancerLocation[Mandatory]`, and the client blocks `clientCompanyPublic` +
  `upworkHistoryData.client.*` (country, totalSpent, totalHires, reviews, paymentVerification).
  Visitor-safe extras not currently selected: `createTime`, `weeklyRetainerBudget`. The
  `/jobs/~<cipher>` **detail page is hard-403'd by Cloudflare**, so there is no HTML fallback for
  the gated data — it truly needs an account.

## Verified `requestVariables` parameter set

| Param | Type / values | Notes |
|---|---|---|
| `userQuery` | string | keyword; omit → all recent jobs |
| `sort` | string (`recency`) | reorders only |
| `highlight` | bool | wraps matched terms in markup; default off |
| `paging` | `{offset, count}` | `count` ≤ 50, `offset` ≤ ~5000 |
| `jobType` | enum: `hourly`/`fixed`/`weekly_retainer` | |
| `contractorTier` | enum: `EntryLevel`/`IntermediateLevel`/`ExpertLevel` | |
| `workload` | enum: `part_time`/`full_time`/`as_needed` | |
| `durationV3` | enum: `ongoing`/`week`/`month`/`semester` | |
| `contractToHire` | bool | |
| `clientHires` | string: `0`/`1-9`/`10-` | |
| `hourlyRate` | string range `"25-50"` | **pair with `jobType=hourly`** |
| `budget` | string range `"1000-4999"`/`"5000-"` | **pair with `jobType=fixed`** |
| `location` | exact country name `"United States"` | client location; `"US"`/`"USA"` do NOT match |
| `skills`, `title`, `ontologySkillUid` | string | accepted, less used |

**Rejected** (not real request fields — some are response-facet names only):
`amount`, `duration`, `category`, `categoryIds`, `occupations`, `clientLocation`, `proposals`,
`paymentVerified`, `daysPosted`, `connectPrice`.

## Code map (`upwork_search.py`, single file)

- `fetch_visitor_token(proxy)` — homepage GET → `visitor_gql_token`, with retries.
- `build_request_variables(filters, offset, count)` — maps `SearchFilters` → request; enforces the
  budget/rate → jobType pairing. **The one piece of non-trivial logic; `_self_check()` covers it.**
- `_post` / `extract_page` — POST + navigate `data.search.universalSearchNuxt.visitorJobSearchV1`;
  raises `UpworkAPIError` on GraphQL errors (never swallows).
- `to_job(raw)` — flattens a result into a `Job`.
- `run_search(filters, limit, proxy)` — token + paginate loop.
- `write_json` / `write_csv`, `build_arg_parser`, `main`.

## Dev commands

```bash
source .venv/bin/activate          # only dep: curl_cffi
python upwork_search.py --self-check                       # logic test, no network
python upwork_search.py -q AI --job-type hourly --tier expert --limit 5
```

## Constraints / conventions

- **One file, stdlib-first.** Only external dep is `curl_cffi` (TLS impersonation is the one thing
  stdlib can't do). Output/CLI/parsing use `argparse`/`csv`/`json`/`dataclasses`. Don't add a
  framework for what a function does.
- Errors surface, never swallowed; specific exception types (`TokenError`, `UpworkAPIError`).
- No default parameter values in function signatures (argparse defaults are fine).
- **Legal:** scraping may violate Upwork's ToS. This hits only the public visitor endpoint with no
  account, but treat it as personal/research use; don't hammer it (that's what the ~5000 cap and
  sequential paging naturally limit). See README "Caveats".

## Tech stack docs

- `curl_cffi` — https://curl-cffi.readthedocs.io/en/latest/ (TLS/JA3 impersonation, `AsyncSession`,
  `impersonate="chrome"`). Verify API here before writing new request code — do not trust memory.

## Upgrade paths

See `README.md` → "Roadmap / possible upgrades" for the researched list (async pagination via the
already-installed `AsyncSession`, `apprise` alerts, `tenacity`, `polars`/DuckDB, `pydantic`, `primp`).
