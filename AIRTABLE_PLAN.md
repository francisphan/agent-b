# Airtable Integration Plan

Adding Airtable as a fourth data source in Agent B, exposed as MCP tools to the Sabueso
Slack bot for on-demand lookups. **Blocked on:** Parke granting Airtable access.

## Why Airtable

Parke's team is putting client data into Airtable. That data is currently invisible to
Sabueso, which only sees Salesforce / NetSuite / Pardot. Adding Airtable closes the gap
for on-demand Slack lookups like "what do we know about Jane Smith?" — Claude can pull
context from whichever system has it.

## Scope (this PR)

- Read tools — list bases/tables, list records, search by field/formula, get single record,
  get table schema
- Write tools — create/update/delete records (gated by `MCP_WRITE_TOKEN`)
- Hybrid schema: curated static schema for known high-value bases + runtime
  `airtable_get_table_schema` for everything else
- Allowlist of base IDs via env var (defense-in-depth beyond the PAT scope)

## File layout

Mirrors the per-integration pattern already in `src/` (see `sf_*`, `ns_*`, `pardot_*`):

```
src/
  airtable_client.py        # pyairtable wrapper, singleton with retry/backoff
  airtable_tools.py         # @mcp.tool() read defs, calls register_tools(mcp)
  airtable_write_tools.py   # @mcp.tool() write defs, gated via AUTH_LEVEL
  airtable_schema.py        # Curated SCHEMA dict for known bases; resource handlers
```

Wire up in `server.py` alongside the other integrations:

```python
from src.airtable_tools import register_tools as register_airtable_tools
from src.airtable_write_tools import register_tools as register_airtable_write_tools
register_airtable_tools(mcp)
register_airtable_write_tools(mcp)

from src.airtable_schema import SCHEMA as AIRTABLE_SCHEMA

@mcp.resource("schema://airtable")
def airtable_schema_resource() -> str:
    return json.dumps(AIRTABLE_SCHEMA, indent=2)

@mcp.resource("schema://airtable/{base_id}/{table_name}")
def airtable_table_resource(base_id: str, table_name: str) -> str:
    # look up in curated SCHEMA, else return error pointing to airtable_get_table_schema tool
    ...
```

## Env vars

- `AIRTABLE_TOKEN` — Personal Access Token (PAT), scoped to specific bases via the
  Airtable UI when generated
- `AIRTABLE_BASE_IDS` — comma-separated allowlist of base IDs the server may touch.
  `airtable_client` rejects calls to anything outside this list even if the PAT could
  reach it. Layer-2 defense.
- `AIRTABLE_TIMEOUT` — default 30s

Add to `pyproject.toml`:
```toml
"pyairtable>=2.3,<3",
```

## Tool list

Read (no token gate):
- `airtable_list_bases` → list all bases the PAT can see (filtered through `AIRTABLE_BASE_IDS`)
- `airtable_list_tables(base_id)` → list tables in a base
- `airtable_get_table_schema(base_id, table_id_or_name)` → field types, names, options.
  This is the runtime-discovery fallback Claude uses when a table isn't in the curated schema.
- `airtable_list_records(base_id, table, view=None, max_records=100, fields=None)` →
  paginated records
- `airtable_search_records(base_id, table, filter_formula, max_records=100)` →
  `filterByFormula` queries, e.g. `SEARCH(LOWER("smith"), LOWER({Name}))`
- `airtable_get_record(base_id, table, record_id)` → single record by ID

Write (gated, calls `require_write_access()`):
- `airtable_create_record(base_id, table, fields)`
- `airtable_update_record(base_id, table, record_id, fields)` (PATCH by default,
  `overwrite=True` flag for PUT)
- `airtable_delete_record(base_id, table, record_id)`
- `airtable_bulk_upsert(base_id, table, records, merge_on)` — batches of up to 10 per
  Airtable API limit

## Hybrid schema strategy

`airtable_schema.py` exports a `SCHEMA` dict in the same shape as `sf_schema.py` /
`ns_schema.py`. Curated entries for the bases Parke actually uses today (filled in
once we have access). For everything else, the system prompt in Sabueso tells Claude:
"If a table isn't in `schema://airtable`, call `airtable_get_table_schema` first before
querying."

Shape:
```python
SCHEMA = {
    "appXXXXXXX": {  # base ID
        "name": "Client Data",
        "tables": {
            "Clients": {
                "fields": [
                    {"name": "Name", "type": "singleLineText"},
                    {"name": "Email", "type": "email"},
                    {"name": "Notes", "type": "multilineText"},
                    # ...
                ],
                "primary_field": "Name",
                "example_queries": [
                    'SEARCH(LOWER("smith"), LOWER({Name}))',
                ],
            },
        },
    },
}
```

## Sabueso-side changes

In `~/workspace/Sabueso`:

1. Add Airtable tool definitions to `src/tools_catalog.py`. The tool names map 1:1 to
   the MCP tool names above. No special `WRITE_OPERATIONS` change since the agent loop
   already blocks write tools by name pattern — make sure the new write tools land in
   that set.

2. Add an Airtable section to `SYSTEM_PROMPT` in `src/nlp.py`:
   - When to reach for Airtable vs Salesforce ("Airtable holds team-curated client
     notes that aren't in Salesforce yet")
   - Schema discovery instruction (curated first, fall back to `airtable_get_table_schema`)
   - Hint about the email column being the common join key (if true — confirm with Parke)

3. Nothing changes in `mcp_client.py` — Agent B is still one MCP server, new tools just
   appear in `tools/list` after the server restarts.

## Open questions for Parke

- Which Airtable base(s) hold the relevant client data? Need base IDs.
- Within those bases, which tables are the high-value ones? (Curate those in
  `airtable_schema.py`.)
- What's the join key to Salesforce? Email is the obvious candidate but may need
  confirmation.
- Are there any tables we should *block* the bot from reading (HR, financials in
  Airtable, etc.)? If so we add a table-level allowlist beyond the base-level one.
- Read-only or read-write? Suggest read-only at first; write requires more thought
  on confirmation UX.

## Rollout order

1. Get Airtable access + PAT + base IDs from Parke
2. Curate schema in `airtable_schema.py` for the bases that matter
3. Implement client + read tools + write tools in Agent B
4. Deploy Agent B to Railway (env vars set, restart)
5. Update Sabueso `tools_catalog.py` + `nlp.py` system prompt
6. Deploy Sabueso to Railway
7. Test in Slack with a known guest

## Files to create / edit

Agent B:
- new: `src/airtable_client.py`, `src/airtable_tools.py`, `src/airtable_write_tools.py`,
  `src/airtable_schema.py`
- edit: `src/server.py`, `pyproject.toml`, `CLAUDE.md`

Sabueso:
- edit: `src/tools_catalog.py`, `src/nlp.py`
