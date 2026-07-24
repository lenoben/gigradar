import { z } from "zod";
import { runSearch } from "@/lib/pythonSearch";

// child_process (via runSearch) requires the Node.js runtime.
export const runtime = "nodejs";

// SearchFilters — every field optional; enums mirror the union types in
// @/lib/types; limit is coerced and clamped to 1..100.
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
  offset: z.coerce
    .number()
    .int()
    .transform((n) => Math.max(0, n))
    .optional(),
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

  const parsed = filtersSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json({ error: issuesToMessage(parsed.error) }, { status: 400 });
  }

  try {
    const { offset = 0, ...filters } = parsed.data;
    const { jobs, total } = await runSearch(filters, offset);
    return Response.json({ jobs, count: jobs.length, total });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "search failed";
    console.error("[api/search] runSearch failed", { message });
    return Response.json({ error: message }, { status: 500 });
  }
}
