"use client"

import * as React from "react"
import { Bell } from "lucide-react"
import { toast } from "sonner"

import type { SearchFilters } from "@/lib/types"
import {
  JOB_TYPE_OPTIONS,
  TIER_OPTIONS,
  WORKLOAD_OPTIONS,
  DURATION_OPTIONS,
  CLIENT_HIRES_OPTIONS,
} from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"

// Basic, pragmatic email shape check — server does the authoritative validation.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// "25-50" -> "25–50", "5000-" -> "5000+", "-50" -> "up to 50".
function formatRange(range: string): string {
  const [min, max] = range.split("-")
  if (min && max) return `${min}–${max}`
  if (min) return `${min}+`
  if (max) return `up to ${max}`
  return range
}

// Set filters -> friendly badge labels. Empty/undefined fields are skipped;
// `limit` is paging noise, not an alert filter, so it's intentionally omitted.
function summarizeFilters(f: SearchFilters): string[] {
  const parts: string[] = []
  if (f.query) parts.push(`“${f.query}”`)
  if (f.jobType)
    parts.push(JOB_TYPE_OPTIONS.find((o) => o.value === f.jobType)?.label ?? f.jobType)
  if (f.tier)
    parts.push(TIER_OPTIONS.find((o) => o.value === f.tier)?.label ?? f.tier)
  if (f.workload)
    parts.push(WORKLOAD_OPTIONS.find((o) => o.value === f.workload)?.label ?? f.workload)
  if (f.duration)
    parts.push(DURATION_OPTIONS.find((o) => o.value === f.duration)?.label ?? f.duration)
  if (f.clientHires)
    parts.push(
      CLIENT_HIRES_OPTIONS.find((o) => o.value === f.clientHires)?.label ?? f.clientHires
    )
  if (f.contractToHire) parts.push("Contract-to-hire")
  if (f.hourlyRate) parts.push(`$${formatRange(f.hourlyRate)}/hr`)
  if (f.fixedBudget) parts.push(`$${formatRange(f.fixedBudget)} fixed`)
  if (f.location) parts.push(f.location)
  return parts
}

// Pull an { error } message out of a non-200 JSON body, else a status fallback.
function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object" && "error" in data) {
    const e = (data as { error: unknown }).error
    if (typeof e === "string" && e.trim()) return e
  }
  return `Subscription failed (${status}). Please try again.`
}

export function SubscribeDialog({ currentFilters }: { currentFilters: SearchFilters }) {
  const [open, setOpen] = React.useState(false)
  const [email, setEmail] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
  const [pending, setPending] = React.useState(false)

  const summary = summarizeFilters(currentFilters)

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = email.trim()
    if (!EMAIL_RE.test(trimmed)) {
      setError("Please enter a valid email address.")
      return
    }
    setError(null)
    setPending(true)
    try {
      const res = await fetch("/api/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: trimmed, filters: currentFilters }),
      })
      if (!res.ok) {
        const data: unknown = await res.json().catch(() => null)
        throw new Error(errorMessage(data, res.status))
      }
      toast.success("You're subscribed — check your inbox.")
      setOpen(false)
      setEmail("")
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Something went wrong. Please try again."
      toast.error(message)
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Bell />
          Get email alerts
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Get email alerts</DialogTitle>
            <DialogDescription>
              We&apos;ll email you when new jobs match your current filters.
            </DialogDescription>
          </DialogHeader>

          <div
            className="flex flex-wrap gap-1.5"
            aria-label="Filters these alerts will match"
          >
            {summary.length > 0 ? (
              summary.map((label) => (
                <Badge key={label} variant="secondary">
                  {label}
                </Badge>
              ))
            ) : (
              <Badge variant="secondary">All recent jobs</Badge>
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="subscribe-email">Email</Label>
            <Input
              id="subscribe-email"
              name="email"
              type="email"
              inputMode="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                if (error) setError(null)
              }}
              required
              disabled={pending}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "subscribe-email-error" : undefined}
            />
            {error && (
              <p
                id="subscribe-email-error"
                role="alert"
                className="text-sm text-destructive"
              >
                {error}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button type="submit" disabled={pending} className="w-full sm:w-auto">
              {pending ? (
                <>
                  <Spinner />
                  Subscribing…
                </>
              ) : (
                "Subscribe"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
