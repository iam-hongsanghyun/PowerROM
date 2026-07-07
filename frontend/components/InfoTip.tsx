"use client";

import { useState } from "react";
import { Info } from "lucide-react";

/**
 * Small "i" icon that reveals a one-line plain-language description on hover
 * or keyboard focus. Purely presentational — no external tooltip dependency.
 */
export function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label={text}
        className="flex h-3.5 w-3.5 items-center justify-center rounded-full text-slate-400 outline-none transition hover:text-slate-600 focus-visible:text-slate-600"
      >
        <Info size={13} />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-20 mb-2 w-56 -translate-x-1/2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-[11px] font-normal normal-case leading-relaxed text-slate-600 shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  );
}
