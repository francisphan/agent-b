"""Salesforce write MCP tool definitions (create, update, delete, upsert)."""

from src.auth import require_write_access
from src.sf_client import (
    create_record,
    update_record,
    delete_record,
    upsert_record,
    bulk_operation,
)


def register_tools(mcp):
    """Register all Salesforce write tools on the given FastMCP instance."""

    @mcp.tool()
    def sf_create_record(object_name: str, data: dict) -> dict:
        """Create a new Salesforce record.

        Args:
            object_name: API name of the SObject (e.g. "Account", "TVRS_Guest__c").
            data: A dict of field name -> value pairs to set on the new record.

        Returns:
            The creation result dict (includes id, success, errors), or an error dict on failure.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}
        try:
            return create_record(object_name, data)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def sf_update_record(object_name: str, record_id: str, data: dict) -> dict:
        """Update an existing Salesforce record by ID.

        Args:
            object_name: API name of the SObject (e.g. "Account", "TVRS_Guest__c").
            record_id: The 15 or 18-character Salesforce record ID.
            data: A dict of field name -> value pairs to update on the record.

        Returns:
            The update result dict, or an error dict on failure.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}
        try:
            return update_record(object_name, record_id, data)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def sf_delete_record(object_name: str, record_id: str) -> dict:
        """Delete a Salesforce record by ID.

        Args:
            object_name: API name of the SObject (e.g. "Account", "TVRS_Guest__c").
            record_id: The 15 or 18-character Salesforce record ID.

        Returns:
            The deletion result dict, or an error dict on failure.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}
        try:
            return delete_record(object_name, record_id)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def sf_upsert_record(
        object_name: str, external_id_field: str, external_id: str, data: dict
    ) -> dict:
        """Upsert a Salesforce record using an external ID field.

        Inserts a new record if no match is found for the external ID, or updates
        the existing record if a match exists.

        Args:
            object_name: API name of the SObject (e.g. "Account", "TVRS_Guest__c").
            external_id_field: API name of the external ID field (e.g. "Email__c").
            external_id: The external ID value to match on.
            data: A dict of field name -> value pairs to set on the record.

        Returns:
            The upsert result dict, or an error dict on failure.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}
        try:
            return upsert_record(object_name, external_id_field, external_id, data)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def sf_bulk_operation(
        object_name: str,
        operation: str,
        records: list[dict],
        external_id_field: str = "",
        batch_size: int = 10000,
    ) -> dict:
        """Perform bulk Salesforce operations for large data volumes.

        Uses the Salesforce Bulk API for efficient processing of hundreds
        or thousands of records in a single call.

        Record format by operation:
        - insert: [{"Name": "Acme", "Industry": "Tech"}, ...] — no Id field needed
        - update: [{"Id": "001xx...", "Name": "New Name"}, ...] — Id required
        - upsert: [{"Email__c": "a@b.com", "Name": "X"}, ...] — external ID field value in each record
        - delete: [{"Id": "001xx..."}, ...] — only Id needed

        Args:
            object_name: API name of the SObject (e.g. "Account", "Contact").
            operation: One of "insert", "update", "upsert", "delete" (case-insensitive).
            records: List of record dicts to process (see format above).
            external_id_field: Required for upsert — the external ID field name (e.g. "Email__c").
            batch_size: Records per batch (default 10000, max 10000).

        Returns:
            A dict with 'success', 'results' (per-record outcomes), and 'count'.
        """
        try:
            require_write_access()
        except PermissionError as e:
            return {"error": str(e)}

        operation = operation.lower().strip()
        allowed_ops = ("insert", "update", "upsert", "delete")
        if operation not in allowed_ops:
            return {
                "error": f"Invalid operation '{operation}'. Must be one of: {', '.join(allowed_ops)}"
            }

        if operation == "upsert" and not external_id_field:
            return {"error": "external_id_field is required for upsert operations."}

        if not records:
            return {"error": "records list is empty."}

        MAX_BULK_RECORDS = 50000
        if len(records) > MAX_BULK_RECORDS:
            return {
                "error": f"Too many records ({len(records)}). "
                f"Maximum is {MAX_BULK_RECORDS} per call. Split into smaller batches."
            }

        capped_batch = min(max(batch_size, 1), 10000)

        try:
            results = bulk_operation(
                object_name,
                operation,
                records,
                external_id_field=external_id_field or None,
                batch_size=capped_batch,
            )
            return {"success": True, "results": results, "count": len(results)}
        except Exception as e:
            return {"error": str(e)}
