"use client";

import * as React from "react";
import { toast } from "sonner";
import { Sparkles, RefreshCw, Copy, Check } from "lucide-react";

import type { JobResult } from "@/lib/types";
import { streamProposal } from "@/lib/openrouter";
import { useSettings } from "@/components/settings-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Spinner } from "@/components/ui/spinner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function ProposalDialog({ job }: { job: JobResult }) {
  const { settings, isConfigured, openSettings } = useSettings();
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const controllerRef = React.useRef<AbortController | null>(null);

  const generate = React.useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setText("");
    try {
      await streamProposal({
        apiKey: settings.openrouterKey,
        model: settings.model,
        job,
        profile: settings.profile,
        signal: controller.signal,
        onToken: (chunk) => setText((prev) => prev + chunk),
      });
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      toast.error("Couldn't draft proposal", {
        description: err instanceof Error ? err.message : "Something went wrong",
      });
    } finally {
      if (controllerRef.current === controller) setBusy(false);
    }
  }, [settings, job]);

  // Auto-draft the first time the dialog opens with a configured key.
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: auto-draft when the dialog opens
    if (open && isConfigured && !text && !busy) void generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) controllerRef.current?.abort();
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <Sparkles className="size-3.5" />
          Draft proposal
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Proposal draft</DialogTitle>
          <DialogDescription className="line-clamp-2">{job.title}</DialogDescription>
        </DialogHeader>

        {!isConfigured ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <Sparkles className="mx-auto mb-3 size-6 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Add your OpenRouter key and a short profile to draft tailored proposals — it all stays
              in your browser.
            </p>
            <Button
              className="mt-4"
              onClick={() => {
                setOpen(false);
                openSettings();
              }}
            >
              Set up AI proposals
            </Button>
          </div>
        ) : (
          <>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={12}
              placeholder={busy ? "Drafting…" : "Your proposal will appear here."}
              className="resize-none font-normal leading-relaxed"
            />
            <DialogFooter className="gap-2 sm:justify-between">
              <Button variant="ghost" onClick={() => void generate()} disabled={busy} className="gap-1.5">
                {busy ? <Spinner className="size-4" /> : <RefreshCw className="size-4" />}
                {busy ? "Drafting…" : text ? "Regenerate" : "Write proposal"}
              </Button>
              <Button onClick={copy} disabled={!text || busy} className="gap-1.5">
                {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
