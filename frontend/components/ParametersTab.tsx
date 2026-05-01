"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Upload, RotateCcw, Save, ChevronDown, ChevronUp } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
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

const BASIC_FIELDS: Array<{ key: keyof GeneratorConfig; label: string; unit: string; thermal?: boolean }> = [
  { key: "capex_usd_kw", label: "CAPEX", unit: "USD/kW" },
  { key: "opex_fixed_usd_kw_yr", label: "Fixed O&M", unit: "USD/kW/yr" },
  { key: "opex_var_usd_mwh", label: "Variable O&M", unit: "USD/MWh" },
  { key: "lifetime_yr", label: "Lifetime", unit: "yr" },
  { key: "emission_factor_tco2_mwh", label: "Emission Factor", unit: "tCO2/MWh" },
  { key: "fuel_usd_mmbtu", label: "Fuel Cost", unit: "USD/MMBtu", thermal: true },
  { key: "heat_rate_mmbtu_mwh", label: "Heat Rate", unit: "MMBtu/MWh", thermal: true },
  { key: "cf_base", label: "Base CF", unit: "(0–1)" },
  { key: "variability_factor", label: "Variability Factor", unit: "(0–1)" },
];

const FUNC_FIELDS: Array<{
  key: "cf_eff_func" | "eta_func" | "integration_cost_func" | "curtailment_func";
  label: string;
  xLabel: string;
  yLabel: string;
  vreOnly?: boolean;
}> = [
  { key: "cf_eff_func", label: "CF Efficiency Function", xLabel: "VRE Share", yLabel: "Effective CF" },
  { key: "eta_func", label: "Thermal Efficiency Function", xLabel: "CF_eff", yLabel: "Thermal Efficiency" },
  { key: "integration_cost_func", label: "Integration Cost Function", xLabel: "Portfolio Share", yLabel: "Integration Cost ($/MWh)" },
  { key: "curtailment_func", label: "Curtailment Function (VRE only)", xLabel: "VRE Share", yLabel: "Curtailment Rate", vreOnly: true },
];

const VRE_GENERATORS = ["solar", "wind_onshore"];

const FUNC_TYPES = ["constant", "linear", "logarithmic", "quadratic", "power", "piecewise"];

const FUNC_PARAMS_BY_TYPE: Record<string, string[]> = {
  constant: ["a"],
  linear: ["a", "b"],
  logarithmic: ["a", "b", "c"],
  quadratic: ["a", "b", "c"],
  power: ["a", "b"],
  piecewise: ["intercept", "threshold", "slope_before", "slope_after"],
};

const X_VARIABLE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "vre_share", label: "VRE Share" },
  { value: "own_share", label: "Own Share" },
  { value: "cf_eff", label: "CF_eff (own)" },
  { value: "non_vre_share", label: "1−VRE Share" },
];

/** Default x-input variable for each function type (matches engine defaults). */
const FUNC_DEFAULT_X: Record<string, string> = {
  cf_eff_func: "vre_share",
  eta_func: "cf_eff",
  integration_cost_func: "own_share",
  curtailment_func: "vre_share",
};

// ---------------------------------------------------------------------------
// Function evaluator & chart helpers
// ---------------------------------------------------------------------------

function evalFunc(func: FuncConfig | undefined, x: number): number {
  if (!func) return 0;
  const p = func.params ?? {};
  const clamp = (v: number) => {
    let result = v;
    if (func.x_min !== undefined && result < func.x_min) result = func.x_min;
    if (func.x_max !== undefined && result > func.x_max) result = func.x_max;
    return result;
  };
  switch (func.type) {
    case "constant": return clamp(p.a ?? 0);
    case "linear": return clamp((p.a ?? 0) + (p.b ?? 0) * x);
    case "logarithmic": return clamp((p.a ?? 0) - (p.b ?? 0) * Math.log1p((p.c ?? 0) * x));
    case "quadratic": return clamp((p.a ?? 0) + (p.b ?? 0) * x + (p.c ?? 0) * x * x);
    case "exponential": return clamp((p.a ?? 0) * Math.exp((p.b ?? 0) * x));
    case "power": return clamp((p.a ?? 0) * Math.pow(Math.max(x, 0), p.b ?? 1));
    case "piecewise": {
      const ic = p.intercept ?? 0, thr = p.threshold ?? 0.5;
      const sb = p.slope_before ?? 0, sa = p.slope_after ?? 0;
      const raw = x <= thr
        ? ic + sb * x
        : ic + sb * thr + sa * (x - thr);
      return clamp(raw);
    }
    default: return 0;
  }
}

const FUNC_FORMULA: Record<string, string> = {
  constant: "f(x) = a",
  linear: "f(x) = a + b·x",
  logarithmic: "f(x) = a − b·ln(1 + c·x)",
  quadratic: "f(x) = a + b·x + c·x²",
  exponential: "f(x) = a·e^(b·x)",
  power: "f(x) = a·x^b",
  piecewise: "f(x) = intercept + slope·x (piecewise at threshold)",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function num(v: number | undefined | null, decimals = 3): string {
  if (v === undefined || v === null || isNaN(v)) return "";
  return String(+v.toFixed(decimals));
}

function parseNum(s: string): number | undefined {
  const n = parseFloat(s);
  return isNaN(n) ? undefined : n;
}

function cloneProfile(p: CountryProfile): CountryProfile {
  return JSON.parse(JSON.stringify(p)) as CountryProfile;
}

// ---------------------------------------------------------------------------
// FunctionChart component
// ---------------------------------------------------------------------------

function FunctionChart({
  generators,
  genConfigs,
  funcKey,
  xLabel,
  yLabel,
  fullHeight = false,
}: {
  generators: string[];
  genConfigs: Record<string, GeneratorConfig>;
  funcKey: "cf_eff_func" | "eta_func" | "integration_cost_func" | "curtailment_func";
  xLabel: string;
  yLabel: string;
  fullHeight?: boolean;
}) {
  const xs = Array.from({ length: 51 }, (_, i) => i / 50);

  const data = xs.map((x) => {
    const point: Record<string, number> = { x };
    for (const gen of generators) {
      const func = genConfigs[gen]?.[funcKey] as FuncConfig | undefined;
      if (func) point[gen] = evalFunc(func, x);
    }
    return point;
  });

  const formulas = [...new Set(
    generators
      .map((g) => {
        const func = genConfigs[g]?.[funcKey] as FuncConfig | undefined;
        return func ? FUNC_FORMULA[func.type] : null;
      })
      .filter(Boolean)
  )];

  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
          {yLabel} vs {xLabel}
        </span>
        <div className="flex flex-wrap gap-2">
          {generators.filter((g) => genConfigs[g]?.[funcKey]).map((g) => (
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
      {formulas.length > 0 && (
        <div className="mb-2 font-mono text-[11px] text-slate-400">{formulas.join("  ·  ")}</div>
      )}
      <ResponsiveContainer width="100%" height={fullHeight ? 340 : 180}>
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
            width={36}
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
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

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  country: string;
}

export function ParametersTab({ country }: Props) {
  const [original, setOriginal] = useState<CountryProfile | null>(null);
  const [draft, setDraft] = useState<CountryProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    country: true,
    generators: true,
    cf_eff_func: false,
    eta_func: false,
    integration_cost_func: false,
    curtailment_func: false,
    ess: false,
  });
  const [funcViewMode, setFuncViewMode] = useState<Record<string, "table" | "chart">>({
    cf_eff_func: "table",
    eta_func: "table",
    integration_cost_func: "table",
    curtailment_func: "chart",
  });
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load profile whenever country changes
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchProfile(country)
      .then((p) => {
        setOriginal(p);
        setDraft(cloneProfile(p));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [country]);

  const isDirty = JSON.stringify(original) !== JSON.stringify(draft);

  function toggleSection(key: string) {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  // Country-level field edits
  function setCountryField(field: "annual_generation_twh" | "discount_rate", val: string) {
    if (!draft) return;
    const n = parseNum(val);
    setDraft({ ...draft, [field]: n ?? draft[field] });
  }

  // Generator basic field edits
  function setGenField(gen: string, field: keyof GeneratorConfig, val: string) {
    if (!draft) return;
    const n = parseNum(val);
    const prev = draft.generators[gen] ?? {};
    const updated: GeneratorConfig = { ...prev, [field]: n };
    setDraft({ ...draft, generators: { ...draft.generators, [gen]: updated } });
  }

  // Function field edits
  function setFuncField(
    gen: string,
    funcKey: "cf_eff_func" | "eta_func" | "integration_cost_func" | "curtailment_func",
    field: "type" | "x_min" | "x_max" | "source" | string,
    val: string,
  ) {
    if (!draft) return;
    const genCfg = draft.generators[gen] ?? {};
    const func: FuncConfig = (genCfg[funcKey] as FuncConfig | undefined) ?? { type: "constant", params: {} };

    let updated: FuncConfig;
    if (field === "type") {
      updated = { ...func, type: val, params: {} };
    } else if (field === "x_min") {
      updated = { ...func, x_min: parseNum(val) };
    } else if (field === "x_max") {
      updated = { ...func, x_max: parseNum(val) };
    } else if (field === "source") {
      updated = { ...func, source: val };
    } else if (field === "x_variable") {
      updated = { ...func, x_variable: val || undefined };
    } else {
      const n = parseNum(val);
      updated = { ...func, params: { ...func.params, [field]: n ?? 0 } };
    }

    setDraft({
      ...draft,
      generators: {
        ...draft.generators,
        [gen]: { ...genCfg, [funcKey]: updated },
      },
    });
  }

  // ESS edits
  function setEssField(field: string, val: string) {
    if (!draft) return;
    const n = parseNum(val);
    if (field.startsWith("short_dur.")) {
      const k = field.slice("short_dur.".length);
      const short = draft.ess?.short_dur ?? {};
      setDraft({ ...draft, ess: { ...draft.ess, short_dur: { ...short, [k]: n } } });
    } else if (field.startsWith("long_dur.req_param_")) {
      const pk = field.slice("long_dur.req_param_".length);
      const long = draft.ess?.long_dur ?? {};
      const reqFunc = long.requirement_func ?? { type: "power", params: {} };
      setDraft({
        ...draft,
        ess: {
          ...draft.ess,
          long_dur: { ...long, requirement_func: { ...reqFunc, params: { ...reqFunc.params, [pk]: n ?? 0 } } },
        },
      });
    } else if (field === "long_dur.req_type") {
      const long = draft.ess?.long_dur ?? {};
      const reqFunc = long.requirement_func ?? { type: "power", params: {} };
      setDraft({ ...draft, ess: { ...draft.ess, long_dur: { ...long, requirement_func: { ...reqFunc, type: val } } } });
    } else if (field.startsWith("long_dur.")) {
      const k = field.slice("long_dur.".length);
      const long = draft.ess?.long_dur ?? {};
      setDraft({ ...draft, ess: { ...draft.ess, long_dur: { ...long, [k]: n } } });
    } else if (field.startsWith("req_param_")) {
      const pk = field.replace("req_param_", "");
      const reqFunc = draft.ess?.requirement_func ?? { type: "power", params: {} };
      setDraft({
        ...draft,
        ess: {
          ...draft.ess,
          requirement_func: { ...reqFunc, params: { ...reqFunc.params, [pk]: n ?? 0 } },
        },
      });
    } else if (field === "req_type") {
      const reqFunc = draft.ess?.requirement_func ?? { type: "power", params: {} };
      setDraft({ ...draft, ess: { ...draft.ess, requirement_func: { ...reqFunc, type: val } } });
    } else {
      setDraft({ ...draft, ess: { ...draft.ess, [field]: n } });
    }
  }

  async function handleSave() {
    if (!draft) return;
    setSaving(true);
    try {
      const saved_profile = await saveProfile(country, draft);
      setOriginal(saved_profile);
      setDraft(cloneProfile(saved_profile));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function handleReset() {
    if (original) setDraft(cloneProfile(original));
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
      setDraft(cloneProfile(newProfile));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400 text-sm">
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
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div>
          <h3 className="text-base font-semibold text-slate-900">
            {draft.name} — Parameters
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Edit values inline and save, or download/upload the Excel workbook
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={profileExcelDownloadUrl(country)}
            download
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50"
          >
            <Download size={14} />
            Download Excel
          </a>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50">
            <Upload size={14} />
            Upload Excel
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
              <RotateCcw size={14} />
              Reset
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

      {/* Country Settings */}
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

      {/* Generator Basic Parameters */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-x-auto">
        <div className="p-4">
          <SectionHeader
            title="Generator Parameters"
            open={openSections.generators ?? true}
            onToggle={() => toggleSection("generators")}
          />
        </div>
        {openSections.generators && (
          <div className="px-4 pb-4">
            <table className="w-full min-w-[700px] text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="px-3 py-2 text-left font-semibold text-slate-600 sticky left-0 bg-slate-50 z-10">
                    Generator
                  </th>
                  {BASIC_FIELDS.map((f) => (
                    <th key={f.key} className="px-2 py-2 text-right font-semibold text-slate-600 whitespace-nowrap">
                      <div>{f.label}</div>
                      <div className="text-xs font-normal text-slate-400">{f.unit}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {generators.map((gen) => {
                  const cfg = draft.generators[gen] ?? {};
                  const accentColor = GENERATOR_COLORS[gen] ?? "#94a3b8";
                  return (
                    <tr
                      key={gen}
                      className="hover:bg-slate-50/60"
                      style={{ borderLeft: `3px solid ${accentColor}` }}
                    >
                      <td className="px-3 py-1.5 font-medium text-slate-700 sticky left-0 bg-white">
                        <span className="flex items-center gap-2">
                          <span
                            className="inline-block h-2.5 w-2.5 rounded-full flex-shrink-0"
                            style={{ background: accentColor }}
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
              Fuel Cost and Heat Rate only apply to thermal generators (Gas, Coal, Nuclear, Other).
            </p>
          </div>
        )}
      </div>

      {/* Function Sheets */}
      {FUNC_FIELDS.map(({ key: funcKey, label, xLabel, yLabel, vreOnly }) => {
        const activeGenerators = vreOnly
          ? generators.filter((g) => VRE_GENERATORS.includes(g))
          : generators;

        return (
          <div key={funcKey} className="rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="p-4">
              <SectionHeader
                title={label}
                open={openSections[funcKey] ?? false}
                onToggle={() => toggleSection(funcKey)}
              />
            </div>
            {openSections[funcKey] && (
              <div className="px-4 pb-4">
                {/* View toggle */}
                <div className="mb-3 flex items-center gap-1.5">
                  <span className="text-xs text-slate-400 mr-1">View:</span>
                  {(["table", "chart"] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setFuncViewMode((prev) => ({ ...prev, [funcKey]: mode }))}
                      className={[
                        "rounded-md px-3 py-1 text-xs font-medium transition",
                        funcViewMode[funcKey] === mode
                          ? "bg-slate-900 text-white"
                          : "bg-slate-100 text-slate-500 hover:bg-slate-200",
                      ].join(" ")}
                    >
                      {mode === "table" ? "📋 Table" : "📈 Chart"}
                    </button>
                  ))}
                </div>

                {funcViewMode[funcKey] === "chart" ? (
                  /* Chart-only view */
                  <FunctionChart
                    generators={activeGenerators}
                    genConfigs={draft.generators}
                    funcKey={funcKey}
                    xLabel={xLabel}
                    yLabel={yLabel}
                    fullHeight
                  />
                ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[700px] text-sm border-collapse">
                      <thead>
                        <tr className="border-b border-slate-200 bg-slate-50">
                          <th className="px-3 py-2 text-left font-semibold text-slate-600">Generator</th>
                          <th className="px-3 py-2 text-left font-semibold text-slate-600">Type</th>
                          <th className="px-2 py-2 text-left font-semibold text-slate-600 whitespace-nowrap">Input (x)</th>
                          <th className="px-2 py-2 text-right font-semibold text-slate-600" colSpan={7}>
                            Parameters (a, b, c / intercept, threshold, slope_before, slope_after)
                          </th>
                          <th className="px-2 py-2 text-right font-semibold text-slate-600">x_min</th>
                          <th className="px-2 py-2 text-right font-semibold text-slate-600">x_max</th>
                          <th className="px-3 py-2 text-left font-semibold text-slate-600">Source</th>
                        </tr>
                        <tr className="border-b border-slate-100 bg-slate-50/50 text-xs text-slate-400">
                          <th /><th /><th />
                          {["a", "b", "c", "intercept", "threshold", "slope_before", "slope_after"].map((p) => (
                            <th key={p} className="px-2 py-1 text-right font-normal">{p}</th>
                          ))}
                          <th /><th /><th />
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-50">
                        {activeGenerators.map((gen) => {
                          const func: FuncConfig = ((draft.generators[gen]?.[funcKey]) as FuncConfig | undefined) ??
                            { type: "constant", params: {} };
                          const activeParams = FUNC_PARAMS_BY_TYPE[func.type] ?? [];
                          const ALL_PARAMS = ["a", "b", "c", "intercept", "threshold", "slope_before", "slope_after"];
                          const accentColor = GENERATOR_COLORS[gen] ?? "#94a3b8";
                          return (
                            <tr
                              key={gen}
                              className="hover:bg-slate-50/60"
                              style={{ borderLeft: `3px solid ${accentColor}` }}
                            >
                              <td className="px-3 py-1 font-medium text-slate-700">
                                <span className="flex items-center gap-2">
                                  <span
                                    className="inline-block h-2.5 w-2.5 rounded-full flex-shrink-0"
                                    style={{ background: accentColor }}
                                  />
                                  {GENERATOR_LABELS[gen] ?? gen}
                                </span>
                              </td>
                              <td className="px-2 py-0.5">
                                <div className="flex flex-col gap-0.5">
                                  <select
                                    value={func.type}
                                    onChange={(e) => setFuncField(gen, funcKey, "type", e.target.value)}
                                    className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-1 focus:ring-sky-300"
                                  >
                                    {FUNC_TYPES.map((t) => (
                                      <option key={t} value={t}>{t}</option>
                                    ))}
                                  </select>
                                  {FUNC_FORMULA[func.type] && (
                                    <code className="text-[10px] text-slate-400 leading-tight px-1">
                                      {FUNC_FORMULA[func.type]}
                                    </code>
                                  )}
                                </div>
                              </td>
                              <td className="px-2 py-0.5">
                                <select
                                  value={func.x_variable ?? FUNC_DEFAULT_X[funcKey] ?? "vre_share"}
                                  onChange={(e) => setFuncField(gen, funcKey, "x_variable", e.target.value)}
                                  className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-1 focus:ring-sky-300 whitespace-nowrap"
                                >
                                  {X_VARIABLE_OPTIONS.map((opt) => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                  ))}
                                </select>
                              </td>
                              {ALL_PARAMS.map((pk) => (
                                <td key={pk} className="px-1 py-0.5">
                                  {activeParams.includes(pk) ? (
                                    <Cell
                                      value={num(func.params?.[pk])}
                                      onChange={(v) => setFuncField(gen, funcKey, pk, v)}
                                    />
                                  ) : (
                                    <span className="block w-full px-2 py-1 text-right text-xs text-slate-200">—</span>
                                  )}
                                </td>
                              ))}
                              <td className="px-1 py-0.5">
                                <Cell
                                  value={num(func.x_min)}
                                  onChange={(v) => setFuncField(gen, funcKey, "x_min", v)}
                                />
                              </td>
                              <td className="px-1 py-0.5">
                                <Cell
                                  value={num(func.x_max)}
                                  onChange={(v) => setFuncField(gen, funcKey, "x_max", v)}
                                />
                              </td>
                              <td className="px-2 py-0.5">
                                <input
                                  type="text"
                                  value={func.source ?? ""}
                                  onChange={(e) => setFuncField(gen, funcKey, "source", e.target.value)}
                                  className="w-full min-w-[120px] rounded border border-transparent bg-transparent px-2 py-1 text-xs text-slate-500 focus:border-sky-400 focus:bg-white focus:outline-none"
                                  placeholder="source…"
                                />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* ESS */}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="p-4">
          <SectionHeader
            title="Energy Storage System (ESS)"
            open={openSections.ess ?? false}
            onToggle={() => toggleSection("ess")}
          />
        </div>
        {openSections.ess && (
          <div className="px-4 pb-4 space-y-4">
            {draft.ess?.short_dur !== undefined ? (
              <>
                {/* Short-duration */}
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Short-Duration (4 hr)</p>
                  <table className="w-full text-sm max-w-md">
                    <tbody className="divide-y divide-slate-50">
                      {[
                        { field: "short_dur.capex_usd_kwh", label: "CAPEX", unit: "USD/kWh" },
                        { field: "short_dur.lifetime_yr", label: "Lifetime", unit: "yr" },
                        { field: "short_dur.cycles_per_year", label: "Cycles/year", unit: "" },
                        { field: "short_dur.dod", label: "Depth of Discharge", unit: "(0–1)" },
                        { field: "short_dur.duration_hr", label: "Duration", unit: "hr" },
                        { field: "short_dur.ev_offset_gwh_per_unit", label: "EV Offset", unit: "GWh/unit" },
                        { field: "short_dur.solar_absorption_fraction", label: "Solar Absorption", unit: "(0–1)" },
                        { field: "short_dur.wind_onshore_absorption_fraction", label: "Wind Absorption", unit: "(0–1)" },
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
                {/* Long-duration */}
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Long-Duration (168 hr)</p>
                  <table className="w-full text-sm max-w-md">
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
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      </TableRow>
                      {Object.entries(draft.ess?.long_dur?.requirement_func?.params ?? {}).map(([pk, pv]) => (
                        <TableRow key={pk} label={`  param: ${pk}`} unit="">
                          <Cell
                            value={num(pv)}
                            onChange={(v) => setEssField(`long_dur.req_param_${pk}`, v)}
                          />
                        </TableRow>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              /* Legacy flat ESS */
              <table className="w-full text-sm max-w-md">
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
                        <option key={t} value={t}>{t}</option>
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
    </div>
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
      <td className="py-1.5 pl-2 text-slate-600 w-48">{label}</td>
      <td className="py-1.5 text-xs text-slate-400 w-20">{unit}</td>
      <td className="py-1.5 pr-2 w-36">{children}</td>
    </tr>
  );
}
