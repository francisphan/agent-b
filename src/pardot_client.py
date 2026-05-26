"""Pardot (Account Engagement) API v5 client, reusing Salesforce OAuth token."""

import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

from src.sf_client import (
    get_client as get_sf_client,
    _reconnect as sf_reconnect,
    _sf_holder,
)

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://pi.pardot.com/api/v5/objects"
BASE_URL_V5 = "https://pi.pardot.com/api/v5"

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry

# Cap how much of an error body we write to the logs — never log an unbounded
# response (it may be large or carry PII).
_MAX_LOG_BODY = 2000

_pardot_session: list[requests.Session | None] = [None]


def _clip(value: object) -> str:
    """Render a value for logging, truncated to a safe length."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_LOG_BODY:
        return f"{text[:_MAX_LOG_BODY]}… (+{len(text) - _MAX_LOG_BODY} chars)"
    return text


def _log_pardot_error(exc: Exception, will_retry: bool) -> None:
    """Log a failed Pardot request with status, URL, and the error body.

    When the exception is a ``requests.HTTPError`` we pull status/url/body off
    the response; otherwise (e.g. the manual ``raise`` in ``_post``/``_patch``,
    whose message already embeds the status and body) we log its repr. WARNING
    when the call will be retried, ERROR when it propagates to the caller.
    """
    level = logging.WARNING if will_retry else logging.ERROR
    note = " (will retry)" if will_retry else ""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = _clip(resp.text)
        except Exception:  # pragma: no cover - body not readable
            body = "<unreadable>"
        logger.log(
            level,
            "Pardot request failed: status=%s url=%s%s | response=%s",
            resp.status_code,
            resp.url,
            note,
            body,
        )
    else:
        logger.log(level, "Pardot request failed%s: %s", note, _clip(repr(exc)))

# Pattern for safe path segments (each part between slashes in endpoint strings).
# Allows alphanumeric, hyphens, dots, underscores — no traversal.
_SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._@+%-]+$")


def _validate_endpoint(endpoint: str) -> str:
    """Validate that an endpoint path has no traversal or injection.

    Each segment between slashes must match a safe pattern.
    Raises ValueError on suspicious input.
    """
    for segment in endpoint.split("/"):
        if not segment:
            continue  # leading/trailing/double slashes are harmless
        if ".." in segment or not _SAFE_SEGMENT_RE.match(segment):
            raise ValueError(f"Invalid endpoint segment: {segment!r} in {endpoint!r}")
    return endpoint


def _get_business_unit_id() -> str:
    buid = os.environ.get("PARDOT_BUSINESS_UNIT_ID")
    if not buid:
        raise RuntimeError("PARDOT_BUSINESS_UNIT_ID environment variable is required.")
    return buid


def _get_access_token() -> str:
    """Get the current Salesforce access token for Pardot API auth."""
    sf = get_sf_client()
    return sf.session_id


def _build_session() -> requests.Session:
    """Create a requests.Session pre-configured with Pardot auth headers."""
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {_get_access_token()}",
            "Pardot-Business-Unit-Id": _get_business_unit_id(),
            "Content-Type": "application/json",
        }
    )
    return session


def get_session() -> requests.Session:
    """Return a configured Pardot session (singleton — rebuilds if needed)."""
    if _pardot_session[0] is None:
        _pardot_session[0] = _build_session()
    return _pardot_session[0]


def _refresh_session():
    """Re-authenticate Salesforce and rebuild the Pardot session."""
    _sf_holder[0] = sf_reconnect()
    _pardot_session[0] = _build_session()


def _with_retry(func):
    """Execute func(session) with retry on transient errors and re-auth on 401."""
    session = get_session()
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(session)
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                status = e.response.status_code
                if status == 401:
                    _refresh_session()
                    session = get_session()
                    continue
                # Don't retry client errors (4xx) — they won't succeed on retry
                if 400 <= status < 500:
                    _log_pardot_error(e, will_retry=False)
                    raise
            last_exc = e
            _log_pardot_error(e, will_retry=attempt < MAX_RETRIES - 1)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
            continue
        except Exception as e:
            last_exc = e
            _log_pardot_error(e, will_retry=attempt < MAX_RETRIES - 1)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
            continue
    raise last_exc


def _get(endpoint: str, params: dict | None = None) -> dict | list:
    """GET helper that returns parsed JSON with retry."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.get(f"{BASE_URL}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    return _with_retry(_do)


# --- Prospects ---


def query_prospects(params: dict | None = None) -> dict:
    """GET /prospects with optional filter params."""
    return _get("prospects", params=params)


def get_prospect(prospect_id: str, fields: str | None = None) -> dict:
    """GET /prospects/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"prospects/{prospect_id}", params=params)


# --- Lists ---


def query_lists(params: dict | None = None) -> dict:
    """GET /lists with optional filter params."""
    return _get("lists", params=params)


def get_list(list_id: str, fields: str | None = None) -> dict:
    """GET /lists/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"lists/{list_id}", params=params)


# --- List Memberships ---


def query_list_memberships(params: dict | None = None) -> dict:
    """GET /list-memberships with optional filter params."""
    return _get("list-memberships", params=params)


def get_list_membership(membership_id: str, fields: str | None = None) -> dict:
    """GET /list-memberships/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"list-memberships/{membership_id}", params=params)


# --- Campaigns ---


def query_campaigns(params: dict | None = None) -> dict:
    """GET /campaigns with optional filter params (read-only)."""
    return _get("campaigns", params=params)


def get_campaign(campaign_id: str, fields: str | None = None) -> dict:
    """GET /campaigns/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"campaigns/{campaign_id}", params=params)


# --- Visitor Activities ---


def query_visitor_activities(params: dict | None = None) -> dict:
    """GET /visitor-activities with optional filter params."""
    return _get("visitor-activities", params=params)


# --- Forms ---


def query_forms(params: dict | None = None) -> dict:
    """GET /forms with optional filter params."""
    return _get("forms", params=params)


def get_form(form_id: str, fields: str | None = None) -> dict:
    """GET /forms/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"forms/{form_id}", params=params)


# --- Emails ---


def query_emails(params: dict | None = None) -> dict:
    """GET /emails with optional filter params."""
    return _get("emails", params=params)


def get_email(email_id: str, fields: str | None = None) -> dict:
    """GET /emails/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"emails/{email_id}", params=params)


# --- List Emails ---


def query_list_emails(params: dict | None = None) -> dict:
    """GET /list-emails with optional filter params."""
    return _get("list-emails", params=params)


def get_list_email(list_email_id: str, fields: str | None = None) -> dict:
    """GET /list-emails/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"list-emails/{list_email_id}", params=params)


# --- Custom Fields ---


def query_custom_fields(params: dict | None = None) -> dict:
    """GET /custom-fields with optional filter params."""
    return _get("custom-fields", params=params)


def get_custom_field(field_id: str, fields: str | None = None) -> dict:
    """GET /custom-fields/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"custom-fields/{field_id}", params=params)


# --- Tags ---


def query_tags(params: dict | None = None) -> dict:
    """GET /tags with optional filter params."""
    return _get("tags", params=params)


def get_tag(tag_id: str, fields: str | None = None) -> dict:
    """GET /tags/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"tags/{tag_id}", params=params)


# --- Tagged Objects ---


def query_tagged_objects(params: dict | None = None) -> dict:
    """GET /tagged-objects with optional filter params."""
    return _get("tagged-objects", params=params)


def get_tagged_object(tagged_object_id: str, fields: str | None = None) -> dict:
    """GET /tagged-objects/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"tagged-objects/{tagged_object_id}", params=params)


# --- Tracker Domains ---


def query_tracker_domains(params: dict | None = None) -> dict:
    """GET /tracker-domains with optional filter params."""
    return _get("tracker-domains", params=params)


# --- Email Templates ---


def query_email_templates(params: dict | None = None) -> dict:
    """GET /email-templates with optional filter params."""
    return _get("email-templates", params=params)


def get_email_template(template_id: str, fields: str | None = None) -> dict:
    """GET /email-templates/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"email-templates/{template_id}", params=params)


# --- Engagement Studio Programs ---


def query_engagement_studio_programs(params: dict | None = None) -> dict:
    """GET /engagement-studio-programs with optional filter params."""
    return _get("engagement-studio-programs", params=params)


def get_engagement_studio_program(program_id: str, fields: str | None = None) -> dict:
    """GET /engagement-studio-programs/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"engagement-studio-programs/{program_id}", params=params)


def download_engagement_studio_program_structure(program_id: str) -> bytes:
    """GET /engagement-studio-programs/{id}/download-program-structure.

    Returns the raw bytes of the structure file (not JSON).
    """
    endpoint = f"engagement-studio-programs/{program_id}/download-program-structure"
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.get(
            f"{BASE_URL}/{endpoint}",
            headers={"Accept": "application/octet-stream"},
        )
        resp.raise_for_status()
        return resp.content

    return _with_retry(_do)


def create_engagement_studio_program(
    structure_file: bytes, name: str, recipient_list_id: str
) -> dict:
    """POST /engagement-studio-programs with multipart form data.

    Args:
        structure_file: Raw bytes of the program structure file.
        name: Name for the new program.
        recipient_list_id: The list ID to use as recipients.

    Returns:
        The created program as a dict.
    """

    def _do(session):
        # Multipart upload — temporarily remove Content-Type so requests sets multipart boundary
        original_ct = session.headers.pop("Content-Type", None)
        try:
            resp = session.post(
                f"{BASE_URL}/engagement-studio-programs",
                files={
                    "programStructureFile": (
                        "program.json",
                        structure_file,
                        "application/json",
                    )
                },
                data={"name": name, "recipientListId": recipient_list_id},
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if original_ct:
                session.headers["Content-Type"] = original_ct

    return _with_retry(_do)


# --- Write Helpers ---


def _post(endpoint: str, body: dict) -> dict:
    """POST helper that returns parsed JSON with retry."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.post(f"{BASE_URL}/{endpoint}", json=body)
        if not resp.ok:
            error_body = resp.text[:2000] if resp.text else "(empty)"
            raise Exception(f"{resp.status_code} {resp.reason}: {error_body}")
        return resp.json()

    return _with_retry(_do)


def _patch(endpoint: str, body: dict) -> dict:
    """PATCH helper that returns parsed JSON with retry."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.patch(f"{BASE_URL}/{endpoint}", json=body)
        if not resp.ok:
            error_body = resp.text[:2000] if resp.text else "(empty)"
            raise Exception(f"{resp.status_code} {resp.reason}: {error_body}")
        if resp.status_code == 204 or not resp.content or not resp.text.strip():
            return {"success": True}
        return resp.json()

    return _with_retry(_do)


def _delete(endpoint: str) -> dict:
    """DELETE helper that returns parsed JSON (or success flag on 204) with retry."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.delete(f"{BASE_URL}/{endpoint}")
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content or not resp.text.strip():
            return {"success": True}
        return resp.json()

    return _with_retry(_do)


# --- Prospect Write Operations ---


def create_prospect(data: dict) -> dict:
    """Create a new Pardot prospect.

    Args:
        data: Dict of prospect fields (e.g. email, firstName, lastName).

    Returns:
        The created prospect as a dict.
    """
    return _post("prospects", data)


def create_email_template(data: dict) -> dict:
    """Create a new Pardot email template.

    Args:
        data: Dict of email template fields (e.g. name, subject, htmlMessage, folderId).
        HML merge tags in htmlMessage will be auto-converted to Pardot variable tags.

    Returns:
        The created email template as a dict.
    """
    if "htmlMessage" in data:
        data = dict(data)
        data["htmlMessage"] = _convert_hml_to_pardot_tags(data["htmlMessage"])
    return _post("email-templates", data)


def update_prospect(prospect_id: str, data: dict) -> dict:
    """Update an existing Pardot prospect.

    Args:
        prospect_id: The Pardot prospect ID.
        data: Dict of fields to update.

    Returns:
        The updated prospect as a dict.
    """
    return _patch(f"prospects/{prospect_id}", data)


def delete_prospect(prospect_id: str) -> dict:
    """Delete a Pardot prospect by ID.

    Args:
        prospect_id: The Pardot prospect ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"prospects/{prospect_id}")


def upsert_prospect_by_email(data: dict) -> dict:
    """Upsert a prospect by email — creates or updates the most recent match.

    Args:
        data: Dict of prospect fields; must include 'email'.

    Returns:
        The created or updated prospect as a dict.
    """
    return _post("prospects/do/upsertLatestByEmail", data)


def undelete_prospect(prospect_id: str) -> dict:
    """Restore a previously deleted Pardot prospect.

    Args:
        prospect_id: The Pardot prospect ID to restore.

    Returns:
        The restored prospect as a dict.
    """
    return _post(f"prospects/{prospect_id}/do/undelete", {})


# --- List Write Operations ---


def create_list(data: dict) -> dict:
    """Create a new Pardot list.

    Args:
        data: Dict of list fields (e.g. name, title, description).

    Returns:
        The created list as a dict.
    """
    return _post("lists", data)


def update_list(list_id: str, data: dict) -> dict:
    """Update an existing Pardot list.

    Args:
        list_id: The Pardot list ID.
        data: Dict of fields to update.

    Returns:
        The updated list as a dict.
    """
    return _patch(f"lists/{list_id}", data)


def delete_list(list_id: str) -> dict:
    """Delete a Pardot list by ID.

    Args:
        list_id: The Pardot list ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"lists/{list_id}")


# --- List Membership Write Operations ---


def create_list_membership(data: dict) -> dict:
    """Create a new list membership.

    Args:
        data: Dict with listId and prospectId.

    Returns:
        The created membership as a dict.
    """
    return _post("list-memberships", data)


def update_list_membership(membership_id: str, data: dict) -> dict:
    """Update an existing list membership.

    Args:
        membership_id: The list membership ID.
        data: Dict of fields to update.

    Returns:
        The updated membership as a dict.
    """
    return _patch(f"list-memberships/{membership_id}", data)


def delete_list_membership(membership_id: str) -> dict:
    """Delete a list membership by ID.

    Args:
        membership_id: The list membership ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"list-memberships/{membership_id}")


# --- Email Write Operations ---


def create_email(data: dict) -> dict:
    """Send a one-to-one Pardot email.

    Args:
        data: Dict with prospectId, emailTemplateId, campaignId, etc.

    Returns:
        The created email as a dict.
    """
    return _post("emails", data)


def create_list_email(data: dict) -> dict:
    """Send a Pardot email to a list.

    Args:
        data: Dict with name, listId, emailTemplateId, campaignId, etc.

    Returns:
        The created list email as a dict.
    """
    return _post("list-emails", data)


# --- Email Template Write Operations ---


def _convert_hml_to_pardot_tags(html: str) -> str:
    """Convert Handlebars (HML) merge tags to legacy Pardot %%variable%% tags.

    The Pardot v5 API requires legacy variable tags even though the UI supports HML.
    Conditional blocks ({{#if}}/{{#unless}}) are NOT supported by the Pardot v5 API
    in any PML syntax, so we collapse the if-branch and strip the unless-branch,
    replacing {{Recipient.FirstName}} with %%first_name%%.
    """
    import re
    # Unsubscribe / preference center
    html = html.replace("{{EmailPreferenceCenter}}", "%%email_preference_center%%")
    html = html.replace("{{UnsubscribeLink}}", "%%unsubscribe%%")
    # Conditional first name: {{#if Recipient.FirstName}}...{{/if}}{{#unless Recipient.FirstName}}...{{/unless}}
    # Pardot v5 API rejects all conditional PML syntax (%%if:%%, %%#if:%%, etc.).
    # Collapse to just the if-branch content; the unless-branch is dropped.
    # {{Recipient.FirstName}} inside the kept branch is converted to %%first_name%% below.
    html = re.sub(
        r'\{\{#if Recipient\.FirstName\}\}(.*?)\{\{/if\}\}\{\{#unless Recipient\.FirstName\}\}(.*?)\{\{/unless\}\}',
        r'\1',
        html,
        flags=re.DOTALL,
    )
    # Simple field references
    html = html.replace("{{Recipient.FirstName}}", "%%first_name%%")
    html = html.replace("{{Recipient.LastName}}", "%%last_name%%")
    html = html.replace("{{Recipient.Email}}", "%%email%%")
    html = html.replace("{{Recipient.Company}}", "%%company%%")
    return html


def update_email_template(template_id: str, data: dict) -> dict:
    """Update an existing Pardot email template.

    Args:
        template_id: The email template ID.
        data: Dict of fields to update (htmlMessage will auto-convert HML to Pardot tags).

    Returns:
        The updated email template as a dict.
    """
    if "htmlMessage" in data:
        data = dict(data)  # Don't mutate the original
        data["htmlMessage"] = _convert_hml_to_pardot_tags(data["htmlMessage"])
    return _patch(f"email-templates/{template_id}", data)


def delete_email_template(template_id: str) -> dict:
    """Delete a Pardot email template by ID.

    Args:
        template_id: The email template ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"email-templates/{template_id}")


# --- Custom Field Write Operations ---


def create_custom_field(data: dict) -> dict:
    """Create a new Pardot custom field.

    Args:
        data: Dict of custom field properties (e.g. name, fieldId, type).

    Returns:
        The created custom field as a dict.
    """
    return _post("custom-fields", data)


def update_custom_field(field_id: str, data: dict) -> dict:
    """Update an existing Pardot custom field.

    Args:
        field_id: The custom field ID.
        data: Dict of fields to update.

    Returns:
        The updated custom field as a dict.
    """
    return _patch(f"custom-fields/{field_id}", data)


def delete_custom_field(field_id: str) -> dict:
    """Delete a Pardot custom field by ID.

    Args:
        field_id: The custom field ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"custom-fields/{field_id}")


# --- Tag Write Operations ---


def create_tag(data: dict) -> dict:
    """Create a new Pardot tag.

    Args:
        data: Dict with tag properties (e.g. name).

    Returns:
        The created tag as a dict.
    """
    return _post("tags", data)


def update_tag(tag_id: str, data: dict) -> dict:
    """Update an existing Pardot tag.

    Args:
        tag_id: The tag ID.
        data: Dict of fields to update.

    Returns:
        The updated tag as a dict.
    """
    return _patch(f"tags/{tag_id}", data)


def delete_tag(tag_id: str) -> dict:
    """Delete a Pardot tag by ID.

    Args:
        tag_id: The tag ID.

    Returns:
        A success dict, or raises on failure.
    """
    return _delete(f"tags/{tag_id}")


# ---------------------------------------------------------------------------
# Helpers for v5 endpoints NOT under /objects/ (exports, imports, external activities)
# ---------------------------------------------------------------------------


def _get_v5(endpoint: str, params: dict | None = None) -> dict | list:
    """GET helper for /api/v5/ endpoints (not under /objects/)."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.get(f"{BASE_URL_V5}/{endpoint}", params=params)
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise RuntimeError(f"{resp.status_code} {resp.reason}: {detail}")
        return resp.json()

    return _with_retry(_do)


def _post_v5(endpoint: str, body: dict) -> dict:
    """POST helper for /api/v5/ endpoints (not under /objects/)."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.post(f"{BASE_URL_V5}/{endpoint}", json=body)
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise RuntimeError(f"{resp.status_code} {resp.reason}: {detail}")
        return resp.json()

    return _with_retry(_do)


def _patch_v5(endpoint: str, body: dict) -> dict:
    """PATCH helper for /api/v5/ endpoints (not under /objects/)."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.patch(f"{BASE_URL_V5}/{endpoint}", json=body)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content or not resp.text.strip():
            return {"success": True}
        return resp.json()

    return _with_retry(_do)


def _get_raw_v5(endpoint: str) -> bytes:
    """GET helper that returns raw bytes (for CSV downloads etc.)."""
    _validate_endpoint(endpoint)

    def _do(session):
        resp = session.get(f"{BASE_URL_V5}/{endpoint}")
        resp.raise_for_status()
        return resp.content

    return _with_retry(_do)


# ---------------------------------------------------------------------------
# Phase 1: Missing operations on existing objects
# ---------------------------------------------------------------------------


def get_visitor_activity(activity_id: str, fields: str | None = None) -> dict:
    """GET /visitor-activities/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"visitor-activities/{activity_id}", params=params)


def get_tracker_domain(domain_id: str, fields: str | None = None) -> dict:
    """GET /tracker-domains/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"tracker-domains/{domain_id}", params=params)


def get_list_email_stats(list_email_id: str) -> dict:
    """GET /list-emails/{id}/stats."""
    return _get(f"list-emails/{list_email_id}/stats")


# ---------------------------------------------------------------------------
# Phase 2: New read-only objects
# ---------------------------------------------------------------------------

# --- Visitors ---


def query_visitors(params: dict | None = None) -> dict:
    """GET /visitors with optional filter params."""
    return _get("visitors", params=params)


def get_visitor(visitor_id: str, fields: str | None = None) -> dict:
    """GET /visitors/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"visitors/{visitor_id}", params=params)


# --- Visits ---


def query_visits(params: dict | None = None) -> dict:
    """GET /visits with optional filter params."""
    return _get("visits", params=params)


def get_visit(visit_id: str, fields: str | None = None) -> dict:
    """GET /visits/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"visits/{visit_id}", params=params)


# --- Prospect Accounts ---


def query_prospect_accounts(params: dict | None = None) -> dict:
    """GET /prospect-accounts with optional filter params."""
    return _get("prospect-accounts", params=params)


def get_prospect_account(account_id: str, fields: str | None = None) -> dict:
    """GET /prospect-accounts/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"prospect-accounts/{account_id}", params=params)


# --- Opportunities ---


def query_opportunities(params: dict | None = None) -> dict:
    """GET /opportunities with optional filter params."""
    return _get("opportunities", params=params)


def get_opportunity(opportunity_id: str, fields: str | None = None) -> dict:
    """GET /opportunities/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"opportunities/{opportunity_id}", params=params)


# --- Lifecycle Stages ---


def query_lifecycle_stages(params: dict | None = None) -> dict:
    """GET /lifecycle-stages with optional filter params."""
    return _get("lifecycle-stages", params=params)


def get_lifecycle_stage(stage_id: str, fields: str | None = None) -> dict:
    """GET /lifecycle-stages/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"lifecycle-stages/{stage_id}", params=params)


# --- Lifecycle Histories ---


def query_lifecycle_histories(params: dict | None = None) -> dict:
    """GET /lifecycle-histories with optional filter params."""
    return _get("lifecycle-histories", params=params)


def get_lifecycle_history(history_id: str, fields: str | None = None) -> dict:
    """GET /lifecycle-histories/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"lifecycle-histories/{history_id}", params=params)


# --- Users ---


def query_users(params: dict | None = None) -> dict:
    """GET /users with optional filter params."""
    return _get("users", params=params)


def get_user(user_id: str, fields: str | None = None) -> dict:
    """GET /users/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"users/{user_id}", params=params)


# --- Account (singleton) ---


def get_account(fields: str | None = None) -> dict:
    """GET /account (singleton — returns the Pardot account record)."""
    params = {"fields": fields} if fields else None
    return _get("account", params=params)


# --- Folders ---


def query_folders(params: dict | None = None) -> dict:
    """GET /folders with optional filter params."""
    return _get("folders", params=params)


def get_folder(folder_id: str, fields: str | None = None) -> dict:
    """GET /folders/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"folders/{folder_id}", params=params)


# --- Folder Contents ---


def query_folder_contents(params: dict | None = None) -> dict:
    """GET /folder-contents with optional filter params."""
    return _get("folder-contents", params=params)


# ---------------------------------------------------------------------------
# Phase 3: New CRUD objects
# ---------------------------------------------------------------------------

# --- Custom Redirects ---


def query_custom_redirects(params: dict | None = None) -> dict:
    """GET /custom-redirects with optional filter params."""
    return _get("custom-redirects", params=params)


def get_custom_redirect(redirect_id: str, fields: str | None = None) -> dict:
    """GET /custom-redirects/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"custom-redirects/{redirect_id}", params=params)


def create_custom_redirect(data: dict) -> dict:
    """POST /custom-redirects."""
    return _post("custom-redirects", data)


def update_custom_redirect(redirect_id: str, data: dict) -> dict:
    """PATCH /custom-redirects/{id}."""
    return _patch(f"custom-redirects/{redirect_id}", data)


def delete_custom_redirect(redirect_id: str) -> dict:
    """DELETE /custom-redirects/{id}."""
    return _delete(f"custom-redirects/{redirect_id}")


# --- Form Handlers ---


def query_form_handlers(params: dict | None = None) -> dict:
    """GET /form-handlers with optional filter params."""
    return _get("form-handlers", params=params)


def get_form_handler(handler_id: str, fields: str | None = None) -> dict:
    """GET /form-handlers/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"form-handlers/{handler_id}", params=params)


def create_form_handler(data: dict) -> dict:
    """POST /form-handlers."""
    return _post("form-handlers", data)


def update_form_handler(handler_id: str, data: dict) -> dict:
    """PATCH /form-handlers/{id}."""
    return _patch(f"form-handlers/{handler_id}", data)


def delete_form_handler(handler_id: str) -> dict:
    """DELETE /form-handlers/{id}."""
    return _delete(f"form-handlers/{handler_id}")


# --- Form Handler Fields ---


def query_form_handler_fields(params: dict | None = None) -> dict:
    """GET /form-handler-fields with optional filter params."""
    return _get("form-handler-fields", params=params)


def get_form_handler_field(field_id: str, fields: str | None = None) -> dict:
    """GET /form-handler-fields/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"form-handler-fields/{field_id}", params=params)


def create_form_handler_field(data: dict) -> dict:
    """POST /form-handler-fields."""
    return _post("form-handler-fields", data)


def update_form_handler_field(field_id: str, data: dict) -> dict:
    """PATCH /form-handler-fields/{id}."""
    return _patch(f"form-handler-fields/{field_id}", data)


def delete_form_handler_field(field_id: str) -> dict:
    """DELETE /form-handler-fields/{id}."""
    return _delete(f"form-handler-fields/{field_id}")


# --- Layout Templates ---


def query_layout_templates(params: dict | None = None) -> dict:
    """GET /layout-templates with optional filter params."""
    return _get("layout-templates", params=params)


def get_layout_template(template_id: str, fields: str | None = None) -> dict:
    """GET /layout-templates/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"layout-templates/{template_id}", params=params)


def create_layout_template(data: dict) -> dict:
    """POST /layout-templates."""
    return _post("layout-templates", data)


def update_layout_template(template_id: str, data: dict) -> dict:
    """PATCH /layout-templates/{id}."""
    return _patch(f"layout-templates/{template_id}", data)


def delete_layout_template(template_id: str) -> dict:
    """DELETE /layout-templates/{id}."""
    return _delete(f"layout-templates/{template_id}")


# --- Files ---


def query_files(params: dict | None = None) -> dict:
    """GET /files with optional filter params."""
    return _get("files", params=params)


def get_file(file_id: str, fields: str | None = None) -> dict:
    """GET /files/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"files/{file_id}", params=params)


def create_file(
    name: str,
    folder_id: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    """POST /files (multipart upload)."""

    def _do(session):
        original_ct = session.headers.pop("Content-Type", None)
        try:
            resp = session.post(
                f"{BASE_URL}/files",
                files={"file": (name, file_bytes, content_type)},
                data={"name": name, "folderId": folder_id},
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if original_ct:
                session.headers["Content-Type"] = original_ct

    return _with_retry(_do)


def update_file(file_id: str, data: dict) -> dict:
    """PATCH /files/{id}."""
    return _patch(f"files/{file_id}", data)


def delete_file(file_id: str) -> dict:
    """DELETE /files/{id}."""
    return _delete(f"files/{file_id}")


# --- Landing Pages ---


def query_landing_pages(params: dict | None = None) -> dict:
    """GET /landing-pages with optional filter params."""
    return _get("landing-pages", params=params)


def get_landing_page(page_id: str, fields: str | None = None) -> dict:
    """GET /landing-pages/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"landing-pages/{page_id}", params=params)


def create_landing_page(data: dict) -> dict:
    """POST /landing-pages."""
    return _post("landing-pages", data)


# --- Dynamic Content ---


def query_dynamic_contents(params: dict | None = None) -> dict:
    """GET /dynamic-contents with optional filter params."""
    return _get("dynamic-contents", params=params)


def get_dynamic_content(content_id: str, fields: str | None = None) -> dict:
    """GET /dynamic-contents/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"dynamic-contents/{content_id}", params=params)


def create_dynamic_content(data: dict) -> dict:
    """POST /dynamic-contents."""
    return _post("dynamic-contents", data)


# --- Dynamic Content Variations ---


def query_dynamic_content_variations(params: dict | None = None) -> dict:
    """GET /dynamic-content-variations with optional filter params."""
    return _get("dynamic-content-variations", params=params)


def get_dynamic_content_variation(variation_id: str, fields: str | None = None) -> dict:
    """GET /dynamic-content-variations/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"dynamic-content-variations/{variation_id}", params=params)


def create_dynamic_content_variation(data: dict) -> dict:
    """POST /dynamic-content-variations."""
    return _post("dynamic-content-variations", data)


# --- Form Fields ---


def query_form_fields(params: dict | None = None) -> dict:
    """GET /form-fields with optional filter params."""
    return _get("form-fields", params=params)


def get_form_field(field_id: str, fields: str | None = None) -> dict:
    """GET /form-fields/{id}."""
    params = {"fields": fields} if fields else None
    return _get(f"form-fields/{field_id}", params=params)


def create_form_field(data: dict) -> dict:
    """POST /form-fields."""
    return _post("form-fields", data)


# --- Forms Write (reads already exist) ---


def create_form(data: dict) -> dict:
    """POST /forms."""
    return _post("forms", data)


def delete_form(form_id: str) -> dict:
    """DELETE /forms/{id}."""
    return _delete(f"forms/{form_id}")


# ---------------------------------------------------------------------------
# Phase 4: Tag operations (cross-cutting)
# ---------------------------------------------------------------------------

# Map of object types to their API endpoint prefix
TAG_OBJECT_TYPES = {
    "prospects": "prospects",
    "lists": "lists",
    "campaigns": "campaigns",
    "emails": "emails",
    "email-templates": "email-templates",
    "custom-fields": "custom-fields",
    "forms": "forms",
    "form-handlers": "form-handlers",
    "form-fields": "form-fields",
    "files": "files",
    "landing-pages": "landing-pages",
    "dynamic-contents": "dynamic-contents",
    "custom-redirects": "custom-redirects",
    "layout-templates": "layout-templates",
    "users": "users",
}


def add_tag(object_type: str, object_id: str, tag_id: int) -> dict:
    """POST /{object_type}/{id}/do/addTag."""
    endpoint = TAG_OBJECT_TYPES.get(object_type)
    if not endpoint:
        raise ValueError(
            f"Unsupported object type '{object_type}'. "
            f"Valid types: {', '.join(sorted(TAG_OBJECT_TYPES))}"
        )
    return _post(f"{endpoint}/{object_id}/do/addTag", {"tagId": tag_id})


def remove_tag(object_type: str, object_id: str, tag_id: int) -> dict:
    """POST /{object_type}/{id}/do/removeTag."""
    endpoint = TAG_OBJECT_TYPES.get(object_type)
    if not endpoint:
        raise ValueError(
            f"Unsupported object type '{object_type}'. "
            f"Valid types: {', '.join(sorted(TAG_OBJECT_TYPES))}"
        )
    return _post(f"{endpoint}/{object_id}/do/removeTag", {"tagId": tag_id})


# ---------------------------------------------------------------------------
# Phase 5: Special action endpoints
# ---------------------------------------------------------------------------


def assign_visitor_to_prospect(visitor_id: str, prospect_id: str) -> dict:
    """POST /visitors/{id}/do/assignToProspect."""
    return _post(
        f"visitors/{visitor_id}/do/assignToProspect", {"prospectId": prospect_id}
    )


def connect_campaign_to_salesforce(
    campaign_id: str, salesforce_campaign_id: str
) -> dict:
    """POST /campaigns/{id}/do/connectSalesforceCampaign."""
    return _post(
        f"campaigns/{campaign_id}/do/connectSalesforceCampaign",
        {"salesforceCampaignId": salesforce_campaign_id},
    )


def merge_tags(target_tag_id: str, source_tag_ids: list[int]) -> dict:
    """POST /tags/{id}/do/mergeTags — merges source tags into target."""
    return _post(f"tags/{target_tag_id}/do/mergeTags", {"tagIds": source_tag_ids})


def create_external_activity(data: dict) -> dict:
    """POST /external-activities (note: not under /objects/)."""
    return _post_v5("external-activities", data)


def query_external_activities(params: dict | None = None) -> dict:
    """GET /external-activities with optional filter params."""
    return _get_v5("external-activities", params=params)


def get_external_activity(activity_id: str, fields: str | None = None) -> dict:
    """GET /external-activities/{id}."""
    params = {"fields": fields} if fields else None
    return _get_v5(f"external-activities/{activity_id}", params=params)


# ---------------------------------------------------------------------------
# Phase 6: Export API
# ---------------------------------------------------------------------------


def create_export(data: dict) -> dict:
    """POST /exports — create a new export job."""
    return _post_v5("exports", data)


def get_export(export_id: str) -> dict:
    """GET /exports/{id} — check export status."""
    return _get_v5(f"exports/{export_id}")


def query_exports(params: dict | None = None) -> dict:
    """GET /exports — list export jobs."""
    return _get_v5("exports", params=params)


def download_export_results(export_id: str) -> bytes:
    """GET /exports/{id}/results — download CSV results."""
    return _get_raw_v5(f"exports/{export_id}/results")


# ---------------------------------------------------------------------------
# Phase 7: Import API
# ---------------------------------------------------------------------------


def create_import(data: dict) -> dict:
    """POST /imports — create a new import job."""
    return _post_v5("imports", data)


def get_import(import_id: str) -> dict:
    """GET /imports/{id} — check import status."""
    return _get_v5(f"imports/{import_id}")


def query_imports(params: dict | None = None) -> dict:
    """GET /imports — list import jobs."""
    return _get_v5("imports", params=params)


def upload_import_batch(import_id: str, csv_bytes: bytes) -> dict:
    """POST /imports/{id}/batches — upload a CSV batch."""
    endpoint = f"imports/{import_id}/batches"
    _validate_endpoint(endpoint)

    def _do(session):
        original_ct = session.headers.pop("Content-Type", None)
        try:
            resp = session.post(
                f"{BASE_URL_V5}/{endpoint}",
                files={"file": ("batch.csv", csv_bytes, "text/csv")},
            )
            resp.raise_for_status()
            # 204 No Content on success
            return resp.json() if resp.content else {"status": "ok"}
        finally:
            if original_ct:
                session.headers["Content-Type"] = original_ct

    return _with_retry(_do)


def submit_import(import_id: str) -> dict:
    """PATCH /imports/{id} — set status to 'Ready' to begin processing."""
    return _patch_v5(f"imports/{import_id}", {"status": "Ready"})


def download_import_errors(import_id: str) -> bytes:
    """GET /imports/{id}/errors — download error CSV."""
    return _get_raw_v5(f"imports/{import_id}/errors")
