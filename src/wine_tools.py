"""Wine-label → owner lookup, purpose-built for the restaurant sommelier use case.

The sommelier photographs a bottle made on-property and wants to know whose wine
it is. The authoritative link is the proprietary brand printed on the label, stored
on the owner's customer record in ``custentity_vom_winebrandname`` (there is no
owner foreign key on the wine item itself). See the curated NetSuite notes for the
field map.

``wine_owner_lookup`` does the whole job in one MCP round-trip:
  1. fuzzy-match the label brand text against every brand-bearing customer,
  2. pull the matched owner's full record,
  3. find the owner's wines + remaining inventory,
  4. best-effort enrich with the cross-system guest_360 profile (in-house status,
     stay history, financials) — wrapped so a Salesforce/OPERA outage degrades to
     a NetSuite-only answer instead of failing.
"""

from __future__ import annotations

import datetime
import re
import unicodedata

from src.ns_client import suiteql_query, rest_get
from src.cross_tools import guest_360

# Brand fields are multi-valued in practice — one customer's label can list
# several names ("La Amante / La Amante Poco Loco", "3 Cabras Locas, Nana, Enea").
_BRAND_SPLIT = re.compile(r"\s*(?:[,/&·•]|\s-\s|\bx\b)\s*", re.IGNORECASE)

# Generic words that carry no brand signal — ignored when scoring so a label
# reading "Malbec Super Premium" doesn't spuriously match on those tokens.
_STOPWORDS = frozenset(
    {
        "wine", "wines", "winery", "vineyard", "vineyards", "bodega", "vinedos",
        "the", "and", "de", "la", "el", "los", "las", "del", "di", "do", "dos",
        "malbec", "cabernet", "sauvignon", "merlot", "bonarda", "syrah", "blend",
        "red", "white", "rose", "tinto", "blanco", "reserva", "premium", "super",
        "750", "ml", "cc", "vintage", "estate", "ros",
    }
)


# Mask credential-like values in free-text notes before returning them. The
# owner `comments` field is internal CRM text that has been observed to contain
# things like an OIC password; the restaurant-facing tool should never echo them.
_SECRET_RE = re.compile(
    r"(?i)\b(oic[\s_]*password|passwords?|passwd|pwd|contrase[nñ]a|clave|"
    r"api[\s_-]*key|secret)\b\s*[:=]?\s*\S+"
)


def _redact_secrets(text):
    """Mask password/secret-like values in free-text, keeping the surrounding note."""
    if not isinstance(text, str) or not text:
        return text

    def _mask(m: re.Match) -> str:
        keyword = re.match(r"(?i)[a-zñ\s_-]+", m.group(0)).group(0).rstrip(" :=")
        return f"{keyword}: [REDACTED]"

    return _SECRET_RE.sub(_mask, text)


# The Vines' packaging/label/sticker component SKUs are prefixed CT- (carton),
# ET- (etiqueta/label), or ST- (sticker) — not finished wine bottles.
_COMPONENT_SKU_RE = re.compile(r"(?i)^(?:ct|et|st)-")


def _to_float(value) -> float:
    """Coerce a quantity to float, defaulting to 0.0 for None/non-numeric values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize(value: str) -> str:
    """Lowercase, strip accents, and reduce to single-spaced alphanumerics."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9]+", " ", stripped.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokens(value: str, *, drop_stopwords: bool = True) -> set[str]:
    toks = {t for t in _normalize(value).split() if len(t) >= 2}
    if drop_stopwords:
        meaningful = toks - _STOPWORDS
        # Keep stopwords only if dropping them would leave nothing to match on.
        return meaningful or toks
    return toks


def _brand_variants(brand_field: str) -> list[str]:
    """Split a possibly multi-valued brand field into individual brand names."""
    variants = [v.strip() for v in _BRAND_SPLIT.split(brand_field) if v.strip()]
    if brand_field.strip() and brand_field.strip() not in variants:
        variants.append(brand_field.strip())
    return variants


def _score(query: str, brand_field: str) -> float:
    """Similarity in [0, 1] between the label query and a customer's brand field.

    Scores each individual brand variant and returns the best. Combines exact
    match, substring containment, and token overlap so partial/OCR-noisy label
    reads still rank their owner first.
    """
    q_norm = _normalize(query)
    q_tokens = _tokens(query)
    if not q_norm or not q_tokens:
        return 0.0

    best = 0.0
    for variant in _brand_variants(brand_field):
        v_norm = _normalize(variant)
        if not v_norm:
            continue
        if q_norm == v_norm:
            return 1.0
        v_tokens = _tokens(variant)
        if not v_tokens:
            continue

        score = 0.0
        # Directional containment: the WHOLE brand appearing in the label (plus
        # varietal/vintage extras) is a strong signal. The label being only a
        # FRAGMENT of a larger brand is weaker, scaled by how much it covers — so
        # a single common word ("Smith") can't masquerade as a confident match.
        if v_norm in q_norm:
            score = 0.9
        elif q_norm in v_norm:
            score = 0.5 + 0.4 * (len(q_norm) / len(v_norm))
        # Token overlap weighted toward BRAND coverage (unmatched brand tokens
        # are penalized; extra label words are tolerated).
        overlap = q_tokens & v_tokens
        if overlap:
            jaccard = len(overlap) / len(q_tokens | v_tokens)
            brand_coverage = len(overlap) / len(v_tokens)
            score = max(score, 0.4 * jaccard + 0.5 * brand_coverage)
        best = max(best, score)
    return best


def _ref_name(field):
    """Extract the human-readable refName from a NetSuite reference field."""
    if isinstance(field, dict):
        return field.get("refName") or field.get("id")
    return field


def _owner_from_record(record: dict) -> dict:
    """Map a full customer REST record to a compact owner summary."""
    g = record.get
    return {
        "customer_id": g("id"),
        "name": g("companyName") or g("entityId"),
        "nickname": g("custentity_vom_nickname"),
        "brand": g("custentity_vom_winebrandname"),
        "owner_code": g("custentity_vom_ownercode") or g("custentity_ce_ownercode"),
        "vineyard": g("custentity37"),
        "category": _ref_name(g("category")),
        "registration_status": _ref_name(g("custentityvom_customer_registration_stat")),
        "email": g("email"),
        "secondary_email": g("custentity_secondaryemail"),
        "phone": g("phone"),
        "alt_phone": g("altPhone"),
        "account_manager": _ref_name(g("custentity_vom_csam")),
        "happiness_level": _ref_name(g("custentity_vom_happinesslevel")),
        "number_of_lots": g("custentity_vom_numberoflots"),
        "website": g("custentity_vom_clientwineprofile"),
        "salesforce_id": g("custentity_vom_salesforceid"),
        "is_inactive": g("isInactive"),
        "comments": _redact_secrets(g("comments")),
    }


def _find_wines(brand: str, owner_code: str | None = None) -> list[dict]:
    """Find an owner's active on-property wine items, with stock.

    There is no owner foreign key on the item, so we scope the search to the
    brand's DISTINCTIVE tokens (generic words like "Malbec"/"Reserva" are excluded
    to avoid pulling other owners' wines) plus the owner code (e.g. CARD/EVAM),
    which is embedded in the owner's bottle/label SKUs. If neither yields anything
    to search on, we return nothing rather than scanning the whole catalog.

    Stock is not exposed on the unified ``item`` table in SuiteQL — it lives in
    ``inventorybalance``, queried separately. Pure packaging/label components
    (named exactly after the brand, e.g. the CT-/ET-/ST- SKUs) are dropped;
    finished wines (Assembly) and descriptive sellable bottle SKUs are kept.
    """
    distinctive = sorted(
        {t for t in _normalize(brand).split() if len(t) >= 3 and t not in _STOPWORDS},
        key=len,
        reverse=True,
    )[:3]
    clauses = [
        f"LOWER(displayname) LIKE '%{t}%' OR LOWER(itemid) LIKE '%{t}%'"
        for t in distinctive
    ]
    code = re.sub(r"[^a-z0-9]", "", (owner_code or "").lower())
    if len(code) >= 3:
        clauses.append(f"LOWER(itemid) LIKE '%{code}%'")
    if not clauses:
        return []

    likes = " OR ".join(clauses)
    rows = suiteql_query(
        "SELECT id, itemid, displayname, itemtype FROM item "
        "WHERE isinactive = 'F' AND itemtype IN ('Assembly', 'InvtPart') "
        f"AND ({likes}) FETCH FIRST 100 ROWS ONLY"
    )
    brand_norm = _normalize(brand)
    items: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict) or "error" in r:
            continue
        try:
            key = str(int(r.get("id")))  # validate ids are integers before reuse
        except (TypeError, ValueError):
            continue
        itemid = r.get("itemid") or ""
        name = r.get("displayname") or itemid
        if r.get("itemtype") != "Assembly" and (
            _COMPONENT_SKU_RE.match(itemid) or _normalize(name) == brand_norm
        ):
            continue  # carton/label/sticker component, not a finished wine
        items[key] = {
            "item_id": r.get("id"),
            "name": name,
            "type": r.get("itemtype"),
            "quantity_available": None,
            "quantity_on_hand": None,
        }

    if items:
        ids_csv = ", ".join(items.keys())  # keys are int-validated above
        try:
            balances = suiteql_query(
                "SELECT item, SUM(quantityavailable) AS qavail, "
                "SUM(quantityonhand) AS qoh FROM inventorybalance "
                f"WHERE item IN ({ids_csv}) GROUP BY item"
            )
            for b in balances:
                if not isinstance(b, dict) or "error" in b:
                    continue
                it = items.get(str(b.get("item")))
                if it:
                    it["quantity_available"] = b.get("qavail")
                    it["quantity_on_hand"] = b.get("qoh")
        except Exception:  # noqa: BLE001 — inventory is best-effort
            pass

    wines = list(items.values())
    wines.sort(key=lambda w: -_to_float(w["quantity_available"]))
    return wines


def _parse_date(value):
    """Parse a NetSuite date string (M/D/YYYY or YYYY-MM-DD) to a date, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _aging_months(blended, bottled):
    """Approx élevage in months between the blending session and bottling date."""
    d1, d2 = _parse_date(blended), _parse_date(bottled)
    if not d1 or not d2 or d2 < d1:
        return None
    return round((d2 - d1).days / 30.44, 1)


def _winemaking_details(customer_id) -> list[dict]:
    """Per-bottling winemaking detail for an owner (barrel, blend, ABV, aging).

    Source: customrecord_vom_bottledetails, keyed to the owner via
    custrecord_vom_bd_customer. The barrel-aging duration isn't a stored field —
    it's approximated from the blending-session → bottling dates (an élevage
    window). Fields are populated unevenly (manual entry), so values may be null.
    """
    try:
        cid = int(customer_id)
    except (TypeError, ValueError):
        return []
    rows = suiteql_query(
        "SELECT custrecord_vom_bd_vintage AS vintage, "
        "BUILTIN.DF(custrecord_vom_bd_varietal) AS varietal, "
        "BUILTIN.DF(custrecord_vom_bd_winetype) AS wine_type, "
        "BUILTIN.DF(custrecord_vom_bd_quality) AS quality, "
        "custrecord_vom_bd_finalblend AS blend, "
        "custrecord_vom_bd_labelalcohol AS abv, "
        "custrecord_vom_bd_barrel_name AS barrel_name, "
        "custrecord_vom_bd_no_1st_use_barrels AS new_oak_barrels, "
        "custrecord_vom_bd_blendingsession AS blended, "
        "custrecord_vom_bd_actbottlingdate AS bottled "
        f"FROM customrecord_vom_bottledetails WHERE custrecord_vom_bd_customer = {cid} "
        "ORDER BY custrecord_vom_bd_vintage DESC FETCH FIRST 50 ROWS ONLY"
    )
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict) or "error" in r:
            continue
        out.append(
            {
                "vintage": r.get("vintage"),
                "varietal": r.get("varietal"),
                "wine_type": r.get("wine_type"),
                "quality": r.get("quality"),
                "blend": r.get("blend"),
                "abv": r.get("abv"),
                "barrel_name": r.get("barrel_name"),
                "new_oak_barrels": r.get("new_oak_barrels"),
                "blended_date": r.get("blended"),
                "bottled_date": r.get("bottled"),
                "aging_months_est": _aging_months(r.get("blended"), r.get("bottled")),
            }
        )
    return out


def lookup_wine_owner(
    label_text: str,
    include_inventory: bool = True,
    include_winemaking: bool = True,
    enrich_guest_profile: bool = True,
) -> dict:
    """Resolve a bottle-label brand to its owner. Backs the wine_owner_lookup tool."""
    result: dict = {
        "query": {"label_text": label_text},
        "match_status": "not_found",
        "owner": None,
        "alternates": [],
        "wines": [],
        "winemaking": [],
        "guest_profile": None,
        "enrichment_status": "skipped",
    }

    if not label_text or not label_text.strip():
        result["error"] = "label_text is required (the brand/name read from the bottle label)."
        return result

    # 1. Pull every brand-bearing customer and rank in Python. One cheap query
    #    keeps matching robust against accents and multi-value fields that
    #    SuiteQL LIKE handles poorly.
    try:
        candidates = suiteql_query(
            "SELECT id, entityid, companyname, "
            "custentity_vom_winebrandname AS brand, "
            "custentity_vom_ownercode AS owner_code, isinactive "
            "FROM customer WHERE custentity_vom_winebrandname IS NOT NULL"
        )
    except Exception as e:  # noqa: BLE001 — surface as data, not a 500
        result["error"] = f"Failed to query NetSuite customers: {e}"
        return result

    scored = []
    for c in candidates:
        if not isinstance(c, dict) or "error" in c:
            continue
        s = _score(label_text, c.get("brand") or "")
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    def _slim(s, c):
        return {
            "customer_id": c.get("id"),
            "name": c.get("companyname") or c.get("entityid"),
            "brand": c.get("brand"),
            "score": round(s, 2),
        }

    if not scored or scored[0][0] < 0.35:
        result["match_status"] = "not_found"
        result["message"] = (
            f"No owner brand matched {label_text!r}. The wine may be a third-party "
            "label (not made on-property) or the brand name needs to be read more "
            "precisely."
        )
        result["alternates"] = [_slim(s, c) for s, c in scored[:5]]
        return result

    top_score, top = scored[0]
    runners = [(s, c) for s, c in scored[1:] if top_score - s <= 0.15 and s >= 0.5]
    result["alternates"] = [_slim(s, c) for s, c in runners[:5]]
    # Ambiguous when a second owner ties the top score (co-ownership groups
    # sharing one exact brand) or the best is partial with a near-equal runner-up.
    # A lone but only-partial match is flagged "low_confidence" so the caller
    # confirms rather than asserting ownership. A unique strong match is "matched".
    second = scored[1][0] if len(scored) > 1 else 0.0
    is_tie = bool(runners) and abs(top_score - second) < 1e-9
    if is_tie or (runners and top_score < 1.0):
        result["match_status"] = "ambiguous"
    elif top_score >= 0.7:
        result["match_status"] = "matched"
    else:
        result["match_status"] = "low_confidence"
    result["match_score"] = round(top_score, 2)
    result["match_confidence"] = (
        "high" if top_score >= 0.85 else "medium" if top_score >= 0.7 else "low"
    )

    # 2. Full owner record (all custom fields).
    try:
        owner = _owner_from_record(rest_get("customer", str(top["id"])))
    except Exception as e:  # noqa: BLE001
        owner = {  # fall back to the slim row so we still answer "who"
            "customer_id": top.get("id"),
            "name": top.get("companyname") or top.get("entityid"),
            "brand": top.get("brand"),
            "owner_code": top.get("owner_code"),
            "is_inactive": top.get("isinactive") == "T",
            "_detail_error": str(e),
        }
    result["owner"] = owner

    # 3. Owner's wines + remaining stock.
    if include_inventory:
        try:
            result["wines"] = _find_wines(
                owner.get("brand") or label_text, owner.get("owner_code")
            )
        except Exception as e:  # noqa: BLE001
            result["wines"] = []
            result["inventory_error"] = str(e)

    # 3b. Winemaking detail (barrel, blend, ABV, aging window) per bottling.
    if include_winemaking:
        try:
            result["winemaking"] = _winemaking_details(owner.get("customer_id"))
        except Exception as e:  # noqa: BLE001 — best-effort
            result["winemaking"] = []
            result["winemaking_error"] = str(e)

    # 4. Best-effort cross-system enrichment (in-house status, stays, money).
    email = owner.get("email")
    if enrich_guest_profile and email:
        try:
            profile = guest_360(email)
            result["guest_profile"] = profile
            errs = profile.get("_errors") or []
            result["enrichment_status"] = "ok" if not errs else f"partial: {'; '.join(errs)}"
        except Exception as e:  # noqa: BLE001
            result["enrichment_status"] = f"unavailable: {e}"
    elif enrich_guest_profile and not email:
        result["enrichment_status"] = "skipped: owner has no email on file"

    return result


def register_tools(mcp):
    """Register the wine-lookup tool on the given FastMCP instance."""

    @mcp.tool()
    def wine_owner_lookup(
        label_text: str,
        include_inventory: bool = True,
        include_winemaking: bool = True,
        enrich_guest_profile: bool = True,
    ) -> dict:
        """Identify the OWNER of an on-property wine from its bottle-label text.

        Purpose-built for the restaurant sommelier: given the brand/name read off
        a wine label (e.g. "Dos Abogados", optionally with varietal/vintage), find
        whose wine it is and return their contact details, their wines + remaining
        stock, the winemaking detail (barrels, blend, ABV, aging), and — best
        effort — whether they are currently in-house.

        The authoritative key is the proprietary brand on the label, matched
        (accent-insensitive, multi-brand aware) against the owner's
        ``custentity_vom_winebrandname`` customer field. Pass whatever the label
        says; matching tolerates partial/noisy reads.

        Args:
            label_text: Brand / wine name read from the label. Extra words
                (varietal, vintage, "The Vines of Mendoza") are fine — they're
                down-weighted automatically.
            include_inventory: Also return the owner's wine items and on-hand
                quantity (default True).
            include_winemaking: Also return per-bottling winemaking detail —
                blend, ABV, barrel name + new-oak barrel count, and an estimated
                aging window (months between blending and bottling). Fields are
                manually entered so coverage is uneven (default True).
            enrich_guest_profile: Also attach the cross-system guest_360 profile
                (stay history, in-house status, financials) when the owner has an
                email. Best-effort — silently degrades to a NetSuite-only result
                if Salesforce/OPERA are unavailable (default True).

        Returns:
            A dict with: match_status ('matched' | 'low_confidence' | 'ambiguous'
            | 'not_found'), match_confidence ('high'|'medium'|'low'), owner
            (contact + CRM summary; secrets in free-text notes are redacted),
            alternates (other close brand matches), wines (inventory), winemaking
            (per-bottling blend/ABV/barrel/aging — aging_months_est is an
            approximation and may be null), guest_profile (or null), and
            enrichment_status. On 'low_confidence' the owner is a best guess —
            confirm with the user before asserting it.
        """
        return lookup_wine_owner(
            label_text,
            include_inventory=include_inventory,
            include_winemaking=include_winemaking,
            enrich_guest_profile=enrich_guest_profile,
        )
