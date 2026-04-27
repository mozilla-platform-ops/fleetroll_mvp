import type { components } from "./types.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type HelloResponse = components["schemas"]["HelloResponse"];
export type HostRow = components["schemas"]["HostRow"];
export type HostsResponse = components["schemas"]["HostsResponse"];

const BASE = "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} returned ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<HealthResponse>("/api/health"),
  hello: () => get<HelloResponse>("/api/hello"),
  hosts: () => get<HostsResponse>("/api/hosts"),
};
