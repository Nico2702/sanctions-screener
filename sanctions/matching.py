"""
Name matching utilities for the NaroIX Sanctions Screener.

Philosophy: send clean base names (without legal suffixes, without share class
markers) to Dilisense and let its built-in fuzzy matching handle the suffix
variations. This produces fewer queries, fewer false positives, and faster
responses than generating dozens of permutations manually.

Key learning from the SenseTime test: OFAC lists "SenseTime Group Limited"
but our masterfile has "SenseTime Group, Inc." — with fuzzy_search=2 Dilisense
matches these correctly. We just need to make sure we send the clean base form.
"""

from __future__ import annotations

import re
from typing import Iterable


# Share-class markers that should be stripped before querying.
# Examples: "UBTECH ROBOTICS CORP LTD CLASS H", "SAMSUNG ELECTRONICS CO LTD GDR"
SHARE_CLASS_PATTERNS = [
    r"\s+CLASS\s+[A-Z]\b",
    r"\s+CLASS\s+\d+\b",
    r"\s+SPONSORED\s+GDR\b",
    r"\s+SPONSORED\s+ADR\b",
    r"\s+SPONSORED\b",       # trailing "Sponsored" alone
    r"\s+GDR\b",
    r"\s+ADR\b",
    r"\s+-\s+[A-Z]\s+SHARES?\b",
]

# Legal suffixes used for (optional) base-name stripping.
# We intentionally don't generate permutations from this list — Dilisense's
# built-in fuzzy matching handles those variations.
LEGAL_SUFFIXES = [
    "Limited", "Ltd", "Ltd.",
    "Corporation", "Corp.", "Corp",
    "Incorporated", "Inc.", "Inc",
    "Company", "Co.", "Co",
    "AG", "SA", "SE", "NV", "PLC", "Plc",
]

_SUFFIX_STRIP_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(s) for s in sorted(LEGAL_SUFFIXES, key=len, reverse=True)) + r")\.?$",
    re.IGNORECASE,
)


def clean_share_class(name: str) -> str:
    """Remove share class markers like 'Class H', 'GDR' etc."""
    out = name
    for pat in SHARE_CLASS_PATTERNS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    return out.strip().rstrip(",")


def strip_legal_suffix(name: str) -> str:
    """
    Remove trailing legal suffix (once).
    'SenseTime Group Limited' -> 'SenseTime Group'
    """
    return _SUFFIX_STRIP_RE.sub("", name).strip().rstrip(",")


def get_base_name(name: str) -> str:
    """Remove share class markers AND trailing legal suffix."""
    return strip_legal_suffix(clean_share_class(name))


def build_query_names(
    primary_name: str,
    alternatives: Iterable = (),
    *,
    include_base: bool = True,
    max_queries: int = 10,
) -> list:
    """
    Main entry point.

    Produces a deduplicated list of name variants to send to Dilisense.
    Strategy: send cleaned versions of primary name + alternatives, plus
    the base form (without legal suffix) of the primary name.

    Does NOT generate legal-suffix permutations — Dilisense's fuzzy_search=2
    handles those natively.

    `max_queries` caps the output to keep API call cost predictable.
    """
    candidates = []

    # Primary name cleaned (share class removed)
    primary_cleaned = clean_share_class(str(primary_name).strip())
    if primary_cleaned:
        candidates.append(primary_cleaned)

    # Base form of primary (share class + legal suffix removed)
    if include_base:
        primary_base = get_base_name(str(primary_name).strip())
        if primary_base and primary_base.lower() != primary_cleaned.lower():
            candidates.append(primary_base)

    # All non-empty alternatives, cleaned
    for alt in alternatives:
        if not alt or not str(alt).strip():
            continue
        alt_str = str(alt).strip()
        alt_cleaned = clean_share_class(alt_str)
        if alt_cleaned:
            candidates.append(alt_cleaned)

    # Deduplicate case-insensitively, preserve order
    seen = set()
    out = []
    for name in candidates:
        key = name.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(name)
        if len(out) >= max_queries:
            break

    return out


# ---------------------------------------------------------------------------
# Source ID → Human-readable + tier mapping
# ---------------------------------------------------------------------------


# Tier 1 = EU / UN / OFAC SDN — formally binding for NaroIX as EU index provider
# Tier 2 = OFAC Non-SDN (NS-CMIC, SSI etc.) + BIS + DoD CMC — US restrictions
# Tier 3 = Other jurisdictions — info only

SOURCE_TIER_MAP = {
    # --- Tier 1: EU / UN / OFAC SDN ---
    "eu_financial_sanction_list": {
        "tier": "1",
        "display_name": "EU Consolidated Financial Sanctions",
        "jurisdiction": "EU",
        "primary_url": "https://webgate.ec.europa.eu/fsd/fsf",
    },
    "un_consolidated_sanctions_list": {
        "tier": "1",
        "display_name": "UN Security Council Consolidated",
        "jurisdiction": "UN",
        "primary_url": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
    },
    "us_department_of_treasury_sdn": {
        "tier": "1",
        "display_name": "OFAC SDN List",
        "jurisdiction": "USA",
        "primary_url": "https://sanctionssearch.ofac.treas.gov/",
    },

    # --- Tier 2: US Non-SDN and related ---
    "us_department_of_treasury_non_sdn": {
        "tier": "2",
        "display_name": "OFAC Non-SDN Consolidated",
        "jurisdiction": "USA",
        "primary_url": "https://ofac.treasury.gov/other-ofac-sanctions-lists",
    },
    "us_bis_entity_list": {
        "tier": "2",
        "display_name": "BIS Entity List (Export Controls)",
        "jurisdiction": "USA",
        "primary_url": "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/entity-list",
    },
    "us_dod_section_1260h_ndaa": {
        "tier": "2",
        "display_name": "DoD Chinese Military Companies (Sec. 1260H)",
        "jurisdiction": "USA",
        "primary_url": "https://www.defense.gov/News/Releases/",
    },

    # --- Tier 3: Other jurisdictions ---
    "uk_hmt_consolidated_list": {
        "tier": "3",
        "display_name": "UK HMT Consolidated",
        "jurisdiction": "UK",
        "primary_url": "https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets",
    },
    "ch_seco_sanctions": {
        "tier": "3",
        "display_name": "Swiss SECO Sanctions",
        "jurisdiction": "CH",
        "primary_url": "https://www.seco.admin.ch/seco/en/home/Aussenwirtschaftspolitik_Wirtschaftliche_Zusammenarbeit/Wirtschaftsbeziehungen/exportkontrollen-und-sanktionen/sanktionen-embargos.html",
    },
}


def get_source_info(source_id: str) -> dict:
    """Look up display metadata for a Dilisense source_id."""
    info = SOURCE_TIER_MAP.get(source_id)
    if info:
        return info
    return {
        "tier": "3",
        "display_name": source_id.replace("_", " ").title(),
        "jurisdiction": "Unknown",
        "primary_url": "",
    }


# ---------------------------------------------------------------------------
# Legal basis extraction
# ---------------------------------------------------------------------------


PROGRAM_LEGAL_BASIS = {
    "CMIC-EO13959": "Executive Order 13959, as amended by EO 14032 (CMIC)",
    "CMIC-EO14032": "Executive Order 14032 (CMIC)",
    "RUSSIA-EO14024": "Executive Order 14024 (Russia Harmful Foreign Activities)",
    "UKRAINE-EO13660": "Executive Order 13660 (Ukraine/Russia)",
    "UKRAINE-EO13661": "Executive Order 13661 (Ukraine/Russia)",
    "UKRAINE-EO13662": "Executive Order 13662 (Ukraine/Russia)",
    "IRAN-EO13599": "Executive Order 13599 (Iranian Government)",
}


def lookup_legal_basis(program_code: str) -> str:
    """Map a program code like 'CMIC-EO13959' to a readable legal basis."""
    if not program_code:
        return ""
    if program_code in PROGRAM_LEGAL_BASIS:
        return PROGRAM_LEGAL_BASIS[program_code]
    for key, val in PROGRAM_LEGAL_BASIS.items():
        if program_code.startswith(key):
            return val
    return program_code
