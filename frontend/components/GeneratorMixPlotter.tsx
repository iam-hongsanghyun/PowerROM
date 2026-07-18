"use client";

import { useCallback, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { RefreshCw, ZoomIn } from "lucide-react";
import { type GeneratorKey, type Shares } from "@/lib/api";
import { ALL_GENERATOR_KEYS, GENERATOR_LABELS as SHARED_GENERATOR_LABELS } from "@/lib/constants";
import { generateGrid, refineBounds, type GridConfig, type ScatterPoint } from "@/lib/gridGeneration";
import { GeneratorDetailView } from "@/components/GeneratorDetailView";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

// Imported from @/lib/constants — single source of truth for generator list and labels.
const ALL_GENERATORS: GeneratorKey[] = [...ALL_GENERATOR_KEYS];
const GENERATOR_LABELS = SHARED_GENERATOR_LABELS;

const COLOR_METRIC_OPTIONS = [
  { value: "lcoe", label: "LCOE ($/MWh)" },
  { value: "emissions", label: "Emissions (tCO2/MWh)" },
  { value: "annualCost", label: "Annual Cost ($B)" },
  { value: "essGwh", label: "ESS Required (GWh)" },
] as const;

type ColorMetric = (typeof COLOR_METRIC_OPTIONS)[number]["value"];

interface Props {
  country: string;
  carbonPrice: number;
  essCostUsdKwh: number;
  evPenetration: number;
  annualDemandTwh: number;
  /** Current base shares from the left panel — non-selected generators are fixed at these values */
  shares: Shares;
}

export function GeneratorMixPlotter({
  country,
  carbonPrice,
  essCostUsdKwh,
  evPenetration,
  annualDemandTwh,
  shares,
}: Props) {
  // Which generators to explore (2 or 3)
  const [selected, setSelected] = useState<GeneratorKey[]>(["solar", "wind_onshore", "gas_ccgt"]);
  const [colorMetric, setColorMetric] = useState<ColorMetric>("lcoe");
  const [resolution, setResolution] = useState(7);
  const [points, setPoints] = useState<ScatterPoint[]>([]);
  const [selectedPoint, setSelectedPoint] = useState<ScatterPoint | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const is3D = selected.length === 3;

  // Fixed shares: non-selected generators keep left panel values; axis generators start at 0
  const fixedShares: Shares = {
    solar: selected.includes("solar") ? 0 : shares.solar,
    wind_onshore: selected.includes("wind_onshore") ? 0 : shares.wind_onshore,
    wind_offshore: selected.includes("wind_offshore") ? 0 : shares.wind_offshore,
    gas_ccgt: selected.includes("gas_ccgt") ? 0 : shares.gas_ccgt,
    coal: selected.includes("coal") ? 0 : shares.coal,
    nuclear: selected.includes("nuclear") ? 0 : shares.nuclear,
    hydro: selected.includes("hydro") ? 0 : shares.hydro,
    other: selected.includes("other") ? 0 : shares.other,
  };

  const fixedTotal = ALL_GENERATORS
    .filter((g) => !selected.includes(g))
    .reduce((s, g) => s + shares[g], 0);

  const maxCombined = Math.max(0, 1.0 - fixedTotal);

  function toggleGenerator(gen: GeneratorKey) {
    setSelected((prev) => {
      if (prev.includes(gen)) {
        if (prev.length <= 2) return prev; // min 2
        return prev.filter((g) => g !== gen);
      } else {
        if (prev.length >= 3) return prev; // max 3
        return [...prev, gen];
      }
    });
    setPoints([]);
    setSelectedPoint(null);
  }

  async function runGenerate(config: GridConfig) {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setLoading(true);
    setError(null);
    setProgress({ done: 0, total: 0 });
    try {
      const result = await generateGrid(config, (done, total) => {
        setProgress({ done, total });
      });
      setPoints((prev) => {
        if (config.bounds) {
          const { x: [xMin, xMax], y: [yMin, yMax] } = config.bounds;
          const zMin = config.bounds.z?.[0] ?? -Infinity;
          const zMax = config.bounds.z?.[1] ?? Infinity;
          const xGen = config.axisGenerators[0];
          const yGen = config.axisGenerators[1];
          const zGen = config.axisGenerators[2];
          const kept = prev.filter(
            (p) =>
              p.xGenerator !== xGen ||
              p.yGenerator !== yGen ||
              p.zGenerator !== (zGen ?? null) ||
              p.x < xMin || p.x > xMax ||
              p.y < yMin || p.y > yMax ||
              (p.z !== null && (p.z < zMin || p.z > zMax)),
          );
          return [...kept, ...result];
        }
        return result;
      });
      setSelectedIndices([]);
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }

  function handleGenerate() {
    setPoints([]);
    const axisGens = selected.slice(0, 3) as
      | [GeneratorKey, GeneratorKey]
      | [GeneratorKey, GeneratorKey, GeneratorKey];

    const customParams = { ess: { capex_usd_kwh: essCostUsdKwh } };
    void runGenerate({
      country,
      carbonPrice,
      evPenetration,
      annualDemandTwh,
      axisGenerators: axisGens,
      fixedShares,
      resolution,
      customParams,
    });
  }

  function handleRefine() {
    if (selectedIndices.length === 0) return;
    const sel = selectedIndices.map((i) => points[i]).filter(Boolean);
    if (sel.length === 0) return;
    const bounds = refineBounds(sel);
    const axisGens = selected.slice(0, 3) as
      | [GeneratorKey, GeneratorKey]
      | [GeneratorKey, GeneratorKey, GeneratorKey];

    const customParams = { ess: { capex_usd_kwh: essCostUsdKwh } };
    void runGenerate({
      country,
      carbonPrice,
      evPenetration,
      annualDemandTwh,
      axisGenerators: axisGens,
      fixedShares,
      resolution: Math.min(resolution + 4, 14),
      bounds,
      customParams,
    });
  }

  const metricValues = points.map((p) => p[colorMetric]);
  const metricLabel = COLOR_METRIC_OPTIONS.find((o) => o.value === colorMetric)?.label ?? colorMetric;

  // Build Plotly data based on 2D vs 3D mode
  const plotData: Plotly.Data[] = is3D
    ? [
        {
          type: "scatter3d",
          mode: "markers",
          x: points.map((p) => p.x),
          y: points.map((p) => p.y),
          z: points.map((p) => p.z ?? 0),
          marker: {
            size: 5,
            color: metricValues,
            colorscale: "Viridis",
            showscale: true,
            colorbar: { title: { text: metricLabel, side: "right" }, thickness: 14, len: 0.7 },
            opacity: 0.85,
          },
          text: points.map(
            (p) =>
              `${GENERATOR_LABELS[p.xGenerator]}: ${(p.x * 100).toFixed(1)}%<br>` +
              `${GENERATOR_LABELS[p.yGenerator]}: ${(p.y * 100).toFixed(1)}%<br>` +
              `${p.zGenerator ? GENERATOR_LABELS[p.zGenerator] : ""}: ${((p.z ?? 0) * 100).toFixed(1)}%<br>` +
              `LCOE: $${p.lcoe.toFixed(1)}/MWh<br>` +
              `Emissions: ${(p.emissions * 1000).toFixed(0)} gCO2/kWh`,
          ),
          hovertemplate: "%{text}<extra></extra>",
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          customdata: points as any,
        } as Plotly.Data,
      ]
    : [
        {
          type: "scatter",
          mode: "markers",
          x: points.map((p) => p.x),
          y: points.map((p) => p.y),
          marker: {
            size: 10,
            color: metricValues,
            colorscale: "Viridis",
            showscale: true,
            colorbar: { title: { text: metricLabel }, thickness: 14 },
            opacity: 0.85,
          },
          text: points.map(
            (p) =>
              `${GENERATOR_LABELS[p.xGenerator]}: ${(p.x * 100).toFixed(1)}%<br>` +
              `${GENERATOR_LABELS[p.yGenerator]}: ${(p.y * 100).toFixed(1)}%<br>` +
              `LCOE: $${p.lcoe.toFixed(1)}/MWh<br>` +
              `Emissions: ${(p.emissions * 1000).toFixed(0)} gCO2/kWh`,
          ),
          hovertemplate: "%{text}<extra></extra>",
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          customdata: points as any,
        } as Plotly.Data,
      ];

  const plotLayout: Partial<Plotly.Layout> = is3D
    ? {
        autosize: true,
        margin: { l: 0, r: 0, b: 0, t: 0 },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        scene: {
          xaxis: { title: { text: `${GENERATOR_LABELS[selected[0]!]} (%)` }, range: [0, maxCombined] },
          yaxis: { title: { text: `${GENERATOR_LABELS[selected[1]!]} (%)` }, range: [0, maxCombined] },
          zaxis: { title: { text: `${GENERATOR_LABELS[selected[2]!]} (%)` }, range: [0, maxCombined] },
        },
      }
    : {
        autosize: true,
        margin: { l: 50, r: 20, b: 50, t: 20 },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(248,250,252,1)",
        xaxis: {
          title: { text: `${GENERATOR_LABELS[selected[0]!]} share` },
          range: [0, maxCombined * 1.05],
          gridcolor: "#e2e8f0",
        },
        yaxis: {
          title: { text: `${GENERATOR_LABELS[selected[1]!]} share` },
          range: [0, maxCombined * 1.05],
          gridcolor: "#e2e8f0",
        },
      };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handlePlotClick = useCallback((event: any) => {
    const pt = event?.points?.[0];
    if (!pt) return;
    const idx = pt.pointIndex as number;
    const data = points[idx];
    if (data) setSelectedPoint(data);
  }, [points]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleSelected = useCallback((event: any) => {
    const indices = (event?.points ?? []).map((p: { pointIndex: number }) => p.pointIndex as number);
    setSelectedIndices(indices);
  }, []);

  return (
    <div className="flex h-full gap-4">
      {/* Left: controls + plot */}
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        {/* Controls */}
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-end gap-6">
            {/* Generator checkboxes */}
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium text-slate-500">
                Explore generators (pick 2–3)
              </span>
              <div className="flex flex-wrap gap-2">
                {ALL_GENERATORS.map((gen) => {
                  const isChecked = selected.includes(gen);
                  const share = shares[gen];
                  const disableUncheck = isChecked && selected.length <= 2;
                  const disableCheck = !isChecked && selected.length >= 3;
                  return (
                    <button
                      key={gen}
                      onClick={() => toggleGenerator(gen)}
                      disabled={disableUncheck || disableCheck}
                      title={
                        disableUncheck
                          ? "Need at least 2 generators"
                          : disableCheck
                            ? "Max 3 generators"
                            : isChecked
                              ? "Click to fix this generator"
                              : `Fixed at ${(share * 100).toFixed(0)}% (from left panel)`
                      }
                      className={[
                        "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition",
                        isChecked
                          ? "border-sky-300 bg-sky-50 font-medium text-sky-800"
                          : "border-slate-200 bg-slate-50 text-slate-500",
                        disableUncheck || disableCheck ? "cursor-not-allowed opacity-40" : "hover:border-sky-200 hover:bg-sky-50/50 cursor-pointer",
                      ].join(" ")}
                    >
                      <span
                        className={[
                          "h-2 w-2 rounded-full",
                          isChecked ? "bg-sky-500" : "bg-slate-300",
                        ].join(" ")}
                      />
                      {GENERATOR_LABELS[gen]}
                      {!isChecked && (
                        <span className="text-xs text-slate-400">
                          {(share * 100).toFixed(0)}%
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-slate-400">
                {is3D ? "3D view" : "2D view"} ·{" "}
                Non-selected generators fixed at left-panel values ·{" "}
                Max explorable combined: <strong className="text-slate-600">{(maxCombined * 100).toFixed(0)}%</strong>
              </p>
            </div>

            {/* Right-side controls */}
            <div className="flex flex-wrap items-end gap-4 ml-auto">
              {/* Color metric */}
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-slate-500">Color by</span>
                <select
                  value={colorMetric}
                  onChange={(e) => setColorMetric(e.target.value as ColorMetric)}
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-sky-300"
                >
                  {COLOR_METRIC_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              {/* Resolution */}
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-slate-500">
                  Grid ({resolution}×{resolution}{is3D ? `×${resolution}` : ""})
                </span>
                <input
                  type="range"
                  min={3}
                  max={10}
                  value={resolution}
                  onChange={(e) => setResolution(Number(e.target.value))}
                  className="w-24 accent-sky-500"
                />
              </label>

              {/* Action buttons */}
              <div className="flex gap-2">
                {selectedIndices.length > 0 && (
                  <button
                    onClick={handleRefine}
                    disabled={loading}
                    className="flex items-center gap-1.5 rounded-lg bg-sky-50 px-3 py-2 text-sm font-medium text-sky-700 transition hover:bg-sky-100 disabled:opacity-50"
                  >
                    <ZoomIn size={14} />
                    Refine ({selectedIndices.length} pts)
                  </button>
                )}
                <button
                  onClick={handleGenerate}
                  disabled={loading || maxCombined <= 0}
                  className="flex items-center gap-1.5 rounded-lg bg-navy px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:opacity-50"
                >
                  <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                  {loading ? "Computing…" : "Generate Grid"}
                </button>
              </div>
            </div>
          </div>

          {maxCombined <= 0 && (
            <p className="mt-3 text-xs text-rose-600">
              Fixed generators total 100% — reduce other generators in the left panel to create room.
            </p>
          )}
        </div>

        {error && (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        {loading && progress.total > 0 && (
          <div className="rounded-2xl border border-sky-100 bg-sky-50 px-4 py-3 text-sm text-sky-700">
            <div className="flex items-center justify-between mb-1">
              <span>Calculating grid…</span>
              <span>{progress.done} / {progress.total} mixes</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-sky-200 overflow-hidden">
              <div
                className="h-full rounded-full bg-sky-500 transition-all"
                style={{ width: `${(progress.done / progress.total) * 100}%` }}
              />
            </div>
          </div>
        )}

        {/* Plot area */}
        <div
          className="relative flex-1 rounded-[2rem] border border-slate-200 bg-white shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)] overflow-hidden"
          style={{ minHeight: 520 }}
        >
          {points.length === 0 && !loading ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400 gap-3">
              <div className="text-5xl">⬡</div>
              <p className="text-sm">
                Select generators above and click{" "}
                <span className="font-semibold text-slate-600">Generate Grid</span>
              </p>
              <p className="text-xs text-slate-300">
                Current base mix will be used to fix non-selected generators
              </p>
            </div>
          ) : (
            <Plot
              data={plotData}
              layout={plotLayout}
              config={{
                responsive: true,
                displayModeBar: true,
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                modeBarButtonsToAdd: is3D ? (["select3d", "lasso3d"] as any) : ["select2d", "lasso2d"],
                modeBarButtonsToRemove: ["toImage"],
                scrollZoom: true,
              }}
              style={{ width: "100%", height: "100%" }}
              onClick={handlePlotClick}
              onSelected={handleSelected}
              onDeselect={() => setSelectedIndices([])}
              useResizeHandler
            />
          )}
        </div>

        {points.length > 0 && (
          <p className="text-xs text-slate-400 text-center">
            Click a point to see details · Box-select then{" "}
            <strong className="text-slate-500">Refine</strong> for higher resolution in that region
          </p>
        )}
      </div>

      {/* Right: detail panel */}
      {selectedPoint && (
        <div className="w-80 shrink-0" style={{ minHeight: 520 }}>
          <GeneratorDetailView
            response={selectedPoint.response}
            onClose={() => setSelectedPoint(null)}
          />
        </div>
      )}
    </div>
  );
}
