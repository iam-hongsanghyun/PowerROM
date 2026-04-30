"use client";

import { useState } from "react";
import * as XLSX from "xlsx";

export function ExcelUploader({
  onParsed,
}: {
  onParsed: (rows: Array<[number, number]>) => void;
}) {
  const [label, setLabel] = useState("Drop or choose an Excel file");

  async function handleFile(file: File) {
    const buffer = await file.arrayBuffer();
    const workbook = XLSX.read(buffer, { type: "array" });
    const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json<Array<string | number>>(firstSheet, { header: 1 });
    const points = rows
      .slice(1)
      .map((row) => [Number(row[0]), Number(row[1])] as [number, number])
      .filter((row) => Number.isFinite(row[0]) && Number.isFinite(row[1]));
    setLabel(file.name);
    onParsed(points);
  }

  return (
    <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-[2rem] border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      <span className="mt-2 text-xs text-slate-500">Expected first two columns: x, y</span>
      <input
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            void handleFile(file);
          }
        }}
      />
    </label>
  );
}
