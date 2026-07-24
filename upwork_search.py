#!/usr/bin/env python3
"""No-account Upwork job search via the public visitor GraphQL API.

Reuses the mechanic proven from asaniczka/Upwork-Job-Scraper: curl_cffi with
Chrome TLS-impersonation fetches a `visitor_gql_token` from the homepage, then
POSTs `visitorJobSearchV1`. No login, no Postgres, no proxy required (proxy is
optional — Upwork often 403s the token fetch from bare datacenter IPs).

Filters, enum values and value formats below were reverse-engineered live
against the API (introspection is disabled) and confirmed by matching result
counts to the response facets.

Usage:
    python upwork_search.py -q AI --job-type hourly --tier expert --limit 20
    python upwork_search.py -q "next.js" --fixed-budget 1000-4999 --format csv --out jobs.csv
    python upwork_search.py --self-check
"""

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, fields

from curl_cffi import requests

UPWORK_HOME = "https://www.upwork.com/"
GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"
TOKEN_COOKIE = "visitor_gql_token"
PAGE_MAX = 50        # API caps `count` at 50
OFFSET_MAX = 5000    # API caps `offset` at ~5000 -> ceiling ~5000 jobs/search
MAX_RETRIES = int(os.environ.get("UPWORK_MAX_RETRIES", "5"))  # lower it (e.g. 2) to fail fast behind a blocked IP
TOKEN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token_cache.json")
TOKEN_TTL = 1200     # reuse a fetched visitor token for 20 min (Upwork rotates ~25 min)

# Verified enum/value sets (see module docstring).
JOB_TYPES = ("hourly", "fixed", "weekly_retainer")
TIERS = {"entry": "EntryLevel", "intermediate": "IntermediateLevel", "expert": "ExpertLevel"}
WORKLOADS = ("part_time", "full_time", "as_needed")
DURATIONS = ("ongoing", "week", "month", "semester")
CLIENT_HIRES = ("0", "1-9", "10-")

# asaniczka's field selection, reused verbatim — resolves with the homepage
# visitor token (the narrower search-app token does not).
GRAPHQL_QUERY = """
query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) {
  search {
    universalSearchNuxt {
      visitorJobSearchV1(request: $requestVariables) {
        paging { total offset count }
        results {
          title
          description
          ontologySkills { prefLabel }
          jobTile {
            job {
              ciphertext: cipherText
              jobType
              hourlyBudgetMin
              hourlyBudgetMax
              contractorTier
              publishTime
              fixedPriceAmount { amount }
            }
          }
        }
      }
    }
  }
}
"""


class TokenError(Exception):
    """Visitor token could not be fetched or was rejected."""


class UpworkAPIError(Exception):
    """The GraphQL API returned errors or an unexpected shape."""


@dataclass(frozen=True)
class SearchFilters:
    query: str | None
    sort: str
    job_type: str | None
    tier: str | None            # entry | intermediate | expert
    workload: str | None
    duration: str | None
    contract_to_hire: bool | None
    client_hires: str | None
    hourly_rate: str | None     # "MIN-MAX", e.g. "25-50"
    fixed_budget: str | None    # "MIN-MAX" or "5000-"
    location: str | None        # client-location country name, e.g. "United States"
    highlight: bool


@dataclass
class Job:
    title: str
    url: str
    job_type: str | None
    published: str | None
    hourly_min: str | None
    hourly_max: str | None
    fixed_budget: str | None
    tier: str | None
    skills: str
    description: str


def fetch_visitor_token(proxy: str | None) -> str:
    """Fetch a visitor_gql_token from Upwork's homepage via Chrome impersonation."""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last = "unknown error"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(UPWORK_HOME, impersonate="chrome", proxies=proxies, timeout=30)
        except requests.RequestsError as exc:
            last = f"request error: {exc}"
        else:
            if resp.status_code == 200:
                token = resp.cookies.get(TOKEN_COOKIE)
                if token:
                    return token
                last = f"HTTP 200 but no {TOKEN_COOKIE!r} cookie (got {list(resp.cookies.keys())[:6]})"
            else:
                last = f"HTTP {resp.status_code}"
        print(f"[token] attempt {attempt}/{MAX_RETRIES} failed: {last}", file=sys.stderr)
        time.sleep(1.0 * attempt)
    raise TokenError(f"could not fetch visitor token after {MAX_RETRIES} attempts: {last}")


def build_request_variables(filters: SearchFilters, offset: int, count: int) -> dict[str, object]:
    """Map filters onto a VisitorJobSearchV1Request object."""
    req: dict[str, object] = {
        "sort": filters.sort,
        "highlight": filters.highlight,
        "paging": {"offset": offset, "count": count},
    }
    if filters.query:
        req["userQuery"] = filters.query
    if filters.job_type:
        req["jobType"] = filters.job_type
    if filters.tier:
        req["contractorTier"] = TIERS[filters.tier]
    if filters.workload:
        req["workload"] = filters.workload
    if filters.duration:
        req["durationV3"] = filters.duration
    if filters.contract_to_hire is not None:
        req["contractToHire"] = filters.contract_to_hire
    if filters.client_hires:
        req["clientHires"] = filters.client_hires
    if filters.location:
        req["location"] = filters.location
    # `budget`/`hourlyRate` are fixed/hourly-only predicates: without the matching
    # jobType, the other type's jobs pass through unfiltered (verified live).
    if filters.hourly_rate:
        req["hourlyRate"] = filters.hourly_rate
        req["jobType"] = "hourly"
    if filters.fixed_budget:
        req["budget"] = filters.fixed_budget
        req["jobType"] = "fixed"
    return req


def _post(headers: dict[str, str], variables: dict[str, object], proxy: str | None) -> dict[str, object]:
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.post(
        GRAPHQL_URL,
        headers=headers,
        json={"query": GRAPHQL_QUERY, "variables": variables},
        impersonate="chrome",
        proxies=proxies,
        timeout=25,
    )
    if resp.status_code == 401:
        raise TokenError("HTTP 401 — visitor token rejected or expired")
    if resp.status_code != 200:
        raise UpworkAPIError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def extract_page(payload: dict[str, object]) -> tuple[list[dict[str, object]], int]:
    if payload.get("errors"):
        raise UpworkAPIError(f"GraphQL errors: {json.dumps(payload['errors'])[:500]}")
    data = payload.get("data") or {}
    node = ((data.get("search") or {}).get("universalSearchNuxt") or {}).get("visitorJobSearchV1")
    if not node:
        raise UpworkAPIError(f"unexpected response shape: {json.dumps(payload)[:300]}")
    paging = node.get("paging") or {}
    return node.get("results") or [], int(paging.get("total") or 0)


def _s(value: object) -> str | None:
    return None if value is None else str(value)


def to_job(raw: dict[str, object]) -> Job:
    job = (raw.get("jobTile") or {}).get("job") or {}
    cipher = job.get("ciphertext") or ""
    skills = ", ".join(s.get("prefLabel", "") for s in (raw.get("ontologySkills") or []))
    fixed = job.get("fixedPriceAmount") or {}
    return Job(
        title=str(raw.get("title") or ""),
        url=f"https://www.upwork.com/jobs/{cipher}" if cipher else "",
        job_type=_s(job.get("jobType")),
        published=_s(job.get("publishTime")),
        hourly_min=_s(job.get("hourlyBudgetMin")),
        hourly_max=_s(job.get("hourlyBudgetMax")),
        fixed_budget=_s(fixed.get("amount")),
        tier=_s(job.get("contractorTier")),
        skills=skills,
        description=str(raw.get("description") or ""),
    )


def get_cached_token(proxy: str | None) -> str:
    """Return a cached visitor token if fresh, else fetch and cache a new one."""
    try:
        with open(TOKEN_CACHE) as fh:
            cached = json.load(fh)
        if time.time() - float(cached["ts"]) < TOKEN_TTL:
            return str(cached["token"])
    except (FileNotFoundError, KeyError, ValueError):
        pass
    token = fetch_visitor_token(proxy)
    try:
        with open(TOKEN_CACHE, "w") as fh:
            json.dump({"token": token, "ts": time.time()}, fh)
    except OSError:
        pass
    return token


def _invalidate_token_cache() -> None:
    try:
        os.remove(TOKEN_CACHE)
    except OSError:
        pass


def _search_with_token(filters: SearchFilters, limit: int, offset: int, token: str, proxy: str | None) -> tuple[list[Job], int]:
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Referer": "https://www.upwork.com/nx/search/jobs/?",
        "X-Upwork-Accept-Language": "en-US",
        "Authorization": f"Bearer {token}",
    }
    jobs: list[Job] = []
    total = 0
    cur = offset
    while len(jobs) < limit and cur <= OFFSET_MAX:
        count = min(PAGE_MAX, limit - len(jobs))
        variables = {"requestVariables": build_request_variables(filters, cur, count)}
        results, total = extract_page(_post(headers, variables, proxy))
        if not results:
            break
        jobs.extend(to_job(r) for r in results)
        cur += PAGE_MAX
        if cur >= total:
            break
    return jobs[:limit], total


def run_search(filters: SearchFilters, limit: int, offset: int, proxy: str | None) -> tuple[list[Job], int]:
    """Search from `offset` using a cached token; on a 401 (rotated token) refresh once and retry.
    Returns (jobs, total) where total is the full result count Upwork reports for the query."""
    try:
        return _search_with_token(filters, limit, offset, get_cached_token(proxy), proxy)
    except TokenError:
        _invalidate_token_cache()
        return _search_with_token(filters, limit, offset, get_cached_token(proxy), proxy)


def write_json(jobs: list[Job], out) -> None:
    json.dump([asdict(j) for j in jobs], out, indent=2, ensure_ascii=False)
    out.write("\n")


def write_csv(jobs: list[Job], out) -> None:
    writer = csv.DictWriter(out, fieldnames=[f.name for f in fields(Job)])
    writer.writeheader()
    for job in jobs:
        writer.writerow(asdict(job))


def _self_check() -> None:
    # fixed-budget must force jobType=fixed, tier maps to the PascalCase enum
    v = build_request_variables(
        SearchFilters("AI", "recency", None, "expert", None, None, None, None, None, "1000-4999", None, False),
        0, 50,
    )
    assert v["contractorTier"] == "ExpertLevel", v
    assert v["jobType"] == "fixed" and v["budget"] == "1000-4999", v
    # hourly-rate must force jobType=hourly; no query -> no userQuery key
    v2 = build_request_variables(
        SearchFilters(None, "recency", None, None, None, None, None, None, "25-50", None, None, False),
        0, 50,
    )
    assert v2["jobType"] == "hourly" and v2["hourlyRate"] == "25-50", v2
    assert "userQuery" not in v2, v2
    # a happy-path parse
    j = to_job({"title": "X", "jobTile": {"job": {"ciphertext": "~abc", "jobType": "HOURLY"}}})
    assert j.url.endswith("~abc") and j.job_type == "HOURLY", j
    # pagination: offset flows into the request paging
    v3 = build_request_variables(
        SearchFilters("AI", "recency", None, None, None, None, None, None, None, None, None, False), 40, 20,
    )
    assert v3["paging"] == {"offset": 40, "count": 20}, v3
    print("self-check OK")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="No-account Upwork job search (visitor GraphQL API).")
    p.add_argument("-q", "--query", help="keyword search; omit to get all recent jobs")
    p.add_argument("--sort", default="recency", help="sort order (default: recency)")
    p.add_argument("--job-type", choices=JOB_TYPES)
    p.add_argument("--tier", choices=tuple(TIERS), help="experience level")
    p.add_argument("--workload", choices=WORKLOADS)
    p.add_argument("--duration", choices=DURATIONS)
    p.add_argument("--contract-to-hire", action="store_true", help="only contract-to-hire jobs")
    p.add_argument("--client-hires", choices=CLIENT_HIRES, help="client's prior hire count")
    p.add_argument("--location", metavar="COUNTRY", help='client location, exact country name e.g. "United States"')
    p.add_argument("--hourly-rate", metavar="MIN-MAX", help='e.g. "25-50" (forces --job-type hourly)')
    p.add_argument("--fixed-budget", metavar="MIN-MAX", help='e.g. "1000-4999" or "5000-" (forces fixed)')
    p.add_argument("--limit", type=int, default=50, help="jobs per call / page size (default 50)")
    p.add_argument("--offset", type=int, default=0, help="pagination start offset (0..~5000)")
    p.add_argument("--meta", action="store_true", help='output {total, offset, count, jobs} JSON for pagination')
    p.add_argument("--format", choices=("json", "csv"), default="json")
    p.add_argument("--out", metavar="FILE", help="output file (default stdout)")
    p.add_argument("--proxy", help="http://user:pass@host:port (recommended for token fetch)")
    p.add_argument("--highlight", action="store_true", help="keep search-term highlight markup")
    p.add_argument("--self-check", action="store_true", help="run logic self-test and exit")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.self_check:
        _self_check()
        return 0
    if args.hourly_rate and args.job_type == "fixed":
        print("error: --hourly-rate is hourly-only; drop --job-type fixed", file=sys.stderr)
        return 2
    if args.fixed_budget and args.job_type == "hourly":
        print("error: --fixed-budget is fixed-only; drop --job-type hourly", file=sys.stderr)
        return 2
    filters = SearchFilters(
        query=args.query,
        sort=args.sort,
        job_type=args.job_type,
        tier=args.tier,
        workload=args.workload,
        duration=args.duration,
        contract_to_hire=True if args.contract_to_hire else None,
        client_hires=args.client_hires,
        hourly_rate=args.hourly_rate,
        fixed_budget=args.fixed_budget,
        location=args.location,
        highlight=args.highlight,
    )
    try:
        jobs, total = run_search(filters, args.limit, args.offset, args.proxy)
    except (TokenError, UpworkAPIError) as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        if not args.proxy:
            print("hint: Upwork often 403s the token fetch from bare IPs — retry or pass --proxy", file=sys.stderr)
        return 1

    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        if args.meta:
            json.dump(
                {"total": total, "offset": args.offset, "count": len(jobs), "jobs": [asdict(j) for j in jobs]},
                out, ensure_ascii=False,
            )
            out.write("\n")
        else:
            (write_csv if args.format == "csv" else write_json)(jobs, out)
    finally:
        if args.out:
            out.close()
    print(f"{len(jobs)} jobs (of {total})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
