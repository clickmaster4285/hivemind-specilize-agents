import type { UsageTotals } from "@/lib/types";

function formatStructuredBlob(text: string): string | null {
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) {
      return data
        .map((item, index) => {
          if (item && typeof item === "object") {
            const row = item as Record<string, unknown>;
            const title = String(row.title || row.name || `Item ${index + 1}`);
            const action = row.action != null ? String(row.action) : "";
            return action ? `${index + 1}. **${title}** — ${action}` : `${index + 1}. **${title}**`;
          }
          return `${index + 1}. ${String(item)}`;
        })
        .join("\n");
    }
    if (data && typeof data === "object") {
      return Object.entries(data as Record<string, unknown>)
        .map(([key, value]) => `- **${key}**: ${typeof value === "string" ? value : JSON.stringify(value)}`)
        .join("\n");
    }
  } catch {
    // not JSON
  }
  return null;
}

/** 950 → "950", 1234 → "1.2k", 48210 → "48k" */
export function compactCount(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
}

export function formatSeconds(s: number): string {
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

/** "1.2k in · 850 out · 14 s" — tokens the model read, tokens it wrote, and its time. */
export function usageLine(u: UsageTotals): string {
  return `${compactCount(u.prompt_tokens)} in · ${compactCount(u.output_tokens)} out · ${formatSeconds(u.seconds)}`;
}

/** The usage of several answers added up (a whole chat); null when none has any. */
export function sumUsage(items: { usage?: UsageTotals }[]): UsageTotals | null {
  const used = items.map((i) => i.usage).filter((u): u is UsageTotals => Boolean(u));
  if (!used.length) return null;
  return used.reduce((a, u) => ({
    calls: a.calls + u.calls,
    prompt_tokens: a.prompt_tokens + u.prompt_tokens,
    output_tokens: a.output_tokens + u.output_tokens,
    seconds: a.seconds + u.seconds,
  }));
}

/** Full agent / assistant answers — never truncate with "…". */
export function formatAgentOutput(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "No output.";
  return formatStructuredBlob(text) ?? text;
}

/** Compact tool payloads — may truncate long dumps in the tool drawer. */
export function formatToolResult(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "No output.";
  if (text.startsWith("TOOL_ERROR:")) {
    return text.replace(/^TOOL_ERROR:\s*/, "Error: ");
  }

  const structured = formatStructuredBlob(text);
  if (structured) return structured;

  if (text.length > 4000) return `${text.slice(0, 4000)}…`;
  return text;
}

export function summarizeArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args as Record<string, unknown>);
  if (entries.length === 0) return "";
  return entries
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}: ${text.length > 60 ? `${text.slice(0, 60)}…` : text}`;
    })
    .join(" · ");
}
