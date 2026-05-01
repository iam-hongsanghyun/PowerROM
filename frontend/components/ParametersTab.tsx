"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Upload, RotateCcw, Save, ChevronDown, ChevronUp, X } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import {
  fetchProfile,
  saveProfile,
  profileExcelDownloadUrl,
  type CountryProfile,
  type GeneratorConfig,
  type FuncConfig,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FuncKey = "cf_eff_func" | "eta_func" | "integration_cost_func" | "curtailment_func";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GENERATOR_LABELS: Record<string, string> = {
  solar: "Solar",
  wind_onshore: "Wind (Onshore)",
  gas_ccgt: "Gas CCGT",
  coal: "Coal",
  nuclear: "Nuclear",
  other: "Other",
};

const GENERATOR_COLORS: Record<string, string> = {
  solar: "#f59e0b",
  wind_onshore: "#3b82f6",
  gas_ccgt: "#8b5cf6",
  coal: "#6b7280",
  nuclear: "#10b981",
  other: "#f97316",
};

const BASIC_FIELDS: Array<{ key: keyof GeneratorConfig; label: string; unit: string }> = [
  { key: "capex_usd_kw", label: "CAPEX", unit: "USD/kW" },
  { key: "opex_fixed_usd_kw_yr", label: "Fixed O&M", unit: "USD/kW/yr" },
  { key: "opex_var_usd_mwh", label: "Variable O&M", unit: "USD/MWh" },
  { key: "lifetime_yr", label: "Lifetime", unit: "yr" },
  { key: "emission_factor_tco2_mwh", label: "Emission Factor", unit: "tCO₂/MWh" },
  { key: "fuel_usd_mmbtu", label: "Fuel Cost", unit: "USD/MMBtu" },
  { key: "heat_rate_mmbtu_mwh", label: "Heat Rate", unit: "MMBtu/MWh" },
  { key: "cf_base", label: "Base CF", unit: "(0–1)" },
  { key: "variability_factor", label: "Variability Factor", unit: "(0–1)" },
];

const FUNC_FIELDS: Array<{
  key: FuncKey;
  label: string;
  xLabel: string;
  yLabel: string;
  vreOnly?: boolean;
}> = [
  {
    key: "cf_eff_func",
    label: "CF Efficiency",
    xLabel: "VRE Share",
    yLabel: "Effective CF",
  },
  {
    key: "eta_func",
    label: "Thermal Efficiency",
    xLabel: "CF_eff (own)",
    yLabel: "Efficiency η",
  },
  {
    key: "integration_cost_func",
    label: "Integration Cost",
    xLabel: "Portfolio Share",
    yLabel: "Cost ($/MWh)",
  },
  {
    key: "curtailment_func",
    label: "Curtailment",
    xLabel: "VRE Share",
    yLabel: "Curtailment Rate",
    vreOnly: true,
  },
];

const VRE_GENERATORS = new Set(["solar", "wind_onshore"]);

const FUNC_TYPES = [
  "constant",
  "linear",
  "logarithmic",
  "quadratic",
  "power",
  "exponential",
  "piecewise",
  "multilinear",
] as const;

const FUNC_FORMULA: Record<string, string> = {
  constant: "f(x) = a",
  linear: "f(x) = a + b·x",
  logarithmic: "f(x) = a − b·ln(1 + c·x)",
  quadratic: "f(x) = a + b·x + c·x²",
  exponential: "f(x) = a · e^(b·x)",
  power: "f(x) = a · x^b",
  piecewise: "f(x) = intercept + slope·x  (two segments at threshold)",
  multilinear: "f = intercept + β₁·x₁ + β₂·x₂ + …",
};

const X_VARIABLE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "vre_share", label: "VRE Share" },
  { value: "own_share", label: "Own Share" },
  { value: "cf_eff", label: "CF_eff (own)" },
  { value: "non_vre_share", label: "1−VRE Share" },
];

const FUNC_DEFAULT_X: Record<string, string> = {
  cf_eff_func: "vre_share",
  eta_func: "cf_eff",
  integration_cost_func: "own_share",
  curtailment_func: "vre_share",
};

/** Per-type slider definitions: key, human label, hint, default range. */
const PARAM_DEFS: Record<
  string,
  Array<{ key: string; label: string; hint: string; min: number; max: number; step: number }>
> = {
  constant: [
    { key: "a", label: "Value (a)", hint: "Constant output regardless of x", min: 0, max: 2, step: 0.005 },
  ],
  linear: [
    { key: "a", label: "Intercept (a)", hint: "Output at x = 0", min: -2, max: 5, step: 0.01 },
    { key: "b", label: "Slope (b)", hint: "Rate of change per unit x", min: -10, max: 10, step: 0.05 },
  ],
  logarithmic: [
    { key: "a", label: "Ceiling (a)", hint: "Output at x = 0  (e.g. nominal CF)", min: 0, max: 1.5, step: 0.005 },
    { key: "b", label: "Decay rate (b)", hint: "How steeply the value drops", min: 0, max: 0.5, step: 0.002 },
    { key: "c", label: "Curvature (c)", hint: "Sharpness of initial decline", min: 0, max: 30, step: 0.1 },
  ],
  quadratic: [
    { key: "a", label: "Intercept (a)", hint: "Output at x = 0", min: -2, max: 5, step: 0.01 },
    { key: "b", label: "Linear term (b)", hint: "Slope at x = 0", min: -5, max: 5, step: 0.05 },
    {
      key: "c",
      label: "Curvature (c)",
      hint: "Positive = upward curve, negative = downward",
      min: -5,
      max: 5,
      step: 0.05,
    },
  ],
  power: [
    { key: "a", label: "Scale (a)", hint: "Output at x = 1", min: 0, max: 5, step: 0.01 },
    {
      key: "b",
      label: "Exponent (b)",
      hint: "< 1: sub-linear  ·  = 1: linear  ·  > 1: super-linear",
      min: 0,
      max: 5,
      step: 0.05,
    },
  ],
  exponential: [
    { key: "a", label: "Scale (a)", hint: "Output at x = 0", min: 0, max: 5, step: 0.05 },
    {
      key: "b",
      label: "Rate (b)",
      hint: "Positive = growth  ·  Negative = decay",
      min: -5,
      max: 5,
      step: 0.05,
    },
  ],
  piecewise: [
    { key: "intercept", label: "Intercept", hint: "Value at x = 0", min: 0, max: 5, step: 0.05 },
    { key: "threshold", label: "Break point", hint: "x-value where slope changes", min: 0, max: 1, step: 0.01 },
    {
      key: "slope_before",
      label: "Slope (before)",
      hint: "Slope for x < break point",
      min: -20,
      max: 20,
      step: 0.1,
    },
    {
      key: "slope_after",
      label: "Slope (after)",
      hint: "Slope for x ≥ break point",
      min: -20,
      max: 20,
      step: 0.1,
    },
  ],
  multilinear: [], // handled by MultilinearEditor
};

/** Default param values to seed the editor when switching types */
const TYPE_DEFAULTS: Record<string, Record<string, number>> = {
  constant: { a: 1 },
  linear: { a: 1, b: 0 },
  logarithmic: { a: 0.22, b: 0.07, c: 2.8 },
  quadratic: { a: 1, b: 0, c: 0 },
  power: { a: 0.12, b: 1.5 },
  exponential: { a: 1, b: 0 },
  piecewise: { intercept: 1, threshold: 0.5, slope_before: 0, slope_after: 0 },
  multilinear: { intercept: 0 },
};

// ---------------------------------------------------------------------------
// Function evaluator — mirrors backend function_catalog.py
// For multilinear, chart preview uses x as a stand-in for all predictors.
// ---------------------------------------------------------------------------

function evalFunc(func: FuncConfig | undefined, x: number): number {
  if (!func) return 0;
  const p = func.params ?? {};

  if (func.type === "multilinear") {
    let result = p.intercept ?? 0;
    for (const [key, slope] of Object.entries(p)) {
      if (key === "intercept") continue;
      result += (slope as number) * x;
    }
    return result;
  }

  const clamp = (v: number) => {
    let r = v;
    if (func.x_min !== undefined && r < func.x_min) r = func.x_min;
    if (func.x_max !== undefined && r > func.x_max) r = func.x_max;
    return r;
  };
  switch (func.type) {
    case "constant":
      return clamp(p.a ?? 0);
    case "linear":
      return clamp((p.a ?? 0) + (p.b ?? 0) * x);
    case "logarithmic":
      return clamp((p.a ?? 0) - (p.b ?? 0) * Math.log1p((p.c ?? 0) * x));
    case "quadratic":
      return clamp((p.a ?? 0) + (p.b ?? 0) * x + (p.c ?? 0) * x * x);
    case "exponential":
      return clamp((p.a ?? 0) * Math.exp((p.b ?? 0) * x));
    case "power":
      return clamp((p.a ?? 0) * Math.pow(Math.max(x, 0), p.b ?? 1));
    case "piecewise": {
      const ic = p.intercept ?? 0,
        thr = p.threshold ?? 0.5;
      const sb = p.slope_before ?? 0,
        sa = p.slope_after ?? 0;
      return clamp(x <= thr ? ic + sb * x : ic + sb * thr + sa * (x - thr));
    }
    default:
      return 0;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function num(v: number | undefined | null, decimals = 3): string {
  if (v === undefined || v === null || isNaN(v as number)) return "";
  return String(+(v as number).toFixed(decimals));
}

function parseNum(s: string): number | undefined {
  const n = parseFloat(s);
  return isNaN(n) ? undefined : n;
}

function cloneDeep<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

// ---------------------------------------------------------------------------
// SliderParam — labelled slider + number input combo
// ---------------------------------------------------------------------------

function SliderParam({
  paramKey,
  label,
  hint,
  value,
  min,
  max,
  step,
  onChange,
}: {
  paramKey: string;
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-1.5">
          <code className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-mono text-slate-600">
            {paramKey}
          </code>
          <span className="truncate text-sm text-slate-700">{label}</span>
        </div>
        <input
          type="number"
          step={step}
          value={isNaN(value) ? "" : +value.toFixed(6)}
          onChange={(e) => {
            const n = parseFloat(e.target.value);
            if (!isNaN(n)) onChange(n);
          }}
          className="w-24 shrink-0 rounded-lg border border-slate-200 bg-white px-2 py-1 text-right text-sm font-mono
                     [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none
                     focus:outline-none focus:ring-1 focus:ring-sky-400"
        />
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={isNaN(value) ? min : Math.min(Math.max(value, min), max)}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full cursor-pointer accent-sky-500"
      />
      {hint && <p className="text-xs leading-relaxed text-slate-400">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MultilinearEditor — intercept + dynamic predictor rows
// ---------------------------------------------------------------------------

function MultilinearEditor({
  func,
  onChange,
}: {
  func: FuncConfig;
  onChange: (f: FuncConfig) => void;
}) {
  const params = func.params ?? {};
  const predictorVars = Object.keys(params).filter((k) => k !== "intercept");
  const usedSet = new Set(predictorVars);
  const available = X_VARIABLE_OPTIONS.filter((o) => !usedSet.has(o.value));

  function setParam(key: string, val: number) {
    onChange({ ...func, params: { ...params, [key]: val } });
  }

  function removePredictor(v: string) {
    const next = { ...params };
    delete next[v];
    onChange({ ...func, params: next });
  }

  function addPredictor(v: string) {
    if (!v) return;
    onChange({ ...func, params: { ...params, [v]: 0 } });
  }

  return (
    <div className="space-y-4">
      <SliderParam
        paramKey="intercept"
        label="Base value"
        hint="Output when all predictors = 0"
        value={params.intercept ?? 0}
        min={-2}
        max={5}
        step={0.01}
        onChange={(v) => setParam("intercept", v)}
      />

      {predictorVars.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Predictors</p>
          {predictorVars.map((v) => {
            const opt = X_VARIABLE_OPTIONS.find((o) => o.value === v);
            return (
              <div key={v} className="rounded-xl border border-slate-200 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-700">{opt?.label ?? v}</span>
                  <button
                    onClick={() => removePredictor(v)}
                    className="rounded p-1 text-slate-400 transition hover:bg-rose-50 hover:text-rose-500"
                  >
                    <X size={12} />
                  </button>
                </div>
                <SliderParam
                  paramKey="β"
                  label="Slope"
                  hint={`Contribution per unit of ${opt?.label ?? v}`}
                  value={params[v] ?? 0}
                  min={-5}
                  max={5}
                  step={0.01}
                  onChange={(val) => setParam(v, val)}
                />
              </div>
            );
          })}
        </div>
      )}

      {available.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) addPredictor(e.target.value);
          }}
          className="w-full cursor-pointer rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2 text-sm text-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-400"
        >
          <option value="">+ Add predictor variable…</option>
          {available.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FunctionChartSingle — preview inside the popup
// ---------------------------------------------------------------------------

function FunctionChartSingle({
  gen,
  func,
  xLabel,
  yLabel,
}: {
  gen: string;
  func: FuncConfig;
  xLabel: string;
  yLabel: string;
}) {
  const color = GENERATOR_COLORS[gen] ?? "#94a3b8";
  const data = Array.from({ length: 101 }, (_, i) => {
    const x = i / 100;
    return { x, y: evalFunc(func, x) };
  });

  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-3">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
        <span className="font-semibold uppercase tracking-wide">
          {yLabel} vs {xLabel}
        </span>
        {func.type !== "multilinear" && func.x_variable && (
          <span className="rounded bg-white px-1.5 py-0.5 text-[10px] text-slate-500 border border-slate-200">
            x = {X_VARIABLE_OPTIONS.find((o) => o.value === func.x_variable)?.label ?? func.x_variable}
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={190}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
          <XAxis
            dataKey="x"
            type="number"
            domain={[0, 1]}
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickCount={6}
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            width={40}
            tickFormatter={(v: number) => v.toFixed(2)}
          />
          <Tooltip
            formatter={(v) => [
              typeof v === "number" ? v.toFixed(4) : String(v),
              yLabel,
            ]}
            labelFormatter={(l) => `x = ${(Number(l) * 100).toFixed(1)}%`}
            contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
          />
          {func.type === "piecewise" && (
            <ReferenceLine
              x={(func.params?.threshold ?? 0.5)}
              stroke="#cbd5e1"
              strokeDasharray="4 3"
              label={{ value: "threshold", position: "top", fontSize: 9, fill: "#94a3b8" }}
            />
          )}
          <Line
            type="monotone"
            dataKey="y"
            stroke={color}
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FunctionChart — overview chart for all generators
// ---------------------------------------------------------------------------

function FunctionChart({
  generators,
  genConfigs,
  funcKey,
  xLabel,
  yLabel,
}: {
  generators: string[];
  genConfigs: Record<string, GeneratorConfig>;
  funcKey: FuncKey;
  xLabel: string;
  yLabel: string;
}) {
  const data = Array.from({ length: 51 }, (_, i) => {
    const x = i / 50;
    const point: Record<string, number> = { x };
    for (const gen of generators) {
      const func = genConfigs[gen]?.[funcKey] as FuncConfig | undefined;
      if (func) point[gen] = evalFunc(func, x);
    }
    return point;
  });

  const hasAny = generators.some((g) => genConfigs[g]?.[funcKey]);

  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          {yLabel} vs {xLabel}
        </span>
        <div className="flex flex-wrap gap-3">
          {generators
            .filter((g) => genConfigs[g]?.[funcKey])
            .map((g) => (
              <span key={g} className="flex items-center gap-1 text-xs text-slate-600">
                <span
                  className="inline-block h-2 w-4 rounded-full"
                  style={{ background: GENERATOR_COLORS[g] ?? "#94a3b8" }}
                />
                {GENERATOR_LABELS[g] ?? g}
              </span>
            ))}
        </div>
      </div>
      {!hasAny ? (
        <div className="flex h-[280px] items-center justify-center text-sm text-slate-400">
          No function configured for this feature
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <XAxis
              dataKey="x"
              type="number"
              domain={[0, 1]}
              tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              tick={{ fontSize: 10, fill: "#94a3b8" }}
              tickCount={6}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "#94a3b8" }}
              width={38}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              formatter={(v, name) => [
                typeof v === "number" ? v.toFixed(3) : String(v),
                GENERATOR_LABELS[String(name)] ?? String(name),
              ]}
              labelFormatter={(l) => `x = ${(Number(l) * 100).toFixed(0)}%`}
              contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
            />
            {generators.map((gen) => {
              const func = genConfigs[gen]?.[funcKey] as FuncConfig | undefined;
              if (!func) return null;
              return (
                <Line
                  key={gen}
                  type="monotone"
                  dataKey={gen}
                  stroke={GENERATOR_COLORS[gen] ?? "#94a3b8"}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FunctionEditorModal — popup for editing a single generator's function
// ---------------------------------------------------------------------------

function FunctionEditorModal({
  gen,
  funcKey,
  initial,
  onClose,
  onApply,
}: {
  gen: string;
  funcKey: FuncKey;
  initial: FuncConfig;
  onClose: () => void;
  onApply: (f: FuncConfig) => void;
}) {
  const [local, setLocal] = useState<FuncConfig>(() => cloneDeep(initial));
  const feat = FUNC_FIELDS.find((f) => f.key === funcKey)!;
  const color = GENERATOR_COLORS[gen] ?? "#94a3b8";
  const isMultilinear = local.type === "multilinear";

  function switchType(type: string) {
    setLocal({
      ...local,
      type,
      params: cloneDeep(TYPE_DEFAULTS[type] ?? {}),
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(15,23,42,0.5)" }}
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-lg overflow-y-auto rounded-2xl bg-white shadow-2xl"
        style={{ maxHeight: "92vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Colour-bar header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b border-slate-100"
          style={{
            borderTop: `4px solid ${color}`,
            borderRadius: "1rem 1rem 0 0",
          }}
        >
          <div className="flex min-w-0 items-center gap-2">
            <span
              className="inline-block h-3 w-3 shrink-0 rounded-full"
              style={{ background: color }}
            />
            <span className="truncate font-semibold text-slate-900">
              {GENERATOR_LABELS[gen] ?? gen}
            </span>
            <span className="mx-1 text-slate-300">·</span>
            <span className="truncate text-sm text-slate-500">{feat.label}</span>
          </div>
          <button
            onClick={onClose}
            className="ml-3 shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-5 px-6 py-5">
          {/* Type + input variable */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
                Function Type
              </label>
              <select
                value={local.type}
                onChange={(e) => switchType(e.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
              >
                {FUNC_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            {!isMultilinear && (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Input Variable (x)
                </label>
                <select
                  value={local.x_variable ?? FUNC_DEFAULT_X[funcKey] ?? "vre_share"}
                  onChange={(e) =>
                    setLocal({ ...local, x_variable: e.target.value || undefined })
                  }
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
                >
                  {X_VARIABLE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Formula pill */}
          {FUNC_FORMULA[local.type] && (
            <div className="rounded-lg bg-slate-50 px-4 py-2.5 font-mono text-sm text-slate-600">
              {FUNC_FORMULA[local.type]}
            </div>
          )}

          {/* Parameters */}
          <div>
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Parameters
            </p>
            {isMultilinear ? (
              <MultilinearEditor func={local} onChange={setLocal} />
            ) : (
              <div className="space-y-5">
                {(PARAM_DEFS[local.type] ?? []).map((pd) => (
                  <SliderParam
                    key={pd.key}
                    paramKey={pd.key}
                    label={pd.label}
                    hint={pd.hint}
                    value={local.params?.[pd.key] ?? 0}
                    min={pd.min}
                    max={pd.max}
                    step={pd.step}
                    onChange={(v) =>
                      setLocal({ ...local, params: { ...local.params, [pd.key]: v } })
                    }
                  />
                ))}
              </div>
            )}
          </div>

          {/* x_min / x_max / source */}
          <div className="grid grid-cols-3 gap-3">
            {(
              [
                { label: "x min", field: "x_min" as const, placeholder: "auto" },
                { label: "x max", field: "x_max" as const, placeholder: "auto" },
              ] as const
            ).map(({ label, field, placeholder }) => (
              <div key={field}>
                <label className="mb-1 block text-xs text-slate-400">{label}</label>
                <input
                  type="number"
                  step="any"
                  value={local[field] ?? ""}
                  onChange={(e) => {
                    const n = parseFloat(e.target.value);
                    setLocal({ ...local, [field]: isNaN(n) ? undefined : n });
                  }}
                  placeholder={placeholder}
                  className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm
                             [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none
                             focus:outline-none focus:ring-1 focus:ring-sky-400"
                />
              </div>
            ))}
            <div>
              <label className="mb-1 block text-xs text-slate-400">Source</label>
              <input
                type="text"
                value={local.source ?? ""}
                onChange={(e) => setLocal({ ...local, source: e.target.value || undefined })}
                placeholder="citation…"
                className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-sky-400"
              />
            </div>
          </div>

          {/* Live preview */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Live Preview
            </p>
            <FunctionChartSingle
              gen={gen}
              func={local}
              xLabel={feat.xLabel}
              yLabel={feat.yLabel}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-6 py-4">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            onClick={() => onApply(local)}
            className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-medium text-white transition hover:bg-slate-700"
          >
            Apply Changes
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Minor shared components
// ---------------------------------------------------------------------------

function Cell({
  value,
  onChange,
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
}) {
  return (
    <input
      type="number"
      step="any"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={[
        "w-full rounded border border-transparent bg-transparent px-2 py-1 text-right text-sm font-mono text-slate-800",
        "focus:border-sky-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-sky-300",
        "hover:bg-slate-50",
        "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
        className,
      ].join(" ")}
    />
  );
}

function SectionHeader({
  title,
  open,
  onToggle,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="flex w-full items-center justify-between rounded-xl bg-gradient-to-r from-slate-800 to-slate-700 px-4 py-2.5 text-left text-sm font-semibold text-white transition hover:from-slate-700 hover:to-slate-600"
    >
      {title}
      {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
    </button>
  );
}

function TableRow({
  label,
  unit,
  children,
}: {
  label: string;
  unit: string;
  children: React.ReactNode;
}) {
  return (
    <tr className="hover:bg-slate-50/60">
      <td className="w-48 py-1.5 pl-2 text-slate-600">{label}</td>
      <td className="w-20 py-1.5 text-xs text-slate-400">{unit}</td>
      <td className="w-36 py-1.5 pr-2">{children}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  country: string;
  /** Called whenever the user edits parameters (null = reset to server defaults). */
  onProfileEdited?: (draft: CountryProfile | null) => void;
}

export function ParametersTab({ country, onProfileEdited }: Props) {
  const [original, setOriginal] = useState<CountryProfile | null>(null);
  const [draft, setDraft] = useState<CountryProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    country: true,
    generators: true,
    functions: true,
    ess: false,
  });
  // Function editor state
  const [selectedFeature, setSelectedFeature] = useState<FuncKey>("cf_eff_func");
  const [editingGen, setEditingGen] = useState<string | null>(null);
  const [popupFunc, setPopupFunc] = useState<FuncConfig | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Update draft AND notify parent so edits propagate to calculations immediately.
  // Only call this from user-triggered actions, not from the initial server fetch.
  function updateDraft(next: CountryProfile) {
    setDraft(next);
    onProfileEdited?.(next);
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchProfile(country)
      .then((p) => {
        setOriginal(p);
        // Use setDraft (not updateDraft) — initial fetch should NOT override the
        // parent's customProfile with server defaults.
        setDraft(cloneDeep(p));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [country]);

  const isDirty = JSON.stringify(original) !== JSON.stringify(draft);

  function toggleSection(key: string) {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function setCountryField(field: "annual_generation_twh" | "discount_rate", val: string) {
    if (!draft) return;
    const n = parseNum(val);
    updateDraft({ ...draft, [field]: n ?? draft[field] });
  }

  function setGenField(gen: string, field: keyof GeneratorConfig, val: string) {
    if (!draft) return;
    const n = parseNum(val);
    const prev = draft.generators[gen] ?? {};
    updateDraft({ ...draft, generators: { ...draft.generators, [gen]: { ...prev, [field]: n } } });
  }

  function setEssField(field: string, val: string) {
    if (!draft) return;
    const n = parseNum(val);
    let next: CountryProfile;
    if (field.startsWith("short_dur.")) {
      const k = field.slice("short_dur.".length);
      const short = draft.ess?.short_dur ?? {};
      next = { ...draft, ess: { ...draft.ess, short_dur: { ...short, [k]: n } } };
    } else if (field.startsWith("long_dur.req_param_")) {
      const pk = field.slice("long_dur.req_param_".length);
      const long = draft.ess?.long_dur ?? {};
      const reqFunc = long.requirement_func ?? { type: "power", params: {} };
      next = {
        ...draft,
        ess: {
          ...draft.ess,
          long_dur: {
            ...long,
            requirement_func: { ...reqFunc, params: { ...reqFunc.params, [pk]: n ?? 0 } },
          },
        },
      };
    } else if (field === "long_dur.req_type") {
      const long = draft.ess?.long_dur ?? {};
      const reqFunc = long.requirement_func ?? { type: "power", params: {} };
      next = {
        ...draft,
        ess: { ...draft.ess, long_dur: { ...long, requirement_func: { ...reqFunc, type: val } } },
      };
    } else if (field.startsWith("long_dur.")) {
      const k = field.slice("long_dur.".length);
      const long = draft.ess?.long_dur ?? {};
      next = { ...draft, ess: { ...draft.ess, long_dur: { ...long, [k]: n } } };
    } else if (field.startsWith("req_param_")) {
      const pk = field.replace("req_param_", "");
      const reqFunc = draft.ess?.requirement_func ?? { type: "power", params: {} };
      next = {
        ...draft,
        ess: {
          ...draft.ess,
          requirement_func: { ...reqFunc, params: { ...reqFunc.params, [pk]: n ?? 0 } },
        },
      };
    } else if (field === "req_type") {
      const reqFunc = draft.ess?.requirement_func ?? { type: "power", params: {} };
      next = { ...draft, ess: { ...draft.ess, requirement_func: { ...reqFunc, type: val } } };
    } else {
      next = { ...draft, ess: { ...draft.ess, [field]: n } };
    }
    updateDraft(next);
  }

  // Open the editor popup for a generator
  function openEditor(gen: string) {
    if (!draft) return;
    const existing =
      (draft.generators[gen]?.[selectedFeature] as FuncConfig | undefined) ??
      ({ type: "constant", params: { a: 1 } } as FuncConfig);
    setPopupFunc(cloneDeep(existing));
    setEditingGen(gen);
  }

  // Commit the edited function back to draft
  function handleApplyEdit(newFunc: FuncConfig) {
    if (!editingGen || !draft) return;
    const genCfg = draft.generators[editingGen] ?? {};
    updateDraft({
      ...draft,
      generators: {
        ...draft.generators,
        [editingGen]: { ...genCfg, [selectedFeature]: newFunc },
      },
    });
    setEditingGen(null);
    setPopupFunc(null);
  }

  async function handleSave() {
    if (!draft) return;
    setSaving(true);
    try {
      const savedProfile = await saveProfile(country, draft);
      setOriginal(savedProfile);
      setDraft(cloneDeep(savedProfile));
      // Server now has the saved values — clear customProfile so calculations
      // use the server profile directly (no redundant custom_params).
      onProfileEdited?.(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function handleReset() {
    if (original) {
      setDraft(cloneDeep(original));
      // Reset: back to server defaults, clear parent's customProfile.
      onProfileEdited?.(null);
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api"}/profile/${country}/excel`,
        { method: "POST", body: formData },
      );
      if (!res.ok) throw new Error(await res.text());
      const newProfile = (await res.json()) as CountryProfile;
      setOriginal(newProfile);
      setDraft(cloneDeep(newProfile));
      // Uploaded profile is now on the server — clear customProfile.
      onProfileEdited?.(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-sm text-slate-400">
        Loading parameters…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
        {error}
      </div>
    );
  }
  if (!draft) return null;

  const generators = Object.keys(draft.generators);

  return (
    <div className="space-y-4">
      {/* ── Toolbar ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div>
          <h3 className="text-base font-semibold text-slate-900">{draft.name} — Parameters</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Edit inline and save, or download/upload the Excel workbook
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={profileExcelDownloadUrl(country)}
            download
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50"
          >
            <Download size={14} /> Download Excel
          </a>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50">
            <Upload size={14} /> Upload Excel
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={handleUpload}
            />
          </label>
          {isDirty && (
            <button
              onClick={handleReset}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            >
              <RotateCcw size={14} /> Reset
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={!isDirty || saving}
            className={[
              "flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition",
              saved
                ? "bg-emerald-500 text-white"
                : isDirty
                  ? "bg-slate-900 text-white hover:bg-slate-700"
                  : "cursor-not-allowed bg-slate-100 text-slate-400",
            ].join(" ")}
          >
            <Save size={14} />
            {saved ? "Saved!" : saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>

      {/* ── Country Settings ─────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="p-4">
          <SectionHeader
            title="Country Settings"
            open={openSections.country ?? true}
            onToggle={() => toggleSection("country")}
          />
        </div>
        {openSections.country && (
          <div className="px-4 pb-4">
            <table className="w-full text-sm">
              <tbody className="divide-y divide-slate-50">
                <TableRow label="Annual Generation" unit="TWh">
                  <Cell
                    value={num(draft.annual_generation_twh, 0)}
                    onChange={(v) => setCountryField("annual_generation_twh", v)}
                  />
                </TableRow>
                <TableRow label="Discount Rate" unit="(0–1)">
                  <Cell
                    value={num(draft.discount_rate, 3)}
                    onChange={(v) => setCountryField("discount_rate", v)}
                  />
                </TableRow>
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Generator Basic Parameters ───────────────────────────────────── */}
      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="p-4">
          <SectionHeader
            title="Generator Parameters"
            open={openSections.generators ?? true}
            onToggle={() => toggleSection("generators")}
          />
        </div>
        {openSections.generators && (
          <div className="px-4 pb-4">
            <table className="w-full min-w-[700px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="sticky left-0 z-10 bg-slate-50 px-3 py-2 text-left font-semibold text-slate-600">
                    Generator
                  </th>
                  {BASIC_FIELDS.map((f) => (
                    <th
                      key={f.key}
                      className="whitespace-nowrap px-2 py-2 text-right font-semibold text-slate-600"
                    >
                      <div>{f.label}</div>
                      <div className="text-xs font-normal text-slate-400">{f.unit}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {generators.map((gen) => {
                  const cfg = draft.generators[gen] ?? {};
                  const accent = GENERATOR_COLORS[gen] ?? "#94a3b8";
                  return (
                    <tr
                      key={gen}
                      className="hover:bg-slate-50/60"
                      style={{ borderLeft: `3px solid ${accent}` }}
                    >
                      <td className="sticky left-0 bg-white px-3 py-1.5 font-medium text-slate-700">
                        <span className="flex items-center gap-2">
                          <span
                            className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ background: accent }}
                          />
                          {GENERATOR_LABELS[gen] ?? gen}
                        </span>
                      </td>
                      {BASIC_FIELDS.map((f) => (
                        <td key={f.key} className="px-1 py-0.5">
                          <Cell
                            value={num(cfg[f.key] as number | undefined)}
                            onChange={(v) => setGenField(gen, f.key, v)}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-slate-400">
              Fuel Cost and Heat Rate only apply to thermal generators. Variability Factor (0 = fully
              dispatchable, 1 = fully intermittent) drives short-duration ESS sizing.
            </p>
          </div>
        )}
      </div>

      {/* ── Generator Functions ──────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="p-4">
          <SectionHeader
            title="Generator Functions"
            open={openSections.functions ?? true}
            onToggle={() => toggleSection("functions")}
          />
        </div>
        {(openSections.functions ?? true) && (
          <div className="space-y-4 px-4 pb-4">
            {/* Feature selector */}
            <div className="flex flex-wrap gap-1.5">
              {FUNC_FIELDS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setSelectedFeature(key)}
                  className={[
                    "rounded-lg px-3 py-1.5 text-sm font-medium transition",
                    selectedFeature === key
                      ? "bg-slate-900 text-white shadow-sm"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                  ].join(" ")}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Axis labels */}
            {(() => {
              const feat = FUNC_FIELDS.find((f) => f.key === selectedFeature)!;
              return (
                <p className="text-xs text-slate-400">
                  <span className="font-semibold text-slate-600">x</span> = {feat.xLabel}
                  {"  →  "}
                  <span className="font-semibold text-slate-600">y</span> = {feat.yLabel}
                  {feat.vreOnly && (
                    <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-amber-600 border border-amber-200 text-[10px]">
                      VRE only
                    </span>
                  )}
                </p>
              );
            })()}

            {/* Generator list + overview chart */}
            {(() => {
              const feat = FUNC_FIELDS.find((f) => f.key === selectedFeature)!;
              const chartGens = feat.vreOnly
                ? generators.filter((g) => VRE_GENERATORS.has(g))
                : generators;
              return (
                <div className="grid gap-4 lg:grid-cols-[200px_1fr]">
                  {/* Left: generator list */}
                  <div className="space-y-1.5">
                    <p className="px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                      Click to edit
                    </p>
                    {chartGens.map((gen) => {
                      const func = draft.generators[gen]?.[selectedFeature] as
                        | FuncConfig
                        | undefined;
                      const accent = GENERATOR_COLORS[gen] ?? "#94a3b8";
                      return (
                        <button
                          key={gen}
                          onClick={() => openEditor(gen)}
                          className="flex w-full items-center gap-2.5 rounded-xl border bg-white px-3 py-2.5 text-left transition hover:border-sky-300 hover:bg-sky-50 hover:shadow-sm"
                          style={{ borderLeftColor: accent, borderLeftWidth: 3 }}
                        >
                          <span
                            className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ background: accent }}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium text-slate-800">
                              {GENERATOR_LABELS[gen] ?? gen}
                            </div>
                            <div className="truncate text-xs text-slate-400">
                              {func
                                ? `${func.type}${func.x_variable ? `  ·  x = ${func.x_variable}` : ""}`
                                : "—"}
                            </div>
                          </div>
                          <span className="text-sm text-slate-300">›</span>
                        </button>
                      );
                    })}
                  </div>

                  {/* Right: overview chart */}
                  <FunctionChart
                    generators={chartGens}
                    genConfigs={draft.generators}
                    funcKey={selectedFeature}
                    xLabel={feat.xLabel}
                    yLabel={feat.yLabel}
                  />
                </div>
              );
            })()}
          </div>
        )}
      </div>

      {/* ── ESS ──────────────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="p-4">
          <SectionHeader
            title="Energy Storage System (ESS)"
            open={openSections.ess ?? false}
            onToggle={() => toggleSection("ess")}
          />
        </div>
        {openSections.ess && (
          <div className="space-y-4 px-4 pb-4">
            {draft.ess?.short_dur !== undefined ? (
              <>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Short-Duration (4 hr)
                  </p>
                  <table className="w-full max-w-md text-sm">
                    <tbody className="divide-y divide-slate-50">
                      {[
                        { field: "short_dur.capex_usd_kwh", label: "CAPEX", unit: "USD/kWh" },
                        { field: "short_dur.lifetime_yr", label: "Lifetime", unit: "yr" },
                        { field: "short_dur.cycles_per_year", label: "Cycles/year", unit: "" },
                        { field: "short_dur.dod", label: "Depth of Discharge", unit: "(0–1)" },
                        { field: "short_dur.duration_hr", label: "Duration", unit: "hr" },
                        { field: "short_dur.ev_offset_gwh_per_unit", label: "EV Offset", unit: "GWh/unit" },
                        { field: "short_dur.solar_absorption_fraction", label: "Solar Absorption", unit: "(0–1)" },
                        {
                          field: "short_dur.wind_onshore_absorption_fraction",
                          label: "Wind Absorption",
                          unit: "(0–1)",
                        },
                      ].map(({ field, label, unit }) => {
                        const k = field.slice("short_dur.".length) as keyof typeof draft.ess.short_dur;
                        return (
                          <TableRow key={field} label={label} unit={unit}>
                            <Cell
                              value={num(draft.ess?.short_dur?.[k] as number | undefined)}
                              onChange={(v) => setEssField(field, v)}
                            />
                          </TableRow>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Long-Duration (168 hr)
                  </p>
                  <table className="w-full max-w-md text-sm">
                    <tbody className="divide-y divide-slate-50">
                      {[
                        { field: "long_dur.capex_usd_kwh", label: "CAPEX", unit: "USD/kWh" },
                        { field: "long_dur.lifetime_yr", label: "Lifetime", unit: "yr" },
                        { field: "long_dur.cycles_per_year", label: "Cycles/year", unit: "" },
                        { field: "long_dur.dod", label: "Depth of Discharge", unit: "(0–1)" },
                        { field: "long_dur.duration_hr", label: "Duration", unit: "hr" },
                        { field: "long_dur.threshold", label: "VRE Threshold", unit: "(0–1)" },
                      ].map(({ field, label, unit }) => {
                        const k = field.slice("long_dur.".length) as keyof typeof draft.ess.long_dur;
                        return (
                          <TableRow key={field} label={label} unit={unit}>
                            <Cell
                              value={num(draft.ess?.long_dur?.[k] as number | undefined)}
                              onChange={(v) => setEssField(field, v)}
                            />
                          </TableRow>
                        );
                      })}
                      <TableRow label="Req. Func Type" unit="">
                        <select
                          value={draft.ess?.long_dur?.requirement_func?.type ?? "power"}
                          onChange={(e) => setEssField("long_dur.req_type", e.target.value)}
                          className="rounded border border-slate-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-sky-300"
                        >
                          {FUNC_TYPES.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </TableRow>
                      {Object.entries(draft.ess?.long_dur?.requirement_func?.params ?? {}).map(
                        ([pk, pv]) => (
                          <TableRow key={pk} label={`  param: ${pk}`} unit="">
                            <Cell
                              value={num(pv)}
                              onChange={(v) => setEssField(`long_dur.req_param_${pk}`, v)}
                            />
                          </TableRow>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              /* Legacy flat ESS */
              <table className="w-full max-w-md text-sm">
                <tbody className="divide-y divide-slate-50">
                  {[
                    { field: "capex_usd_kwh", label: "CAPEX", unit: "USD/kWh" },
                    { field: "lifetime_yr", label: "Lifetime", unit: "yr" },
                    { field: "cycles_per_year", label: "Cycles/year", unit: "" },
                    { field: "dod", label: "Depth of Discharge", unit: "(0–1)" },
                    { field: "ev_offset_gwh_per_unit", label: "EV Offset", unit: "GWh/unit" },
                  ].map(({ field, label, unit }) => (
                    <TableRow key={field} label={label} unit={unit}>
                      <Cell
                        value={num((draft.ess as Record<string, number | undefined>)[field])}
                        onChange={(v) => setEssField(field, v)}
                      />
                    </TableRow>
                  ))}
                  <TableRow label="Requirement Func Type" unit="">
                    <select
                      value={draft.ess?.requirement_func?.type ?? "power"}
                      onChange={(e) => setEssField("req_type", e.target.value)}
                      className="rounded border border-slate-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-sky-300"
                    >
                      {FUNC_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </TableRow>
                  {Object.entries(draft.ess?.requirement_func?.params ?? {}).map(([pk, pv]) => (
                    <TableRow key={pk} label={`  param: ${pk}`} unit="">
                      <Cell
                        value={num(pv)}
                        onChange={(v) => setEssField(`req_param_${pk}`, v)}
                      />
                    </TableRow>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {/* ── Editor popup ─────────────────────────────────────────────────── */}
      {editingGen && popupFunc && (
        <FunctionEditorModal
          gen={editingGen}
          funcKey={selectedFeature}
          initial={popupFunc}
          onClose={() => {
            setEditingGen(null);
            setPopupFunc(null);
          }}
          onApply={handleApplyEdit}
        />
      )}
    </div>
  );
}
