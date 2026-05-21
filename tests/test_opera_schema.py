"""Tests for the curated OPERA schema and the opera_get_opera_schema tool."""

from src.opera_schema import GLOBAL_TIPS, SCHEMA


def test_schema_has_expected_tables():
    expected = {
        "NAME",
        "NAME_PHONE",
        "NAME_ADDRESS",
        "RESERVATION_NAME",
        "RESERVATION_DAILY_ELEMENT_NAME",
        "RESERVATION_DAILY_ELEMENTS",
        "NAME_COMMENT",
        "NAME$NOTES",
        "RESERVATION_COMMENT",
        "RESERVATION_ALERTS",
    }
    assert expected.issubset(SCHEMA.keys())


def test_every_table_has_key_fields():
    for table, schema in SCHEMA.items():
        assert "key_fields" in schema, f"{table} missing key_fields"
        assert schema["key_fields"], f"{table} has empty key_fields"
        for field in schema["key_fields"]:
            assert "name" in field
            assert "type" in field


def test_reservation_name_includes_vines_filter_in_tips():
    blob = " ".join(GLOBAL_TIPS).upper()
    assert "VINES" in blob
    assert "NAME_TYPE" in blob


def test_name_dollar_notes_documents_quoting():
    desc = SCHEMA["NAME$NOTES"]["description"]
    assert '"NAME$NOTES"' in desc or "double-quote" in desc.lower()
