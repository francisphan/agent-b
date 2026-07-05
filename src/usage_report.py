"""Aggregate the tool-usage JSONL log into a readable report.

Reads the per-tool-call records written by :mod:`src.tool_logging` (default
``.cache/tool_usage.jsonl``, or ``TOOL_USAGE_LOG``) and summarises usage so you
can spot patterns and regressions: which tools are busy, how slow they are
(p50/p95), and which ones error.

    python -m src.usage_report                      # default log, text report
    python -m src.usage_report path/to/usage.jsonl  # explicit file
    python -m src.usage_report --since 2026-05-26    # only records on/after
    python -m src.usage_report --top 10              # limit the tool table
    python -m src.usage_report --json                # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any


def default_log_path() -> str:
    path = os.getenv("TOOL_USAGE_LOG")
    if path:
        return path
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".cache",
        "tool_usage.jsonl",
    )


def load_records(path: str, since: str | None = None) -> list[dict]:
    """Load tool_call records from a JSONL file, optionally filtered by time.

    ``since`` is compared lexicographically against the ISO-8601 ``ts`` field,
    which works because ISO-8601 sorts chronologically.
    """
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event", "tool_call") != "tool_call":
                continue
            if since and (rec.get("ts") or "") < since:
                continue
            records.append(rec)
    return records


def _percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..1). None for empty input."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def aggregate(records: list[dict]) -> dict[str, Any]:
    """Reduce raw tool_call records to summary statistics."""
    total = len(records)
    errors = sum(1 for r in records if r.get("status") == "error")
    degraded = sum(1 for r in records if r.get("status") == "degraded")
    by_auth: Counter = Counter(r.get("auth", "unknown") for r in records)
    # In-band errors ({"error": ...} returns) carry no error_type — they aren't
    # raised exceptions — so label them "(in-band)" rather than "Unknown".
    error_types: Counter = Counter(
        (r.get("error_type") or "(in-band)") for r in records if r.get("status") == "error"
    )

    durations: dict[str, list[float]] = defaultdict(list)
    err_counts: Counter = Counter()
    call_counts: Counter = Counter()
    result_bytes: dict[str, list[int]] = defaultdict(list)
    for r in records:
        tool = r.get("tool", "?")
        call_counts[tool] += 1
        if isinstance(r.get("duration_ms"), (int, float)):
            durations[tool].append(float(r["duration_ms"]))
        if r.get("status") == "error":
            err_counts[tool] += 1
        if isinstance(r.get("result_bytes"), (int, float)):
            result_bytes[tool].append(int(r["result_bytes"]))

    tools = []
    for tool, calls in call_counts.most_common():
        d = durations.get(tool, [])
        rb = result_bytes.get(tool, [])
        tools.append(
            {
                "tool": tool,
                "calls": calls,
                "errors": err_counts.get(tool, 0),
                "error_rate": err_counts.get(tool, 0) / calls if calls else 0.0,
                "p50_ms": _percentile(d, 0.50),
                "p95_ms": _percentile(d, 0.95),
                "max_ms": max(d) if d else None,
                "avg_result_bytes": round(sum(rb) / len(rb)) if rb else None,
            }
        )

    timestamps = sorted(r["ts"] for r in records if r.get("ts"))
    return {
        "total": total,
        "ok": total - errors - degraded,
        "errors": errors,
        "degraded": degraded,
        "error_rate": errors / total if total else 0.0,
        "span": {
            "first": timestamps[0] if timestamps else None,
            "last": timestamps[-1] if timestamps else None,
        },
        "by_auth": dict(by_auth),
        "error_types": dict(error_types.most_common()),
        "tools": tools,
        "turns": _aggregate_turns(records),
    }


def _aggregate_turns(records: list[dict]) -> dict[str, Any]:
    """Group tool calls by correlation id (one id == one bot turn).

    Records without a ``corr`` (e.g. calls not originating from a tagged bot
    turn) are counted separately as ``untagged_calls`` rather than collapsed
    into a single bogus turn.
    """
    by_corr: dict[str, list[dict]] = defaultdict(list)
    untagged = 0
    for r in records:
        corr = r.get("corr")
        if corr:
            by_corr[corr].append(r)
        else:
            untagged += 1

    items = []
    for corr, recs in by_corr.items():
        tss = sorted(r["ts"] for r in recs if r.get("ts"))
        items.append(
            {
                "corr": corr,
                "calls": len(recs),
                "errors": sum(1 for r in recs if r.get("status") == "error"),
                "tools": sorted({r.get("tool", "?") for r in recs}),
                "total_ms": sum(
                    float(r["duration_ms"])
                    for r in recs
                    if isinstance(r.get("duration_ms"), (int, float))
                ),
                "first": tss[0] if tss else None,
                "last": tss[-1] if tss else None,
            }
        )
    # Busiest turns first, then most recent.
    items.sort(key=lambda t: (t["calls"], t["last"] or ""), reverse=True)

    calls_per_turn = [t["calls"] for t in items]
    return {
        "count": len(items),
        "untagged_calls": untagged,
        "with_errors": sum(1 for t in items if t["errors"]),
        "calls_per_turn": {
            "avg": round(sum(calls_per_turn) / len(calls_per_turn), 1) if calls_per_turn else None,
            "p50": _percentile([float(c) for c in calls_per_turn], 0.50),
            "p95": _percentile([float(c) for c in calls_per_turn], 0.95),
            "max": max(calls_per_turn) if calls_per_turn else None,
        },
        "items": items,
    }


def _ms(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "-"


def render_text(stats: dict[str, Any], top: int | None = None) -> str:
    if stats["total"] == 0:
        return "No tool-call records found."

    lines: list[str] = []
    span = stats["span"]
    lines.append(
        f"Tool usage — {stats['total']} calls "
        f"({stats['ok']} ok, {stats['errors']} errors, "
        f"{stats.get('degraded', 0)} degraded, "
        f"{stats['error_rate'] * 100:.1f}% error rate)"
    )
    lines.append(f"Window: {span['first']}  →  {span['last']}")
    auth = ", ".join(f"{k}={v}" for k, v in sorted(stats["by_auth"].items()))
    lines.append(f"By caller: {auth}")
    lines.append("")

    header = f"{'tool':<28} {'calls':>6} {'err':>5} {'err%':>6} {'p50ms':>7} {'p95ms':>7} {'maxms':>7} {'avgB':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    rows = stats["tools"][:top] if top else stats["tools"]
    for t in rows:
        lines.append(
            f"{t['tool']:<28} {t['calls']:>6} {t['errors']:>5} "
            f"{t['error_rate'] * 100:>5.0f}% {_ms(t['p50_ms']):>7} "
            f"{_ms(t['p95_ms']):>7} {_ms(t['max_ms']):>7} "
            f"{(t['avg_result_bytes'] if t['avg_result_bytes'] is not None else '-'):>8}"
        )

    if stats["error_types"]:
        lines.append("")
        lines.append("Errors by type:")
        for etype, count in stats["error_types"].items():
            lines.append(f"  {count:>5}  {etype}")

    turns = stats.get("turns")
    if turns and turns["count"]:
        cpt = turns["calls_per_turn"]
        lines.append("")
        lines.append(
            f"Turns — {turns['count']} tagged "
            f"({turns['with_errors']} with errors), "
            f"{cpt['avg']} calls/turn avg, p95 {_ms(cpt['p95'])}, max {cpt['max']}"
            + (f"; {turns['untagged_calls']} untagged calls" if turns["untagged_calls"] else "")
        )
        theader = f"{'corr':<14} {'calls':>6} {'err':>5} {'total_ms':>9}  tools"
        lines.append(theader)
        lines.append("-" * len(theader))
        rows = turns["items"][:top] if top else turns["items"]
        for t in rows:
            lines.append(
                f"{t['corr']:<14} {t['calls']:>6} {t['errors']:>5} "
                f"{_ms(t['total_ms']):>9}  {', '.join(t['tools'])}"
            )
    elif turns and turns["untagged_calls"]:
        lines.append("")
        lines.append(
            f"Turns — none tagged (all {turns['untagged_calls']} calls lack a "
            "correlation id; bot may predate correlation support)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=None, help="JSONL log path")
    parser.add_argument("--since", help="Only records with ts >= this (ISO-8601)")
    parser.add_argument("--top", type=int, default=None, help="Limit tool rows")
    parser.add_argument("--json", action="store_true", help="Emit JSON, not text")
    args = parser.parse_args(argv)

    path = args.path or default_log_path()
    try:
        records = load_records(path, since=args.since)
    except FileNotFoundError:
        print(f"No usage log at {path}", file=sys.stderr)
        return 1

    stats = aggregate(records)
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print(render_text(stats, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
