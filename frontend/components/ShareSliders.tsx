"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, GripVertical } from "lucide-react";

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

function move(order: GeneratorKey[], key: GeneratorKey, delta: -1 | 1): GeneratorKey[] {
  const from = order.indexOf(key);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) return order;
  const next = [...order];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item!);
  return next;
}

export function ShareSliders({
  capacityInputs,
  generatorOrder,
  onChange,
  onOrderChange,
}: {
  capacityInputs: Record<GeneratorKey, string>;
  generatorOrder: GeneratorKey[];
  onChange: (key: GeneratorKey, value: string) => void;
  onOrderChange: (order: GeneratorKey[]) => void;
}) {
  const [draggingKey, setDraggingKey] = useState<GeneratorKey | null>(null);
  const rowRefs = useRef(new Map<GeneratorKey, HTMLDivElement | null>());
  const generators = completeOrder(generatorOrder);

  // Pointer-based drag reordering: grab a row's handle and move it up/down,
  // reordering live as the pointer crosses into a neighbouring row. This replaces
  // native HTML5 drag-and-drop, which only fired from a tiny handle and felt janky.
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
  const maxCapacity = Math.max(...Object.values(parsedCapacities).map((value) => Math.max(0, value)), 1);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Merit Order</h3>
        <span className="text-xs text-slate-400">GW</span>
      </div>
      {generators.map((key, index) => {
        const value = parsedCapacities[key];
        const displayValue = Math.max(0, value);
        const capacityShare = totalCapacity > 0 ? displayValue / totalCapacity : 0;
        const visualPct = maxCapacity > 0 ? (displayValue / maxCapacity) * 100 : 0;
        const label = GENERATOR_LABELS[key] ?? key;
        const color = GENERATOR_COLORS[key] ?? "#64748b";
        return (
          <div
            key={key}
            ref={(el) => {
              rowRefs.current.set(key, el);
            }}
            className={[
              "space-y-2 rounded-2xl border bg-white p-3 transition",
              draggingKey === key
                ? "border-slate-400 shadow-lg ring-2 ring-slate-300"
                : "border-slate-200",
              draggingKey && draggingKey !== key ? "opacity-60" : "",
            ].join(" ")}
          >
            <div
              onPointerDown={(event) => {
                event.preventDefault();
                setDraggingKey(key);
              }}
              title="Drag to reorder"
              aria-label={`Drag ${label} to reorder`}
              className={[
                "flex touch-none select-none items-center justify-between text-sm font-medium text-slate-800",
                draggingKey === key ? "cursor-grabbing" : "cursor-grab",
              ].join(" ")}
            >
              <span className="flex min-w-0 items-center gap-2">
                <GripVertical size={16} className="shrink-0 text-slate-400" />
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-500">
                  {index + 1}
                </span>
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                />
                <span className="truncate">{label}</span>
              </span>
              <span className="shrink-0">{capacityShare > 0 ? `${(capacityShare * 100).toFixed(1)}% cap` : "0% cap"}</span>
            </div>
            <div className="flex gap-2">
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() => onOrderChange(move(generators, key, -1))}
                  disabled={index === 0}
                  title="Move up"
                  aria-label={`Move ${label} up`}
                  className="rounded-lg border border-slate-200 p-1 text-slate-500 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <ChevronUp size={13} />
                </button>
                <button
                  type="button"
                  onClick={() => onOrderChange(move(generators, key, 1))}
                  disabled={index === generators.length - 1}
                  title="Move down"
                  aria-label={`Move ${label} down`}
                  className="rounded-lg border border-slate-200 p-1 text-slate-500 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <ChevronDown size={13} />
                </button>
              </div>
              <input
                type="text"
                inputMode="decimal"
                value={capacityInputs[key]}
                onChange={(event) => onChange(key, event.target.value)}
                aria-label={`${label} capacity in GW`}
                className="min-w-0 flex-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
              />
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(100, visualPct)}%`,
                  backgroundColor: color,
                }}
              />
            </div>
          </div>
        );
      })}

      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        <div className="flex items-center justify-between">
          <span>Total Installed Capacity</span>
          <span className="font-semibold">{totalCapacity.toFixed(1)} GW</span>
        </div>
      </div>
    </div>
  );
}
