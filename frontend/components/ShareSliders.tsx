"use client";

import { useEffect, useRef, useState } from "react";
import { GripVertical } from "lucide-react";

import type { Capacities, GeneratorKey } from "@/lib/api";
import { ALL_GENERATOR_KEYS, GENERATOR_COLORS, GENERATOR_LABELS } from "@/lib/constants";

const DEFAULT_ORDER = [...ALL_GENERATOR_KEYS] as GeneratorKey[];

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
  generatorOrder,
  calculatedShares,
  onChange,
  onOrderChange,
}: {
  capacityInputs: Record<GeneratorKey, string>;
  generatorOrder: GeneratorKey[];
  /** Model-calculated generation share per generator (0–1) from the last run. */
  calculatedShares?: Record<string, number>;
  onChange: (key: GeneratorKey, value: string) => void;
  onOrderChange: (order: GeneratorKey[]) => void;
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

      {generators.map((key, index) => {
        const displayValue = Math.max(0, parsedCapacities[key]);
        const capacityShare = totalCapacity > 0 ? displayValue / totalCapacity : 0;
        const share = hasCalculated ? calculatedShares[key] ?? 0 : capacityShare;
        const label = GENERATOR_LABELS[key] ?? key;
        const color = GENERATOR_COLORS[key] ?? "#64748b";
        return (
          <div
            key={key}
            ref={(el) => {
              rowRefs.current.set(key, el);
            }}
            className={[
              "flex items-center gap-2 rounded-lg border bg-white px-2 py-1.5 transition",
              draggingKey === key
                ? "border-slate-400 shadow-md ring-1 ring-slate-300"
                : "border-slate-200",
              draggingKey && draggingKey !== key ? "opacity-60" : "",
            ].join(" ")}
          >
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
              <span className="w-3.5 shrink-0 text-[11px] tabular-nums text-slate-400">{index + 1}</span>
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
              <span className="truncate text-sm text-slate-800">{label}</span>
            </div>

            <input
              type="text"
              inputMode="decimal"
              value={capacityInputs[key]}
              onChange={(event) => onChange(key, event.target.value)}
              aria-label={`${label} capacity in GW`}
              className="w-16 shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 outline-none transition focus:border-slate-400"
            />

            <span
              className="w-11 shrink-0 text-right text-xs font-medium tabular-nums text-slate-500"
              title={hasCalculated ? "Calculated generation share" : "Capacity share (run to see generation share)"}
            >
              {(share * 100).toFixed(1)}%
            </span>
          </div>
        );
      })}

      <div className="flex items-center justify-between px-2 pt-1 text-[11px] text-slate-500">
        <span>Total capacity</span>
        <span className="font-semibold text-slate-600">{totalCapacity.toFixed(1)} GW</span>
      </div>
    </div>
  );
}
