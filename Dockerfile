# Dual runtime: Node (Next.js web app) + Python (upwork_search.py via curl_cffi).
# Layout in the image mirrors the repo so web/src/lib/pythonSearch.ts resolves
# ROOT = path.resolve(process.cwd(), "..") -> /app, python at /app/.venv, script
# at /app/upwork_search.py.
FROM node:24-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python scraper + its one dependency (curl_cffi ships manylinux wheels — no build tools needed)
COPY upwork_search.py ./upwork_search.py
RUN python3 -m venv /app/.venv \
  && /app/.venv/bin/pip install --no-cache-dir curl_cffi

# Next.js app: install deps, then build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build \
  && mkdir -p /app/web/data

ENV NODE_ENV=production
ENV PORT=3000
EXPOSE 3000

# next start (production). Runs from /app/web so process.cwd()=/app/web, ROOT=/app.
CMD ["npm", "run", "start"]
