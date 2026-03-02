import logging
import os
from datetime import datetime

import pandas as pd
import streamlit as st

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Internal Link Optimizer",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .main { background-color: #0f1117; }
    .stProgress > div > div { background-color: #f5a623; }
    div[data-testid="stMetric"] { background: #1e2130; border: 1px solid #2e3450; border-radius: 8px; padding: 12px; }
    div[data-testid="stLinkButton"] a {
        background: #4285F4 !important;
        color: white !important;
        border: none !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        font-size: 15px !important;
    }
    div[data-testid="stLinkButton"] a:hover { background: #3367D6 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── OAuth config helper ───────────────────────────────────────────────────────
def _get_oauth_config() -> tuple[str, str, str]:
    """
    Load OAuth credentials from st.secrets (preferred) or environment variables.
    Returns (client_id, client_secret, redirect_uri).
    """
    try:
        client_id = st.secrets["google"]["client_id"]
        client_secret = st.secrets["google"]["client_secret"]
        redirect_uri = st.secrets["google"]["redirect_uri"]
    except (KeyError, AttributeError):
        from src.config.settings import (
            GOOGLE_OAUTH_CLIENT_ID,
            GOOGLE_OAUTH_CLIENT_SECRET,
            GOOGLE_OAUTH_REDIRECT_URI,
        )
        client_id = GOOGLE_OAUTH_CLIENT_ID
        client_secret = GOOGLE_OAUTH_CLIENT_SECRET
        redirect_uri = GOOGLE_OAUTH_REDIRECT_URI

    if not client_id or not client_secret:
        st.error(
            "Google OAuth credentials are not configured. "
            "Add them to `.streamlit/secrets.toml` or your `.env` file. "
            "See the README for setup instructions."
        )
        st.stop()

    return client_id, client_secret, redirect_uri


# ── Session state initialization ──────────────────────────────────────────────
def _init_state():
    defaults = {
        "step": "setup",
        "run_id": None,
        "client_name": "",
        "gsc_property": "",
        "gsc_credentials": None,   # OAuth credentials dict
        "oauth_state": None,       # CSRF state token
        "oauth_flow": None,        # Flow object — must persist for PKCE token exchange
        "selected_model": "anthropic/claude-sonnet-4-6",
        "location_code": 2840,     # DataForSEO location code (default US)
        "language_code": "en",     # DataForSEO language code
        "queries_df": None,
        "pages_df": None,
        "profile": None,
        "clusters": None,
        "page_taxonomy_df": None,
        "silo_structure": None,
        "recommendations_df": None,
        "csv_path": None,
        "html_report_path": None,
        "silo_fig": None,
        "error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ── OAuth callback handler ─────────────────────────────────────────────────────
# Must run before any page rendering to catch the redirect code
_query_params = st.query_params.to_dict()

if "code" in _query_params and st.session_state.gsc_credentials is None:
    _code = _query_params["code"]
    _state = _query_params.get("state", "")

    # Verify CSRF state
    _expected_state = st.session_state.oauth_state or ""
    if _expected_state and _state != _expected_state:
        st.error("OAuth state mismatch — possible CSRF attempt. Please try connecting again.")
        st.query_params.clear()
        st.stop()

    try:
        from src.agents.gsc_fetcher import exchange_code_for_credentials, get_oauth_flow
        client_id, client_secret, redirect_uri = _get_oauth_config()

        # Reuse the stored flow so the PKCE code_verifier is available
        flow = st.session_state.oauth_flow
        if flow is None:
            flow = get_oauth_flow(client_id, client_secret, redirect_uri)

        with st.spinner("Completing Google sign-in..."):
            creds = exchange_code_for_credentials(_code, flow)

        st.session_state.gsc_credentials = creds
        st.session_state.oauth_state = None
        st.query_params.clear()
        st.rerun()

    except Exception as _e:
        st.error(f"Google sign-in failed: {_e}\n\nPlease try again.")
        st.query_params.clear()
        st.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _reset():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    _init_state()


def _disconnect_gsc():
    st.session_state.gsc_credentials = None
    st.session_state.oauth_state = None


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔗 Internal Link Optimizer")
    st.caption("SILO-based internal linking strategy powered by GSC data + AI")

    st.divider()

    # GSC connection status
    if st.session_state.gsc_credentials:
        st.success("Google Search Console connected")
        if st.button("Disconnect GSC", use_container_width=True):
            _disconnect_gsc()
            st.rerun()
    else:
        st.warning("GSC not connected")

    st.divider()

    if st.session_state.step == "results":
        st.subheader("Export Results")

        if st.session_state.csv_path and os.path.exists(st.session_state.csv_path):
            with open(st.session_state.csv_path, "rb") as f:
                st.download_button(
                    "Download CSV",
                    data=f.read(),
                    file_name=os.path.basename(st.session_state.csv_path),
                    mime="text/csv",
                    use_container_width=True,
                )

        if st.session_state.html_report_path and os.path.exists(st.session_state.html_report_path):
            with open(st.session_state.html_report_path, "rb") as f:
                st.download_button(
                    "Download HTML Report",
                    data=f.read(),
                    file_name=os.path.basename(st.session_state.html_report_path),
                    mime="text/html",
                    use_container_width=True,
                )

        if st.session_state.run_id:
            st.success(f"Saved to Supabase\nRun ID: `{st.session_state.run_id[:8]}...`")

        st.divider()
        if st.button("New Analysis", use_container_width=True):
            _reset()
            st.rerun()

    st.divider()
    from src.config.settings import AVAILABLE_MODELS
    st.session_state.selected_model = st.selectbox(
        "AI Model",
        options=AVAILABLE_MODELS,
        index=AVAILABLE_MODELS.index(st.session_state.selected_model)
        if st.session_state.selected_model in AVAILABLE_MODELS else 0,
        help="Model used for all AI analysis steps",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: SETUP
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state.step == "setup":
    st.title("Internal Link Optimization Agent")
    st.markdown("Analyze GSC data + business context to build a complete SILO-based internal linking strategy.")

    st.divider()

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("1. Client Setup")
        client_name = st.text_input(
            "Client Name",
            placeholder="e.g., Acme SEO Agency",
            help="Used for naming output files and Supabase records",
        )

        # ── GSC Authentication ────────────────────────────────────────────────
        st.subheader("2. Google Search Console")

        if st.session_state.gsc_credentials is None:
            # Not connected — show sign-in button
            st.markdown("Connect your Google account to access GSC properties.")

            try:
                from src.agents.gsc_fetcher import get_auth_url

                client_id, client_secret, redirect_uri = _get_oauth_config()

                # get_auth_url returns the URL with state + flow (flow holds PKCE verifier)
                auth_url, oauth_state, oauth_flow = get_auth_url(client_id, client_secret, redirect_uri)
                st.session_state.oauth_state = oauth_state
                st.session_state.oauth_flow = oauth_flow

                st.link_button(
                    "Sign in with Google",
                    url=auth_url,
                    use_container_width=True,
                )
                st.caption(
                    "You'll be redirected to Google's login page. "
                    "Grant read-only access to Search Console."
                )

            except Exception as e:
                st.error(f"Could not generate sign-in link: {e}")

            selected_property = ""

        else:
            # Connected — show property selector
            st.success("Connected to Google Search Console")

            try:
                from src.agents.gsc_fetcher import list_properties
                with st.spinner("Loading your GSC properties..."):
                    gsc_properties = list_properties(st.session_state.gsc_credentials)

                if gsc_properties:
                    selected_property = st.selectbox(
                        "Select property to analyze",
                        options=gsc_properties,
                        help="Choose the GSC property (website) you want to optimize",
                    )
                else:
                    st.warning(
                        "No GSC properties found for this account. "
                        "Make sure you have at least Restricted access in Google Search Console."
                    )
                    selected_property = ""

            except Exception as e:
                st.error(f"Failed to load properties: {e}")
                if "expired" in str(e).lower() or "invalid" in str(e).lower():
                    st.info("Your session may have expired. Try disconnecting and reconnecting.")
                selected_property = ""

        st.divider()

        date_range = st.slider(
            "Data Range (days back)",
            min_value=30,
            max_value=365,
            value=90,
            step=30,
            help="90 days recommended. Use 180+ for seasonal content.",
        )

        filter_branded = st.checkbox(
            "Filter out branded queries",
            value=False,
            help="Remove queries containing the brand name from clustering",
        )

        st.divider()
        st.subheader("4. Target Market")
        st.caption(
            "Used for DataForSEO search volume + keyword difficulty lookup. "
            "Leave as-is if DataForSEO is not configured."
        )

        from src.utils.dataforseo import LOCATION_OPTIONS
        location_name = st.selectbox(
            "Target Country",
            options=list(LOCATION_OPTIONS.keys()),
            index=0,
            help="Country for search volume and keyword difficulty data",
        )
        location_code = LOCATION_OPTIONS[location_name]

        language_code = st.selectbox(
            "Language",
            options=["en", "es", "fr", "de", "pt", "nl", "it"],
            index=0,
            help="Language for keyword metrics lookup",
        )

    with col2:
        st.subheader("3. Business Profile")
        st.markdown(
            "Upload a document describing the business: nature, USP, target audience, pain points, services/products."
        )

        profile_method = st.radio(
            "Profile source",
            ["Upload file", "Google Doc URL"],
            horizontal=True,
        )

        profile_bytes = None
        profile_filename = ""
        profile_url = ""

        if profile_method == "Upload file":
            profile_upload = st.file_uploader(
                "Upload Business Profile",
                type=["txt", "md", "pdf", "docx"],
                help="Supported: .txt, .md, .pdf, .docx",
            )
            if profile_upload:
                profile_bytes = profile_upload.read()
                profile_filename = profile_upload.name
                st.success(f"Loaded: {profile_filename}")
        else:
            profile_url = st.text_input(
                "Google Doc URL",
                placeholder="https://docs.google.com/document/d/...",
                help="Must be set to 'Anyone with the link can view'",
            )

    st.divider()

    # Validate and run
    can_run = bool(
        client_name
        and st.session_state.gsc_credentials
        and selected_property
        and (profile_bytes or profile_url)
    )

    if not can_run:
        missing = []
        if not client_name:
            missing.append("client name")
        if not st.session_state.gsc_credentials:
            missing.append("Google sign-in")
        elif not selected_property:
            missing.append("GSC property selection")
        if not (profile_bytes or profile_url):
            missing.append("business profile")
        st.info(f"Still needed: {', '.join(missing)}")

    if st.button("Run Analysis", disabled=not can_run, type="primary", use_container_width=True):
        st.session_state.client_name = client_name
        st.session_state.gsc_property = selected_property
        st.session_state._profile_bytes = profile_bytes
        st.session_state._profile_filename = profile_filename
        st.session_state._profile_url = profile_url
        st.session_state._date_range = date_range
        st.session_state._filter_branded = filter_branded
        st.session_state.location_code = location_code
        st.session_state.language_code = language_code
        st.session_state.step = "running"
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: RUNNING
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == "running":
    st.title(f"Analyzing: {st.session_state.client_name}")

    progress_bar = st.progress(0)
    status_text = st.empty()

    step_indicators = {i: st.empty() for i in range(1, 7)}
    step_names = {
        1: "Fetching GSC Data",
        2: "Parsing Business Profile",
        3: "Clustering Keywords",
        4: "Categorizing Content",
        5: "Generating Link Recommendations",
        6: "Building Outputs",
    }

    for i, name in step_names.items():
        step_indicators[i].markdown(f"⬜ **Step {i}/6:** {name}")

    def update_step(step_num: int, status: str = "running", detail: str = ""):
        icon = {"running": "🔄", "done": "✅", "error": "❌"}.get(status, "⬜")
        msg = f"{icon} **Step {step_num}/6:** {step_names[step_num]}"
        if detail:
            msg += f" — {detail}"
        step_indicators[step_num].markdown(msg)
        progress_bar.progress(min(step_num / 6, 1.0))
        if detail:
            status_text.caption(detail)

    try:
        import src.utils.openrouter as _openrouter
        _openrouter.MODEL_REASONING = st.session_state.selected_model
        _openrouter.MODEL_FAST = st.session_state.selected_model

        from src.utils.helpers import generate_run_id
        from src.utils.document_parser import parse_document
        from src.agents.gsc_fetcher import fetch_gsc_data
        from src.agents.profile_parser import parse_business_profile
        from src.agents.keyword_clusterer import cluster_keywords
        from src.agents.content_categorizer import categorize_content
        from src.agents.link_recommender import generate_link_recommendations
        from src.agents.output_generator import (
            build_silo_diagram,
            export_csv,
            export_html_report,
            prepare_supabase_records,
        )

        run_id = generate_run_id()
        st.session_state.run_id = run_id

        # ── Step 1: GSC Data ──────────────────────────────────────────────────
        update_step(1, "running")

        queries_df, pages_df = fetch_gsc_data(
            credentials_dict=st.session_state.gsc_credentials,
            site_url=st.session_state.gsc_property,
            days_back=st.session_state._date_range,
            filter_branded=st.session_state._filter_branded,
            progress_callback=lambda msg: status_text.caption(msg),
        )
        st.session_state.queries_df = queries_df
        st.session_state.pages_df = pages_df
        update_step(1, "done", f"{len(queries_df):,} queries · {len(pages_df):,} pages")

        # ── Step 2: Business Profile ──────────────────────────────────────────
        update_step(2, "running")

        raw_text = parse_document(
            file_bytes=st.session_state._profile_bytes,
            filename=st.session_state._profile_filename,
            url=st.session_state._profile_url,
        )
        profile = parse_business_profile(raw_text)
        st.session_state.profile = profile
        update_step(2, "done", f"{profile.brand_name} · {len(profile.services)} services identified")

        # ── Step 3: Keyword Clustering ────────────────────────────────────────
        update_step(3, "running")

        clusters = cluster_keywords(
            queries_df=queries_df,
            profile=profile,
            pages_df=pages_df,
            location_code=st.session_state.location_code,
            language_code=st.session_state.language_code,
            progress_callback=lambda msg: status_text.caption(msg),
        )
        st.session_state.clusters = clusters
        update_step(3, "done", f"{len(clusters)} clusters identified")

        # ── Step 4: Content Categorization ───────────────────────────────────
        update_step(4, "running")

        page_taxonomy_df, silo_structure = categorize_content(
            pages_df=pages_df,
            profile=profile,
            clusters=clusters,
            progress_callback=lambda msg: status_text.caption(msg),
        )
        st.session_state.page_taxonomy_df = page_taxonomy_df
        st.session_state.silo_structure = silo_structure
        update_step(4, "done", f"{len(silo_structure)} SILOs · {page_taxonomy_df['page_type'].value_counts().to_dict()}")

        # ── Step 5: Link Recommendations ─────────────────────────────────────
        update_step(5, "running")

        recommendations_df = generate_link_recommendations(
            silo_structure=silo_structure,
            page_taxonomy_df=page_taxonomy_df,
            clusters=clusters,
            profile=profile,
            progress_callback=lambda msg: status_text.caption(msg),
        )
        st.session_state.recommendations_df = recommendations_df
        total_recs = len(recommendations_df) if not recommendations_df.empty else 0
        update_step(5, "done", f"{total_recs} recommendations generated")

        # ── Step 6: Outputs ───────────────────────────────────────────────────
        update_step(6, "running", "Building SILO diagram...")

        silo_fig, _ = build_silo_diagram(
            page_taxonomy_df=page_taxonomy_df,
            recommendations_df=recommendations_df,
            silo_structure=silo_structure,
            client_name=st.session_state.client_name,
        )
        st.session_state.silo_fig = silo_fig

        status_text.caption("Exporting CSV...")
        csv_path = export_csv(recommendations_df, st.session_state.client_name)
        st.session_state.csv_path = csv_path

        status_text.caption("Generating HTML report...")
        html_report_path = export_html_report(
            page_taxonomy_df=page_taxonomy_df,
            recommendations_df=recommendations_df,
            clusters=clusters,
            silo_structure=silo_structure,
            client_name=st.session_state.client_name,
            gsc_property=st.session_state.gsc_property,
        )
        st.session_state.html_report_path = html_report_path

        status_text.caption("Saving to Supabase...")
        try:
            from src.utils import supabase_client as sb
            sb.create_analysis_run(run_id, st.session_state.client_name, st.session_state.gsc_property, st.session_state._date_range)
            supabase_records = prepare_supabase_records(
                run_id, pages_df, clusters, page_taxonomy_df, silo_structure, recommendations_df
            )
            sb.save_all_results(
                run_id,
                supabase_records["gsc_pages"],
                supabase_records["keyword_clusters"],
                supabase_records["page_taxonomy"],
                supabase_records["silo_structure"],
                supabase_records["link_recommendations"],
            )
        except Exception as sb_err:
            logger.warning("Supabase save failed (continuing): %s", sb_err)
            st.warning(f"Supabase save failed: {sb_err}\nResults are still available locally.")

        update_step(6, "done")
        progress_bar.progress(1.0)
        status_text.success("Analysis complete!")

        st.session_state.step = "results"
        st.rerun()

    except Exception as e:
        logger.exception("Analysis failed")
        st.error(f"Analysis failed: {e}")
        st.session_state.error = str(e)
        if st.button("Back to Setup"):
            st.session_state.step = "setup"
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: RESULTS DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == "results":
    page_taxonomy_df = st.session_state.page_taxonomy_df
    recommendations_df = st.session_state.recommendations_df
    clusters = st.session_state.clusters
    silo_structure = st.session_state.silo_structure

    st.title(f"Results: {st.session_state.client_name}")
    st.caption(f"Property: {st.session_state.gsc_property} · Run ID: {st.session_state.run_id}")

    # ── Summary metrics ───────────────────────────────────────────────────────
    type_counts = page_taxonomy_df["page_type"].value_counts().to_dict() if page_taxonomy_df is not None else {}
    total_recs = len(recommendations_df) if recommendations_df is not None and not recommendations_df.empty else 0
    p1 = len(recommendations_df[recommendations_df["priority"] == 1]) if total_recs > 0 else 0
    p2 = len(recommendations_df[recommendations_df["priority"] == 2]) if total_recs > 0 else 0
    p3 = len(recommendations_df[recommendations_df["priority"] == 3]) if total_recs > 0 else 0

    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    col1.metric("Total Pages", len(page_taxonomy_df) if page_taxonomy_df is not None else 0)
    col2.metric("Clusters", len(clusters) if clusters else 0)
    col3.metric("SILOs", len(silo_structure) if silo_structure else 0)
    col4.metric("P1 Critical", p1, help="Pillar ↔ Cluster structural links")
    col5.metric("P2 Authority", p2, help="Authority boost links")
    col6.metric("P3 Recommended", p3, help="Blog→money + orphan fixes")
    col7.metric("Orphans", type_counts.get("orphan_candidate", 0), help="Pages needing integration")

    st.divider()

    # ── SILO Diagram ──────────────────────────────────────────────────────────
    st.subheader("SILO Architecture Diagram")
    st.caption("Gold=Pillar · Blue=Cluster Post · Green=Money · Red=Orphan · Edge thickness = Priority")

    if st.session_state.silo_fig is not None:
        st.plotly_chart(st.session_state.silo_fig, use_container_width=True)

    st.divider()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "Link Recommendations",
        "Page Taxonomy",
        "Keyword Clusters",
        "Per-Page Report",
    ])

    with tab1:
        st.subheader(f"All Link Recommendations ({total_recs})")

        if recommendations_df is not None and not recommendations_df.empty:
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                priority_filter = st.multiselect(
                    "Filter by Priority", options=[1, 2, 3], default=[1, 2, 3],
                    format_func=lambda x: f"P{x}",
                )
            with col_f2:
                silo_names = sorted(recommendations_df["silo_name"].dropna().unique().tolist())
                silo_filter = st.multiselect("Filter by SILO", options=["All"] + silo_names, default=["All"])
            with col_f3:
                type_filter = st.multiselect(
                    "Filter by Type",
                    options=recommendations_df["link_type"].unique().tolist(),
                    default=recommendations_df["link_type"].unique().tolist(),
                )

            filtered = recommendations_df[
                recommendations_df["priority"].isin(priority_filter) &
                recommendations_df["link_type"].isin(type_filter)
            ]
            if "All" not in silo_filter:
                filtered = filtered[filtered["silo_name"].isin(silo_filter)]

            display_cols = ["priority", "source_url", "target_url", "anchor_text", "link_type", "silo_name", "reason"]
            display_cols = [c for c in display_cols if c in filtered.columns]
            st.dataframe(filtered[display_cols].rename(columns={"priority": "P"}), use_container_width=True, height=500)
        else:
            st.info("No recommendations generated.")

    with tab2:
        st.subheader("Page Classification & SILO Membership")

        if page_taxonomy_df is not None and not page_taxonomy_df.empty:
            type_filter_tax = st.multiselect(
                "Filter by Type",
                options=page_taxonomy_df["page_type"].unique().tolist(),
                default=page_taxonomy_df["page_type"].unique().tolist(),
            )
            filtered_tax = page_taxonomy_df[page_taxonomy_df["page_type"].isin(type_filter_tax)]
            display_cols = ["url", "page_type", "cluster_label", "clicks", "impressions", "opportunity_score"]
            display_cols = [c for c in display_cols if c in filtered_tax.columns]
            st.dataframe(filtered_tax[display_cols].sort_values("clicks", ascending=False), use_container_width=True, height=500)
        else:
            st.info("No page taxonomy data available.")

    with tab3:
        n_clusters = len(clusters) if clusters else 0
        st.subheader(f"Keyword Clusters ({n_clusters})")

        if clusters:
            # Sort clusters by total_search_volume descending (traffic potential)
            sorted_clusters = sorted(
                clusters.items(),
                key=lambda x: x[1].get("total_search_volume", 0),
                reverse=True,
            )

            for cluster_id, cluster in sorted_clusters:
                sv = cluster.get("total_search_volume", 0)
                kd = cluster.get("avg_difficulty", 0)
                sv_label = f" · {sv:,} mo. searches" if sv else ""
                kd_label = f" · KD {kd:.0f}" if kd else ""
                header = (
                    f"{cluster['label']} ({cluster['intent']}) "
                    f"— {cluster['query_count']} queries{sv_label}{kd_label}"
                )
                with st.expander(header):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if sv or kd:
                            st.markdown("**Traffic Potential**")
                            if sv:
                                st.caption(f"• Monthly search volume: **{sv:,}**")
                            if kd:
                                st.caption(f"• Avg keyword difficulty: **{kd:.0f}/100**")
                    with col_b:
                        assigned = cluster.get("page_assignments", [])
                        if assigned:
                            st.markdown(f"**Assigned Pages ({len(assigned)})**")
                            for url in assigned[:5]:
                                st.caption(f"• {url}")
                            if len(assigned) > 5:
                                st.caption(f"  ...and {len(assigned) - 5} more")
                    if cluster.get("queries"):
                        st.markdown("**Sample Queries**")
                        st.caption(", ".join(cluster["queries"][:15]))
        else:
            st.info("No cluster data available.")

    with tab4:
        st.subheader("Per-Page Internal Link Plan")

        if page_taxonomy_df is not None and not page_taxonomy_df.empty:
            selected_url = st.selectbox("Select a page", options=page_taxonomy_df["url"].tolist())

            if selected_url:
                row = page_taxonomy_df[page_taxonomy_df["url"] == selected_url].iloc[0]

                badge_colors = {
                    "pillar": ":orange[PILLAR]",
                    "cluster_post": ":blue[CLUSTER POST]",
                    "money_page": ":green[MONEY PAGE]",
                    "orphan_candidate": ":red[ORPHAN]",
                }
                st.markdown(f"### {selected_url}")
                st.markdown(
                    f"{badge_colors.get(row['page_type'], row['page_type'])} &nbsp;|&nbsp; "
                    f"Cluster: **{row.get('cluster_label', 'unassigned')}** &nbsp;|&nbsp; "
                    f"Clicks: **{int(row.get('clicks', 0)):,}** &nbsp;|&nbsp; "
                    f"Impressions: **{int(row.get('impressions', 0)):,}**"
                )

                if recommendations_df is not None and not recommendations_df.empty:
                    incoming = recommendations_df[recommendations_df["target_url"] == selected_url]
                    outgoing = recommendations_df[recommendations_df["source_url"] == selected_url]

                    col_in, col_out = st.columns(2)
                    with col_in:
                        st.markdown(f"**Incoming Links ({len(incoming)})**")
                        st.caption("Pages that should link TO this page")
                        if not incoming.empty:
                            for _, rec in incoming.iterrows():
                                st.markdown(
                                    f"- From: `{rec['source_url'][:60]}`\n"
                                    f"  Anchor: *\"{rec['anchor_text']}\"* (P{rec['priority']})"
                                )
                        else:
                            st.caption("No incoming link recommendations")

                    with col_out:
                        st.markdown(f"**Outgoing Links ({len(outgoing)})**")
                        st.caption("Pages this page should link TO")
                        if not outgoing.empty:
                            for _, rec in outgoing.iterrows():
                                st.markdown(
                                    f"- To: `{rec['target_url'][:60]}`\n"
                                    f"  Anchor: *\"{rec['anchor_text']}\"* (P{rec['priority']})"
                                )
                        else:
                            st.caption("No outgoing link recommendations")
        else:
            st.info("No page data available.")
