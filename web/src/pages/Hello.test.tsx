import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { Hello } from "./Hello";

vi.mock("../lib/api", () => ({
  api: {
    hello: vi.fn(),
  },
}));

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("Hello", () => {
  it("renders greeting from /api/hello", async () => {
    vi.mocked(api.hello).mockResolvedValue({
      message: "Hello, fleetroll",
      version: "0.2.3",
      db_ok: true,
    });

    render(<Hello />, { wrapper });

    const heading = await screen.findByText("Hello, fleetroll");
    expect(heading).toBeInTheDocument();
    expect(screen.getByText("DB ok")).toBeInTheDocument();
  });

  it("renders DB error badge when db_ok is false", async () => {
    vi.mocked(api.hello).mockResolvedValue({
      message: "Hello, fleetroll",
      version: "0.2.3",
      db_ok: false,
    });

    render(<Hello />, { wrapper });

    await screen.findByText("Hello, fleetroll");
    expect(screen.getByText("DB error")).toBeInTheDocument();
  });
});
