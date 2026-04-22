import type { components } from "./types.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type HelloResponse = components["schemas"]["HelloResponse"];

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
};
