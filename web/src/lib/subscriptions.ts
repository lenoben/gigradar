// JSONL-backed subscription store (one JSON object per line). Deliberately a
// flat file, not a database — the volume is a handful of personal alert
// subscriptions. ponytail: JSONL + full rewrite; move to SQLite if this ever
// grows past a few thousand rows or needs concurrent writers.

import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, appendFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { SearchFilters } from "@/lib/types";

export type Subscription = {
  id: string;
  email: string;
  filters: SearchFilters;
  createdAt: string;
  seenUrls: string[];
};

// Runtime cwd is <repoRoot>/web, so data/ lives directly under it.
const DATA_DIR = path.join(process.cwd(), "data");
const STORE = path.join(DATA_DIR, "subscriptions.jsonl");

function ensureStore(): void {
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  if (!existsSync(STORE)) writeFileSync(STORE, "", "utf8");
}

export function listSubscriptions(): Subscription[] {
  ensureStore();
  return readFileSync(STORE, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    // Trusted boundary: we are the only writer of this file.
    .map((line) => JSON.parse(line) as Subscription);
}

export function addSubscription(email: string, filters: SearchFilters): Subscription {
  ensureStore();
  const subscription: Subscription = {
    id: randomUUID(),
    email,
    filters,
    createdAt: new Date().toISOString(),
    seenUrls: [],
  };
  appendFileSync(STORE, `${JSON.stringify(subscription)}\n`, "utf8");
  return subscription;
}

/** Merge `urls` into the subscription's seenUrls (deduped) and rewrite the store. */
export function markSeen(id: string, urls: string[]): void {
  const subscriptions = listSubscriptions();
  if (!subscriptions.some((s) => s.id === id)) {
    throw new Error(`markSeen: no subscription with id ${id}`);
  }
  const updated = subscriptions.map((s) =>
    s.id === id ? { ...s, seenUrls: Array.from(new Set([...s.seenUrls, ...urls])) } : s,
  );
  writeFileSync(STORE, updated.map((s) => JSON.stringify(s)).join("\n") + "\n", "utf8");
}
