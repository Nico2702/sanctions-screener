"""
NaroIX Sanctions Screener — Streamlit app (Phase 1 + GitHub OAuth)

Authentication:
    - Uses GitHub OAuth via auth.py (same pattern as eod-financials)
    - Whitelist of GitHub usernames in .streamlit/secrets.toml
    - Call auth.require_login() at the top of main()

Required secrets (see secrets.toml.template):
    DILISENSE_API_KEY = "..."

    [github_oauth]
    client_id = "..."
    client_secret = "..."
    redirect_uri = "https://sanctions-screener.streamlit.app"
    allowed_users = ["Nico2702", ...]
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import auth
from sanctions.dilisense_client import (
    DilisenseAuthError,
    DilisenseClient,
    DilisenseQuotaError,
    ScreeningResult,
    SanctionHit,
)
from sanctions.masterfile import (
    extract_alternatives,
    get_country_of_risk,
    get_row_by_isin,
    load_masterfile,
)
from sanctions.matching import (
    build_query_names,
    get_source_info,
    lookup_legal_basis,
)


DEFAULT_MASTERFILE = Path("data/MASTER_FILE_SANCTION_CHECK.xlsx")

st.set_page_config(
    page_title="NaroIX Sanctions Screener",
    page_icon="🛡️",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Dilisense API key
# ---------------------------------------------------------------------------


def get_api_key():
    try:
        key = st.secrets.get("DILISENSE_API_KEY")
        if key:
            return key
    except Exception:
        pass
    env_key = os.environ.get("DILISENSE_API_KEY")
    if env_key:
        return env_key
    return st.session_state.get("_manual_api_key")


# ---------------------------------------------------------------------------
# Sidebar (renders AFTER auth, so the user badge from auth.py appears first)
# ---------------------------------------------------------------------------


def render_sidebar():
    st.sidebar.title("🛡️ Sanctions Screener")
    st.sidebar.caption("NaroIX — Phase 1")

    # API key status
    st.sidebar.subheader("Dilisense API Key")
    existing_key = get_api_key()
    if existing_key:
        st.sidebar.success(f"API key loaded ({existing_key[:4]}…)")
    else:
        manual = st.sidebar.text_input(
            "Paste API key (session only)",
            type="password",
        )
        if manual:
            st.session_state["_manual_api_key"] = manual.strip()
            st.rerun()
    api_key = get_api_key()

    # Masterfile
    st.sidebar.subheader("Masterfile")
    uploaded = st.sidebar.file_uploader(
        "Upload masterfile (Excel)",
        type=["xlsx"],
    )

    df = None
    if uploaded is not None:
        try:
            df = load_masterfile(uploaded.read())
            st.sidebar.success(f"Uploaded masterfile loaded ({len(df)} rows)")
        except Exception as exc:
            st.sidebar.error(f"Failed to load uploaded file: {exc}")
    elif DEFAULT_MASTERFILE.exists():
        try:
            df = load_masterfile(DEFAULT_MASTERFILE)
            st.sidebar.info(f"Using default masterfile ({len(df)} rows)")
        except Exception as exc:
            st.sidebar.error(f"Failed to load default masterfile: {exc}")
    else:
        st.sidebar.warning("No masterfile available.")

    return api_key, df


# ---------------------------------------------------------------------------
# Result card rendering
# ---------------------------------------------------------------------------


def _tier_hits(result, tier):
    return [h for h in result.hits if get_source_info(h.source_id)["tier"] == tier]


def _eu_is_blocking(result):
    return len(_tier_hits(result, "1")) > 0


def _us_is_restricting(result):
    for h in result.hits:
        info = get_source_info(h.source_id)
        if info["jurisdiction"] == "USA":
            return True
    return False


def render_result_card(result):
    if result.error:
        st.error(f"❌ **{result.isin}** — {result.primary_name}")
        st.error(f"Error: {result.error}")
        return

    if not result.is_flagged:
        st.success(f"🟢 **{result.isin}** — {result.primary_name}")
        st.caption(f"Status: CLEAN (0 hits across {len(result.queried_names)} name variants queried)")
        st.caption(f"Response time: {result.response_time_ms:.0f} ms")
        return

    st.error(f"🔴 **{result.isin}** — {result.primary_name}")
    st.markdown(f"**Status: FLAGGED ({result.hit_count} sanction hit{'s' if result.hit_count != 1 else ''} found)**")

    tier1 = _tier_hits(result, "1")
    tier2 = _tier_hits(result, "2")
    tier3 = _tier_hits(result, "3")

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("##### Tier breakdown")
        _render_tier_line("Tier 1 (EU / UN / OFAC SDN)", tier1, severe=True)
        _render_tier_line("Tier 2 (US Non-SDN / BIS / DoD)", tier2, severe=False)
        _render_tier_line("Tier 3 (Other jurisdictions)", tier3, severe=False)

    with col2:
        st.markdown("##### Implications")
        st.markdown(f"**EU Blocking:** {'Yes' if _eu_is_blocking(result) else 'No'}")
        st.markdown(f"**US Investment Restriction:** {'Yes' if _us_is_restricting(result) else 'No'}")
        st.caption(f"Query variants: {len(result.queried_names)} · Response: {result.response_time_ms:.0f} ms")

    with st.expander(f"▼ Show details ({result.hit_count} hits)"):
        for i, hit in enumerate(result.hits, 1):
            _render_hit_detail(hit, i, result.hit_count)


def _render_tier_line(label, hits, *, severe):
    if not hits:
        st.markdown(f"- **{label}:** no hits ✅")
        return
    marker = "🚫" if severe else "⚠️"
    st.markdown(f"- **{label}:** {len(hits)} hit{'s' if len(hits) != 1 else ''} {marker}")
    for h in hits:
        info = get_source_info(h.source_id)
        st.markdown(f"    - {info['display_name']}")


def _render_hit_detail(hit, index, total):
    info = get_source_info(hit.source_id)
    st.markdown(f"**Hit {index}/{total} — {info['display_name']}**")

    col_a, col_b = st.columns(2)
    with col_a:
        st.text(f"Source ID:     {hit.source_id}")
        st.text(f"Jurisdiction:  {info['jurisdiction']}")
        if hit.primary_program:
            st.text(f"Program:       {hit.primary_program}")
            legal = lookup_legal_basis(hit.primary_program)
            if legal and legal != hit.primary_program:
                st.text(f"Legal Basis:   {legal}")
    with col_b:
        st.text(f"Listed as:     {hit.name}")
        if hit.list_date:
            st.text(f"Listing Date:  {hit.list_date.strftime('%d %b %Y')}")
        if hit.alias_names:
            st.text(f"Aliases:       {', '.join(hit.alias_names[:3])}")
        st.text(f"Dilisense ID:  {hit.dilisense_id}")

    if hit.other_information:
        st.caption("Other information from source:")
        for line in hit.other_information:
            st.caption(f"  • {line}")

    if info["primary_url"]:
        st.markdown(f"🔗 [Primary source ({info['jurisdiction']})]({info['primary_url']})")

    st.divider()


# ---------------------------------------------------------------------------
# Tab: Single Check
# ---------------------------------------------------------------------------


def tab_single_check(client, df):
    st.header("Single Check")
    st.caption("Screen a single entity against Dilisense.")

    if client is None:
        st.warning("Enter a Dilisense API key in the sidebar to enable screening.")
        return

    col_a, col_b = st.columns([1, 2])
    with col_a:
        mode = st.radio(
            "Lookup by",
            ["ISIN (from masterfile)", "Custom name"],
        )
    with col_b:
        isin = ""
        custom_name = ""
        custom_aliases_raw = ""
        if mode == "ISIN (from masterfile)":
            if df is None or df.empty:
                st.error("No masterfile loaded.")
                return
            isin_options = df["ISIN"].dropna().tolist()
            default_idx = isin_options.index("KYG8020E1199") if "KYG8020E1199" in isin_options else 0
            isin = st.selectbox("Choose ISIN", options=isin_options, index=default_idx)
        else:
            isin = st.text_input("ISIN (free text, can be empty)", value="")
            custom_name = st.text_input("Company name", value="")
            custom_aliases_raw = st.text_area("Alternative names (one per line)", value="", height=80)

    run = st.button("🔍 Run check", type="primary")
    if not run:
        return

    if mode == "ISIN (from masterfile)":
        row = get_row_by_isin(df, isin)
        if row is None:
            st.error(f"ISIN {isin} not found in masterfile.")
            return
        primary = row["Company Name"]
        alternatives = extract_alternatives(row)
        country = get_country_of_risk(row)
        st.caption(f"Primary name: **{primary}**  |  Country of Risk: {country or 'n/a'}  |  Alternatives: {len(alternatives)}")
    else:
        if not custom_name.strip():
            st.error("Please enter a company name.")
            return
        primary = custom_name.strip()
        alternatives = [a.strip() for a in custom_aliases_raw.splitlines() if a.strip()]

    query_names = build_query_names(primary, alternatives)
    with st.expander(f"Query variants generated ({len(query_names)})"):
        for n in query_names:
            st.text(f"  • {n}")

    with st.spinner(f"Querying Dilisense with {len(query_names)} name variants..."):
        try:
            result = client.check_entity(isin or "(no-isin)", primary, query_names)
        except DilisenseAuthError as exc:
            st.error(f"Authentication failed: {exc}")
            return
        except DilisenseQuotaError as exc:
            st.error(f"API quota exceeded: {exc}")
            return

    st.divider()
    render_result_card(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # Step 1: require GitHub OAuth login (blocks render if anonymous)
    auth.require_login()

    # Step 2: render sidebar and get API key + masterfile
    api_key, df = render_sidebar()

    # Step 3: initialize Dilisense client
    client = None
    if api_key:
        try:
            client = DilisenseClient(api_key=api_key)
        except Exception as exc:
            st.sidebar.error(f"Client init failed: {exc}")

    # Step 4: main content
    user = auth.current_user()
    st.title("NaroIX Sanctions Screener")
    st.caption(
        f"Phase 1 — Single Check   |   Dilisense API   |   "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}   |   "
        f"Signed in as @{user.get('login', 'unknown')}"
    )

    tab_single_check(client, df)


if __name__ == "__main__":
    main()
