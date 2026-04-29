import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { Hosts } from "./Hosts";

vi.mock("../lib/api", () => ({
  api: {
    hosts: vi.fn(),
  },
}));

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

const fakeRow = {
  status: "OK",
  host: "host1",
  uptime: "3d",
  override: "absent",
  role: "t-linux-talos",
  os: "L",
  sha: "-",
  vlt_sha: "-",
  mtime: "-",
  err: "-",
  tc_quar: "-",
  tc_act: "5m",
  tc_j_sf: "-",
  pp_last: "30m",
  pp_sha: "abc1234",
  pp_exp: "abc1234",
  pp_match: "Y",
  healthy: "Y",
  data: "1h/2h",
  note: "-",
};

describe("Hosts", () => {
  it("renders a host row from /api/hosts", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [fakeRow],
      generated_at: "2026-04-27T00:00:00Z",
    });

    render(<Hosts />, { wrapper });

    expect(await screen.findByText("host1")).toBeInTheDocument();
    expect(screen.getByText("t-linux-talos")).toBeInTheDocument();
    expect(screen.getByText("1 hosts")).toBeInTheDocument();
  });

  it("renders empty state when no hosts", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [],
      generated_at: "2026-04-27T00:00:00Z",
    });

    render(<Hosts />, { wrapper });

    expect(await screen.findByText("No hosts in database.")).toBeInTheDocument();
    expect(screen.getByText("0 hosts")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", async () => {
    vi.mocked(api.hosts).mockRejectedValue(new Error("network error"));

    render(<Hosts />, { wrapper });

    expect(
      await screen.findByText(/Failed to load hosts/),
    ).toBeInTheDocument();
  });

  it("passes filter param to api.hosts when user types and presses Enter", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [fakeRow],
      generated_at: "2026-04-27T00:00:00Z",
    });

    render(<Hosts />, { wrapper });

    const input = await screen.findByPlaceholderText(/os=linux/);
    fireEvent.change(input, { target: { value: "os=L" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(vi.mocked(api.hosts)).toHaveBeenCalledWith({ filter: "os=L" });
    });
  });

  it("clears filter and calls api.hosts without filter when Clear is clicked", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [fakeRow],
      generated_at: "2026-04-27T00:00:00Z",
    });

    render(<Hosts />, { wrapper });

    const input = await screen.findByPlaceholderText(/os=linux/);
    fireEvent.change(input, { target: { value: "os=L" } });
    fireEvent.keyDown(input, { key: "Enter" });

    const clearBtn = await screen.findByRole("button", { name: /clear/i });
    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(vi.mocked(api.hosts)).toHaveBeenCalledWith({ filter: undefined });
    });
  });

  it("shows validation error message on 400 response", async () => {
    vi.mocked(api.hosts).mockResolvedValueOnce({
      rows: [fakeRow],
      generated_at: "2026-04-27T00:00:00Z",
    });

    render(<Hosts />, { wrapper });

    const input = await screen.findByPlaceholderText(/os=linux/);

    const err = Object.assign(new Error("Unknown column: bogus_col"), {
      status: 400,
      detail: "Unknown column: bogus_col",
    });
    vi.mocked(api.hosts).mockRejectedValueOnce(err);

    fireEvent.change(input, { target: { value: "bogus_col=x" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("Unknown column: bogus_col")).toBeInTheDocument();
  });
});
