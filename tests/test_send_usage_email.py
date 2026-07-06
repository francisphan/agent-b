"""Tests for scripts/send_usage_email.py — the pure render/subject functions.

The script lives outside the src package (it's a standalone stdlib+requests
tool), so we load it by path. Rendering is kept side-effect-free so it can be
exercised here without any network.
"""

import base64
import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "send_usage_email.py"
_FIXTURE = _ROOT / "scripts" / "sample_usage_report.json"

_spec = importlib.util.spec_from_file_location("send_usage_email", _SCRIPT)
sue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sue)


@pytest.fixture
def report():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class TestSubject:
    def test_subject_format(self, report):
        subject = sue.build_subject(report)
        assert subject == "Agent B usage — week of Jun 30: 84 calls, 4 errors"

    def test_subject_on_empty_report(self):
        subj = sue.build_subject({"totals": {"calls": 0, "error": 0}, "per_day": {}})
        assert "0 calls, 0 errors" in subj


class TestRenderHtml:
    def test_contains_tool_names(self, report):
        html = sue.render_html(report)
        assert "guest_360_profile" in html
        assert "sf_soql_query" in html
        assert "wine_owner_lookup" in html

    def test_busiest_tool_bar_is_full_width(self, report):
        # guest_360_profile has the most calls (26) → its volume bar is 100%.
        assert "width:100%" in sue.render_html(report)

    def test_bar_width_is_proportional(self, report):
        # sf_soql_query 22 calls of a 26 peak → round(100*22/26) == 85%.
        assert "width:85%" in sue.render_html(report)

    def test_wine_accent_present(self, report):
        assert sue.ACCENT in sue.render_html(report)

    def test_week_over_week_up_arrow(self, report):
        # 84 calls this week vs 71 previous → up arrow.
        assert "▲" in sue.render_html(report)

    def test_top_error_snippet_rendered(self, report):
        assert "NetSuite 400: Invalid search query" in sue.render_html(report)

    def test_no_script_or_external_images(self, report):
        html = sue.render_html(report)
        assert "<script" not in html.lower()
        assert "src=" not in html  # no external/data images at all
        assert "<style" not in html.lower()

    def test_empty_report_renders_without_error(self):
        empty = {
            "days": 7,
            "generated_at": "2026-07-06T11:00:00+00:00",
            "until": "2026-07-06T11:00:00+00:00",
            "totals": {"calls": 0, "ok": 0, "error": 0, "degraded": 0, "error_rate": 0.0},
            "prev_totals": {"calls": 0, "error": 0, "degraded": 0},
            "per_day": {},
            "per_tool": [],
            "auth_split": {},
            "top_errors": [],
        }
        html = sue.render_html(empty)
        assert "No tool calls in this window." in html
        assert "No errors this week" in html


class TestRenderText:
    def test_text_contains_tools_and_totals(self, report):
        text = sue.render_text(report)
        assert "guest_360_profile" in text
        assert "Total calls: 84" in text


class TestHelpers:
    def test_pct_guards_zero_denominator(self):
        assert sue._pct(5, 0) == 0
        assert sue._pct(1, 4) == 25
        assert sue._pct(999, 10) == 100  # clamped

    def test_build_raw_message_roundtrips(self):
        raw = sue.build_raw_message("me@x.com", ["a@b.com", "c@d.com"], "Subj", "<b>hi</b>", "hi")
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
        assert "Subject: Subj" in decoded
        assert "a@b.com, c@d.com" in decoded
        assert "text/plain" in decoded
        assert "text/html" in decoded
