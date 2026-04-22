import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          100: "#dce6ff",
          200: "#b8ccff",
          300: "#85a9ff",
          400: "#5080ff",
          500: "#2b5bff",
          600: "#1a3edb",
          700: "#1530b0",
          800: "#162990",
          900: "#172876",
        },
        status: {
          online: "#22c55e",
          warn: "#f59e0b",
          crit: "#ef4444",
          idle: "#94a3b8",
          unknown: "#6b7280",
        },
      },
      fontFamily: {
        sans: ['"DM Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"DM Mono"', "ui-monospace", "monospace"],
      },
      fontSize: {
        display: ["1.5rem", { lineHeight: "2rem", fontWeight: "600" }],
        heading: ["1rem", { lineHeight: "1.5rem", fontWeight: "600" }],
        body: ["0.875rem", { lineHeight: "1.25rem" }],
        caption: ["0.75rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
} satisfies Config;
