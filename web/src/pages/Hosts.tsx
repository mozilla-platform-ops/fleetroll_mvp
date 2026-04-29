import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
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

function th(label: string, title: string) {
  return () => <abbr title={title}>{label}</abbr>;
}

const columns = [
  columnHelper.accessor("status", {
    header: th("STATUS", "Audit collection status: OK = last run succeeded, FAIL = last run errored, UNK = host known but never audited"),
    cell: (info) => {
      const v = info.getValue();
      return (
        <span className={cn("font-medium", statusColors[statusVariant(v)])}>
          {v}
        </span>
      );
    },
  }),
  columnHelper.accessor("host", {
    header: th("HOST", "Hostname (FQDN suffix stripped)"),
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("os", {
    header: th("OS", "Operating system: L=Linux, M=macOS, W=Windows"),
  }),
  columnHelper.accessor("role", {
    header: th("ROLE", "Puppet role assigned to this host"),
  }),
  columnHelper.accessor("vlt_sha", {
    header: th("VLT_SHA", "SHA of the vault.yaml file currently on disk"),
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("sha", {
    header: th("OVR_BCH", "Override branch currently checked out on this host"),
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("uptime", {
    header: th("UPTIME", "System uptime"),
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_last", {
    header: th("PP_LAST", "Time since last successful Puppet run"),
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_sha", {
    header: th("PP_SHA", "Git SHA that Puppet last applied on this host"),
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_exp", {
    header: th("PP_EXP", "Expected SHA for this host's role (from GitHub)"),
    cell: (info) => (
      <span className="font-mono text-caption">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("pp_match", {
    header: th("PP_MATCH", "Whether PP_SHA matches PP_EXP (Y = in sync, N = drift detected)"),
  }),
  columnHelper.accessor("tc_act", {
    header: th("TC_ACT", "Time since last TaskCluster worker activity"),
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("tc_j_sf", {
    header: th("TC_T_DUR", "Duration of the last TaskCluster task run on this host"),
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("tc_quar", {
    header: th("TC_QUAR", "TaskCluster quarantine status for this worker"),
  }),
  columnHelper.accessor("data", {
    header: th("DATA", "Data freshness: audit age / TC data age"),
    cell: (info) => (
      <span className="tabular-nums">{info.getValue()}</span>
    ),
  }),
  columnHelper.accessor("healthy", {
    header: th("HEALTHY", "Overall health assessment: Y=healthy, N=unhealthy, -=unknown"),
    cell: (info) => {
      const v = info.getValue();
      return (
        <span className={cn("font-medium", statusColors[healthVariant(v)])}>
          {v}
        </span>
      );
    },
  }),
  columnHelper.accessor("note", {
    header: th("NOTE", "Operator note for this host"),
  }),
];

export function Hosts() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [inputValue, setInputValue] = useState(searchParams.get("filter") ?? "");
  const [activeFilter, setActiveFilter] = useState(searchParams.get("filter") ?? "");
  const inputRef = useRef<HTMLInputElement>(null);

  const applyFilter = (value: string) => {
    setActiveFilter(value);
    setSearchParams(value ? { filter: value } : {}, { replace: true });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") applyFilter(inputValue);
    if (e.key === "Escape") {
      setInputValue("");
      applyFilter("");
    }
  };

  const handleClear = () => {
    setInputValue("");
    applyFilter("");
    inputRef.current?.focus();
  };

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["hosts", activeFilter],
    queryFn: () => api.hosts({ filter: activeFilter || undefined }),
  });

  const filterError =
    isError && (error as { status?: number; detail?: string })?.status === 400
      ? ((error as { detail?: string }).detail ?? "Invalid filter expression")
      : null;

  const table = useReactTable({
    data: data?.rows ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (isLoading && !data) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-body text-status-idle">Loading…</p>
      </div>
    );
  }

  if ((isError || !data) && !filterError) {
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
          {data?.rows.length ?? 0} hosts
        </span>
      </div>
      <div className="mb-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => applyFilter(inputValue)}
            placeholder="os=linux pp_last>1h sort:pp_last:desc"
            className="flex-1 rounded border border-neutral-300 bg-transparent px-3 py-1.5 font-mono text-caption placeholder:text-neutral-400 focus:border-neutral-500 focus:outline-none dark:border-neutral-700 dark:placeholder:text-neutral-600 dark:focus:border-neutral-400"
          />
          {inputValue && (
            <button
              onClick={handleClear}
              className="rounded border border-neutral-300 px-3 py-1.5 text-caption text-status-idle hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
            >
              Clear
            </button>
          )}
        </div>
        {filterError && (
          <p className="mt-1 text-caption text-status-crit">{filterError}</p>
        )}
      </div>
      <div className="overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800 dark:bg-neutral-900">
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
                className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {(data?.rows.length ?? 0) === 0 && !filterError && (
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
