import { z } from "zod";
import { addSubscription } from "@/lib/subscriptions";
import { sendConfirmation } from "@/lib/email";

// addSubscription writes to the filesystem — requires the Node.js runtime.
export const runtime = "nodejs";

// Same SearchFilters schema as /api/search (duplicated because the shared file
// set is fixed and neither route file should be imported by the other).
const filtersSchema = z.object({
  query: z.string().optional(),
  jobType: z.enum(["hourly", "fixed", "weekly_retainer"]).optional(),
  tier: z.enum(["entry", "intermediate", "expert"]).optional(),
  workload: z.enum(["part_time", "full_time", "as_needed"]).optional(),
  duration: z.enum(["ongoing", "week", "month", "semester"]).optional(),
  clientHires: z.enum(["0", "1-9", "10-"]).optional(),
  contractToHire: z.boolean().optional(),
  hourlyRate: z.string().optional(),
  fixedBudget: z.string().optional(),
  location: z.string().optional(),
  limit: z.coerce
    .number()
    .int()
    .transform((n) => Math.min(100, Math.max(1, n)))
    .optional(),
});

const subscribeSchema = z.object({
  email: z.email(),
  filters: filtersSchema,
});

function issuesToMessage(error: z.ZodError): string {
  return error.issues.map((issue) => `${issue.path.join(".") || "body"}: ${issue.message}`).join("; ");
}

// ponytail: in-memory per-IP fixed-window limiter — single-instance only (state
// resets on redeploy, not shared across replicas). This endpoint sends an email
// to any submitted address, so the throttle stops a script from bombing a target
// or draining the Resend quota. Move to a shared store (Redis) only if you run
// multiple replicas or need a hard global cap.
const RATE_LIMIT = 5; // successful signups per window per IP
const WINDOW_MS = 15 * 60 * 1000;
const hits = new Map<string, { count: number; resetAt: number }>();

function checkRate(ip: string): { ok: boolean; retryAfterSec: number } {
  const now = Date.now();
  const entry = hits.get(ip);
  if (!entry || now >= entry.resetAt) {
    hits.set(ip, { count: 1, resetAt: now + WINDOW_MS });
    if (hits.size > 5000) for (const [k, v] of hits) if (now >= v.resetAt) hits.delete(k); // bound memory
    return { ok: true, retryAfterSec: 0 };
  }
  entry.count += 1;
  if (entry.count > RATE_LIMIT) return { ok: false, retryAfterSec: Math.ceil((entry.resetAt - now) / 1000) };
  return { ok: true, retryAfterSec: 0 };
}

// Prefer x-real-ip (set by the reverse proxy, not client-spoofable) over the
// leftmost x-forwarded-for (which the client can forge to rotate the key).
function clientIp(req: Request): string {
  const real = req.headers.get("x-real-ip");
  if (real) return real.trim();
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0].trim();
  return "unknown";
}

export async function POST(req: Request): Promise<Response> {
  const rate = checkRate(clientIp(req));
  if (!rate.ok) {
    return Response.json(
      { error: "Too many requests. Please try again in a few minutes." },
      { status: 429, headers: { "Retry-After": String(rate.retryAfterSec) } },
    );
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: `invalid JSON body: ${message}` }, { status: 400 });
  }

  const parsed = subscribeSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json({ error: issuesToMessage(parsed.error) }, { status: 400 });
  }

  const { email, filters } = parsed.data;
  try {
    addSubscription(email, filters);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "failed to store subscription";
    console.error("[api/subscribe] store failed", { email, message });
    return Response.json({ error: message }, { status: 500 });
  }

  // Confirmation email is best-effort: a missing/failing key must not fail signup.
  const emailSent = await sendConfirmation(email, filters);
  return Response.json({ ok: true, emailSent });
}
