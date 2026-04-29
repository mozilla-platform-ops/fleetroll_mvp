import type { components } from "./types.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type HelloResponse = components["schemas"]["HelloResponse"];
export type HostRow = components["schemas"]["HostRow"];
export type HostsResponse = components["schemas"]["HostsResponse"];
export type HostsSummary = components["schemas"]["HostsSummary"];

const BASE = "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string };
    throw Object.assign(new Error(body.detail ?? `${path} returned ${res.status}`), { status: res.status, detail: body.detail });
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthResponse>("/api/health"),
  hello: () => get<HelloResponse>("/api/hello"),
  hosts: ({ filter }: { filter?: string } = {}) => {
    const params = new URLSearchParams();
    if (filter) params.set("filter", filter);
    const qs = params.size > 0 ? `?${params}` : "";
    return get<HostsResponse>(`/api/hosts${qs}`);
  },
};
