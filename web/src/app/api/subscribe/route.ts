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

export async function POST(req: Request): Promise<Response> {
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
