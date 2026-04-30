import { useState } from "react";

const STORAGE_KEY = "fleetroll.filterHistory";
const MAX_ENTRIES = 10;

export type HistoryEntry = { query: string; ts: number };

function readHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((x): HistoryEntry[] => {
      if (typeof x === "string") return [{ query: x, ts: 0 }];
      if (x && typeof x === "object" && "query" in x && "ts" in x) return [x as HistoryEntry];
      return [];
    });
  } catch {
    return [];
  }
}

function writeHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // localStorage may be unavailable (private browsing, storage full, etc.)
  }
}

export function useFilterHistory() {
  const [recent, setRecent] = useState<HistoryEntry[]>(readHistory);

  function push(expr: string): void {
    if (!expr.trim()) return;
    const updated = recent.filter((e) => e.query !== expr);
    updated.unshift({ query: expr, ts: Date.now() });
    const capped = updated.slice(0, MAX_ENTRIES);
    writeHistory(capped);
    setRecent(capped);
  }

  function clear(): void {
    writeHistory([]);
    setRecent([]);
  }

  return { recent, push, clear };
}
