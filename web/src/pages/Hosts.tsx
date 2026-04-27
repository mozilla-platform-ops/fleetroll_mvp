import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { type HostRow, api } from "../lib/api";
import { cn } from "../lib/cn";

const columnHelper = createColumnHelper<HostRow>();

type StatusVariant = "online" | "warn" | "crit" | "unknown";

function healthVariant(healthy: string): StatusVariant {
  if (healthy === "Y") return "online";
  if (healthy === "N") return "crit";
  return "unknown";
}

function statusVariant(status: string): StatusVariant {
  if (status === "OK") return "online";
  if (status === "FAIL") return "crit";
  if (status === "UNK") return "unknown";
  return "warn";
}

const statusColors: Record<StatusVariant, string> = {
  online: "text-status-online",
  warn: "text-status-warn",
  crit: "text-status-crit",
  unknown: "text-status-unknown",
};

const columns = [
  columnHelper.accessor("healthy", {
    header: "HEALTHY",
    cell: (info) => {
      const v = info.getValue();
      return (
        <span className={cn("font-medium", statusColors[healthVariant(v)])}>
          {v}
        </span>
      );
    },
  }),
  columnHelper.accessor("host", {
    header: "HOST",
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("role", { header: "ROLE" }),
  columnHelper.accessor("os", { header: "OS" }),
  columnHelper.accessor("status", {
    header: "STATUS",
    cell: (info) => {
      const v = info.getValue();
      return (
        <span className={cn("font-medium", statusColors[statusVariant(v)])}>
          {v}
        </span>
      );
    },
  }),
  columnHelper.accessor("sha", {
    header: "SHA",
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("vlt_sha", {
    header: "VLT_SHA",
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_last", {
    header: "PP_LAST",
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_sha", {
    header: "PP_SHA",
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_match", { header: "PP_MATCH" }),
  columnHelper.accessor("tc_act", {
    header: "TC_ACT",
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("uptime", {
    header: "UPTIME",
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("data", {
    header: "DATA",
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("note", { header: "NOTE" }),
];

export function Hosts() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["hosts"],
    queryFn: () => api.hosts(),
  });

  const table = useReactTable({
    data: data?.rows ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
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
        <p className="text-body text-status-crit">
          Failed to load hosts. Is the backend running?
        </p>
      </div>
    );
  }

  return (
    <main className="p-4">
      <div className="mb-3 flex items-baseline gap-3">
        <h1 className="text-display">Hosts</h1>
        <span className="text-caption text-status-idle tabular-nums">
          {data.rows.length} hosts
        </span>
      </div>
      <div className="overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
        <table className="w-full text-body">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr
                key={headerGroup.id}
                className="border-b border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900"
              >
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-3 py-2 text-left text-caption font-semibold uppercase tracking-wide text-status-idle"
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-900"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {data.rows.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-6 text-center text-caption text-status-idle"
                >
                  No hosts in database.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}
