"""Curated Salesforce schema for The Vines of Mendoza.

This module provides a SCHEMA dict that documents the most useful fields,
relationships, and external IDs for each key Salesforce object. It is used
by the sf_get_schema MCP tool to help AI agents write correct SOQL queries
without needing to call sf_describe_object first.

Objects covered:
  - TVRS_Guest__c  (custom guest/reservation record — central to hospitality ops)
  - Account        (Person Accounts — guest profiles)
  - Contact        (linked to TVRS_Guest__c via Contact__c lookup)
  - Opportunity    (sales pipeline — villa sales, wine memberships)
  - Lead           (unconverted prospects)
  - Campaign       (marketing campaigns)
  - CampaignMember (junction: Contact/Lead ↔ Campaign)
  - Task           (activities linked to Accounts/Opportunities)
"""

SCHEMA: dict[str, dict] = {
    "TVRS_Guest__c": {
        "label": "TVRS Guest (Reservation)",
        "description": (
            "Custom object representing a guest reservation/stay at The Vines Resort & Spa. "
            "Each record is one check-in. Linked to Contact via Contact__c lookup. "
            "Email__c is the external ID used for upserts from OPERA PMS."
        ),
        "external_ids": ["Email__c"],
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "Name", "type": "string", "note": "Auto-number or auto-name"},
            {"name": "Email__c", "type": "email", "note": "External ID, used for upserts"},
            {"name": "Guest_First_Name__c", "type": "string"},
            {"name": "Guest_Last_Name__c", "type": "string"},
            {"name": "Check_In_Date__c", "type": "date"},
            {"name": "Check_Out_Date__c", "type": "date"},
            {"name": "Villa_number__c", "type": "string"},
            {"name": "City__c", "type": "string"},
            {"name": "State_Province__c", "type": "string"},
            {"name": "Country__c", "type": "string"},
            {"name": "Language__c", "type": "picklist", "note": "English, Spanish, Portuguese, Unknown"},
            {"name": "Telephone__c", "type": "phone"},
            {"name": "Comments__c", "type": "textarea", "note": "Reservation comments"},
            {"name": "Contact__c", "type": "reference", "referenceTo": "Contact",
             "relationshipName": "Contact__r"},
        ],
        "notable_booleans": [
            "Future_Sales_Prospect__c",
            "TVG__c",
            "Greeted_at_Check_In__c",
            "Received_PV_Explanation__c",
            "Vineyard_Tour__c",
            "Did_TVG_Tasting_With_Sales_Rep__c",
            "Did_TVG_Tasting_with_Sommelier__c",
            "Villa_Tour__c",
            "Attended_Happy_Hour__c",
            "Brochure_Clicked__c",
            "Replied_to_Mkt_campaign_2025__c",
            "In_Conversation__c",
            "Not_interested__c",
            "Ready_for_pardot_email_list__c",
            "In_Conversation_PV__c",
            "Follow_up__c",
            "Ready_for_PV_mail__c",
        ],
        "relationships": {
            "parent": [
                {
                    "field": "Contact__c",
                    "object": "Contact",
                    "relationshipName": "Contact__r",
                    "note": "Lookup to Contact; traverse to Account via Contact__r.AccountId",
                },
            ],
        },
        "example_soql": [
            (
                "SELECT Id, Guest_First_Name__c, Guest_Last_Name__c, Email__c, "
                "Check_In_Date__c, Check_Out_Date__c, Villa_number__c, "
                "Contact__r.AccountId "
                "FROM TVRS_Guest__c "
                "WHERE Check_In_Date__c >= TODAY "
                "ORDER BY Check_In_Date__c ASC LIMIT 10"
            ),
            (
                "SELECT Contact__r.AccountId acctId, COUNT(Id) cnt "
                "FROM TVRS_Guest__c "
                "GROUP BY Contact__r.AccountId"
            ),
        ],
    },
    "Account": {
        "label": "Account (Person Account)",
        "description": (
            "Standard Account object — this org uses Person Accounts (IsPersonAccount = true). "
            "Person Accounts merge Account + Contact into one record. "
            "Fields like PersonEmail, PersonTitle, Primary_Language__pc are available."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "Name", "type": "string"},
            {"name": "PersonEmail", "type": "email", "note": "Person Account email"},
            {"name": "PersonTitle", "type": "string"},
            {"name": "Website", "type": "url"},
            {"name": "Description", "type": "textarea"},
            {"name": "IsPersonAccount", "type": "boolean"},
            {"name": "Primary_Language__pc", "type": "picklist", "note": "Custom person account field"},
            {"name": "BillingState", "type": "string",
             "note": "Stored as FULL NAME, not the 2-letter code — e.g. 'Texas' (1.5k accts), "
                     "'California', 'New York', 'Sao Paulo'. Filtering on 'TX' returns ZERO rows. "
                     "This is the canonical place to filter people by US state/region; NetSuite "
                     "customers have no queryable state (see ns_schema customer notes)."},
            {"name": "BillingCity", "type": "string"},
            {"name": "BillingCountry", "type": "string", "note": "Full name, e.g. 'United States'"},
            {"name": "ShippingState", "type": "string", "note": "Full name; often blank — prefer BillingState"},
            {"name": "OwnerId", "type": "reference", "referenceTo": "User"},
            {"name": "CreatedDate", "type": "datetime"},
            {"name": "LastModifiedDate", "type": "datetime"},
        ],
        "relationships": {
            "children": [
                {
                    "childObject": "Contact",
                    "field": "AccountId",
                    "relationshipName": "Contacts",
                },
                {
                    "childObject": "Opportunity",
                    "field": "AccountId",
                    "relationshipName": "Opportunities",
                },
            ],
        },
        "example_soql": [
            (
                "SELECT Id, Name, PersonEmail, IsPersonAccount, Description, Website "
                "FROM Account "
                "WHERE IsPersonAccount = true LIMIT 10"
            ),
            (
                "SELECT Id, Name, "
                "(SELECT Id, Name FROM Contacts), "
                "(SELECT Id, Name, StageName FROM Opportunities) "
                "FROM Account LIMIT 5"
            ),
            (
                "-- People in a US state: BillingState is the FULL name, not 'TX'\n"
                "SELECT Id, Name, PersonEmail, BillingCity "
                "FROM Account WHERE BillingState = 'Texas' ORDER BY BillingCity"
            ),
        ],
    },
    "Contact": {
        "label": "Contact",
        "description": (
            "Standard Contact object. Linked to Account via AccountId. "
            "TVRS_Guest__c records reference Contact via Contact__c lookup. "
            "Has_TVRS_Guest_Record__c boolean indicates if guest records exist."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "FirstName", "type": "string"},
            {"name": "LastName", "type": "string"},
            {"name": "Email", "type": "email"},
            {"name": "Phone", "type": "phone"},
            {"name": "AccountId", "type": "reference", "referenceTo": "Account",
             "relationshipName": "Account"},
            {"name": "Has_TVRS_Guest_Record__c", "type": "boolean",
             "note": "True if linked TVRS_Guest__c records exist"},
            {"name": "CreatedDate", "type": "datetime"},
            {"name": "LastModifiedDate", "type": "datetime"},
        ],
        "relationships": {
            "parent": [
                {
                    "field": "AccountId",
                    "object": "Account",
                    "relationshipName": "Account",
                },
            ],
            "children": [
                {
                    "childObject": "TVRS_Guest__c",
                    "field": "Contact__c",
                    "note": "Guest reservations for this contact",
                },
                {
                    "childObject": "CampaignMember",
                    "field": "ContactId",
                    "relationshipName": "CampaignMembers",
                },
            ],
        },
        "example_soql": [
            (
                "SELECT Id, FirstName, LastName, Email, AccountId "
                "FROM Contact "
                "WHERE Email != null LIMIT 10"
            ),
        ],
    },
    "Opportunity": {
        "label": "Opportunity",
        "description": (
            "Standard Opportunity object for sales pipeline. "
            "Used for villa sales, wine memberships, and other revenue. "
            "Linked to Account via AccountId. Owner is the sales rep."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "Name", "type": "string"},
            {"name": "StageName", "type": "picklist"},
            {"name": "Amount", "type": "currency"},
            {"name": "CloseDate", "type": "date"},
            {"name": "IsClosed", "type": "boolean"},
            {"name": "IsWon", "type": "boolean"},
            {"name": "AccountId", "type": "reference", "referenceTo": "Account",
             "relationshipName": "Account"},
            {"name": "OwnerId", "type": "reference", "referenceTo": "User",
             "relationshipName": "Owner"},
            {"name": "CreatedDate", "type": "datetime"},
            {"name": "LastModifiedDate", "type": "datetime"},
        ],
        "relationships": {
            "parent": [
                {
                    "field": "AccountId",
                    "object": "Account",
                    "relationshipName": "Account",
                },
                {
                    "field": "OwnerId",
                    "object": "User",
                    "relationshipName": "Owner",
                },
            ],
            "children": [
                {
                    "childObject": "OpportunityContactRole",
                    "field": "OpportunityId",
                    "note": "Junction to Contact — use for contact-level opp queries",
                },
                {
                    "childObject": "Task",
                    "field": "WhatId",
                    "note": "Activities/tasks linked to this opportunity",
                },
            ],
        },
        "example_soql": [
            (
                "SELECT Id, Name, StageName, Amount, CloseDate, "
                "OwnerId, Owner.Name, Owner.Email, "
                "AccountId, Account.Name "
                "FROM Opportunity "
                "WHERE IsClosed = false "
                "ORDER BY LastModifiedDate DESC LIMIT 10"
            ),
            (
                "SELECT StageName, COUNT(Id) cnt "
                "FROM Opportunity "
                "WHERE IsClosed = false "
                "GROUP BY StageName "
                "ORDER BY COUNT(Id) DESC"
            ),
        ],
    },
    "Lead": {
        "label": "Lead",
        "description": (
            "Standard Lead object for unconverted prospects. "
            "Once converted, IsConverted = true and ConvertedContactId is set."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "FirstName", "type": "string"},
            {"name": "LastName", "type": "string"},
            {"name": "Email", "type": "email"},
            {"name": "Phone", "type": "phone"},
            {"name": "Company", "type": "string"},
            {"name": "Status", "type": "picklist"},
            {"name": "IsConverted", "type": "boolean"},
            {"name": "ConvertedContactId", "type": "reference", "referenceTo": "Contact"},
            {"name": "ConvertedAccountId", "type": "reference", "referenceTo": "Account"},
            {"name": "OwnerId", "type": "reference", "referenceTo": "User"},
            {"name": "CreatedDate", "type": "datetime"},
        ],
        "example_soql": [
            (
                "SELECT Id, FirstName, LastName, Email, Company, Status "
                "FROM Lead "
                "WHERE IsConverted = false "
                "ORDER BY CreatedDate DESC LIMIT 10"
            ),
        ],
    },
    "Campaign": {
        "label": "Campaign",
        "description": (
            "Standard Campaign object for marketing campaigns. "
            "Members are tracked via CampaignMember junction object."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "Name", "type": "string"},
            {"name": "Status", "type": "picklist"},
            {"name": "Type", "type": "picklist"},
            {"name": "StartDate", "type": "date"},
            {"name": "EndDate", "type": "date"},
            {"name": "IsActive", "type": "boolean"},
            {"name": "NumberOfContacts", "type": "int"},
            {"name": "NumberOfLeads", "type": "int"},
            {"name": "OwnerId", "type": "reference", "referenceTo": "User"},
        ],
        "relationships": {
            "children": [
                {
                    "childObject": "CampaignMember",
                    "field": "CampaignId",
                    "relationshipName": "CampaignMembers",
                },
            ],
        },
        "example_soql": [
            (
                "SELECT Id, Name, Status, Type, StartDate, EndDate, IsActive "
                "FROM Campaign "
                "WHERE IsActive = true "
                "ORDER BY StartDate DESC LIMIT 10"
            ),
        ],
    },
    "CampaignMember": {
        "label": "Campaign Member",
        "description": (
            "Junction object linking Contacts or Leads to Campaigns. "
            "Query by ContactId or CampaignId to find associations."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "ContactId", "type": "reference", "referenceTo": "Contact"},
            {"name": "LeadId", "type": "reference", "referenceTo": "Lead"},
            {"name": "CampaignId", "type": "reference", "referenceTo": "Campaign",
             "relationshipName": "Campaign"},
            {"name": "Status", "type": "picklist"},
            {"name": "CreatedDate", "type": "datetime"},
        ],
        "example_soql": [
            (
                "SELECT Id, ContactId, CampaignId, Campaign.Name, Status "
                "FROM CampaignMember "
                "WHERE ContactId = '<contact_id>' "
                "ORDER BY CreatedDate DESC"
            ),
        ],
    },
    "Task": {
        "label": "Task (Activity)",
        "description": (
            "Standard Task object for activities. WhatId links to Account or Opportunity. "
            "WhoId links to Contact or Lead. Used to track sales touches."
        ),
        "key_fields": [
            {"name": "Id", "type": "id"},
            {"name": "Subject", "type": "string"},
            {"name": "Status", "type": "picklist"},
            {"name": "WhatId", "type": "reference", "note": "Polymorphic: Account, Opportunity, etc."},
            {"name": "WhoId", "type": "reference", "note": "Polymorphic: Contact, Lead"},
            {"name": "OwnerId", "type": "reference", "referenceTo": "User"},
            {"name": "CreatedById", "type": "reference", "referenceTo": "User"},
            {"name": "CreatedDate", "type": "datetime"},
            {"name": "ActivityDate", "type": "date"},
        ],
        "example_soql": [
            (
                "SELECT Id, Subject, Status, WhatId, WhoId, CreatedDate "
                "FROM Task "
                "WHERE WhatId = '<opportunity_id>' "
                "ORDER BY CreatedDate DESC"
            ),
        ],
    },
}


# Convenience: quick lookup of object names
OBJECT_NAMES = sorted(SCHEMA.keys())


def get_schema(object_name: str | None = None) -> dict:
    """Return schema for a specific object, or the full SCHEMA dict if no name given.

    Args:
        object_name: API name of the SObject (e.g. "TVRS_Guest__c"). Case-insensitive lookup.
                     If None, returns the entire schema dict.

    Returns:
        Schema dict for the requested object, or full schema.

    Raises:
        KeyError: If the object_name is not found in the curated schema.
    """
    if object_name is None:
        return SCHEMA

    # Case-insensitive lookup
    for name, schema in SCHEMA.items():
        if name.lower() == object_name.lower():
            return {name: schema}

    available = ", ".join(OBJECT_NAMES)
    raise KeyError(
        f"Object '{object_name}' not found in curated schema. "
        f"Available objects: {available}. "
        f"Use sf_describe_object for objects not in this list."
    )
