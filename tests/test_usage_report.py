"""Tests for src/usage_report.py — JSONL aggregation."""

import json
from datetime import datetime, timezone

from src.usage_report import _percentile, aggregate, load_records, render_text, weekly_report


def _rec(tool, status="ok", dur=10, auth="read", result_bytes=100, **extra):
    r = {
        "ts": extra.pop("ts", "2026-05-26T12:00:00+00:00"),
        "event": "tool_call",
        "tool": tool,
        "auth": auth,
        "status": status,
        "duration_ms": dur,
    }
    if status == "ok":
        r["result_bytes"] = result_bytes
    else:
        r["error_type"] = extra.pop("error_type", "ValueError")
    r.update(extra)
    return r


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 0.95) is None

    def test_single(self):
        assert _percentile([42], 0.95) == 42

    def test_p50_is_median(self):
        assert _percentile([1, 2, 3], 0.50) == 2

    def test_p95_interpolates(self):
        # 0..100 → p95 lands at 95
        assert _percentile(list(range(101)), 0.95) == 95


class TestAggregate:
    def test_counts_and_error_rate(self):
        recs = [
            _rec("sf_soql_query"),
            _rec("sf_soql_query", status="error", error_type="ToolError"),
            _rec("ns_suiteql_query"),
        ]
        stats = aggregate(recs)
        assert stats["total"] == 3
        assert stats["ok"] == 2
        assert stats["errors"] == 1
        assert round(stats["error_rate"], 3) == round(1 / 3, 3)
        assert stats["error_types"] == {"ToolError": 1}

    def test_per_tool_latency_and_sorted_by_calls(self):
        recs = [_rec("busy", dur=d) for d in (10, 20, 30, 40)]
        recs += [_rec("quiet", dur=5)]
        stats = aggregate(recs)
        assert [t["tool"] for t in stats["tools"]] == ["busy", "quiet"]
        busy = stats["tools"][0]
        assert busy["calls"] == 4
        assert busy["p50_ms"] == 25  # median of 10,20,30,40
        assert busy["max_ms"] == 40

    def test_per_tool_error_rate(self):
        recs = [
            _rec("t", status="error"),
            _rec("t", status="error"),
            _rec("t"),
            _rec("t"),
        ]
        t = aggregate(recs)["tools"][0]
        assert t["errors"] == 2
        assert t["error_rate"] == 0.5

    def test_by_auth_breakdown(self):
        recs = [_rec("t", auth="read"), _rec("t", auth="write"), _rec("t", auth="read")]
        assert aggregate(recs)["by_auth"] == {"read": 2, "write": 1}

    def test_degraded_counted_separately_from_ok_and_errors(self):
        recs = [
            _rec("g", status="degraded"),
            _rec("g"),
            _rec("g", status="error"),
        ]
        stats = aggregate(recs)
        assert stats["total"] == 3
        assert stats["degraded"] == 1
        assert stats["errors"] == 1
        assert stats["ok"] == 1  # total - errors - degraded, no longer counts degraded

    def test_inband_errors_labeled_in_error_types(self):
        # status=error with no error_type == an in-band {"error": ...} return.
        recs = [
            {"event": "tool_call", "tool": "t", "status": "error", "duration_ms": 5},
            _rec("t", status="error", error_type="ToolError"),
        ]
        error_types = aggregate(recs)["error_types"]
        assert error_types["(in-band)"] == 1
        assert error_types["ToolError"] == 1

    def test_empty(self):
        stats = aggregate([])
        assert stats["total"] == 0
        assert stats["tools"] == []
        assert stats["span"] == {"first": None, "last": None}


class TestLoadRecords:
    def test_skips_blank_and_bad_lines_and_non_tool_events(self, tmp_path):
        p = tmp_path / "u.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(_rec("a")),
                    "not json",
                    "",
                    json.dumps({"event": "something_else", "tool": "x"}),
                    json.dumps(_rec("b")),
                ]
            )
        )
        recs = load_records(str(p))
        assert [r["tool"] for r in recs] == ["a", "b"]

    def test_since_filter(self, tmp_path):
        p = tmp_path / "u.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(_rec("old", ts="2026-05-25T09:00:00+00:00")),
                    json.dumps(_rec("new", ts="2026-05-26T09:00:00+00:00")),
                ]
            )
        )
        recs = load_records(str(p), since="2026-05-26")
        assert [r["tool"] for r in recs] == ["new"]


class TestTurns:
    def test_groups_by_corr(self):
        recs = [
            _rec("sf_search", corr="t1"),
            _rec("sf_soql_query", corr="t1", status="error"),
            _rec("ns_suiteql_query", corr="t2"),
        ]
        turns = aggregate(recs)["turns"]
        assert turns["count"] == 2
        assert turns["untagged_calls"] == 0
        assert turns["with_errors"] == 1
        t1 = next(t for t in turns["items"] if t["corr"] == "t1")
        assert t1["calls"] == 2
        assert t1["errors"] == 1
        assert t1["tools"] == ["sf_search", "sf_soql_query"]  # sorted unique

    def test_untagged_calls_counted_separately(self):
        recs = [_rec("sf_search"), _rec("sf_search", corr="t1")]
        turns = aggregate(recs)["turns"]
        assert turns["count"] == 1
        assert turns["untagged_calls"] == 1

    def test_calls_per_turn_stats(self):
        recs = [_rec("a", corr="t1"), _rec("a", corr="t1"), _rec("a", corr="t2")]
        cpt = aggregate(recs)["turns"]["calls_per_turn"]
        assert cpt["max"] == 2
        assert cpt["avg"] == 1.5

    def test_sorted_busiest_first(self):
        recs = [_rec("a", corr="quiet")]
        recs += [_rec("a", corr="busy") for _ in range(3)]
        items = aggregate(recs)["turns"]["items"]
        assert [t["corr"] for t in items] == ["busy", "quiet"]

    def test_empty_has_no_turns(self):
        turns = aggregate([])["turns"]
        assert turns["count"] == 0
        assert turns["items"] == []


def _epoch(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc).timestamp()


def _wrec(
    tool="sf_soql_query",
    status="ok",
    dur=10,
    ts=None,
    auth="read",
    error=None,
    client=None,
    end_user=None,
):
    """A Redis-mirror-shaped record: epoch float ts, like usage_store stores."""
    r = {
        "event": "tool_call",
        "tool": tool,
        "status": status,
        "duration_ms": dur,
        "auth": auth,
        "ts": _epoch(2026, 7, 1) if ts is None else ts,
    }
    if status == "ok":
        r["result_bytes"] = 100
    else:
        r["error_type"] = "ValueError"
    if error is not None:
        r["error"] = error
    if client is not None:
        r["client"] = client
    if end_user is not None:
        r["end_user"] = end_user
    return r


class TestWeeklyReport:
    def test_totals_and_prev_totals(self):
        recs = [_wrec(status="ok"), _wrec(status="error"), _wrec(status="degraded")]
        prev = [_wrec(status="ok"), _wrec(status="ok")]
        rep = weekly_report(recs, prev)
        assert rep["totals"]["calls"] == 3
        assert rep["totals"]["ok"] == 1
        assert rep["totals"]["error"] == 1
        assert rep["totals"]["degraded"] == 1
        assert rep["prev_totals"]["calls"] == 2
        assert rep["prev_totals"]["error"] == 0

    def test_per_day_keys_from_epoch_ts(self):
        recs = [
            _wrec(ts=_epoch(2026, 7, 1)),
            _wrec(ts=_epoch(2026, 7, 1)),
            _wrec(ts=_epoch(2026, 7, 3)),
        ]
        per_day = weekly_report(recs, [])["per_day"]
        assert per_day == {"2026-07-01": 2, "2026-07-03": 1}

    def test_per_tool_sorted_with_degraded_and_percentiles(self):
        recs = [_wrec(tool="busy", dur=d) for d in (10, 20, 30, 40)]
        recs.append(_wrec(tool="busy", status="degraded", dur=50, error="leg down"))
        recs.append(_wrec(tool="quiet", dur=5))
        per_tool = weekly_report(recs, [])["per_tool"]
        assert [t["tool"] for t in per_tool] == ["busy", "quiet"]  # busiest first
        busy = per_tool[0]
        assert busy["calls"] == 5
        assert busy["degraded"] == 1
        assert busy["p50_ms"] is not None
        assert busy["p95_ms"] is not None
        # Row is trimmed to the email's fields (no max_ms / avg_result_bytes).
        assert set(busy) == {"tool", "calls", "errors", "degraded", "p50_ms", "p95_ms"}

    def test_top_errors_counts_snippets(self):
        recs = [
            _wrec(status="error", error="boom A"),
            _wrec(status="error", error="boom A"),
            _wrec(status="degraded", error="boom B"),
            _wrec(status="ok"),  # no error field, must not appear
        ]
        top = weekly_report(recs, [])["top_errors"]
        assert top[0] == {"error": "boom A", "count": 2}
        assert {"error": "boom B", "count": 1} in top
        assert all("boom" in e["error"] for e in top)

    def test_auth_split(self):
        recs = [_wrec(auth="read"), _wrec(auth="write"), _wrec(auth="read")]
        assert weekly_report(recs, [])["auth_split"] == {"read": 2, "write": 1}

    def test_empty(self):
        rep = weekly_report([], [])
        assert rep["totals"]["calls"] == 0
        assert rep["prev_totals"]["calls"] == 0
        assert rep["per_tool"] == []
        assert rep["per_day"] == {}
        assert rep["top_errors"] == []
        assert rep["per_client"] == []
        assert rep["per_end_user"] == []


class TestPerClient:
    def test_rollup_sorted_with_top_tools(self):
        recs = [
            _wrec(client="sabueso", tool="a"),
            _wrec(client="sabueso", tool="a", status="error"),
            _wrec(client="sabueso", tool="b", status="degraded"),
            _wrec(client="alice@x.com", tool="c"),
        ]
        pc = weekly_report(recs, [])["per_client"]
        assert [c["client"] for c in pc] == ["sabueso", "alice@x.com"]  # busiest first
        sab = pc[0]
        assert sab["calls"] == 3
        assert sab["errors"] == 1
        assert sab["degraded"] == 1
        assert sab["top_tools"][0] == {"tool": "a", "calls": 2}

    def test_missing_client_bucketed_anonymous(self):
        pc = weekly_report([_wrec()], [])["per_client"]
        assert pc[0]["client"] == "anonymous"


class TestPerEndUser:
    def test_only_sabueso_records_with_end_user(self):
        recs = [
            _wrec(client="sabueso", end_user="U1", tool="a"),
            _wrec(client="sabueso", end_user="U1", tool="a"),
            _wrec(client="sabueso", end_user="U2", tool="b"),
            _wrec(client="sabueso", tool="c"),  # no end_user → excluded
            _wrec(client="alice@x.com", end_user="U9", tool="d"),  # not sabueso → excluded
        ]
        peu = weekly_report(recs, [])["per_end_user"]
        assert [u["end_user"] for u in peu] == ["U1", "U2"]  # busiest first
        assert peu[0]["calls"] == 2
        assert peu[0]["top_tools"][0] == {"tool": "a", "calls": 2}

    def test_empty_when_no_end_users(self):
        recs = [_wrec(client="sabueso", tool="a")]
        assert weekly_report(recs, [])["per_end_user"] == []


class TestRenderText:
    def test_empty_message(self):
        assert render_text(aggregate([])) == "No tool-call records found."

    def test_includes_tool_and_totals(self):
        out = render_text(aggregate([_rec("sf_soql_query"), _rec("sf_soql_query")]))
        assert "sf_soql_query" in out
        assert "2 calls" in out

    def test_shows_degraded_count(self):
        recs = [_rec("guest_360_profile", status="degraded"), _rec("sf_soql_query")]
        out = render_text(aggregate(recs))
        assert "1 degraded" in out

    def test_top_limit(self):
        recs = [_rec(f"tool{i}") for i in range(5)]
        out = render_text(aggregate(recs), top=2)
        # Only 2 of the 5 tool rows should appear.
        assert sum(f"tool{i}" in out for i in range(5)) == 2

    def test_renders_turns_section(self):
        recs = [_rec("sf_search", corr="abc123"), _rec("sf_search", corr="abc123")]
        out = render_text(aggregate(recs))
        assert "Turns —" in out
        assert "abc123" in out

    def test_turns_note_when_all_untagged(self):
        out = render_text(aggregate([_rec("sf_search"), _rec("sf_search")]))
        assert "none tagged" in out
