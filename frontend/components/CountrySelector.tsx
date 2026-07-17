"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CountrySummary } from "@/lib/api";

/**
 * Searchable country picker. With 160+ countries a flat <select> is unusable, so this renders a
 * text input that filters by name or ISO code and a dropdown list of matches.
 */
export function CountrySelector({
  countries,
  value,
  onChange,
}: {
  countries: CountrySummary[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);

  const selected = countries.find((c) => c.code === value);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...countries].sort((a, b) => a.name.localeCompare(b.name));
    if (!q) return sorted;
    return sorted.filter(
      (c) => c.name.toLowerCase().includes(q) || c.code.toLowerCase().startsWith(q),
    );
  }, [countries, query]);

  useEffect(() => {
    const onClickOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  useEffect(() => setHighlighted(0), [query]);

  const pick = (code: string) => {
    onChange(code);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-label="Country"
        value={open ? query : selected ? `${selected.code} · ${selected.name}` : ""}
        placeholder="Search country…"
        onFocus={() => {
          setQuery("");
          setOpen(true);
        }}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (!open) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setHighlighted((h) => Math.min(h + 1, matches.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setHighlighted((h) => Math.max(h - 1, 0));
          } else if (event.key === "Enter") {
            event.preventDefault();
            if (matches[highlighted]) pick(matches[highlighted].code);
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
      />
      {open ? (
        <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-2xl border border-slate-200 bg-white py-1 shadow-lg">
          {matches.length === 0 ? (
            <li className="px-4 py-2 text-sm text-slate-400">No matches</li>
          ) : (
            matches.map((country, index) => (
              <li key={country.code}>
                <button
                  type="button"
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setHighlighted(index)}
                  onClick={() => pick(country.code)}
                  className={`flex w-full items-baseline gap-2 px-4 py-2 text-left text-sm ${
                    index === highlighted ? "bg-slate-100" : ""
                  } ${country.code === value ? "font-semibold text-slate-900" : "text-slate-700"}`}
                >
                  <span className="w-7 shrink-0 text-[11px] tabular-nums text-slate-400">
                    {country.code}
                  </span>
                  <span className="truncate">{country.name}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
