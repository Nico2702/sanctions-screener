"""
Name matching utilities for the NaroIX Sanctions Screener.

The main job: take a masterfile row with a primary name + 6 alternative names,
and produce a compact, deduplicated set of query variants that maximizes
the chance of hitting Dilisense's name-based matching.

Key learning from the SenseTime test: OFAC lists entities with exact
legal suffixes (e.g. "SenseTime Group Limited") that may differ from
the name variants in the masterfile. So we permute legal suffixes.
"""

from __future__ import annotations

import re
from typing import Iterable


# Share-class markers that should be stripped before generating suffix variants.
# Examples: "UBTECH ROBOTICS CORP LTD CLASS H", "SAMSUNG ELECTRONICS CO LTD GDR"
SHARE_CLASS_PATTERNS = [
    r"\s+CLASS\s+[A-Z]\b",
    r"\s+CLASS\s+\d+\b",
    r"\s+GDR\b",
    r"\s+ADR\b",
    r"\s+SPONSORED\s+GDR\b",
    r"\s+SPONSORED\s+ADR\b",
    r"\s+-\s+[A-Z]\s+SHARES?\b",
]

# Legal suffixes to add/remove. Ordered roughly by global prevalence.
# Note: 'Group' is deliberately NOT in this list because it's a meaningful
# name component (e.g. "SenseTime Group"), not a legal suffix.
LEGAL_SUFFIXES = [
    "Limited", "Ltd", "Ltd.",
    "Corporation", "Corp.", "Corp",
    "Incorporated", "Inc.", "Inc",
    "Company", "Co.", "Co",
    "AG", "SA", "SE", "NV", "PLC", "Plc",
]

# A compact subset used when generating variants — we don't need every
# possible form, just the ones most commonly seen in sanctions listings.
# This keeps the Dilisense query count per entity low.
LEGAL_SUFFIXES_CORE = [
    "Limited", "Ltd",
    "Corporation", "Corp.",
    "Inc.",
    "Co., Ltd.",
    "Group Limited",
]

# Regex to detect and strip any of the above suffixes from the end of a name.
_SUFFIX_STRIP_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(s) for s in sorted(LEGAL_SUFFIXES, key=len, reverse=True)) + r")\.?$",
    re.IGNORECASE,
)


def clean_share_class(name: str) -> str:
    """Remove share class markers like 'Class H', 'GDR' etc."""
    out = name
    for pat in SHARE_CLASS_PATTERNS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    return out.strip()


def strip_legal_suffix(name: str) -> str:
    """
    Remove trailing legal suffix (once). 'SenseTime Group Limited' -> 'SenseTime Group'.
    Leaves 'SenseTime Group' unchanged.
    """
    return _SUFFIX_STRIP_RE.sub("", name).strip().rstrip(",")


def get_base_name(name: str) -> str:
    """
    Normalize a masterfile name to its 'base' form:
    remove share class markers AND trailing legal suffix.
    """
    cleaned = clean_share_class(name)
    return strip_legal_suffix(cleaned)


def generate_legal_variants(base: str) -> list[str]:
    """
    Given a base name (no suffix), generate the common legal-suffix permutations.
    Uses LEGAL_SUFFIXES_CORE (a compact ~7-item set) to keep API cost low.
    Dedup is caller's responsibility.
    """
    if not base:
        return []
    variants: list[str] = [base]  # base form itself (no suffix)
    for suffix in LEGAL_SUFFIXES_CORE:
        variants.append(f"{base} {suffix}")
    return variants


def build_query_names(
    primary_name: str,
    alternatives: Iterable[str] = (),
    *,
    include_legal_variants: bool = True,
    max_queries: int = 12,
) -> list[str]:
    """
    Main entry point.

    Produces a deduplicated list of name variants to send to Dilisense,
    combining the primary name, alternative spellings, and (optionally)
    legal-suffix permutations of each base name.

    `max_queries` caps the output to keep API call cost predictable.
    Dilisense counts each name in a batch as one query.
    """
    candidates: list[str] = []

    # Step 1: collect all raw names from the masterfile row
    raw_names = [primary_name] + [a for a in alternatives if a and str(a).strip()]
    raw_names = [str(n).strip() for n in raw_names if n and str(n).strip()]

    # Step 2: for each raw name, always include the cleaned form (no share class)
    # and optionally its legal-suffix variants
    base_forms: set[str] = set()
    for raw in raw_names:
        cleaned = clean_share_class(raw)
        candidates.append(cleaned)
        if include_legal_variants:
            base = get_base_name(raw)
            if base and base.lower() not in {b.lower() for b in base_forms}:
                base_forms.add(base)
                candidates.extend(generate_legal_variants(base))

    # Step 3: deduplicate (case-insensitive) while preserving order
    seen: set[str] = set()
    out: list[str] = []
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
#
# These are the known Dilisense source_ids for the most critical lists.
# The list is not exhaustive; any unknown source_id is categorized as "other"
# and displayed in Tier 3 by default.

SOURCE_TIER_MAP: dict[str, dict[str, str]] = {
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


def get_source_info(source_id: str) -> dict[str, str]:
    """
    Look up display metadata for a Dilisense source_id.
    Unknown source_ids fall back to a Tier 3 'Other' entry.
    """
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


# Known program codes -> human-readable legal basis.
PROGRAM_LEGAL_BASIS: dict[str, str] = {
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
    # Exact match first
    if program_code in PROGRAM_LEGAL_BASIS:
        return PROGRAM_LEGAL_BASIS[program_code]
    # Prefix match (CMIC-EO13959-X -> CMIC-EO13959)
    for key, val in PROGRAM_LEGAL_BASIS.items():
        if program_code.startswith(key):
            return val
    return program_code  # fall back to the raw code
