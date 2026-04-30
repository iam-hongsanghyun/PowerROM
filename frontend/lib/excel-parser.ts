import * as XLSX from "xlsx";

export function parseFirstSheetPoints(buffer: ArrayBuffer): Array<[number, number]> {
  const workbook = XLSX.read(buffer, { type: "array" });
  const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<Array<string | number>>(firstSheet, { header: 1 });
  return rows
    .slice(1)
    .map((row) => [Number(row[0]), Number(row[1])] as [number, number])
    .filter((row) => Number.isFinite(row[0]) && Number.isFinite(row[1]));
}
