"""Surface composite-tool candidates from real bot usage.

Joins the *human request* with the *tool chain* it produced, so you can see
which English asks repeatedly fan out into the same multi-tool sequence — those
recurring sequences are the strongest candidates to collapse into a single
atomic/composite MCP tool (the way ``guest_360_profile`` already does).

Two data sources, joined on the per-turn correlation id:

* **Sabueso turn metrics** (``SABUESO_METRICS_LOG``, default ``../Sabueso/.cache/
  metrics.jsonl``) — one record per bot turn with the ordered ``tool_calls``
  list and, when ``SABUESO_DEBUG_PAYLOADS`` is on, the raw ``request`` text.
* **agent-b tool usage** (``TOOL_USAGE_LOG``, default ``.cache/
  tool_usage.jsonl``) — one record per tool call with redacted ``args`` and
  timing. Optional; used to enrich each candidate chain with concrete args and
  total tool time so you can prioritise the expensive ones.

    python -m src.request_analyzer                      # default paths
    python -m src.request_analyzer --since 2026-05-01   # only recent turns
    python -m src.request_analyzer --min-chain 2        # chains of >=2 tools
    python -m src.request_analyzer --top 15             # limit tables
    python -m src.request_analyzer --json               # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

from src.usage_report import _percentile

# Turn events that carry a tool chain worth analysing. write_confirmed /
# write_cancelled are button follow-ups, not fresh reasoning turns.
_TURN_EVENTS = {"agent_turn"}


def default_metrics_path() -> str:
    path = os.getenv("SABUESO_METRICS_LOG")
    if path:
        return path
    agent_b_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workspace = os.path.dirname(agent_b_root)
    return os.path.join(workspace, "Sabueso", ".cache", "metrics.jsonl")


def default_usage_path() -> str:
    path = os.getenv("TOOL_USAGE_LOG")
    if path:
        return path
    agent_b_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(agent_b_root, ".cache", "tool_usage.jsonl")


def _iter_jsonl(path: str):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_turns(path: str, since: str | None = None) -> list[dict]:
    """Load per-turn metrics records (agent turns only), optionally since a date."""
    turns: list[dict] = []
    for rec in _iter_jsonl(path):
        if rec.get("event", "agent_turn") not in _TURN_EVENTS:
            continue
        if since and (rec.get("ts") or "") < since:
            continue
        turns.append(rec)
    return turns


def load_calls_by_corr(path: str, since: str | None = None) -> dict[str, list[dict]]:
    """Group agent-b tool-usage records by correlation id, in file order."""
    by_corr: dict[str, list[dict]] = defaultdict(list)
    for rec in _iter_jsonl(path):
        if rec.get("event", "tool_call") != "tool_call":
            continue
        if since and (rec.get("ts") or "") < since:
            continue
        corr = rec.get("corr")
        if corr:
            by_corr[corr].append(rec)
    return dict(by_corr)


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def analyze(
    turns: list[dict],
    calls_by_corr: dict[str, list[dict]] | None,
    *,
    min_chain: int,
    top: int,
) -> dict[str, Any]:
    calls_by_corr = calls_by_corr or {}
    chain_lengths = [len(t.get("tool_calls") or []) for t in turns]

    zero = sum(1 for n in chain_lengths if n == 0)
    single = sum(1 for n in chain_lengths if n == 1)
    multi = sum(1 for n in chain_lengths if n >= 2)

    # Group turns by their exact ordered tool sequence — the composite signal.
    by_chain: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for t in turns:
        chain = tuple(t.get("tool_calls") or [])
        if len(chain) >= min_chain:
            by_chain[chain].append(t)

    tool_freq: Counter = Counter()
    cooccur: Counter = Counter()
    for t in turns:
        tools = t.get("tool_calls") or []
        tool_freq.update(tools)
        for a, b in combinations(sorted(set(tools)), 2):
            cooccur[(a, b)] += 1

    chains = []
    total = len(turns) or 1
    for chain, recs in sorted(by_chain.items(), key=lambda kv: len(kv[1]), reverse=True):
        requests = [r["request"] for r in recs if r.get("request")]
        steps = [r["steps"] for r in recs if isinstance(r.get("steps"), int)]
        # Pick one example turn we can enrich with real (redacted) args.
        example_args: list[dict] = []
        example_total_ms: float | None = None
        for r in recs:
            corr = r.get("correlation_id")
            calls = calls_by_corr.get(corr or "")
            if calls:
                example_args = [{"tool": c.get("tool"), "args": c.get("args")} for c in calls]
                example_total_ms = sum(
                    float(c["duration_ms"])
                    for c in calls
                    if isinstance(c.get("duration_ms"), (int, float))
                )
                break
        chains.append(
            {
                "chain": list(chain),
                "length": len(chain),
                "count": len(recs),
                "share": len(recs) / total,
                "avg_steps": round(sum(steps) / len(steps), 1) if steps else None,
                "sample_requests": [_clip(q) for q in requests[:3]],
                "example_total_tool_ms": example_total_ms,
                "example_args": example_args,
            }
        )

    lengths_f = [float(n) for n in chain_lengths]
    return {
        "metrics": {
            "turns": len(turns),
            "with_tools": single + multi,
            "zero_tool": zero,
            "single_tool": single,
            "multi_tool": multi,
            "multi_tool_share": multi / len(turns) if turns else 0.0,
            "chain_len": {
                "avg": round(sum(chain_lengths) / len(chain_lengths), 1)
                if chain_lengths
                else None,
                "p50": _percentile(lengths_f, 0.50),
                "p95": _percentile(lengths_f, 0.95),
                "max": max(chain_lengths) if chain_lengths else None,
            },
            "requests_captured": sum(1 for t in turns if t.get("request")),
        },
        "enrichment": bool(calls_by_corr),
        "candidate_chains": chains[:top],
        "candidate_chains_total": len(chains),
        "tool_frequency": tool_freq.most_common(top),
        "co_occurrence": [
            {"pair": list(pair), "count": n} for pair, n in cooccur.most_common(top)
        ],
    }


def _ms(value: float | None) -> str:
    return f"{value:.0f}ms" if value is not None else "-"


def render_text(stats: dict[str, Any]) -> str:
    m = stats["metrics"]
    if m["turns"] == 0:
        return (
            "No agent-turn records found. Has the bot run since "
            "SABUESO_DEBUG_PAYLOADS was enabled? Check the metrics path."
        )

    cl = m["chain_len"]
    lines: list[str] = []
    lines.append(
        f"Requests — {m['turns']} turns "
        f"({m['with_tools']} used tools, {m['zero_tool']} none); "
        f"{m['multi_tool']} multi-tool ({m['multi_tool_share'] * 100:.0f}%)"
    )
    lines.append(
        f"Chain length: avg {cl['avg']}, p50 {cl['p50']}, p95 {cl['p95']}, max {cl['max']}"
    )
    if not m["requests_captured"]:
        lines.append(
            "  (request text not captured — set SABUESO_DEBUG_PAYLOADS=true to "
            "label chains with the asks that produced them)"
        )
    lines.append(
        "  tool-usage enrichment: "
        + ("on (args + timing shown)" if stats["enrichment"] else "off")
    )
    lines.append("")

    chains = stats["candidate_chains"]
    lines.append(
        f"Composite-tool candidates — {stats['candidate_chains_total']} distinct "
        "chains (most frequent first):"
    )
    if not chains:
        lines.append("  none at this --min-chain threshold")
    for i, c in enumerate(chains, 1):
        share = c["share"] * 100
        timing = (
            f", ~{_ms(c['example_total_tool_ms'])} tool time"
            if c["example_total_tool_ms"] is not None
            else ""
        )
        lines.append(f"\n{i}. [{c['count']}x, {share:.0f}%] {' → '.join(c['chain'])}{timing}")
        for q in c["sample_requests"]:
            lines.append(f'     "{q}"')
        for ex in c["example_args"]:
            args = json.dumps(ex["args"], default=str) if ex["args"] else "{}"
            lines.append(f"     · {ex['tool']}({_clip(args, 120)})")

    lines.append("")
    lines.append("Tool frequency (calls across turns):")
    for tool, n in stats["tool_frequency"]:
        lines.append(f"  {n:>4}  {tool}")

    if stats["co_occurrence"]:
        lines.append("")
        lines.append("Tools co-occurring in a turn (order-independent):")
        for item in stats["co_occurrence"]:
            a, b = item["pair"]
            lines.append(f"  {item['count']:>4}  {a} + {b}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", default=None, help="Sabueso metrics JSONL path")
    parser.add_argument(
        "--tool-usage",
        default=None,
        help="agent-b tool-usage JSONL for arg/timing enrichment (optional)",
    )
    parser.add_argument("--since", help="Only turns with ts >= this (ISO-8601)")
    parser.add_argument(
        "--min-chain",
        type=int,
        default=2,
        help="Minimum tools in a chain to count as a candidate (default 2)",
    )
    parser.add_argument("--top", type=int, default=15, help="Limit table rows")
    parser.add_argument("--json", action="store_true", help="Emit JSON, not text")
    args = parser.parse_args(argv)

    metrics_path = args.metrics or default_metrics_path()
    try:
        turns = load_turns(metrics_path, since=args.since)
    except FileNotFoundError:
        print(
            f"No metrics log at {metrics_path}\n"
            "Point --metrics at Sabueso's metrics.jsonl (or set SABUESO_METRICS_LOG).",
            file=sys.stderr,
        )
        return 1

    usage_path = args.tool_usage or default_usage_path()
    try:
        calls_by_corr = load_calls_by_corr(usage_path, since=args.since)
    except FileNotFoundError:
        calls_by_corr = {}

    stats = analyze(turns, calls_by_corr, min_chain=args.min_chain, top=args.top)
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print(render_text(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
