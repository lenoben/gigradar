"use client";

import * as React from "react";
import {
  SearchIcon,
  SlidersHorizontalIcon,
  ChevronsUpDownIcon,
  Loader2Icon,
} from "lucide-react";

import type {
  SearchFilters,
  JobType,
  Tier,
  Workload,
  Duration,
  ClientHires,
} from "@/lib/types";
import {
  JOB_TYPE_OPTIONS,
  TIER_OPTIONS,
  WORKLOAD_OPTIONS,
  DURATION_OPTIONS,
  CLIENT_HIRES_OPTIONS,
  COUNTRY_OPTIONS,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

type SearchPanelProps = {
  filters: SearchFilters;
  onFiltersChange: (next: SearchFilters) => void;
  onSearch: (filters: SearchFilters) => void;
  loading: boolean;
};

// One labelled Select with a leading "Any" option (radix forbids an empty value,
// so "any" is the sentinel for undefined).
function FilterSelect({
  id,
  label,
  placeholder,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  placeholder: string;
  value: string | undefined;
  options: { value: string; label: string }[];
  onChange: (value: string | undefined) => void;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      <Select
        value={value ?? "any"}
        onValueChange={(v) => onChange(v === "any" ? undefined : v)}
      >
        <SelectTrigger id={id} className="h-9 w-full">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="any">{placeholder}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// All the advanced filters. Rendered twice (desktop inline + mobile Sheet);
// useId keeps label/control ids unique across the two live instances.
function FilterFields({
  filters,
  onFiltersChange,
  onSearch,
  className,
}: {
  filters: SearchFilters;
  onFiltersChange: (next: SearchFilters) => void;
  onSearch: (filters: SearchFilters) => void;
  className: string;
}) {
  const uid = React.useId();
  const [locOpen, setLocOpen] = React.useState(false);
  const [mode, setMode] = React.useState<"hourly" | "fixed">(
    filters.fixedBudget != null ? "fixed" : "hourly"
  );

  // Commit = update state AND run the search (discrete controls: selects, switch, location).
  const commit = (patch: Partial<SearchFilters>) => {
    const next = { ...filters, ...patch };
    onFiltersChange(next);
    onSearch(next);
  };

  // Rate/budget: text-ish, so update state only; the search flushes on Search / Show results.
  const raw = (mode === "fixed" ? filters.fixedBudget : filters.hourlyRate) ?? "";
  const dash = raw.indexOf("-");
  const rateMin = dash >= 0 ? raw.slice(0, dash) : raw;
  const rateMax = dash >= 0 ? raw.slice(dash + 1) : "";
  const setRate = (min: string, max: string, m: "hourly" | "fixed") => {
    const range = min || max ? `${min}-${max}` : undefined;
    onFiltersChange({
      ...filters,
      hourlyRate: m === "hourly" ? range : undefined,
      fixedBudget: m === "fixed" ? range : undefined,
    });
  };
  const switchMode = (m: "hourly" | "fixed") => {
    setMode(m);
    setRate(rateMin, rateMax, m);
  };

  return (
    <div className={className}>
      <FilterSelect
        id={`${uid}-jobType`}
        label="Job type"
        placeholder="Any type"
        value={filters.jobType}
        options={JOB_TYPE_OPTIONS}
        onChange={(v) => commit({ jobType: v as JobType | undefined })}
      />
      <FilterSelect
        id={`${uid}-tier`}
        label="Experience level"
        placeholder="Any level"
        value={filters.tier}
        options={TIER_OPTIONS}
        onChange={(v) => commit({ tier: v as Tier | undefined })}
      />
      <FilterSelect
        id={`${uid}-workload`}
        label="Workload"
        placeholder="Any workload"
        value={filters.workload}
        options={WORKLOAD_OPTIONS}
        onChange={(v) => commit({ workload: v as Workload | undefined })}
      />
      <FilterSelect
        id={`${uid}-duration`}
        label="Project length"
        placeholder="Any length"
        value={filters.duration}
        options={DURATION_OPTIONS}
        onChange={(v) => commit({ duration: v as Duration | undefined })}
      />
      <FilterSelect
        id={`${uid}-clientHires`}
        label="Client hires"
        placeholder="Any history"
        value={filters.clientHires}
        options={CLIENT_HIRES_OPTIONS}
        onChange={(v) => commit({ clientHires: v as ClientHires | undefined })}
      />

      {/* Client location — searchable combobox */}
      <div className="grid gap-1.5">
        <Label className="text-xs font-medium text-muted-foreground">
          Client location
        </Label>
        <Popover open={locOpen} onOpenChange={setLocOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              role="combobox"
              aria-expanded={locOpen}
              className="h-9 w-full justify-between font-normal"
            >
              <span className={cn("truncate", !filters.location && "text-muted-foreground")}>
                {filters.location ?? "Any location"}
              </span>
              <ChevronsUpDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0">
            <Command>
              <CommandInput placeholder="Search country…" />
              <CommandList>
                <CommandEmpty>No country found.</CommandEmpty>
                <CommandGroup>
                  <CommandItem
                    value="Any location"
                    data-checked={!filters.location}
                    onSelect={() => {
                      commit({ location: undefined });
                      setLocOpen(false);
                    }}
                  >
                    Any location
                  </CommandItem>
                  {COUNTRY_OPTIONS.map((c) => (
                    <CommandItem
                      key={c}
                      value={c}
                      data-checked={filters.location === c}
                      onSelect={() => {
                        commit({ location: c });
                        setLocOpen(false);
                      }}
                    >
                      {c}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      </div>

      {/* Budget — Hourly rate / Fixed price toggle + min–max */}
      <div className="grid gap-1.5 sm:col-span-2">
        <Label className="text-xs font-medium text-muted-foreground">Budget</Label>
        <div className="flex items-center gap-2">
          <div className="flex shrink-0 rounded-lg border p-0.5">
            {(["hourly", "fixed"] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={mode === m}
                onClick={() => switchMode(m)}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-xs font-medium capitalize transition-colors",
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {m}
              </button>
            ))}
          </div>
          <Input
            type="number"
            inputMode="numeric"
            min={0}
            placeholder="Min"
            value={rateMin}
            onChange={(e) => setRate(e.target.value, rateMax, mode)}
            aria-label={`Minimum ${mode === "hourly" ? "hourly rate" : "budget"}`}
            className="h-9"
          />
          <span aria-hidden className="text-muted-foreground">
            –
          </span>
          <Input
            type="number"
            inputMode="numeric"
            min={0}
            placeholder="Max"
            value={rateMax}
            onChange={(e) => setRate(rateMin, e.target.value, mode)}
            aria-label={`Maximum ${mode === "hourly" ? "hourly rate" : "budget"}`}
            className="h-9"
          />
          <span className="w-8 shrink-0 text-xs text-muted-foreground">
            {mode === "hourly" ? "/hr" : "fixed"}
          </span>
        </div>
      </div>

      {/* Contract-to-hire */}
      <div className="grid gap-1.5">
        <Label
          htmlFor={`${uid}-cth`}
          className="text-xs font-medium text-muted-foreground"
        >
          Contract-to-hire
        </Label>
        <div className="flex h-9 items-center">
          <Switch
            id={`${uid}-cth`}
            checked={!!filters.contractToHire}
            onCheckedChange={(c) => commit({ contractToHire: c ? true : undefined })}
          />
        </div>
      </div>

      {/* Results per page */}
      <div className="grid gap-1.5">
        <Label
          htmlFor={`${uid}-limit`}
          className="text-xs font-medium text-muted-foreground"
        >
          Results
        </Label>
        <Select
          value={String(filters.limit ?? 20)}
          onValueChange={(v) => commit({ limit: Number(v) })}
        >
          <SelectTrigger id={`${uid}-limit`} className="h-9 w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[20, 50, 100].map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n} per page
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

export function SearchPanel({
  filters,
  onFiltersChange,
  onSearch,
  loading,
}: SearchPanelProps) {
  const [sheetOpen, setSheetOpen] = React.useState(false);

  const activeCount = [
    filters.jobType,
    filters.tier,
    filters.workload,
    filters.duration,
    filters.clientHires,
    filters.location,
    filters.hourlyRate,
    filters.fixedBudget,
    filters.contractToHire ? "on" : undefined,
  ].filter(Boolean).length;

  const clearFilters = () => {
    const next: SearchFilters = { query: filters.query, limit: filters.limit };
    onFiltersChange(next);
    onSearch(next);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Always-visible search bar (sticky on scroll) */}
      <div className="sticky top-0 z-30 flex items-center gap-2 bg-background/85 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <div className="relative flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={filters.query ?? ""}
            onChange={(e) => onFiltersChange({ ...filters, query: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSearch(filters);
            }}
            placeholder="Search jobs, skills, keywords…"
            aria-label="Search jobs"
            className="h-11 pl-9 text-base"
          />
        </div>
        <Button
          onClick={() => onSearch(filters)}
          disabled={loading}
          className="h-11 px-4 sm:px-6"
        >
          {loading ? (
            <Loader2Icon className="animate-spin" />
          ) : (
            <SearchIcon className="sm:hidden" />
          )}
          <span className="hidden sm:inline">Search</span>
        </Button>

        {/* Mobile: filters live in a Sheet */}
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetTrigger asChild>
            <Button
              variant="outline"
              className="relative h-11 px-3 md:hidden"
              aria-label={activeCount ? `Filters, ${activeCount} active` : "Filters"}
            >
              <SlidersHorizontalIcon />
              {activeCount > 0 && (
                <Badge className="absolute -top-1.5 -right-1.5 h-5 min-w-5 justify-center px-1 tabular-nums">
                  {activeCount}
                </Badge>
              )}
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
            <SheetHeader className="border-b">
              <SheetTitle>Filters</SheetTitle>
              <SheetDescription>Refine your job search.</SheetDescription>
            </SheetHeader>
            <div className="flex-1 overflow-y-auto p-4">
              <FilterFields
                filters={filters}
                onFiltersChange={onFiltersChange}
                onSearch={onSearch}
                className="grid grid-cols-1 gap-4 sm:grid-cols-2"
              />
            </div>
            <SheetFooter className="flex-row justify-between border-t">
              <Button variant="ghost" onClick={clearFilters} disabled={activeCount === 0}>
                Clear all
              </Button>
              <SheetClose asChild>
                <Button onClick={() => onSearch(filters)}>Show results</Button>
              </SheetClose>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      </div>

      {/* Desktop: filters inline */}
      <div className="hidden md:block">
        <FilterFields
          filters={filters}
          onFiltersChange={onFiltersChange}
          onSearch={onSearch}
          className="grid grid-cols-2 gap-3 lg:grid-cols-4"
        />
      </div>
    </div>
  );
}
