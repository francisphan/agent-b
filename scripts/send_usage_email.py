#!/usr/bin/env python3
"""Fetch Agent B's weekly usage report and email it as an HTML digest.

Standalone by design — stdlib + ``requests`` only, so the GitHub Actions cron
can run it with a single ``pip install requests`` (no need to install the whole
agent-b package). It GETs ``{AGENT_B_URL}/usage/report?days=7`` with a bearer
token, renders a Gmail-safe HTML email, and sends it via the Gmail REST API
using a refresh-token (no google client libraries).

Environment:
    AGENT_B_URL          Base URL of the Agent B server (e.g. https://…up.railway.app)
    MCP_API_TOKEN        Bearer token for the /usage/report endpoint
    GMAIL_CLIENT_ID      OAuth client id for the Gmail sender
    GMAIL_CLIENT_SECRET  OAuth client secret
    GMAIL_REFRESH_TOKEN  Long-lived refresh token for the sender mailbox
    GMAIL_SENDER         From: address (the mailbox the refresh token belongs to)
    USAGE_REPORT_TO      Comma-separated recipient list

Usage:
    python scripts/send_usage_email.py                 # fetch + send
    python scripts/send_usage_email.py --dry-run       # fetch + render to stdout, no send
    python scripts/send_usage_email.py --dry-run --fixture scripts/sample_usage_report.json
    python scripts/send_usage_email.py --days 14       # override the window

The HTML is deliberately Gmail-safe: inline styles only (no <style> blocks, no
JS), no external or data: images, and all "charts" are plain <div> bars sized
with ``width:N%``. Error snippets are already PII-masked server-side.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import requests

# --- Palette (wine accent on white; greys for bars; red/amber only for faults) --
ACCENT = "#7C2D45"
INK = "#1F2937"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
TRACK = "#EEF0F2"
BAR = "#9AA2AD"
RED = "#B42318"
AMBER = "#B54708"

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------
def _esc(value: object) -> str:
    return escape(str(value))


def _pct(n: float, denom: float) -> int:
    """Integer percentage 0..100, guarding a zero denominator."""
    if not denom:
        return 0
    return max(0, min(100, round(100 * n / denom)))


def _fmt_ms(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "–"
    return f"{value:.0f}ms"


def _fmt_date(iso_date: str, fmt: str = "%b %d") -> str:
    """Format a ``YYYY-MM-DD`` (or ISO datetime) string; pass through on failure."""
    try:
        return datetime.fromisoformat(iso_date[:19]).strftime(fmt)
    except (ValueError, TypeError):
        return iso_date


def _rate(part: float, whole: float) -> str:
    if not whole:
        return "0%"
    return f"{100 * part / whole:.1f}%"


def _day_axis(report: dict) -> list[str]:
    """The ISO dates (oldest→newest) the day bars should show.

    Prefers a full ``days``-long calendar axis anchored on the report's window
    end so days with zero calls still render as empty bars; falls back to
    whatever dates actually appear when that metadata is absent (e.g. a bare
    fixture).
    """
    days = int(report.get("days") or 7)
    end_iso = report.get("until") or report.get("generated_at")
    end = None
    if end_iso:
        try:
            end = datetime.fromisoformat(str(end_iso)[:19]).date()
        except ValueError:
            end = None
    if end is None:
        return sorted(report.get("per_day", {}).keys())
    return [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def build_subject(report: dict) -> str:
    """e.g. 'Agent B usage — week of Jun 29: 84 calls, 4 errors'."""
    axis = _day_axis(report)
    start = axis[0] if axis else (report.get("since") or "")[:10]
    week_of = _fmt_date(start) if start else "?"
    totals = report.get("totals", {})
    calls = totals.get("calls", 0)
    errors = totals.get("error", 0)
    return f"Agent B usage — week of {week_of}: {calls} calls, {errors} errors"


# ---------------------------------------------------------------------------
# HTML rendering (pure functions — no network, so they're unit-testable)
# ---------------------------------------------------------------------------
def _tile(label: str, value: str, sub: str = "", color: str = INK) -> str:
    sub_html = (
        f'<div style="font-size:11px;color:{MUTED};margin-top:2px;">{_esc(sub)}</div>'
        if sub
        else ""
    )
    return (
        '<td style="padding:0 6px;vertical-align:top;width:25%;">'
        f'<div style="border:1px solid {BORDER};border-radius:8px;padding:12px 14px;">'
        f'<div style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;'
        f'color:{MUTED};">{_esc(label)}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color};margin-top:4px;">'
        f"{_esc(value)}</div>{sub_html}</div></td>"
    )


def _summary_tiles(report: dict) -> str:
    totals = report.get("totals", {})
    prev = report.get("prev_totals", {})
    calls = totals.get("calls", 0)
    errors = totals.get("error", 0)
    degraded = totals.get("degraded", 0)
    prev_calls = prev.get("calls", 0)

    diff = calls - prev_calls
    if diff > 0:
        wow_symbol, wow_color = "▲", ACCENT
    elif diff < 0:
        wow_symbol, wow_color = "▼", MUTED
    else:
        wow_symbol, wow_color = "—", MUTED
    wow_pct = f"{round(100 * diff / prev_calls)}%" if prev_calls else "n/a"
    wow_value = f"{wow_symbol} {abs(diff)}"

    err_color = RED if errors else INK
    deg_color = AMBER if degraded else INK
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;margin:0 -6px;"><tr>'
        + _tile("Total calls", f"{calls}", f"{prev_calls} prev week")
        + _tile("Error rate", _rate(errors, calls), f"{errors} errors", err_color)
        + _tile("Degraded rate", _rate(degraded, calls), f"{degraded} degraded", deg_color)
        + _tile("Week over week", wow_value, f"{wow_pct} vs prev", wow_color)
        + "</tr></table>"
    )


def _bar(width_pct: int, color: str, height: int = 10) -> str:
    return (
        f'<div style="background:{TRACK};border-radius:4px;height:{height}px;width:100%;">'
        f'<div style="background:{color};border-radius:4px;height:{height}px;'
        f'width:{width_pct}%;"></div></div>'
    )


def _day_bars(report: dict) -> str:
    per_day = report.get("per_day", {})
    axis = _day_axis(report)
    peak = max([per_day.get(d, 0) for d in axis], default=0)
    rows = []
    for d in axis:
        count = per_day.get(d, 0)
        label = f"{_fmt_date(d, '%a')} {_fmt_date(d)}"
        rows.append(
            "<tr>"
            f'<td style="width:96px;font-size:12px;color:{MUTED};padding:3px 8px 3px 0;'
            f'white-space:nowrap;">{_esc(label)}</td>'
            f'<td style="padding:3px 8px;">{_bar(_pct(count, peak), ACCENT)}</td>'
            f'<td style="width:36px;text-align:right;font-size:12px;color:{INK};'
            f'font-weight:600;padding:3px 0;">{count}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;">' + "".join(rows) + "</table>"
    )


def _tool_table(report: dict) -> str:
    tools = report.get("per_tool", [])
    peak = max([t.get("calls", 0) for t in tools], default=0)
    head = (
        "<tr>"
        f'<th style="text-align:left;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.04em;padding:0 8px 6px 0;">Tool</th>'
        f'<th style="text-align:left;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.04em;padding:0 8px 6px;width:34%;">Volume</th>'
        f'<th style="text-align:right;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.04em;padding:0 8px 6px;">Calls</th>'
        f'<th style="text-align:right;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.04em;padding:0 8px 6px;">p50 / p95</th>'
        f'<th style="text-align:right;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.04em;padding:0 0 6px 8px;">Err / Deg</th>'
        "</tr>"
    )
    rows = []
    for t in tools:
        errors = t.get("errors", 0)
        degraded = t.get("degraded", 0)
        err_txt = f'<span style="color:{RED if errors else MUTED};">{errors}</span>'
        deg_txt = f'<span style="color:{AMBER if degraded else MUTED};">{degraded}</span>'
        lat = f"{_fmt_ms(t.get('p50_ms'))} / {_fmt_ms(t.get('p95_ms'))}"
        rows.append(
            f'<tr style="border-top:1px solid {BORDER};">'
            f'<td style="font-size:13px;color:{INK};padding:8px 8px 8px 0;'
            f'font-family:monospace;">{_esc(t.get("tool", "?"))}</td>'
            f'<td style="padding:8px;">{_bar(_pct(t.get("calls", 0), peak), BAR)}</td>'
            f'<td style="text-align:right;font-size:13px;color:{INK};font-weight:600;'
            f'padding:8px;">{t.get("calls", 0)}</td>'
            f'<td style="text-align:right;font-size:12px;color:{MUTED};padding:8px;'
            f'white-space:nowrap;">{lat}</td>'
            f'<td style="text-align:right;font-size:13px;padding:8px 0 8px 8px;'
            f'white-space:nowrap;">{err_txt} / {deg_txt}</td>'
            "</tr>"
        )
    if not rows:
        rows.append(
            f'<tr><td colspan="5" style="font-size:13px;color:{MUTED};padding:8px 0;">'
            "No tool calls in this window.</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;">' + head + "".join(rows) + "</table>"
    )


def _top_errors(report: dict) -> str:
    errors = report.get("top_errors", [])
    if not errors:
        return f'<div style="font-size:13px;color:{MUTED};">No errors this week. 🎉</div>'
    items = []
    for e in errors:
        items.append(
            f'<tr><td style="font-size:13px;color:{INK};padding:6px 8px 6px 0;'
            f'font-family:monospace;">{_esc(e.get("error", ""))}</td>'
            f'<td style="text-align:right;font-size:13px;color:{RED};font-weight:600;'
            f'padding:6px 0;white-space:nowrap;">×{e.get("count", 0)}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;">' + "".join(items) + "</table>"
    )


def _client_display(client: str) -> str:
    return "sabueso (Slack bot)" if client == "sabueso" else client


def _tools_summary(top_tools: list) -> str:
    return ", ".join(f"{_esc(t.get('tool', '?'))} ({t.get('calls', 0)})" for t in top_tools)


def _clients_table(report: dict) -> str:
    """The 'Who's calling' section: one row per client, Slack end-users nested.

    Client volume bars scale to the busiest client; the end-user rows under
    sabueso scale to sabueso's own total so they're comparable to each other.
    """
    clients = report.get("per_client", [])
    end_users = report.get("per_end_user", [])
    peak = max([c.get("calls", 0) for c in clients], default=0)
    sabueso_calls = next((c.get("calls", 0) for c in clients if c.get("client") == "sabueso"), 0)

    rows = []
    for c in clients:
        name = _client_display(c.get("client", "anonymous"))
        tools = _tools_summary(c.get("top_tools", []))
        rows.append(
            "<tr>"
            f'<td style="font-size:13px;color:{INK};font-weight:600;padding:8px 8px 8px 0;'
            f'white-space:nowrap;">{_esc(name)}</td>'
            f'<td style="padding:8px;width:34%;">{_bar(_pct(c.get("calls", 0), peak), ACCENT)}</td>'
            f'<td style="text-align:right;font-size:13px;color:{INK};font-weight:600;'
            f'padding:8px;">{c.get("calls", 0)}</td>'
            f'<td style="font-size:12px;color:{MUTED};padding:8px 0 8px 8px;">{tools}</td>'
            "</tr>"
        )
        if c.get("client") == "sabueso":
            for u in end_users:
                utools = _tools_summary(u.get("top_tools", []))
                rows.append(
                    "<tr>"
                    f'<td style="font-size:12px;color:{MUTED};padding:4px 8px 4px 16px;'
                    f'white-space:nowrap;font-family:monospace;">↳ {_esc(u.get("end_user", ""))}</td>'
                    f'<td style="padding:4px 8px;">{_bar(_pct(u.get("calls", 0), sabueso_calls), BAR, 8)}</td>'
                    f'<td style="text-align:right;font-size:12px;color:{MUTED};'
                    f'padding:4px 8px;">{u.get("calls", 0)}</td>'
                    f'<td style="font-size:11px;color:{MUTED};padding:4px 0 4px 8px;">{utools}</td>'
                    "</tr>"
                )
    if not rows:
        rows.append(
            f'<tr><td colspan="4" style="font-size:13px;color:{MUTED};padding:8px 0;">'
            "No calls recorded.</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;">' + "".join(rows) + "</table>"
    )


def _section(title: str, body: str) -> str:
    return (
        f'<div style="font-size:13px;font-weight:700;color:{ACCENT};'
        f'text-transform:uppercase;letter-spacing:.05em;margin:26px 0 10px;">{_esc(title)}</div>'
        + body
    )


def _auth_line(report: dict) -> str:
    split = report.get("auth_split", {})
    if not split:
        return ""
    parts = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in sorted(split.items()))
    return f'<div style="font-size:12px;color:{MUTED};margin-top:8px;">By caller: {parts}</div>'


def render_html(report: dict) -> str:
    """Render the full Gmail-safe HTML body from a weekly-report dict."""
    axis = _day_axis(report)
    totals = report.get("totals", {})
    start = axis[0] if axis else (report.get("since") or "")[:10]
    end = axis[-1] if axis else (report.get("until") or "")[:10]
    window = f"{_fmt_date(start)} – {_fmt_date(end)}" if start and end else ""
    subtitle = (
        f"{totals.get('calls', 0)} calls · {totals.get('error', 0)} errors · "
        f"{totals.get('degraded', 0)} degraded"
    )
    return (
        f'<div style="background:#F4F5F7;padding:24px 0;font-family:'
        f'-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;"><tr><td align="center">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;max-width:640px;width:100%;background:#FFFFFF;'
        f'border:1px solid {BORDER};border-radius:12px;">'
        '<tr><td style="padding:24px 28px 4px;">'
        f'<div style="font-size:20px;font-weight:800;color:{ACCENT};">Agent B · Weekly usage</div>'
        f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{_esc(window)}</div>'
        f'<div style="font-size:13px;color:{INK};margin-top:2px;">{_esc(subtitle)}</div>'
        "</td></tr>"
        '<tr><td style="padding:16px 28px 0;">' + _summary_tiles(report) + "</td></tr>"
        '<tr><td style="padding:0 28px;">'
        + _section("Calls per day", _day_bars(report))
        + _section("By tool", _tool_table(report))
        + _auth_line(report)
        + _section("Who's calling", _clients_table(report))
        + _section("Top errors", _top_errors(report))
        + "</td></tr>"
        '<tr><td style="padding:20px 28px 26px;">'
        f'<div style="font-size:11px;color:{MUTED};border-top:1px solid {BORDER};'
        f'padding-top:14px;">Generated {_esc(report.get("generated_at", ""))} · '
        "Agent B usage mirror (Redis).</div>"
        "</td></tr></table></td></tr></table></div>"
    )


def render_text(report: dict) -> str:
    """Plain-text fallback for the multipart/alternative email."""
    totals = report.get("totals", {})
    lines = [
        build_subject(report),
        "",
        f"Total calls: {totals.get('calls', 0)}",
        f"Errors: {totals.get('error', 0)}   Degraded: {totals.get('degraded', 0)}",
        "",
        "By tool:",
    ]
    for t in report.get("per_tool", []):
        lines.append(
            f"  {t.get('tool', '?'):<28} {t.get('calls', 0):>5} calls  "
            f"p50 {_fmt_ms(t.get('p50_ms'))}  p95 {_fmt_ms(t.get('p95_ms'))}  "
            f"err {t.get('errors', 0)}  deg {t.get('degraded', 0)}"
        )
    if report.get("per_client"):
        lines += ["", "Who's calling:"]
        end_users = report.get("per_end_user", [])
        for c in report["per_client"]:
            lines.append(
                f"  {_client_display(c.get('client', '?')):<28} {c.get('calls', 0):>5} calls"
            )
            if c.get("client") == "sabueso":
                for u in end_users:
                    lines.append(f"    {u.get('end_user', ''):<26} {u.get('calls', 0):>5} calls")
    if report.get("top_errors"):
        lines += ["", "Top errors:"]
        for e in report["top_errors"]:
            lines.append(f"  x{e.get('count', 0)}  {e.get('error', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Network: fetch the report and send via Gmail
# ---------------------------------------------------------------------------
def fetch_report(base_url: str, token: str | None, days: int) -> dict:
    url = base_url.rstrip("/") + "/usage/report"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.get(url, params={"days": days}, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = requests.post(
        GMAIL_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_raw_message(
    sender: str, recipients: list[str], subject: str, html: str, text: str
) -> str:
    """Build a multipart/alternative MIME message, base64url-encoded for Gmail."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def send_email(access_token: str, raw: str) -> dict:
    resp = requests.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        raise SystemExit(2)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Report window in days (default 7)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch/render and print the HTML to stdout without sending.",
    )
    parser.add_argument(
        "--fixture",
        help="Render from a local JSON report file instead of fetching (implies no network).",
    )
    parser.add_argument(
        "--save-json",
        metavar="PATH",
        help="Also write the full report JSON to PATH (works with --dry-run too).",
    )
    args = parser.parse_args(argv)

    if args.fixture:
        report = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    else:
        base_url = _require_env("AGENT_B_URL")
        report = fetch_report(base_url, os.getenv("MCP_API_TOKEN"), args.days)

    # Persist the snapshot before sending so a later email failure can't lose it
    # (the workflow commits this file for a downstream auditor to consume).
    if args.save_json:
        out = Path(args.save_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote report snapshot to {out}", file=sys.stderr)

    subject = build_subject(report)
    html = render_html(report)
    text = render_text(report)

    if args.dry_run:
        print(html)
        return 0

    sender = _require_env("GMAIL_SENDER")
    recipients = [e.strip() for e in _require_env("USAGE_REPORT_TO").split(",") if e.strip()]
    access_token = get_access_token(
        _require_env("GMAIL_CLIENT_ID"),
        _require_env("GMAIL_CLIENT_SECRET"),
        _require_env("GMAIL_REFRESH_TOKEN"),
    )
    raw = build_raw_message(sender, recipients, subject, html, text)
    result = send_email(access_token, raw)
    print(f"Sent usage report to {', '.join(recipients)} (message id {result.get('id')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
