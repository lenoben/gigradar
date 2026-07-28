"use client";

import * as React from "react";
import { Settings2 } from "lucide-react";

import { DEFAULT_MODEL } from "@/lib/openrouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type ProposalSettings = {
  openrouterKey: string;
  model: string;
  profile: string;
};

const STORAGE_KEY = "upwork-search:proposal-settings";
const DEFAULTS: ProposalSettings = { openrouterKey: "", model: DEFAULT_MODEL, profile: "" };

type SettingsContextValue = {
  settings: ProposalSettings;
  save: (next: ProposalSettings) => void;
  isConfigured: boolean;
  openSettings: () => void;
};

const SettingsContext = React.createContext<SettingsContextValue | null>(null);

export function useSettings(): SettingsContextValue {
  const ctx = React.useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within <SettingsProvider>");
  return ctx;
}

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = React.useState<ProposalSettings>(DEFAULTS);
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: hydrate from localStorage after mount (unavailable during SSR)
      if (raw) setSettings({ ...DEFAULTS, ...(JSON.parse(raw) as Partial<ProposalSettings>) });
    } catch {
      // absent or corrupt storage — fall back to defaults.
    }
  }, []);

  const save = React.useCallback((next: ProposalSettings) => {
    setSettings(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // storage disabled (private mode) — keep it in memory for this session.
    }
  }, []);

  const value = React.useMemo<SettingsContextValue>(
    () => ({
      settings,
      save,
      isConfigured: settings.openrouterKey.trim().length > 0,
      openSettings: () => setOpen(true),
    }),
    [settings, save],
  );

  return (
    <SettingsContext.Provider value={value}>
      {children}
      <SettingsDialog
        open={open}
        onOpenChange={setOpen}
        settings={settings}
        onSave={(next) => {
          save(next);
          setOpen(false);
        }}
      />
    </SettingsContext.Provider>
  );
}

/** Gear button that opens the settings dialog. Must render inside SettingsProvider. */
export function SettingsButton() {
  const { openSettings, isConfigured } = useSettings();
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={openSettings}
      aria-label="Proposal settings"
      title={isConfigured ? "Proposal settings" : "Set up AI proposals"}
      className="relative"
    >
      <Settings2 className="size-5" />
      {!isConfigured && (
        <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-primary" aria-hidden />
      )}
    </Button>
  );
}

function SettingsDialog({
  open,
  onOpenChange,
  settings,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: ProposalSettings;
  onSave: (next: ProposalSettings) => void;
}) {
  const [draft, setDraft] = React.useState<ProposalSettings>(settings);

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: reset the form to saved settings when the dialog opens
    if (open) setDraft(settings);
  }, [open, settings]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>AI proposal settings</DialogTitle>
          <DialogDescription>
            Used to draft tailored proposals. Your key stays in this browser and is sent only to
            OpenRouter — never to this site.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="or-key">OpenRouter API key</Label>
            <Input
              id="or-key"
              type="password"
              autoComplete="off"
              placeholder="sk-or-v1-…"
              value={draft.openrouterKey}
              onChange={(e) => setDraft({ ...draft, openrouterKey: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Get one at{" "}
              <a
                href="https://openrouter.ai/keys"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2"
              >
                openrouter.ai/keys
              </a>
              . You pay OpenRouter directly for what you use.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="or-model">Model</Label>
            <Input
              id="or-model"
              placeholder={DEFAULT_MODEL}
              value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Any id from{" "}
              <a
                href="https://openrouter.ai/models"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2"
              >
                openrouter.ai/models
              </a>
              .
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="or-profile">Your background</Label>
            <Textarea
              id="or-profile"
              rows={5}
              placeholder="Your skills, experience, notable projects, hourly rate, tone — anything that should shape your proposals."
              value={draft.profile}
              onChange={(e) => setDraft({ ...draft, profile: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              The more specific, the more tailored (and less generic) each proposal.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button onClick={() => onSave(draft)}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
