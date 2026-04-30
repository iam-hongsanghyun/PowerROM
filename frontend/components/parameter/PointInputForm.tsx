"use client";

import { useState } from "react";

export function PointInputForm({
  onSubmit,
}: {
  onSubmit: (points: Array<[number, number]>) => void;
}) {
  const [raw, setRaw] = useState("0.1, 0.14\n0.2, 0.13\n0.4, 0.11");

  return (
    <div className="rounded-[2rem] border border-slate-200 bg-white p-5">
      <h3 className="text-base font-semibold text-slate-900">Manual Point Input</h3>
      <textarea
        value={raw}
        onChange={(event) => setRaw(event.target.value)}
        className="mt-4 min-h-32 w-full rounded-2xl border border-slate-200 p-3 text-sm outline-none"
      />
      <button
        type="button"
        onClick={() => {
          const points = raw
            .split("\n")
            .map((line) => line.split(",").map((item) => Number(item.trim())))
            .filter((pair) => pair.length >= 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]))
            .map((pair) => [pair[0], pair[1]] as [number, number]);
          onSubmit(points);
        }}
        className="mt-4 rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white"
      >
        Fit Points
      </button>
    </div>
  );
}
