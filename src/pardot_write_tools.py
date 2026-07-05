"""Pardot / Account Engagement write MCP tool definitions."""

import base64

from src.auth import require_write_access
from src.pardot_client import (
    add_tag,
    assign_visitor_to_prospect,
    connect_campaign_to_salesforce,
    create_custom_field,
    create_custom_redirect,
    create_dynamic_content,
    create_dynamic_content_variation,
    create_email,
    create_email_template,
    create_engagement_studio_program,
    create_export,
    create_external_activity,
    create_file,
    create_form,
    create_form_field,
    create_form_handler,
    create_form_handler_field,
    create_import,
    create_landing_page,
    create_layout_template,
    create_list,
    create_list_email,
    create_list_membership,
    create_prospect,
    create_tag,
    delete_custom_field,
    delete_custom_redirect,
    delete_email_template,
    delete_file,
    delete_form,
    delete_form_handler,
    delete_form_handler_field,
    delete_layout_template,
    delete_list,
    delete_list_membership,
    delete_prospect,
    delete_tag,
    merge_tags,
    remove_tag,
    submit_import,
    undelete_prospect,
    update_custom_field,
    update_custom_redirect,
    update_email_template,
    update_file,
    update_form_handler,
    update_form_handler_field,
    update_layout_template,
    update_list,
    update_list_membership,
    update_prospect,
    update_tag,
    upload_import_batch,
    upsert_prospect_by_email,
)


def register_tools(mcp):
    """Register all Pardot write tools on the given FastMCP instance."""

    @mcp.tool()
    def pardot_create_prospect(data: dict) -> dict:
        """Create a new Pardot prospect.

        Args:
            data: Dict of prospect fields (e.g. email, firstName, lastName, company).

        Returns:
            The created prospect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_prospect(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_prospect(prospect_id: str, data: dict) -> dict:
        """Update an existing Pardot prospect by ID.

        Args:
            prospect_id: The Pardot prospect ID to update.
            data: Dict of fields to update (e.g. firstName, lastName, company).

        Returns:
            The updated prospect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_prospect(prospect_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_prospect(prospect_id: str) -> dict:
        """Delete a Pardot prospect by ID.

        Args:
            prospect_id: The Pardot prospect ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_prospect(prospect_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_upsert_prospect(data: dict) -> dict:
        """Upsert a Pardot prospect by email address.

        Creates a new prospect if no match exists, or updates the most recent
        prospect with that email. The data dict must include an 'email' field.

        Args:
            data: Dict of prospect fields (must include 'email', plus any other fields).

        Returns:
            The created or updated prospect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return upsert_prospect_by_email(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_undelete_prospect(prospect_id: str) -> dict:
        """Restore a previously deleted Pardot prospect.

        Args:
            prospect_id: The Pardot prospect ID to restore.

        Returns:
            The restored prospect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return undelete_prospect(prospect_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_create_email_template(data: dict) -> dict:
        """Create a new Pardot email template.

        Args:
            data: Dict of email template fields (e.g. name, subject, htmlMessage, textMessage, folderId).

        Returns:
            The created email template as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_email_template(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_email_template(template_id: str, data: dict) -> dict:
        """Update an existing Pardot email template.

        Args:
            template_id: The email template ID to update.
            data: Dict of fields to update (e.g. name, subject, htmlMessage).

        Returns:
            The updated email template as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_email_template(template_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_email_template(template_id: str) -> dict:
        """Delete a Pardot email template by ID.

        Args:
            template_id: The email template ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_email_template(template_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- List Write ---

    @mcp.tool()
    def pardot_create_list(data: dict) -> dict:
        """Create a new Pardot static list.

        Args:
            data: Dict of list fields (e.g. name, title, description).

        Returns:
            The created list as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_list(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_list(list_id: str, data: dict) -> dict:
        """Update an existing Pardot static list.

        Args:
            list_id: The Pardot list ID to update.
            data: Dict of fields to update (e.g. name, title, description).

        Returns:
            The updated list as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_list(list_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_list(list_id: str) -> dict:
        """Delete a Pardot static list by ID.

        Args:
            list_id: The Pardot list ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_list(list_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- List Membership Write ---

    @mcp.tool()
    def pardot_create_list_membership(data: dict) -> dict:
        """Add a prospect to a Pardot list.

        Args:
            data: Dict with listId and prospectId (required).

        Returns:
            The created membership as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_list_membership(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_list_membership(membership_id: str, data: dict) -> dict:
        """Update an existing Pardot list membership.

        Args:
            membership_id: The list membership ID to update.
            data: Dict of fields to update (e.g. optedOut).

        Returns:
            The updated membership as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_list_membership(membership_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_list_membership(membership_id: str) -> dict:
        """Remove a prospect from a Pardot list.

        Args:
            membership_id: The list membership ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_list_membership(membership_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Email Write ---

    @mcp.tool()
    def pardot_create_email(data: dict) -> dict:
        """Send a one-to-one Pardot email to a single prospect.

        Args:
            data: Dict with prospectId, emailTemplateId, campaignId, and optional fields.

        Returns:
            The created email as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_email(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_create_list_email(data: dict) -> dict:
        """Send a Pardot email to a list of prospects.

        Args:
            data: Dict with name, listId, emailTemplateId, campaignId, and optional fields.

        Returns:
            The created list email as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_list_email(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Custom Field Write ---

    @mcp.tool()
    def pardot_create_custom_field(data: dict) -> dict:
        """Create a new Pardot custom field.

        Args:
            data: Dict of custom field properties (e.g. name, fieldId, type).

        Returns:
            The created custom field as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_custom_field(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_custom_field(field_id: str, data: dict) -> dict:
        """Update an existing Pardot custom field.

        Args:
            field_id: The custom field ID to update.
            data: Dict of fields to update.

        Returns:
            The updated custom field as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_custom_field(field_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_custom_field(field_id: str) -> dict:
        """Delete a Pardot custom field by ID.

        Args:
            field_id: The custom field ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_custom_field(field_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Tag Write ---

    @mcp.tool()
    def pardot_create_tag(data: dict) -> dict:
        """Create a new Pardot tag.

        Args:
            data: Dict with tag properties (e.g. name).

        Returns:
            The created tag as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_tag(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_tag(tag_id: str, data: dict) -> dict:
        """Update an existing Pardot tag.

        Args:
            tag_id: The tag ID to update.
            data: Dict of fields to update (e.g. name).

        Returns:
            The updated tag as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_tag(tag_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_tag(tag_id: str) -> dict:
        """Delete a Pardot tag by ID.

        Args:
            tag_id: The tag ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_tag(tag_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Engagement Studio Program Write ---

    @mcp.tool()
    def pardot_create_engagement_studio_program(
        structure_file_base64: str, name: str, recipient_list_id: str
    ) -> dict:
        """Create a new Pardot Engagement Studio program from a structure file.

        Use pardot_download_engagement_studio_program_structure to obtain the
        structure file from an existing program, then pass it here to clone it.

        Args:
            structure_file_base64: Base64-encoded program structure file content
                (as returned by pardot_download_engagement_studio_program_structure).
            name: Name for the new Engagement Studio program.
            recipient_list_id: The Pardot list ID to use as recipients.

        Returns:
            The created program as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            raw = base64.b64decode(structure_file_base64)
            return create_engagement_studio_program(raw, name, recipient_list_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 3: New CRUD objects (write portion)
    # =======================================================================

    # --- Custom Redirects ---

    @mcp.tool()
    def pardot_create_custom_redirect(data: dict) -> dict:
        """Create a new Pardot custom redirect (tracked link).

        Args:
            data: Dict of custom redirect fields (e.g. name, url, destinationUrl, campaignId, folderId).

        Returns:
            The created custom redirect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_custom_redirect(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_custom_redirect(redirect_id: str, data: dict) -> dict:
        """Update an existing Pardot custom redirect.

        Args:
            redirect_id: The custom redirect ID to update.
            data: Dict of fields to update.

        Returns:
            The updated custom redirect as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_custom_redirect(redirect_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_custom_redirect(redirect_id: str) -> dict:
        """Delete a Pardot custom redirect by ID.

        Args:
            redirect_id: The custom redirect ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_custom_redirect(redirect_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Form Handlers ---

    @mcp.tool()
    def pardot_create_form_handler(data: dict) -> dict:
        """Create a new Pardot form handler.

        Args:
            data: Dict of form handler fields (e.g. name, url, campaignId, folderId).

        Returns:
            The created form handler as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_form_handler(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_form_handler(handler_id: str, data: dict) -> dict:
        """Update an existing Pardot form handler.

        Args:
            handler_id: The form handler ID to update.
            data: Dict of fields to update.

        Returns:
            The updated form handler as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_form_handler(handler_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_form_handler(handler_id: str) -> dict:
        """Delete a Pardot form handler by ID.

        Args:
            handler_id: The form handler ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_form_handler(handler_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Form Handler Fields ---

    @mcp.tool()
    def pardot_create_form_handler_field(data: dict) -> dict:
        """Create a new Pardot form handler field.

        Args:
            data: Dict of field properties (e.g. formHandlerId, prospectFieldId, fieldName).

        Returns:
            The created form handler field as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_form_handler_field(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_form_handler_field(field_id: str, data: dict) -> dict:
        """Update an existing Pardot form handler field.

        Args:
            field_id: The form handler field ID to update.
            data: Dict of fields to update.

        Returns:
            The updated form handler field as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_form_handler_field(field_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_form_handler_field(field_id: str) -> dict:
        """Delete a Pardot form handler field by ID.

        Args:
            field_id: The form handler field ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_form_handler_field(field_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Layout Templates ---

    @mcp.tool()
    def pardot_create_layout_template(data: dict) -> dict:
        """Create a new Pardot layout template.

        Args:
            data: Dict of layout template fields (e.g. name, folderId).

        Returns:
            The created layout template as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_layout_template(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_layout_template(template_id: str, data: dict) -> dict:
        """Update an existing Pardot layout template.

        Args:
            template_id: The layout template ID to update.
            data: Dict of fields to update.

        Returns:
            The updated layout template as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_layout_template(template_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_layout_template(template_id: str) -> dict:
        """Delete a Pardot layout template by ID.

        Args:
            template_id: The layout template ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_layout_template(template_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Files ---

    @mcp.tool()
    def pardot_create_file(
        name: str,
        folder_id: str,
        file_base64: str,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Upload a new Pardot file.

        Args:
            name: File name.
            folder_id: The folder ID to upload into.
            file_base64: Base64-encoded file content.
            content_type: MIME type (default application/octet-stream).

        Returns:
            The created file as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            raw = base64.b64decode(file_base64)
            return create_file(name, folder_id, raw, content_type)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_update_file(file_id: str, data: dict) -> dict:
        """Update an existing Pardot file's metadata.

        Args:
            file_id: The file ID to update.
            data: Dict of fields to update (e.g. name, folderId).

        Returns:
            The updated file as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return update_file(file_id, data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_file(file_id: str) -> dict:
        """Delete a Pardot file by ID.

        Args:
            file_id: The file ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_file(file_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Landing Pages ---

    @mcp.tool()
    def pardot_create_landing_page(data: dict) -> dict:
        """Create a new Pardot landing page.

        Args:
            data: Dict of landing page fields (e.g. name, campaignId, folderId, layoutTemplateId).

        Returns:
            The created landing page as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_landing_page(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Dynamic Content ---

    @mcp.tool()
    def pardot_create_dynamic_content(data: dict) -> dict:
        """Create new Pardot dynamic content.

        Args:
            data: Dict of dynamic content fields (e.g. name, basedOn, folderId).

        Returns:
            The created dynamic content as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_dynamic_content(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Dynamic Content Variations ---

    @mcp.tool()
    def pardot_create_dynamic_content_variation(data: dict) -> dict:
        """Create a new Pardot dynamic content variation.

        Args:
            data: Dict of variation fields (e.g. dynamicContentId, comparison, value, content).

        Returns:
            The created variation as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_dynamic_content_variation(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Form Fields ---

    @mcp.tool()
    def pardot_create_form_field(data: dict) -> dict:
        """Create a new Pardot form field.

        Args:
            data: Dict of form field properties (e.g. formId, prospectFieldId, type).

        Returns:
            The created form field as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_form_field(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # --- Forms Write ---

    @mcp.tool()
    def pardot_create_form(data: dict) -> dict:
        """Create a new Pardot form.

        Args:
            data: Dict of form fields (e.g. name, campaignId, folderId, layoutTemplateId).

        Returns:
            The created form as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_form(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_delete_form(form_id: str) -> dict:
        """Delete a Pardot form by ID.

        Args:
            form_id: The form ID to delete.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return delete_form(form_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 4: Tag operations (cross-cutting)
    # =======================================================================

    @mcp.tool()
    def pardot_add_tag(object_type: str, object_id: str, tag_id: int) -> dict:
        """Add a tag to a Pardot object.

        Args:
            object_type: The object type (e.g. 'prospects', 'lists', 'campaigns',
                'emails', 'email-templates', 'custom-fields', 'forms', 'form-handlers',
                'form-fields', 'files', 'landing-pages', 'dynamic-contents',
                'custom-redirects', 'layout-templates', 'users').
            object_id: The ID of the object to tag.
            tag_id: The tag ID to add.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return add_tag(object_type, object_id, tag_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_remove_tag(object_type: str, object_id: str, tag_id: int) -> dict:
        """Remove a tag from a Pardot object.

        Args:
            object_type: The object type (e.g. 'prospects', 'lists', 'campaigns',
                'emails', 'email-templates', 'custom-fields', 'forms', 'form-handlers',
                'form-fields', 'files', 'landing-pages', 'dynamic-contents',
                'custom-redirects', 'layout-templates', 'users').
            object_id: The ID of the object to untag.
            tag_id: The tag ID to remove.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return remove_tag(object_type, object_id, tag_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 5: Special action endpoints
    # =======================================================================

    @mcp.tool()
    def pardot_assign_visitor(visitor_id: str, prospect_id: str) -> dict:
        """Assign a Pardot visitor to a prospect.

        Args:
            visitor_id: The visitor ID to assign.
            prospect_id: The prospect ID to assign the visitor to.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return assign_visitor_to_prospect(visitor_id, prospect_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_connect_campaign_to_sf(campaign_id: str, salesforce_campaign_id: str) -> dict:
        """Connect a Pardot campaign to a Salesforce campaign.

        Args:
            campaign_id: The Pardot campaign ID.
            salesforce_campaign_id: The Salesforce campaign ID to connect.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return connect_campaign_to_salesforce(campaign_id, salesforce_campaign_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_merge_tags(target_tag_id: str, source_tag_ids: list[int]) -> dict:
        """Merge multiple Pardot tags into a single target tag.

        All objects tagged with source tags will be retagged with the target tag.
        Source tags are deleted after merge.

        Args:
            target_tag_id: The tag ID to merge into (kept).
            source_tag_ids: List of tag IDs to merge from (deleted after merge).

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return merge_tags(target_tag_id, source_tag_ids)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_create_external_activity(data: dict) -> dict:
        """Create a Pardot external activity (track activity from external systems).

        Args:
            data: Dict with type, value, prospectId, and optional activityDate.

        Returns:
            The created external activity as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_external_activity(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 6: Export API (write)
    # =======================================================================

    @mcp.tool()
    def pardot_create_export(data: dict) -> dict:
        """Create a Pardot export job for bulk data extraction.

        Args:
            data: Dict with 'object' (e.g. 'Prospect', 'VisitorActivity'),
                'fields' (list of field names), and optional filter criteria
                like 'createdAfter', 'updatedBefore', 'deleted'.

        Returns:
            The created export job as a dict (includes 'id' and 'state'), or an error dict on failure.
        """
        try:
            require_write_access()
            return create_export(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 7: Import API (write)
    # =======================================================================

    @mcp.tool()
    def pardot_create_import(data: dict) -> dict:
        """Create a Pardot import job for bulk prospect upsert.

        Args:
            data: Dict with import configuration (e.g. operation, object,
                matchType like 'matchEmail', and options like restoreDeleted,
                overwrite, createOnNoMatch, nullOverwrite).

        Returns:
            The created import job as a dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return create_import(data)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_upload_import_batch(import_id: str, csv_base64: str) -> dict:
        """Upload a CSV batch to a Pardot import job.

        Args:
            import_id: The import job ID.
            csv_base64: Base64-encoded CSV data (max 10MB per batch, up to 10 batches).

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            raw = base64.b64decode(csv_base64)
            return upload_import_batch(import_id, raw)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_submit_import(import_id: str) -> dict:
        """Submit a Pardot import job for processing (sets state to 'Ready').

        Args:
            import_id: The import job ID to submit.

        Returns:
            A success dict, or an error dict on failure.
        """
        try:
            require_write_access()
            return submit_import(import_id)
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    # pardot_download_import_errors is in pardot_tools.py (read tools) since it's a read operation
