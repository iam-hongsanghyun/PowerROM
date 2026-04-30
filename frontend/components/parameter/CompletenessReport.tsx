"use client";

export function CompletenessReport({
  components,
}: {
  components: Record<string, Record<string, string | number | null>> | null;
}) {
  const iconByStatus: Record<string, string> = {
    fitted: "✅",
    default: "📚",
    missing: "❌",
    partial: "⚠️",
  };

  return (
    <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_-48px_rgba(15,23,42,0.45)]">
      <h3 className="text-base font-semibold text-slate-900">Completeness Report</h3>
      <div className="mt-4 space-y-3">
        {components ? (
          Object.entries(components).map(([name, metadata]) => {
            const status = String(metadata.status ?? "partial");
            return (
              <div
                key={name}
                className="flex items-center justify-between rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3 text-sm"
              >
                <span className="text-slate-700">
                  {iconByStatus[status] ?? "⚠️"} {name}
                </span>
                <span className="text-slate-500">
                  {status}
                  {metadata.r2 ? ` · R² ${Number(metadata.r2).toFixed(2)}` : ""}
                </span>
              </div>
            );
          })
        ) : (
          <p className="text-sm text-slate-500">No validation data yet.</p>
        )}
      </div>
    </div>
  );
}
