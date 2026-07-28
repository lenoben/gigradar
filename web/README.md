# Upwork Job Search — web app

The Next.js frontend for [upwork-jobs](https://github.com/mishafyi/upwork-jobs). Full docs, the
feature list, Docker self-hosting, and the ⚠️ **proxy requirement** live in the
[root README → Web UI](../README.md#web-ui).

**Prerequisite:** this app shells out to the Python tool, so do the Python
[Setup](../README.md#setup) first (it creates the repo-root `.venv` that `/api/search` calls).
Then, and with **Node 20+**:

```bash
npm install
npm run dev        # http://localhost:3000
```

- `POST /api/search` — runs `../upwork_search.py` via `../.venv/bin/python`, returns `{ jobs, total }`.
- `POST /api/subscribe` — stores a subscription and emails a confirmation via Resend.
- `scripts/notify.mjs` — the new-job alert watcher (run on a schedule; see root README).

Config: copy `.env.example` → `.env.local` — `UPWORK_PROXY`, `RESEND_API_KEY`, `RESEND_FROM` (all
optional). The AI proposal drafter is **BYOK**: users add their own OpenRouter key in the app's ⚙
settings (stored in their browser, called client-side — nothing server-side, no env needed).
