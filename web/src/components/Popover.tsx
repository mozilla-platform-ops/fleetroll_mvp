import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";

type Props = {
  trigger: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

export function Popover({ trigger, children, className }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [alignRight, setAlignRight] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (panelRef.current) {
      const rect = panelRef.current.getBoundingClientRect();
      if (rect.right > window.innerWidth) setAlignRight(true);
    }
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      setAlignRight(false);
    };
  }, [open]);

  function handleMouseEnter() {
    openTimerRef.current = setTimeout(() => setOpen(true), 150);
  }

  function handleMouseLeave() {
    if (openTimerRef.current) clearTimeout(openTimerRef.current);
    setOpen(false);
  }

  return (
    <div
      ref={containerRef}
      className="relative inline-block"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={() => setOpen((o) => !o)}
    >
      <span className="cursor-default underline decoration-dotted decoration-neutral-400 dark:decoration-neutral-600">
        {trigger}
      </span>
      {open && (
        <div
          ref={panelRef}
          className={cn(
            "absolute top-full z-30 mt-1 min-w-48 rounded border border-neutral-200 bg-white p-2 shadow-md dark:border-neutral-700 dark:bg-neutral-900",
            alignRight ? "right-0" : "left-0",
            className,
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

type RowProps = {
  label: string;
  value: string;
  mono?: boolean;
};

export function PopoverRow({ label, value, mono = false }: RowProps) {
  if (!value) return null;
  return (
    <div className="flex gap-2 py-0.5 text-caption">
      <span className="w-12 shrink-0 text-neutral-400 dark:text-neutral-500">{label}</span>
      <span className={cn("break-all", mono && "font-mono")}>{value}</span>
    </div>
  );
}
