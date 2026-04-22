import { useQuery } from "@tanstack/react-query";
import { Badge } from "../components/Badge";
import { api } from "../lib/api";

export function Hello() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["hello"],
    queryFn: () => api.hello(),
  });

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-body text-status-idle">Loading…</p>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-body text-status-crit">Failed to load. Is the backend running?</p>
      </div>
    );
  }

  return (
    <main className="flex h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-display">{data.message}</h1>
      <p className="font-mono text-caption text-status-idle">v{data.version}</p>
      <Badge variant={data.db_ok ? "online" : "crit"} label={data.db_ok ? "DB ok" : "DB error"} />
    </main>
  );
}
