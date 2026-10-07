# My freelancer profile

Copy to profile.md (gitignored) and rewrite it in your own words.

How gigradar reads this file:
- Every `## Heading` is one skill area. A job is compared with each area and scored by
  the best match, so keep areas focused: a job usually fits one of them, not all.
- The heading shows up in alerts as the reason ("82 · Rust backend · ...").
- Everything above the first `##` (this text) and <!-- comments --> are ignored.
- Write like a job post would: concrete stacks, problem types, domains. 3-10 lines per area.
- Raw material: `python -m gigradar.profile_import <your CV/portfolio files>` converts
  documents into profile_sources/*.md to copy from. Scoring only ever reads this file.

## Rust / Go backend

<!-- What you build, with which stack, for whom. -->
Backend services and APIs in Rust (axum, tokio, sqlx) and Go. REST and gRPC APIs,
background workers, queue consumers. Performance work: profiling, cutting latency and
memory, replacing slow Python services with Rust.

## Next.js / SvelteKit frontend

Web apps in Next.js (App Router, React Server Components) and SvelteKit with TypeScript
and Tailwind. Dashboards, admin panels, SaaS frontends, auth flows, Stripe checkout.

## PostgreSQL / data

Schema design, query tuning and migrations in PostgreSQL. ETL pipelines and scrapers in
Python, reporting with DuckDB.
