import { useState } from "react";

const STORAGE_KEY = "fleetroll.filterHistory";
const MAX_ENTRIES = 10;

function readHistory(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === "string");
  } catch {
    return [];
  }
}

function writeHistory(entries: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // localStorage may be unavailable (private browsing, storage full, etc.)
  }
}

export function useFilterHistory() {
  const [recent, setRecent] = useState<string[]>(readHistory);

  function push(expr: string): void {
    if (!expr.trim()) return;
    const updated = recent.filter((e) => e !== expr);
    updated.unshift(expr);
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
