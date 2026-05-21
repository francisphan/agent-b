"""Pardot / Account Engagement MCP tool definitions."""

import base64

from src.pardot_client import (
    download_engagement_studio_program_structure,
    download_export_results,
    download_import_errors,
    get_account,
    get_campaign,
    get_custom_field,
    get_custom_redirect,
    get_dynamic_content,
    get_dynamic_content_variation,
    get_email,
    get_email_template,
    get_engagement_studio_program,
    get_export,
    get_external_activity,
    get_file,
    get_folder,
    get_form,
    get_form_field,
    get_form_handler,
    get_form_handler_field,
    get_import,
    get_landing_page,
    get_layout_template,
    get_lifecycle_history,
    get_lifecycle_stage,
    get_list,
    get_list_email,
    get_list_email_stats,
    get_list_membership,
    get_opportunity,
    get_prospect,
    get_prospect_account,
    get_tag,
    get_tagged_object,
    get_tracker_domain,
    get_user,
    get_visit,
    get_visitor,
    get_visitor_activity,
    query_campaigns,
    query_custom_fields,
    query_custom_redirects,
    query_dynamic_content_variations,
    query_dynamic_contents,
    query_email_templates,
    query_emails,
    query_engagement_studio_programs,
    query_exports,
    query_external_activities,
    query_files,
    query_folder_contents,
    query_folders,
    query_form_fields,
    query_form_handler_fields,
    query_form_handlers,
    query_forms,
    query_imports,
    query_landing_pages,
    query_layout_templates,
    query_lifecycle_histories,
    query_lifecycle_stages,
    query_list_emails,
    query_list_memberships,
    query_lists,
    query_opportunities,
    query_prospect_accounts,
    query_prospects,
    query_tagged_objects,
    query_tags,
    query_tracker_domains,
    query_users,
    query_visitor_activities,
    query_visits,
    query_visitors,
)

# Pardot API v5 requires explicit field selection — these are sensible defaults.
DEFAULT_PROSPECT_FIELDS = (
    "id,email,firstName,lastName,company,jobTitle,city,state,country,"
    "phone,score,grade,source,campaignId,salesforceId,createdAt,updatedAt"
)
DEFAULT_LIST_FIELDS = "id,name,title,description,isPublic,isDynamic,folderId,campaignId,createdAt,updatedAt"
DEFAULT_CAMPAIGN_FIELDS = "id,name,cost,folderId,salesforceId,createdAt,updatedAt"
DEFAULT_FORM_FIELDS = "id,name,folderId,campaignId,trackerDomainId,createdAt,updatedAt"
DEFAULT_EMAIL_TEMPLATE_FIELDS = (
    "id,name,subject,htmlMessage,textMessage,folderId,isOneToOneEmail,"
    "isAutoResponderEmail,isDripEmail,isListEmail,type,campaignId,createdAt,updatedAt"
)
DEFAULT_VISITOR_ACTIVITY_FIELDS = (
    "id,prospectId,visitorId,type,typeName,details,emailId,formId,"
    "formHandlerId,campaignId,listEmailId,createdAt"
)
DEFAULT_LIST_MEMBERSHIP_FIELDS = "id,listId,prospectId,optedOut,createdAt,updatedAt"
DEFAULT_EMAIL_FIELDS = (
    "id,name,subject,campaignId,prospectId,emailTemplateId,"
    "trackerDomainId,sentAt,type,listEmailId"
)
DEFAULT_LIST_EMAIL_FIELDS = (
    "id,name,subject,campaignId,emailTemplateId,isSent,sentAt,trackerDomainId,createdAt"
)
DEFAULT_CUSTOM_FIELD_FIELDS = (
    "id,name,fieldId,type,isRequired,isRecordMultipleResponses,createdAt,updatedAt"
)
DEFAULT_TAG_FIELDS = "id,name,createdAt,updatedAt"
DEFAULT_TAGGED_OBJECT_FIELDS = "id,tagId,objectType,objectId,createdAt"
DEFAULT_TRACKER_DOMAIN_FIELDS = "id,domain,isPrimary,isDeleted"
DEFAULT_ENGAGEMENT_STUDIO_PROGRAM_FIELDS = (
    "id,name,status,recipientListId,senderType,senderId,"
    "folderId,campaignId,createdAt,updatedAt"
)
DEFAULT_VISITOR_FIELDS = "id,pageViewCount,prospectId,createdAt,updatedAt"
DEFAULT_VISIT_FIELDS = (
    "id,visitorId,prospectId,visitorPageViewCount,"
    "durationInSeconds,campaignId,createdAt"
)
DEFAULT_PROSPECT_ACCOUNT_FIELDS = (
    "id,name,city,state,country,phone,website,"
    "annualRevenue,employeeCount,createdAt,updatedAt"
)
DEFAULT_OPPORTUNITY_FIELDS = (
    "id,name,value,probability,type,status,closedAt,campaignId,createdAt,updatedAt"
)
DEFAULT_LIFECYCLE_STAGE_FIELDS = "id,name,position,isLocked,createdAt,updatedAt"
DEFAULT_LIFECYCLE_HISTORY_FIELDS = (
    "id,prospectId,previousStageId,nextStageId,secondsInStage,createdAt"
)
DEFAULT_USER_FIELDS = "id,email,firstName,lastName,role,createdAt,updatedAt"
DEFAULT_ACCOUNT_FIELDS = "id,company,website,createdAt"
DEFAULT_FOLDER_FIELDS = "id,name,parentFolderId,createdAt,updatedAt"
DEFAULT_FOLDER_CONTENT_FIELDS = "id,folderId,objectType,objectId"
DEFAULT_CUSTOM_REDIRECT_FIELDS = (
    "id,name,url,destinationUrl,campaignId,trackerDomainId,folderId,createdAt,updatedAt"
)
DEFAULT_FORM_HANDLER_FIELDS = (
    "id,name,url,campaignId,trackerDomainId,folderId,createdAt,updatedAt"
)
DEFAULT_FORM_HANDLER_FIELD_FIELDS = "id,formHandlerId,prospectFieldId,fieldName,isRequired,dataFormat,createdAt,updatedAt"
DEFAULT_LAYOUT_TEMPLATE_FIELDS = "id,name,folderId,createdAt,updatedAt"
DEFAULT_FILE_FIELDS = "id,name,folderId,url,size,createdAt,updatedAt"
DEFAULT_LANDING_PAGE_FIELDS = (
    "id,name,url,campaignId,trackerDomainId,folderId,createdAt,updatedAt"
)
DEFAULT_DYNAMIC_CONTENT_FIELDS = "id,name,basedOn,folderId,createdAt,updatedAt"
DEFAULT_DYNAMIC_CONTENT_VARIATION_FIELDS = (
    "id,dynamicContentId,comparison,value,content,createdAt,updatedAt"
)
DEFAULT_FORM_FIELD_FIELDS = (
    "id,formId,prospectFieldId,fieldName,type,isRequired,createdAt,updatedAt"
)
DEFAULT_EXTERNAL_ACTIVITY_FIELDS = "id,type,value,prospectId,activityDate,createdAt"


def register_tools(mcp):
    """Register all Pardot tools on the given FastMCP instance."""

    @mcp.tool()
    def pardot_query_prospects(
        fields: str = "", order_by: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot prospects with optional field selection and ordering.

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            order_by: Field name to sort results by (e.g. "createdAt", "lastActivityAt").
            limit: Maximum number of prospects to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with prospect data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_PROSPECT_FIELDS}
            if order_by:
                params["orderBy"] = order_by
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_prospects(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_prospect(prospect_id: str) -> dict:
        """Get a single Pardot prospect by ID.

        Args:
            prospect_id: The Pardot prospect ID.

        Returns:
            The prospect as a dict, or an error dict on failure.
        """
        try:
            return get_prospect(prospect_id, fields=DEFAULT_PROSPECT_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_lists(
        fields: str = "", order_by: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot static lists.

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            order_by: Field name to sort results by.
            limit: Maximum number of lists to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with list data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LIST_FIELDS}
            if order_by:
                params["orderBy"] = order_by
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_lists(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_list(list_id: str) -> dict:
        """Get a single Pardot static list by ID.

        Args:
            list_id: The Pardot list ID.

        Returns:
            The list as a dict, or an error dict on failure.
        """
        try:
            return get_list(list_id, fields=DEFAULT_LIST_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_list_memberships(
        list_id: str = "", prospect_id: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot list memberships with optional filters.

        Args:
            list_id: Filter by list ID (empty for all).
            prospect_id: Filter by prospect ID (empty for all).
            limit: Maximum number of memberships to return (default 200, max 999).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with membership data. To paginate, pass max(id) from values as id_greater_than.
        """
        try:
            params = {"fields": DEFAULT_LIST_MEMBERSHIP_FIELDS}
            if list_id:
                params["listId"] = list_id
            if prospect_id:
                params["prospectId"] = prospect_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_list_memberships(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_campaigns(
        fields: str = "", order_by: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot campaigns (read-only).

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            order_by: Field name to sort results by.
            limit: Maximum number of campaigns to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with campaign data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_CAMPAIGN_FIELDS}
            if order_by:
                params["orderBy"] = order_by
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_campaigns(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_campaign(campaign_id: str) -> dict:
        """Get a single Pardot campaign by ID.

        Args:
            campaign_id: The Pardot campaign ID.

        Returns:
            The campaign as a dict, or an error dict on failure.
        """
        try:
            return get_campaign(campaign_id, fields=DEFAULT_CAMPAIGN_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_visitor_activities(
        prospect_id: str = "",
        activity_type: str = "",
        created_after: str = "",
        created_before: str = "",
        campaign_id: str = "",
        limit: int = 200,
        id_greater_than: str = "",
    ) -> dict:
        """Query Pardot visitor activities with optional filters.

        Args:
            prospect_id: Filter by prospect ID (empty for all).
            activity_type: Filter by numeric activity type. Common types: 3=form submission, 6=email sent, 11=email open, 12=email click, 35=unsubscribe, 36=hard bounce.
            created_after: ISO date filter, e.g. "2026-03-01". Maps to createdAtAfterOrEqualTo.
            created_before: ISO date filter, e.g. "2026-04-01". Maps to createdAtBefore.
            campaign_id: Filter by Pardot campaign ID.
            limit: Maximum number of activities to return (default 200, max 999).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with activity data. To paginate, pass max(id) from values as id_greater_than.
        """
        try:
            params = {"fields": DEFAULT_VISITOR_ACTIVITY_FIELDS}
            if prospect_id:
                params["prospectId"] = prospect_id
            if activity_type:
                params["type"] = activity_type
            if created_after:
                params["createdAtAfterOrEqualTo"] = created_after
            if created_before:
                params["createdAtBefore"] = created_before
            if campaign_id:
                params["campaignId"] = campaign_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_visitor_activities(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_forms(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot forms.

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            limit: Maximum number of forms to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with form data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_FORM_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_forms(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_form(form_id: str) -> dict:
        """Get a single Pardot form by ID.

        Args:
            form_id: The Pardot form ID.

        Returns:
            The form as a dict, or an error dict on failure.
        """
        try:
            return get_form(form_id, fields=DEFAULT_FORM_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_tracker_domains(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot tracker domains.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of results (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with tracker domain data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_TRACKER_DOMAIN_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_tracker_domains(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_query_email_templates(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot email templates.

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            limit: Maximum number of templates to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with template data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_EMAIL_TEMPLATE_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_email_templates(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_email_template(template_id: str) -> dict:
        """Get a single Pardot email template by ID.

        Args:
            template_id: The Pardot email template ID.

        Returns:
            The template as a dict, or an error dict on failure.
        """
        try:
            return get_email_template(template_id, fields=DEFAULT_EMAIL_TEMPLATE_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- List Membership Read ---

    @mcp.tool()
    def pardot_get_list_membership(membership_id: str) -> dict:
        """Get a single Pardot list membership by ID.

        Args:
            membership_id: The list membership ID.

        Returns:
            The membership as a dict, or an error dict on failure.
        """
        try:
            return get_list_membership(
                membership_id, fields=DEFAULT_LIST_MEMBERSHIP_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Email Read ---

    @mcp.tool()
    def pardot_query_emails(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot emails (one-to-one sends).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of emails to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with email data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_EMAIL_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_emails(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_email(email_id: str) -> dict:
        """Get a single Pardot email by ID.

        Args:
            email_id: The Pardot email ID.

        Returns:
            The email as a dict, or an error dict on failure.
        """
        try:
            return get_email(email_id, fields=DEFAULT_EMAIL_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- List Email Read ---

    @mcp.tool()
    def pardot_query_list_emails(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot list emails (batch sends to lists).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of list emails to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with list email data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LIST_EMAIL_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_list_emails(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_list_email(list_email_id: str) -> dict:
        """Get a single Pardot list email by ID.

        Args:
            list_email_id: The Pardot list email ID.

        Returns:
            The list email as a dict, or an error dict on failure.
        """
        try:
            return get_list_email(list_email_id, fields=DEFAULT_LIST_EMAIL_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Custom Field Read ---

    @mcp.tool()
    def pardot_query_custom_fields(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot custom fields.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of custom fields to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with custom field data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_CUSTOM_FIELD_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_custom_fields(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_custom_field(field_id: str) -> dict:
        """Get a single Pardot custom field by ID.

        Args:
            field_id: The Pardot custom field ID.

        Returns:
            The custom field as a dict, or an error dict on failure.
        """
        try:
            return get_custom_field(field_id, fields=DEFAULT_CUSTOM_FIELD_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Tag Read ---

    @mcp.tool()
    def pardot_query_tags(fields: str = "", limit: int = 200, id_greater_than: str = "") -> dict:
        """Query Pardot tags.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of tags to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with tag data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_TAG_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_tags(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_tag(tag_id: str) -> dict:
        """Get a single Pardot tag by ID.

        Args:
            tag_id: The Pardot tag ID.

        Returns:
            The tag as a dict, or an error dict on failure.
        """
        try:
            return get_tag(tag_id, fields=DEFAULT_TAG_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Tagged Object Read ---

    @mcp.tool()
    def pardot_query_tagged_objects(
        tag_id: str = "", object_type: str = "", id_greater_than: str = ""
    ) -> dict:
        """Query Pardot tagged objects with optional filters.

        Args:
            tag_id: Filter by tag ID (empty for all).
            object_type: Filter by object type (empty for all).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with tagged object data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": DEFAULT_TAGGED_OBJECT_FIELDS}
            if tag_id:
                params["tagId"] = tag_id
            if object_type:
                params["objectType"] = object_type
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_tagged_objects(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_tagged_object(tagged_object_id: str) -> dict:
        """Get a single Pardot tagged object by ID.

        Args:
            tagged_object_id: The tagged object ID.

        Returns:
            The tagged object as a dict, or an error dict on failure.
        """
        try:
            return get_tagged_object(
                tagged_object_id, fields=DEFAULT_TAGGED_OBJECT_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Engagement Studio Program Read ---

    @mcp.tool()
    def pardot_query_engagement_studio_programs(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot Engagement Studio programs.

        Args:
            fields: Comma-separated field names to return (empty for all default fields).
            limit: Maximum number of programs to return (default 200).
            id_greater_than: For pagination, pass the max 'id' from the previous page's values. More reliable than nextPageToken (which silently fails on dynamic lists).

        Returns:
            A dict with program data and optionally 'nextPageToken' for pagination, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_ENGAGEMENT_STUDIO_PROGRAM_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_engagement_studio_programs(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_engagement_studio_program(program_id: str) -> dict:
        """Get a single Pardot Engagement Studio program by ID.

        Args:
            program_id: The Engagement Studio program ID.

        Returns:
            The program as a dict, or an error dict on failure.
        """
        try:
            return get_engagement_studio_program(
                program_id, fields=DEFAULT_ENGAGEMENT_STUDIO_PROGRAM_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_download_engagement_studio_program_structure(program_id: str) -> dict:
        """Download the structure file of a Pardot Engagement Studio program.

        The structure file can be used to clone or recreate a program via
        pardot_create_engagement_studio_program. Returns the file content as a
        base64-encoded string.

        Args:
            program_id: The Engagement Studio program ID whose structure to download.

        Returns:
            A dict with 'programId' and 'structureFileBase64' (base64-encoded content),
            or an error dict on failure.
        """
        try:
            raw = download_engagement_studio_program_structure(program_id)
            return {
                "programId": program_id,
                "structureFileBase64": base64.b64encode(raw).decode("utf-8"),
            }
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 1: Missing operations on existing objects
    # =======================================================================

    @mcp.tool()
    def pardot_get_visitor_activity(activity_id: str) -> dict:
        """Get a single Pardot visitor activity by ID.

        Args:
            activity_id: The visitor activity ID.

        Returns:
            The visitor activity as a dict, or an error dict on failure.
        """
        try:
            return get_visitor_activity(
                activity_id, fields=DEFAULT_VISITOR_ACTIVITY_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_tracker_domain(domain_id: str) -> dict:
        """Get a single Pardot tracker domain by ID.

        Args:
            domain_id: The tracker domain ID.

        Returns:
            The tracker domain as a dict, or an error dict on failure.
        """
        try:
            return get_tracker_domain(domain_id, fields=DEFAULT_TRACKER_DOMAIN_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_list_email_stats(list_email_id: str) -> dict:
        """Get send statistics for a Pardot list email.

        Args:
            list_email_id: The list email ID.

        Returns:
            Stats dict with sent, delivered, opened, clicked, bounced, etc., or an error dict on failure.
        """
        try:
            return get_list_email_stats(list_email_id)
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 2: New read-only objects
    # =======================================================================

    # --- Visitors ---

    @mcp.tool()
    def pardot_query_visitors(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot visitors (website tracking records).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of visitors to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with visitor data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_VISITOR_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_visitors(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_visitor(visitor_id: str) -> dict:
        """Get a single Pardot visitor by ID.

        Args:
            visitor_id: The Pardot visitor ID.

        Returns:
            The visitor as a dict, or an error dict on failure.
        """
        try:
            return get_visitor(visitor_id, fields=DEFAULT_VISITOR_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Visits ---

    @mcp.tool()
    def pardot_query_visits(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot visits (individual website sessions).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of visits to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with visit data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_VISIT_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_visits(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_visit(visit_id: str) -> dict:
        """Get a single Pardot visit by ID.

        Args:
            visit_id: The Pardot visit ID.

        Returns:
            The visit as a dict, or an error dict on failure.
        """
        try:
            return get_visit(visit_id, fields=DEFAULT_VISIT_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Prospect Accounts ---

    @mcp.tool()
    def pardot_query_prospect_accounts(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot prospect accounts (company groupings).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of accounts to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with prospect account data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_PROSPECT_ACCOUNT_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_prospect_accounts(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_prospect_account(account_id: str) -> dict:
        """Get a single Pardot prospect account by ID.

        Args:
            account_id: The prospect account ID.

        Returns:
            The prospect account as a dict, or an error dict on failure.
        """
        try:
            return get_prospect_account(
                account_id, fields=DEFAULT_PROSPECT_ACCOUNT_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Opportunities ---

    @mcp.tool()
    def pardot_query_opportunities(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot opportunities (read-only).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of opportunities to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with opportunity data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_OPPORTUNITY_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_opportunities(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_opportunity(opportunity_id: str) -> dict:
        """Get a single Pardot opportunity by ID.

        Args:
            opportunity_id: The Pardot opportunity ID.

        Returns:
            The opportunity as a dict, or an error dict on failure.
        """
        try:
            return get_opportunity(opportunity_id, fields=DEFAULT_OPPORTUNITY_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Lifecycle Stages ---

    @mcp.tool()
    def pardot_query_lifecycle_stages(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot lifecycle stages (funnel stages).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of stages to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with lifecycle stage data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LIFECYCLE_STAGE_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_lifecycle_stages(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_lifecycle_stage(stage_id: str) -> dict:
        """Get a single Pardot lifecycle stage by ID.

        Args:
            stage_id: The lifecycle stage ID.

        Returns:
            The lifecycle stage as a dict, or an error dict on failure.
        """
        try:
            return get_lifecycle_stage(stage_id, fields=DEFAULT_LIFECYCLE_STAGE_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Lifecycle Histories ---

    @mcp.tool()
    def pardot_query_lifecycle_histories(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot lifecycle histories (prospect stage transitions).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of histories to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with lifecycle history data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LIFECYCLE_HISTORY_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_lifecycle_histories(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_lifecycle_history(history_id: str) -> dict:
        """Get a single Pardot lifecycle history by ID.

        Args:
            history_id: The lifecycle history ID.

        Returns:
            The lifecycle history as a dict, or an error dict on failure.
        """
        try:
            return get_lifecycle_history(
                history_id, fields=DEFAULT_LIFECYCLE_HISTORY_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Users ---

    @mcp.tool()
    def pardot_query_users(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot users.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of users to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with user data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_USER_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_users(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_user(user_id: str) -> dict:
        """Get a single Pardot user by ID.

        Args:
            user_id: The Pardot user ID.

        Returns:
            The user as a dict, or an error dict on failure.
        """
        try:
            return get_user(user_id, fields=DEFAULT_USER_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Account (singleton) ---

    @mcp.tool()
    def pardot_get_account() -> dict:
        """Get the Pardot account record (singleton — returns your account info).

        Returns:
            The account as a dict, or an error dict on failure.
        """
        try:
            return get_account(fields=DEFAULT_ACCOUNT_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Folders ---

    @mcp.tool()
    def pardot_query_folders(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot folders.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of folders to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with folder data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_FOLDER_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_folders(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_folder(folder_id: str) -> dict:
        """Get a single Pardot folder by ID.

        Args:
            folder_id: The Pardot folder ID.

        Returns:
            The folder as a dict, or an error dict on failure.
        """
        try:
            return get_folder(folder_id, fields=DEFAULT_FOLDER_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Folder Contents ---

    @mcp.tool()
    def pardot_query_folder_contents(
        folder_id: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot folder contents (objects in folders).

        Args:
            folder_id: Filter by folder ID (empty for all).
            limit: Maximum number of items to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with folder content data, or an error dict on failure.
        """
        try:
            params = {"fields": DEFAULT_FOLDER_CONTENT_FIELDS}
            if folder_id:
                params["folderId"] = folder_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_folder_contents(params)
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 3: New CRUD objects (read portion)
    # =======================================================================

    # --- Custom Redirects ---

    @mcp.tool()
    def pardot_query_custom_redirects(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot custom redirects (tracked links).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of redirects to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with custom redirect data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_CUSTOM_REDIRECT_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_custom_redirects(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_custom_redirect(redirect_id: str) -> dict:
        """Get a single Pardot custom redirect by ID.

        Args:
            redirect_id: The custom redirect ID.

        Returns:
            The custom redirect as a dict, or an error dict on failure.
        """
        try:
            return get_custom_redirect(
                redirect_id, fields=DEFAULT_CUSTOM_REDIRECT_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Form Handlers ---

    @mcp.tool()
    def pardot_query_form_handlers(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot form handlers (third-party form integrations).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of form handlers to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with form handler data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_FORM_HANDLER_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_form_handlers(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_form_handler(handler_id: str) -> dict:
        """Get a single Pardot form handler by ID.

        Args:
            handler_id: The form handler ID.

        Returns:
            The form handler as a dict, or an error dict on failure.
        """
        try:
            return get_form_handler(handler_id, fields=DEFAULT_FORM_HANDLER_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Form Handler Fields ---

    @mcp.tool()
    def pardot_query_form_handler_fields(
        form_handler_id: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot form handler fields.

        Args:
            form_handler_id: Filter by form handler ID (empty for all).
            limit: Maximum number of fields to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with form handler field data, or an error dict on failure.
        """
        try:
            params = {"fields": DEFAULT_FORM_HANDLER_FIELD_FIELDS}
            if form_handler_id:
                params["formHandlerId"] = form_handler_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_form_handler_fields(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_form_handler_field(field_id: str) -> dict:
        """Get a single Pardot form handler field by ID.

        Args:
            field_id: The form handler field ID.

        Returns:
            The form handler field as a dict, or an error dict on failure.
        """
        try:
            return get_form_handler_field(
                field_id, fields=DEFAULT_FORM_HANDLER_FIELD_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Layout Templates ---

    @mcp.tool()
    def pardot_query_layout_templates(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot layout templates.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of templates to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with layout template data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LAYOUT_TEMPLATE_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_layout_templates(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_layout_template(template_id: str) -> dict:
        """Get a single Pardot layout template by ID.

        Args:
            template_id: The layout template ID.

        Returns:
            The layout template as a dict, or an error dict on failure.
        """
        try:
            return get_layout_template(
                template_id, fields=DEFAULT_LAYOUT_TEMPLATE_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Files ---

    @mcp.tool()
    def pardot_query_files(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot files (uploaded assets).

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of files to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with file data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_FILE_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_files(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_file(file_id: str) -> dict:
        """Get a single Pardot file by ID.

        Args:
            file_id: The Pardot file ID.

        Returns:
            The file metadata as a dict, or an error dict on failure.
        """
        try:
            return get_file(file_id, fields=DEFAULT_FILE_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Landing Pages ---

    @mcp.tool()
    def pardot_query_landing_pages(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot landing pages.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of landing pages to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with landing page data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_LANDING_PAGE_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_landing_pages(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_landing_page(page_id: str) -> dict:
        """Get a single Pardot landing page by ID.

        Args:
            page_id: The landing page ID.

        Returns:
            The landing page as a dict, or an error dict on failure.
        """
        try:
            return get_landing_page(page_id, fields=DEFAULT_LANDING_PAGE_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # --- Dynamic Content ---

    @mcp.tool()
    def pardot_query_dynamic_contents(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot dynamic content.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of items to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with dynamic content data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_DYNAMIC_CONTENT_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_dynamic_contents(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_dynamic_content(content_id: str) -> dict:
        """Get a single Pardot dynamic content by ID.

        Args:
            content_id: The dynamic content ID.

        Returns:
            The dynamic content as a dict, or an error dict on failure.
        """
        try:
            return get_dynamic_content(
                content_id, fields=DEFAULT_DYNAMIC_CONTENT_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Dynamic Content Variations ---

    @mcp.tool()
    def pardot_query_dynamic_content_variations(
        dynamic_content_id: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot dynamic content variations.

        Args:
            dynamic_content_id: Filter by dynamic content ID (empty for all).
            limit: Maximum number of variations to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with variation data, or an error dict on failure.
        """
        try:
            params = {"fields": DEFAULT_DYNAMIC_CONTENT_VARIATION_FIELDS}
            if dynamic_content_id:
                params["dynamicContentId"] = dynamic_content_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_dynamic_content_variations(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_dynamic_content_variation(variation_id: str) -> dict:
        """Get a single Pardot dynamic content variation by ID.

        Args:
            variation_id: The dynamic content variation ID.

        Returns:
            The variation as a dict, or an error dict on failure.
        """
        try:
            return get_dynamic_content_variation(
                variation_id, fields=DEFAULT_DYNAMIC_CONTENT_VARIATION_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # --- Form Fields ---

    @mcp.tool()
    def pardot_query_form_fields(
        form_id: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot form fields.

        Args:
            form_id: Filter by form ID (empty for all).
            limit: Maximum number of fields to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with form field data, or an error dict on failure.
        """
        try:
            params = {"fields": DEFAULT_FORM_FIELD_FIELDS}
            if form_id:
                params["formId"] = form_id
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_form_fields(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_form_field(field_id: str) -> dict:
        """Get a single Pardot form field by ID.

        Args:
            field_id: The form field ID.

        Returns:
            The form field as a dict, or an error dict on failure.
        """
        try:
            return get_form_field(field_id, fields=DEFAULT_FORM_FIELD_FIELDS)
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 5: External Activities (read)
    # =======================================================================

    @mcp.tool()
    def pardot_query_external_activities(
        fields: str = "", limit: int = 200, id_greater_than: str = ""
    ) -> dict:
        """Query Pardot external activities.

        Args:
            fields: Comma-separated field names to return (empty for defaults).
            limit: Maximum number of activities to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with external activity data, or an error dict on failure.
        """
        try:
            params = {"fields": fields or DEFAULT_EXTERNAL_ACTIVITY_FIELDS}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_external_activities(params)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_external_activity(activity_id: str) -> dict:
        """Get a single Pardot external activity by ID.

        Args:
            activity_id: The external activity ID.

        Returns:
            The external activity as a dict, or an error dict on failure.
        """
        try:
            return get_external_activity(
                activity_id, fields=DEFAULT_EXTERNAL_ACTIVITY_FIELDS
            )
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 6: Export API (read)
    # =======================================================================

    @mcp.tool()
    def pardot_query_exports(limit: int = 200, id_greater_than: str = "") -> dict:
        """Query Pardot export jobs.

        Args:
            limit: Maximum number of exports to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with export job data, or an error dict on failure.
        """
        try:
            params = {}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_exports(params or None)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_export(export_id: str) -> dict:
        """Get the status of a Pardot export job by ID.

        Args:
            export_id: The export job ID.

        Returns:
            The export job as a dict (including state, resultRefs), or an error dict on failure.
        """
        try:
            return get_export(export_id)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_download_export_results(export_id: str) -> dict:
        """Download the CSV results of a completed Pardot export.

        Args:
            export_id: The export job ID (must be in 'Complete' state).

        Returns:
            A dict with 'exportId' and 'csvBase64' (base64-encoded CSV), or an error dict on failure.
        """
        try:
            raw = download_export_results(export_id)
            return {
                "exportId": export_id,
                "csvBase64": base64.b64encode(raw).decode("utf-8"),
            }
        except Exception as e:
            return {"error": str(e)}

    # =======================================================================
    # Phase 7: Import API (read)
    # =======================================================================

    @mcp.tool()
    def pardot_query_imports(limit: int = 200, id_greater_than: str = "") -> dict:
        """Query Pardot import jobs.

        Args:
            limit: Maximum number of imports to return (default 200).
            cursor: Pagination cursor from a previous response's 'nextPageToken'.

        Returns:
            A dict with import job data, or an error dict on failure.
        """
        try:
            params = {}
            if limit != 200:
                params["limit"] = limit
            if id_greater_than:
                params["idGreaterThan"] = id_greater_than
            return query_imports(params or None)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_get_import(import_id: str) -> dict:
        """Get the status of a Pardot import job by ID.

        Args:
            import_id: The import job ID.

        Returns:
            The import job as a dict (including state, batchesRef), or an error dict on failure.
        """
        try:
            return get_import(import_id)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def pardot_download_import_errors(import_id: str) -> dict:
        """Download error CSV from a Pardot import job.

        Args:
            import_id: The import job ID.

        Returns:
            A dict with 'importId' and 'csvBase64' (base64-encoded error CSV),
            or an error dict on failure.
        """
        try:
            raw = download_import_errors(import_id)
            return {
                "importId": import_id,
                "csvBase64": base64.b64encode(raw).decode("utf-8"),
            }
        except Exception as e:
            return {"error": str(e)}
