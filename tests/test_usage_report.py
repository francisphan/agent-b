"""Tests for src/usage_report.py — JSONL aggregation."""

import json

from src.usage_report import _percentile, aggregate, load_records, render_text


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


class TestRenderText:
    def test_empty_message(self):
        assert render_text(aggregate([])) == "No tool-call records found."

    def test_includes_tool_and_totals(self):
        out = render_text(aggregate([_rec("sf_soql_query"), _rec("sf_soql_query")]))
        assert "sf_soql_query" in out
        assert "2 calls" in out

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
