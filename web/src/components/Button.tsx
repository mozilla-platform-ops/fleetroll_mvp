import { cn } from "../lib/cn";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary";
}

export function Button({ variant = "primary", className, children, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center rounded px-3 py-1.5 text-body font-medium transition-colors",
        variant === "primary"
          ? "bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50"
          : "bg-transparent text-brand-600 hover:bg-brand-50 disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
