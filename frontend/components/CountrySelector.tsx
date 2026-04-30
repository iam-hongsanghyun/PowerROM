"use client";

import type { CountrySummary } from "@/lib/api";

export function CountrySelector({
  countries,
  value,
  onChange,
}: {
  countries: CountrySummary[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-slate-800">Country</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none"
      >
        {countries.map((country) => (
          <option key={country.code} value={country.code}>
            {country.code} · {country.name}
          </option>
        ))}
      </select>
    </label>
  );
}
