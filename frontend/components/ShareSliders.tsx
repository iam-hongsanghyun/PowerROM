"use client";

import { useEffect, useRef, useState } from "react";
import { GripVertical } from "lucide-react";

import type { Capacities, GeneratorKey } from "@/lib/api";
import { ALL_GENERATOR_KEYS, GENERATOR_COLORS, GENERATOR_LABELS } from "@/lib/constants";

const DEFAULT_ORDER = [...ALL_GENERATOR_KEYS] as GeneratorKey[];

/** User-set storage rated power (GW) per tier. Duration is set in the Parameters ESS section. */
export interface StorageInput {
  shortPowerGw: number;
  phsPowerGw: number;
  longPowerGw: number;
}

// Storage tiers shown as rows below the generators — dispatched endogenously (not merit-ordered),
// each independently expandable to help meet 100% load. `field` indexes StorageInput; `expandKey`
// is the per-tier flag sent to the solver; `addedKey` is the report key the solver grows under.
const STORAGE_TIERS = [
  { field: "shortPowerGw", expandKey: "storage_short", addedKey: "storage", label: "Battery (short)", color: "#8BC34A" },
  { field: "phsPowerGw", expandKey: "storage_phs", addedKey: "storage_phs", label: "Pumped hydro (PHS)", color: "#26A69A" },
  { field: "longPowerGw", expandKey: "storage_long", addedKey: "storage_long", label: "Seasonal (long)", color: "#558B2F" },
] as const;

function completeOrder(generatorOrder: GeneratorKey[]): GeneratorKey[] {
  const seen = new Set<GeneratorKey>();
  const ordered: GeneratorKey[] = [];
  for (const key of generatorOrder) {
    if (!seen.has(key)) {
      ordered.push(key);
      seen.add(key);
    }
  }
  for (const key of DEFAULT_ORDER) {
    if (!seen.has(key)) ordered.push(key);
  }
  return ordered;
}

function reorder(order: GeneratorKey[], draggedKey: GeneratorKey, targetKey: GeneratorKey): GeneratorKey[] {
  const from = order.indexOf(draggedKey);
  const to = order.indexOf(targetKey);
  if (from < 0 || to < 0 || from === to) return order;
  const next = [...order];
  next.splice(from, 1);
  next.splice(to, 0, draggedKey);
  return next;
}

export function ShareSliders({
  capacityInputs,
  minCfInputs,
  maxCfInputs,
  generatorOrder,
  calculatedShares,
  expandable,
  meetFullLoad,
  addedCapacities,
  expansionNote,
  storage,
  onChange,
  onMinCfChange,
  onMaxCfChange,
  onOrderChange,
  onExpandableToggle,
  onMeetFullLoadChange,
  onStorageChange,
}: {
  capacityInputs: Record<GeneratorKey, string>;
  /** Per-generator must-run floor CF (0–1), as text; blank = unconstrained. */
  minCfInputs: Record<GeneratorKey, string>;
  /** Per-generator availability-ceiling CF (0–1), as text; blank = unconstrained. */
  maxCfInputs: Record<GeneratorKey, string>;
  generatorOrder: GeneratorKey[];
  /** Model-calculated generation share per generator (0–1) from the last run. */
  calculatedShares?: Record<string, number>;
  /** Generators or storage tiers ("storage_short"/"storage_phs"/"storage_long") the solver may grow to meet 100% load. */
  expandable: Set<string>;
  meetFullLoad: boolean;
  /** GW the solver added per generator/storage tier on the last run. */
  addedCapacities?: Record<string, number>;
  expansionNote?: string;
  /** User-set storage rated power (GW) per tier, shown as rows below the generators. */
  storage: StorageInput;
  onChange: (key: GeneratorKey, value: string) => void;
  onMinCfChange: (key: GeneratorKey, value: string) => void;
  onMaxCfChange: (key: GeneratorKey, value: string) => void;
  onOrderChange: (order: GeneratorKey[]) => void;
  onExpandableToggle: (key: string) => void;
  onMeetFullLoadChange: (value: boolean) => void;
  onStorageChange: (value: StorageInput) => void;
}) {
  const [draggingKey, setDraggingKey] = useState<GeneratorKey | null>(null);
  const rowRefs = useRef(new Map<GeneratorKey, HTMLDivElement | null>());
  const generators = completeOrder(generatorOrder);

  // Pointer-based drag reordering: grab a row and move it up/down, reordering live
  // as the pointer crosses into a neighbouring row.
  useEffect(() => {
    if (!draggingKey) return;

    const handleMove = (event: PointerEvent) => {
      let targetKey: GeneratorKey | null = null;
      for (const [key, el] of rowRefs.current) {
        if (!el || key === draggingKey) continue;
        const rect = el.getBoundingClientRect();
        if (event.clientY >= rect.top && event.clientY <= rect.bottom) {
          targetKey = key;
          break;
        }
      }
      if (targetKey) onOrderChange(reorder(completeOrder(generatorOrder), draggingKey, targetKey));
    };
    const stop = () => setDraggingKey(null);

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [draggingKey, generatorOrder, onOrderChange]);

  const parsedCapacities = Object.fromEntries(
    Object.entries(capacityInputs).map(([key, value]) => {
      const parsed = Number(value);
      return [key, Number.isFinite(parsed) ? parsed : 0];
    }),
  ) as Capacities;
  const totalCapacity = Object.values(parsedCapacities).reduce((sum, value) => sum + Math.max(0, value), 0);
  const hasCalculated = calculatedShares !== undefined;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Merit Order</h3>
        <span className="text-[10px] text-slate-400">
          {hasCalculated ? "GW · gen share" : "GW · cap share"}
        </span>
      </div>

      <label className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
        <span>
          Meet 100% load
          <span className="ml-1 text-[10px] text-slate-400">grow the checked generators &amp; storage, cheapest-first</span>
        </span>
        <input
          type="checkbox"
          checked={meetFullLoad}
          onChange={(event) => onMeetFullLoadChange(event.target.checked)}
          className="h-4 w-4 rounded border-slate-300"
        />
      </label>

      {generators.map((key, index) => {
        const displayValue = Math.max(0, parsedCapacities[key]);
        const capacityShare = totalCapacity > 0 ? displayValue / totalCapacity : 0;
        const share = hasCalculated ? calculatedShares[key] ?? 0 : capacityShare;
        const added = addedCapacities?.[key] ?? 0;
        const label = GENERATOR_LABELS[key] ?? key;
        const color = GENERATOR_COLORS[key] ?? "#64748b";
        return (
          <div
            key={key}
            ref={(el) => {
              rowRefs.current.set(key, el);
            }}
            className={[
              "flex flex-col gap-1.5 rounded-lg border bg-white px-2 py-1.5 transition",
              draggingKey === key
                ? "border-slate-400 shadow-md ring-1 ring-slate-300"
                : "border-slate-200",
              draggingKey && draggingKey !== key ? "opacity-60" : "",
            ].join(" ")}
          >
            <div className="flex items-center gap-1.5">
            <div
              onPointerDown={(event) => {
                event.preventDefault();
                setDraggingKey(key);
              }}
              title="Drag to reorder merit position"
              aria-label={`Drag ${label} to reorder`}
              className={[
                "flex min-w-0 flex-1 touch-none select-none items-center gap-1.5",
                draggingKey === key ? "cursor-grabbing" : "cursor-grab",
              ].join(" ")}
            >
              <GripVertical size={13} className="shrink-0 text-slate-300" />
              <span className="w-3.5 shrink-0 text-[11px] tabular-nums text-slate-400">
                {generators.length - index}
              </span>
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
              <span className="truncate text-sm text-slate-800">{label}</span>
            </div>

            <div className="relative shrink-0">
              <input
                type="text"
                inputMode="decimal"
                value={capacityInputs[key]}
                onChange={(event) => onChange(key, event.target.value)}
                aria-label={`${label} capacity in GW`}
                className="w-16 rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
              />
              {added > 0 ? (
                <span
                  title="Capacity added to meet 100% load"
                  className="pointer-events-none absolute -top-1.5 -right-1 rounded bg-emerald-500 px-1 text-[9px] font-semibold leading-tight text-white shadow-sm"
                >
                  +{added.toFixed(0)}
                </span>
              ) : null}
            </div>

            <input
              type="checkbox"
              checked={expandable.has(key)}
              onChange={() => onExpandableToggle(key)}
              aria-label={`Make ${label} expandable`}
              title="Expandable — the solver may grow this generator to meet 100% load"
              className="h-3.5 w-3.5 shrink-0 rounded border-slate-300"
            />

            <span
              className="w-11 shrink-0 text-right text-xs font-medium tabular-nums text-slate-500"
              title={hasCalculated ? "Calculated generation share" : "Capacity share (run to see generation share)"}
            >
              {(share * 100).toFixed(1)}%
            </span>
            </div>

            <div className="flex items-center gap-1.5 pl-6 text-[10px] text-slate-400">
              <span className="uppercase tracking-[0.1em]">CF</span>
              <input
                type="text"
                inputMode="decimal"
                value={minCfInputs[key] ?? ""}
                onChange={(event) => onMinCfChange(key, event.target.value)}
                placeholder="min"
                aria-label={`${label} minimum capacity factor`}
                title="Must-run floor: this generator runs at least capacity × min CF every hour (0–1). Blank = off."
                className="w-14 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-right text-[11px] tabular-nums text-slate-700 outline-none transition placeholder:text-slate-300 focus:border-slate-400"
              />
              <span className="text-slate-300">–</span>
              <input
                type="text"
                inputMode="decimal"
                value={maxCfInputs[key] ?? ""}
                onChange={(event) => onMaxCfChange(key, event.target.value)}
                placeholder="max"
                aria-label={`${label} maximum capacity factor`}
                title="Availability ceiling: this generator never dispatches above capacity × max CF (0–1). Blank = off."
                className="w-14 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-right text-[11px] tabular-nums text-slate-700 outline-none transition placeholder:text-slate-300 focus:border-slate-400"
              />
              <span className="text-slate-300">min/max CF</span>
            </div>
          </div>
        );
      })}

      {/* Storage tiers — below the generators, treated like a generator: each has a rated-power (GW)
          input and its own expandable checkbox, but no merit position or CF (dispatched endogenously). */}
      <div className="pt-1">
        <div className="flex items-baseline justify-between px-1 pb-1">
          <h4 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">Storage</h4>
          <span className="text-[10px] text-slate-400">GW · endogenous · duration in Parameters</span>
        </div>
        {STORAGE_TIERS.map((tier) => {
          const added = addedCapacities?.[tier.addedKey] ?? 0;
          return (
            <div
              key={tier.expandKey}
              className="mb-2 flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1.5 last:mb-0"
            >
              <div className="flex min-w-0 flex-1 items-center gap-1.5">
                <span className="w-[33px] shrink-0" aria-hidden />
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: tier.color }} />
                <span className="truncate text-sm text-slate-800">{tier.label}</span>
              </div>

              <div className="relative shrink-0">
                <input
                  type="number"
                  min={0}
                  value={storage[tier.field]}
                  onChange={(event) =>
                    onStorageChange({ ...storage, [tier.field]: Math.max(0, Number(event.target.value)) })
                  }
                  aria-label={`${tier.label} rated power in GW`}
                  className="w-16 rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
                />
                {added > 0 ? (
                  <span
                    title="Storage power added to meet 100% load"
                    className="pointer-events-none absolute -top-1.5 -right-1 rounded bg-emerald-500 px-1 text-[9px] font-semibold leading-tight text-white shadow-sm"
                  >
                    +{added.toFixed(0)}
                  </span>
                ) : null}
              </div>

              <input
                type="checkbox"
                checked={expandable.has(tier.expandKey)}
                onChange={() => onExpandableToggle(tier.expandKey)}
                aria-label={`Make ${tier.label} expandable`}
                title="Expandable — the solver may build this storage tier to meet 100% load"
                className="h-3.5 w-3.5 shrink-0 rounded border-slate-300"
              />

              <span className="w-11 shrink-0" aria-hidden />
            </div>
          );
        })}
      </div>

      {expansionNote ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
          {expansionNote}
        </p>
      ) : null}

      <div className="flex items-center justify-between px-2 pt-1 text-[11px] text-slate-500">
        <span>Total capacity</span>
        <span className="font-semibold text-slate-600">{totalCapacity.toFixed(1)} GW</span>
      </div>
    </div>
  );
}
