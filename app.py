import io
import json
import logging
import os
import tempfile
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

# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .main { background-color: #0f1117; }
    .stProgress > div > div { background-color: #f5a623; }
    .metric-card { background: #1e2130; border: 1px solid #2e3450; border-radius: 8px; padding: 16px; }
    div[data-testid="stMetric"] { background: #1e2130; border: 1px solid #2e3450; border-radius: 8px; padding: 12px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Session state initialization ──────────────────────────────────────────────
def _init_state():
    defaults = {
        "step": "setup",          # setup | running | results
        "run_id": None,
        "client_name": "",
        "gsc_property": "",
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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _reset():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    _init_state()


def _progress_container():
    """Return a status container for live progress updates."""
    return st.empty()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔗 Internal Link Optimizer")
    st.caption("SILO-based internal linking strategy powered by GSC data + AI")

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
    st.caption("Models: Claude Sonnet (reasoning) + Gemini Flash (batching)")


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

        st.subheader("2. GSC Authentication")
        sa_upload = st.file_uploader(
            "Upload Service Account JSON",
            type=["json"],
            help="Your Google service account key file with GSC read access",
        )

        gsc_properties = []
        selected_property = ""

        if sa_upload:
            try:
                # Write to temp file to use with Google API
                with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as tmp:
                    tmp.write(sa_upload.read())
                    st.session_state["_sa_tmp_path"] = tmp.name

                from src.agents.gsc_fetcher import list_properties
                with st.spinner("Loading GSC properties..."):
                    gsc_properties = list_properties(st.session_state["_sa_tmp_path"])

                if gsc_properties:
                    selected_property = st.selectbox("Select GSC Property", gsc_properties)
                else:
                    st.warning("No GSC properties found for this service account.")
            except Exception as e:
                st.error(f"Failed to authenticate with GSC: {e}")

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
        client_name and
        sa_upload and
        selected_property and
        (profile_bytes or profile_url)
    )

    if not can_run:
        missing = []
        if not client_name:
            missing.append("client name")
        if not sa_upload:
            missing.append("service account JSON")
        if sa_upload and not selected_property:
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
        st.session_state.step = "running"
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: RUNNING
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == "running":
    st.title(f"Analyzing: {st.session_state.client_name}")

    progress_bar = st.progress(0)
    status_text = st.empty()

    step_indicators = {
        1: st.empty(),
        2: st.empty(),
        3: st.empty(),
        4: st.empty(),
        5: st.empty(),
        6: st.empty(),
    }

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

        def gsc_progress(msg):
            status_text.caption(msg)

        queries_df, pages_df = fetch_gsc_data(
            service_account_json_path=st.session_state["_sa_tmp_path"],
            site_url=st.session_state.gsc_property,
            days_back=st.session_state._date_range,
            filter_branded=st.session_state._filter_branded,
            progress_callback=gsc_progress,
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

        def cluster_progress(msg):
            status_text.caption(msg)

        clusters = cluster_keywords(
            queries_df=queries_df,
            profile=profile,
            pages_df=pages_df,
            progress_callback=cluster_progress,
        )
        st.session_state.clusters = clusters
        update_step(3, "done", f"{len(clusters)} clusters identified")

        # ── Step 4: Content Categorization ───────────────────────────────────
        update_step(4, "running")

        def cat_progress(msg):
            status_text.caption(msg)

        page_taxonomy_df, silo_structure = categorize_content(
            pages_df=pages_df,
            profile=profile,
            clusters=clusters,
            progress_callback=cat_progress,
        )
        st.session_state.page_taxonomy_df = page_taxonomy_df
        st.session_state.silo_structure = silo_structure
        update_step(4, "done", f"{len(silo_structure)} SILOs · {page_taxonomy_df['page_type'].value_counts().to_dict()}")

        # ── Step 5: Link Recommendations ─────────────────────────────────────
        update_step(5, "running")

        def rec_progress(msg):
            status_text.caption(msg)

        recommendations_df = generate_link_recommendations(
            silo_structure=silo_structure,
            page_taxonomy_df=page_taxonomy_df,
            clusters=clusters,
            profile=profile,
            progress_callback=rec_progress,
        )
        st.session_state.recommendations_df = recommendations_df
        total_recs = len(recommendations_df) if not recommendations_df.empty else 0
        update_step(5, "done", f"{total_recs} recommendations generated")

        # ── Step 6: Outputs ───────────────────────────────────────────────────
        update_step(6, "running", "Building SILO diagram...")
        status_text.caption("Building SILO diagram...")

        silo_fig, html_diagram_path = build_silo_diagram(
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
        for i in range(1, 7):
            if "running" in step_indicators[i]._html:
                update_step(i, "error", str(e))
                break
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
    profile = st.session_state.profile

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
    col4.metric("P1 Critical", p1, delta=None, help="Pillar ↔ Cluster structural links")
    col5.metric("P2 Authority", p2, delta=None, help="Authority boost links")
    col6.metric("P3 Recommended", p3, delta=None, help="Blog→money + orphan fixes")
    col7.metric("Orphans", type_counts.get("orphan_candidate", 0), delta=None, help="Pages needing integration")

    st.divider()

    # ── SILO Diagram ─────────────────────────────────────────────────────────
    st.subheader("SILO Architecture Diagram")
    st.caption("Nodes: Gold=Pillar · Blue=Cluster Post · Green=Money · Red=Orphan · Edge thickness = Priority")

    if st.session_state.silo_fig is not None:
        st.plotly_chart(st.session_state.silo_fig, use_container_width=True)

    st.divider()

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "Link Recommendations",
        "Page Taxonomy",
        "Keyword Clusters",
        "Per-Page Report",
    ])

    # Tab 1: Link Recommendations
    with tab1:
        st.subheader(f"All Link Recommendations ({total_recs})")

        if recommendations_df is not None and not recommendations_df.empty:
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                priority_filter = st.multiselect(
                    "Filter by Priority",
                    options=[1, 2, 3],
                    default=[1, 2, 3],
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

            st.dataframe(
                filtered[display_cols].rename(columns={"priority": "P"}),
                use_container_width=True,
                height=500,
            )
        else:
            st.info("No recommendations generated.")

    # Tab 2: Page Taxonomy
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

            st.dataframe(
                filtered_tax[display_cols].sort_values("clicks", ascending=False),
                use_container_width=True,
                height=500,
            )
        else:
            st.info("No page taxonomy data available.")

    # Tab 3: Keyword Clusters
    with tab3:
        st.subheader(f"Keyword Clusters ({len(clusters) if clusters else 0})")

        if clusters:
            for cluster_id, cluster in clusters.items():
                with st.expander(
                    f"{cluster['label']} ({cluster['intent']}) — {cluster['query_count']} queries"
                ):
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        st.markdown("**LSI Terms**")
                        for term in cluster.get("lsi_terms", []):
                            st.caption(f"• {term}")
                    with col_b:
                        st.markdown("**NLP Entities**")
                        for entity in cluster.get("entities", []):
                            st.caption(f"• {entity}")
                    with col_c:
                        st.markdown("**Anchor Variants**")
                        for anchor in cluster.get("anchor_variants", []):
                            st.caption(f'"{anchor}"')

                    if cluster.get("queries"):
                        st.markdown("**Sample Queries**")
                        st.caption(", ".join(cluster["queries"][:10]))
        else:
            st.info("No cluster data available.")

    # Tab 4: Per-Page Report
    with tab4:
        st.subheader("Per-Page Internal Link Plan")

        if page_taxonomy_df is not None and not page_taxonomy_df.empty:
            all_urls = page_taxonomy_df["url"].tolist()
            selected_url = st.selectbox("Select a page", options=all_urls)

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
