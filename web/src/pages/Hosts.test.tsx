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

const fakeSummary = {
  version: "1.2.3",
  db_path: "/home/user/.fleetroll/hosts.db",
  total_hosts: 1,
  fqdn_suffix: null,
  log_size_warnings: [],
  data_is_stale: false,
};

const fakeRow = {
  status: "OK",
  host: "host1",
  uptime: "3d",
  override: "absent",
  role: "t-linux-talos",
  os: "L",
  sha: "-",
  vlt_sha: "-",
  ovr_branch: "",
  ovr_sha_full: "",
  vlt_sha_full: "",
  vlt_mnemonic: "",
  vlt_symlink: "",
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
  gwhc_worker: "running",
  gwhc_disk: "ALERT /:4GiB(9%)",
  gwhc_disk_alert: "alert",
  gwhc_age: "<1m",
  gwhc_ts: "2026-11-16T20:00:00Z",
  gwhc_source: "sudo -n gwhc --json",
  config: "unknown",
  verify_why: "vault:v1_null_is_ambiguous",
  verify_age: "5m",
  activity: "unknown",
  activity_reasons: "tc_observation_unlinked",
  puppet_result: "succeeded",
  revision_match: "match",
  override_match: "match",
  vault_match: "unknown",
  tc_lookup: "unknown",
  workload: "unknown",
  host_fresh: "fresh",
  reference_fresh: "fresh",
  tc_fresh: "unknown",
  verification_schema_version: 1,
  verification: { components: { vault: { state: "unknown", reasons: ["v1_null_is_ambiguous"], observation_ts: "2026-11-16T20:00:00Z", freshness: { state: "fresh", age_seconds: 300 } } } },
};

describe("Hosts", () => {
  it("renders a host row from /api/hosts", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [fakeRow],
      generated_at: "2026-04-27T00:00:00Z",
      summary: fakeSummary,
    });

    render(<Hosts />, { wrapper });

    expect(await screen.findByText("host1")).toBeInTheDocument();
    expect(screen.getByText("t-linux-talos")).toBeInTheDocument();
    expect(screen.getByText("hosts=1")).toBeInTheDocument();
    expect(screen.getByText("running")).toHaveAttribute("title", "sudo -n gwhc --json at 2026-11-16T20:00:00Z");
    expect(screen.getByText("ALERT /:4GiB(9%)")).toHaveClass("text-status-crit");
    fireEvent.click(screen.getByLabelText("Configuration verification"));
    expect(screen.getByText(/unknown; v1_null_is_ambiguous; fresh; 300s old/)).toBeInTheDocument();
  });

  it("renders empty state when no hosts", async () => {
    vi.mocked(api.hosts).mockResolvedValue({
      rows: [],
      generated_at: "2026-04-27T00:00:00Z",
      summary: { ...fakeSummary, total_hosts: 0 },
    });

    render(<Hosts />, { wrapper });

    expect(await screen.findByText("No hosts in database.")).toBeInTheDocument();
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
      summary: fakeSummary,
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
      summary: fakeSummary,
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
      summary: fakeSummary,
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
