# Agent B

<img src="assets/agent-b.png" alt="Agent B mascot: a German shepherd secret agent in dark glasses, an earpiece and a black suit with a red tie" width="96">

An MCP server that gives AI clients one set of tools over The Vines of Mendoza's
business systems: **Salesforce**, **NetSuite**, **Pardot** (Account Engagement) and
**OPERA PMS**. Claude Desktop connects to it as a custom connector, and the
[Sabueso](https://github.com/francisphan/Sabueso) Slack bot calls it to answer staff
questions about guests and customers.

```
Claude Desktop ─┐                       ┌─▶ Salesforce   (read + gated writes)
                ├─ MCP/HTTP ─▶ Agent B ─┼─▶ NetSuite     (read + gated writes)
Sabueso bot ────┘   (OAuth or token)    ├─▶ Pardot       (curated reads; full surface opt-in)
                                        └─▶ OPERA PMS    (read-only, via opera-pms-api)
```

## What it offers

- **Per-system tools** for Salesforce (SOQL, search, describe, records), NetSuite
  (SuiteQL, REST records, schema) and Pardot. Write tools (create, update, upsert,
  delete) need write access.
- **Composite lookups** that join systems in one call, such as a full **guest 360**
  across Salesforce, NetSuite and OPERA, `person_brief`, and `wine_owner_lookup` by
  wine label text.
- **OPERA PMS, read-only**: arrivals, in-house guests, stay history and guest
  profiles. OPERA is reached through the `opera-pms-api` service over a Tailscale
  tunnel, never by connecting to its Oracle database directly.
- **Resources and prompts**: schema resources (`schema://salesforce`,
  `schema://netsuite`, `schema://opera`, per object or table) and a query-patterns
  guide, plus ready-made prompts for common lookups.

Pardot exposes a lean, curated set of read tools by default. Set
`PARDOT_TOOLS_ENABLED=true` for its full surface (74 read and 54 write tools).

## Access

Authentication fails closed: the server refuses to start with no auth configured.

- **OAuth (Google)** for Claude Desktop's custom connector. Sign-ins can be limited
  to one Workspace domain; only the emails listed in `OAUTH_WRITE_EMAILS` get the
  write scope, and everyone else is read-only. OAuth state is kept in Redis so
  connections survive deploys.
- **Static bearer tokens** for server-to-server callers like Sabueso:
  `MCP_API_TOKEN` (read) and `MCP_WRITE_TOKEN` (read + write).
- `MCP_ALLOW_INSECURE_NO_AUTH=true` runs it open, for local development only.

## Running it

```bash
cp .env.example .env      # fill in credentials; every variable is documented there
pip install -e ".[dev]"
python -m src             # serves MCP on :8000 (streamable HTTP; MCP_TRANSPORT=sse for SSE)
pytest                    # tests
ruff check . && ruff format --check .
```

`GET /health` is the health check. `GET /usage/report` returns a weekly tool-usage
report (bearer-protected), and a scheduled workflow emails it out. Every tool call is
logged as one redacted line plus a JSONL analytics record (`TOOL_USAGE_LOG`).

## Deployment

Runs on Railway from the `Dockerfile`; `start.sh` brings up Tailscale in
userspace mode when `TS_AUTHKEY` is set, so OPERA calls can reach `opera-pms-api`
over the tailnet, then starts the server. A GKE Autopilot version (Terraform and
Kubernetes manifests) lives on the `deploy/gke` branch.

## Layout

| Path | What |
| --- | --- |
| `src/server.py` | FastMCP server, auth, health and usage routes, resources, prompts |
| `src/*_tools.py`, `src/*_write_tools.py` | Tools for each system; writes kept separate |
| `src/cross_tools.py`, `src/person_tools.py`, `src/wine_tools.py` | Lookups that span systems |
| `src/netsuite/` | NetSuite client: TBA auth, SuiteQL, REST, retries, paging |
| `src/oauth_*.py` | Google OAuth provider, callback and token store |
| `src/query_validator.py`, `src/sanitize.py` | Query checks and input sanitizing |
| `scripts/send_usage_email.py` | Emails the weekly usage report |
