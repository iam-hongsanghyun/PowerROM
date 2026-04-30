"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";

type ChartSize = {
  width: number;
  height: number;
};

export function ChartViewport({ children }: { children: (size: ChartSize) => ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<ChartSize>({ width: 0, height: 0 });

  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      return;
    }

    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      const height = Math.floor(entry.contentRect.height);

      if (width <= 0 || height <= 0) {
        return;
      }

      setSize((previous) =>
        previous.width === width && previous.height === height ? previous : { width, height },
      );
    });

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="h-56 min-w-0">
      {size.width > 0 && size.height > 0 ? children(size) : null}
    </div>
  );
}
