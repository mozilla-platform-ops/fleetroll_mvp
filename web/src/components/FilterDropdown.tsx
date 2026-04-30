import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";

export type FilterItem = {
  label: string;
  query: string;
  description?: string;
};

type Props = {
  buttonLabel: string;
  items: FilterItem[];
  emptyMessage: string;
  onSelect: (query: string) => void;
  onClear?: () => void;
  activeQuery?: string;
};

export function FilterDropdown({ buttonLabel, items, emptyMessage, onSelect, onClear, activeQuery }: Props) {
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [alignRight, setAlignRight] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setHighlighted(0);
    if (dropdownRef.current) {
      const rect = dropdownRef.current.getBoundingClientRect();
      if (rect.right > window.innerWidth) setAlignRight(true);
    }
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      setAlignRight(false);
    };
  }, [open]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!open) return;
    if (e.key === "Escape") {
      setOpen(false);
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      setHighlighted((h) => Math.min(h + 1, items.length - 1));
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setHighlighted((h) => Math.max(h - 1, 0));
      e.preventDefault();
    } else if (e.key === "Enter" && items[highlighted]) {
      onSelect(items[highlighted].query);
      setOpen(false);
      e.preventDefault();
    }
  }

  return (
    <div ref={containerRef} className="relative" onKeyDown={handleKeyDown}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-neutral-300 px-3 py-1.5 text-caption text-status-idle hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
      >
        {buttonLabel}
      </button>
      {open && (
        <div ref={dropdownRef} className={cn("absolute top-full z-20 mt-1 w-72 overflow-hidden rounded border border-neutral-200 bg-white shadow-md dark:border-neutral-700 dark:bg-neutral-900", alignRight ? "right-0" : "left-0")}>
          {items.length === 0 ? (
            <p className="px-3 py-2 text-caption text-status-idle">{emptyMessage}</p>
          ) : (
            <ul role="listbox">
              {items.map((item, i) => {
                const isActive = activeQuery !== undefined && item.query === activeQuery;
                return (
                  <li
                    key={item.query}
                    role="option"
                    aria-selected={i === highlighted}
                    onMouseEnter={() => setHighlighted(i)}
                    onClick={() => {
                      onSelect(item.query);
                      setOpen(false);
                    }}
                    className={cn(
                      "cursor-pointer px-3 py-2 flex gap-2 items-start",
                      i === highlighted
                        ? "bg-neutral-100 dark:bg-neutral-800"
                        : "hover:bg-neutral-50 dark:hover:bg-neutral-800",
                    )}
                  >
                    <span className={cn("mt-0.5 shrink-0 text-caption", isActive ? "text-status-online" : "invisible")}>●</span>
                    <div className="min-w-0">
                      <div className="truncate font-medium text-caption">{item.label}</div>
                      <div className="truncate font-mono text-caption text-status-idle">{item.query}</div>
                      {item.description && (
                        <div className="text-caption text-status-idle">{item.description}</div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {onClear && items.length > 0 && (
            <div className="border-t border-neutral-100 dark:border-neutral-800">
              <button
                type="button"
                onClick={() => {
                  onClear();
                  setOpen(false);
                }}
                className="w-full px-3 py-1.5 text-left text-caption text-status-idle hover:text-status-crit"
              >
                Clear history
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
