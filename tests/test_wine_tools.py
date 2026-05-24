"""Tests for the wine-label → owner lookup tool."""

from unittest.mock import MagicMock, patch

from src.wine_tools import (
    _brand_variants,
    _find_wines,
    _normalize,
    _redact_secrets,
    _score,
    lookup_wine_owner,
    register_tools,
)


class TestRegistration:
    def test_registers_wine_owner_lookup(self):
        mcp = MagicMock()
        captured = {}

        def capture_tool():
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register_tools(mcp)
        assert "wine_owner_lookup" in captured


class TestNormalizeAndScore:
    def test_normalize_strips_accents_and_punctuation(self):
        assert _normalize("Uco´s Playground") == "uco s playground"
        assert _normalize("Uco's Playground") == "uco s playground"
        assert _normalize("El Padán") == "el padan"

    def test_exact_match_scores_one(self):
        assert _score("Dos Abogados", "Dos Abogados") == 1.0

    def test_apostrophe_vs_accent_is_exact(self):
        # OCR yields a straight apostrophe; NetSuite stores an acute accent.
        assert _score("Uco's Playground", "Uco´s Playground") == 1.0

    def test_extra_label_words_still_match_strongly(self):
        assert _score("Dos Abogados Malbec 2019", "Dos Abogados") >= 0.9

    def test_multi_value_brand_field(self):
        variants = _brand_variants("3 Cabras Locas, Nana, Enea Enea")
        assert "Nana" in variants
        assert _score("La Amante", "La Amante / La Amante Poco Loco") >= 0.9

    def test_unrelated_brand_scores_low(self):
        assert _score("Random Other Wine", "Dos Abogados") < 0.35

    def test_lone_fragment_is_only_partial(self):
        # A single common word that's merely a substring of a longer brand must
        # NOT score as a confident match (>=0.7 would trip "matched").
        assert _score("Smith", "Smith Family Estate") < 0.7


class TestRedactSecrets:
    def test_masks_password_like_values(self):
        out = _redact_secrets("OIC password: Carlson151! and clave=abc123")
        assert "Carlson151!" not in out
        assert "abc123" not in out
        assert "[REDACTED]" in out

    def test_leaves_ordinary_notes_intact(self):
        note = "Sensitive lawyers, CC both partners on everything."
        assert _redact_secrets(note) == note

    def test_handles_non_string(self):
        assert _redact_secrets(None) is None


class TestFindWines:
    def test_generic_only_brand_does_not_scan_catalog(self):
        # Brand made only of stopwords + no owner code → nothing distinctive to
        # search on, so we must NOT issue a catalog-wide query (cross-owner bleed).
        with patch("src.wine_tools.suiteql_query") as mock_q:
            assert _find_wines("Reserva Malbec", None) == []
            mock_q.assert_not_called()

    def test_drops_packaging_component_skus(self):
        # CT-/ET-/ST- prefixed SKUs are carton/label/sticker components (incl. for
        # the owner's OTHER brands), not finished wines — they must be dropped even
        # when their displayname doesn't equal the queried brand.
        def side_effect(query, *a, **k):
            q = query.lower()
            if "from item" in q:
                return [
                    {"id": "1", "itemid": "VTA3322-18 DOS ABOGADOS Malbec BF 2018 750ML",
                     "displayname": None, "itemtype": "InvtPart"},
                    {"id": "2", "itemid": "ET-MBRRBPR-CARD-01", "displayname": "Primrose",
                     "itemtype": "InvtPart"},
                    {"id": "3", "itemid": "CT-MBRRBPR-CARD-01", "displayname": "Primrose",
                     "itemtype": "InvtPart"},
                    {"id": "4", "itemid": "Dos Abogados SP Malbec", "displayname": None,
                     "itemtype": "Assembly"},
                ]
            if "from inventorybalance" in q:
                return [{"item": "1", "qavail": "264", "qoh": "264"}]
            return []

        with patch("src.wine_tools.suiteql_query", side_effect=side_effect):
            wines = _find_wines("Dos Abogados", "CARD")
        ids = {w["item_id"] for w in wines}
        assert ids == {"1", "4"}  # VTA bottle + Assembly kept; ET-/CT- dropped

    def test_non_numeric_quantity_does_not_crash_sort(self):
        def side_effect(query, *a, **k):
            q = query.lower()
            if "from item" in q:
                return [{"id": "1", "itemid": "Pyrus SP Malbec", "displayname": None,
                         "itemtype": "Assembly"}]
            if "from inventorybalance" in q:
                return [{"item": "1", "qavail": "n/a", "qoh": None}]
            return []

        with patch("src.wine_tools.suiteql_query", side_effect=side_effect):
            wines = _find_wines("Pyrus", "BRAD")
        assert len(wines) == 1
        assert wines[0]["quantity_available"] == "n/a"  # surfaced, didn't crash


def _fake_query(*, candidates, items=None, balances=None):
    """Build a suiteql_query side_effect that routes by query text."""
    items = items or []
    balances = balances or []

    def _side_effect(query, *args, **kwargs):
        q = query.lower()
        if "from customer" in q:
            return candidates
        if "from inventorybalance" in q:
            return balances
        if "from item" in q:
            return items
        return []

    return _side_effect


CANDIDATES = [
    {"id": "681", "entityid": "Evans, Michael Haydn", "companyname": "Evans, Michael Haydn",
     "brand": "Uco´s Playground", "owner_code": "EVAM", "isinactive": "F"},
    {"id": "647", "entityid": "Carlson, Donald", "companyname": "Carlson, Donald",
     "brand": "Dos Abogados", "owner_code": "CARD", "isinactive": "F"},
]

OWNER_RECORD = {
    "id": "681", "companyName": "Evans, Michael Haydn", "entityId": "Evans, Michael Haydn",
    "email": "michael@vinesofmendoza.com", "custentity_vom_winebrandname": "Uco´s Playground",
    "custentity_vom_ownercode": "EVAM", "custentity_vom_salesforceid": "003i000000GwGys",
    "custentity37": "Evans Vineyard", "custentity_vom_numberoflots": 2,
    "category": {"refName": "Ownership Group"}, "isInactive": False,
}


class TestLookupWineOwner:
    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_matched_owner_with_inventory_and_enrichment(self, mock_q, mock_get, mock_360):
        mock_q.side_effect = _fake_query(
            candidates=CANDIDATES,
            items=[
                {"id": "29180", "itemid": "Uco's Playground SP Cabernet", "displayname": None,
                 "itemtype": "Assembly"},
                # Pure packaging component (named exactly after the brand) — dropped.
                {"id": "20999", "itemid": "ET-CFRRESP-EVAM-02", "displayname": "Uco´s Playground",
                 "itemtype": "InvtPart"},
            ],
            balances=[{"item": "29180", "qavail": "120", "qoh": "120"}],
        )
        mock_get.return_value = OWNER_RECORD
        mock_360.return_value = {"identity": {"first_name": "Michael"}, "stays": []}

        r = lookup_wine_owner("Uco's Playground Malbec Cab Franc 2014")

        assert r["match_status"] == "matched"
        assert r["owner"]["customer_id"] == "681"
        assert r["owner"]["name"] == "Evans, Michael Haydn"
        assert r["owner"]["vineyard"] == "Evans Vineyard"
        assert r["owner"]["category"] == "Ownership Group"
        # Packaging component filtered out; only the finished wine remains.
        assert [w["item_id"] for w in r["wines"]] == ["29180"]
        assert r["wines"][0]["quantity_available"] == "120"
        assert r["enrichment_status"] == "ok"
        assert r["guest_profile"]["identity"]["first_name"] == "Michael"

    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_enrichment_failure_still_returns_owner(self, mock_q, mock_get, mock_360):
        mock_q.side_effect = _fake_query(candidates=CANDIDATES, items=[], balances=[])
        mock_get.return_value = OWNER_RECORD
        mock_360.side_effect = Exception("Salesforce down")

        r = lookup_wine_owner("Uco's Playground")

        assert r["match_status"] == "matched"
        assert r["owner"]["customer_id"] == "681"
        assert r["enrichment_status"].startswith("unavailable")

    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_not_found_returns_alternates(self, mock_q, mock_get, mock_360):
        mock_q.side_effect = _fake_query(candidates=CANDIDATES)
        r = lookup_wine_owner("Chateau Margaux 1995")
        assert r["match_status"] == "not_found"
        assert r["owner"] is None
        mock_get.assert_not_called()

    def test_blank_label_is_rejected(self):
        r = lookup_wine_owner("   ")
        assert "error" in r
        assert r["match_status"] == "not_found"

    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_lone_partial_match_is_low_confidence(self, mock_q, mock_get, mock_360):
        candidates = [
            {"id": "100", "entityid": "Smith, John", "companyname": "Smith, John",
             "brand": "Smith Family Estate", "owner_code": "SMIJ", "isinactive": "F"},
        ]
        mock_q.side_effect = _fake_query(candidates=candidates, items=[], balances=[])
        mock_get.return_value = {"id": "100", "companyName": "Smith, John",
                                 "custentity_vom_winebrandname": "Smith Family Estate"}
        r = lookup_wine_owner("Smith")
        assert r["match_status"] == "low_confidence"
        assert r["match_confidence"] == "low"
        assert r["owner"]["customer_id"] == "100"  # best guess still returned

    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_owner_comments_are_redacted(self, mock_q, mock_get, mock_360):
        mock_q.side_effect = _fake_query(candidates=CANDIDATES, items=[], balances=[])
        mock_get.return_value = {
            **OWNER_RECORD,
            "comments": "Buyout done. OIC password: Carlson151!",
        }
        r = lookup_wine_owner("Uco's Playground")
        assert "Carlson151!" not in r["owner"]["comments"]
        assert "[REDACTED]" in r["owner"]["comments"]

    @patch("src.wine_tools.guest_360")
    @patch("src.wine_tools.rest_get")
    @patch("src.wine_tools.suiteql_query")
    def test_ambiguous_co_ownership(self, mock_q, mock_get, mock_360):
        shared = [
            {"id": "724", "entityid": "Heylen, Wim", "companyname": "Heylen, Wim",
             "brand": "Angelus XXI", "owner_code": "HEYW", "isinactive": "T"},
            {"id": "8503", "entityid": "De Krock, Ann", "companyname": "De Krock, Ann",
             "brand": "Angelus XXI", "owner_code": None, "isinactive": "T"},
        ]
        mock_q.side_effect = _fake_query(candidates=shared, items=[], balances=[])
        mock_get.return_value = {"id": "724", "companyName": "Heylen, Wim",
                                 "custentity_vom_winebrandname": "Angelus XXI"}
        r = lookup_wine_owner("Angelus XXI")
        assert r["match_status"] == "ambiguous"
        assert any(a["customer_id"] == "8503" for a in r["alternates"])
