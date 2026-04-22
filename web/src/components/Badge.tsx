import { cn } from "../lib/cn";

type BadgeVariant = "online" | "warn" | "crit" | "idle" | "unknown";

interface BadgeProps {
  variant: BadgeVariant;
  label: string;
}

const variantClasses: Record<BadgeVariant, string> = {
  online: "bg-status-online/20 text-status-online",
  warn: "bg-status-warn/20 text-status-warn",
  crit: "bg-status-crit/20 text-status-crit",
  idle: "bg-status-idle/20 text-status-idle",
  unknown: "bg-status-unknown/20 text-status-unknown",
};

export function Badge({ variant, label }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-caption font-medium",
        variantClasses[variant],
      )}
    >
      {label}
    </span>
  );
}
