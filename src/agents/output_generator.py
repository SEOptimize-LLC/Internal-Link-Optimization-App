import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader

from src.config.settings import OUTPUTS_DIR, TEMPLATES_DIR, PRIORITY_LABELS
from src.utils.helpers import get_export_filename, get_url_path, truncate_url

logger = logging.getLogger(__name__)


# Color scheme for page types
PAGE_TYPE_COLORS = {
    "pillar": "#F5A623",       # Gold
    "cluster_post": "#4A90D9",  # Blue
    "money_page": "#27AE60",    # Green
    "orphan_candidate": "#E74C3C",  # Red
}

PAGE_TYPE_LABELS = {
    "pillar": "Pillar",
    "cluster_post": "Cluster Post",
    "money_page": "Money Page",
    "orphan_candidate": "Orphan",
}

PRIORITY_EDGE_WIDTHS = {1: 3.0, 2: 1.5, 3: 0.8}
PRIORITY_EDGE_COLORS = {1: "#E74C3C", 2: "#F39C12", 3: "#95A5A6"}


def _ensure_outputs_dir(client_name: str) -> Path:
    """Create client-specific output directory."""
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in client_name.lower())
    date_str = datetime.now().strftime("%Y%m%d")
    client_dir = Path(OUTPUTS_DIR) / f"{safe_name}_{date_str}"
    client_dir.mkdir(parents=True, exist_ok=True)
    return client_dir


def build_silo_diagram(
    page_taxonomy_df: pd.DataFrame,
    recommendations_df: pd.DataFrame,
    silo_structure: dict,
    client_name: str,
) -> tuple[go.Figure, str]:
    """
    Build an interactive SILO visualization using networkx + plotly.

    Returns:
        Tuple of (plotly Figure, html export path)
    """
    G = nx.DiGraph()

    # Add nodes
    for _, row in page_taxonomy_df.iterrows():
        url = row["url"]
        page_type = row["page_type"]
        G.add_node(
            url,
            page_type=page_type,
            label=get_url_path(url),
            full_url=url,
            clicks=row.get("clicks", 0),
            silo=row.get("cluster_label", ""),
        )

    # Add edges from P1 and P2 recommendations only (avoid visual clutter)
    display_recs = recommendations_df[recommendations_df["priority"].isin([1, 2])] if not recommendations_df.empty else pd.DataFrame()
    for _, row in display_recs.iterrows():
        src = row["source_url"]
        tgt = row["target_url"]
        if G.has_node(src) and G.has_node(tgt):
            G.add_edge(
                src, tgt,
                priority=row["priority"],
                anchor_text=row.get("anchor_text", ""),
                link_type=row.get("link_type", ""),
            )

    # Use spring layout with stronger repulsion for readability
    pos = nx.spring_layout(G, k=2.5, seed=42)

    # Build plotly traces
    edge_traces = []
    priority_groups = {1: [], 2: [], 3: []}

    for edge in G.edges(data=True):
        priority = edge[2].get("priority", 3)
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        priority_groups[priority].append((x0, y0, x1, y1, edge[2].get("anchor_text", "")))

    for priority, edges_data in priority_groups.items():
        if not edges_data:
            continue
        x_coords, y_coords, hover_texts = [], [], []
        for x0, y0, x1, y1, anchor in edges_data:
            x_coords.extend([x0, x1, None])
            y_coords.extend([y0, y1, None])
            hover_texts.extend([f"→ {anchor}", "", ""])

        edge_traces.append(
            go.Scatter(
                x=x_coords,
                y=y_coords,
                mode="lines",
                line=dict(
                    width=PRIORITY_EDGE_WIDTHS.get(priority, 1),
                    color=PRIORITY_EDGE_COLORS.get(priority, "#ccc"),
                ),
                hoverinfo="text",
                text=hover_texts,
                name=PRIORITY_LABELS.get(priority, f"P{priority}"),
                opacity=0.7,
            )
        )

    # Node traces by type
    node_traces = []
    for page_type, color in PAGE_TYPE_COLORS.items():
        type_nodes = [n for n, d in G.nodes(data=True) if d.get("page_type") == page_type]
        if not type_nodes:
            continue

        x_vals = [pos[n][0] for n in type_nodes]
        y_vals = [pos[n][1] for n in type_nodes]
        hover = [
            f"<b>{truncate_url(n, 50)}</b><br>"
            f"Type: {PAGE_TYPE_LABELS.get(G.nodes[n].get('page_type', ''), 'unknown')}<br>"
            f"Cluster: {G.nodes[n].get('silo', '')}<br>"
            f"Clicks: {G.nodes[n].get('clicks', 0):,}"
            for n in type_nodes
        ]
        sizes = [max(8, min(20, 8 + G.nodes[n].get("clicks", 0) / 50)) for n in type_nodes]

        node_traces.append(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="markers",
                marker=dict(
                    size=sizes,
                    color=color,
                    line=dict(width=1, color="white"),
                ),
                text=hover,
                hoverinfo="text",
                name=PAGE_TYPE_LABELS.get(page_type, page_type),
            )
        )

    layout = go.Layout(
        title=dict(
            text=f"Internal Link SILO Structure — {client_name}",
            font=dict(size=16),
        ),
        showlegend=True,
        hovermode="closest",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="#1a1a2e",
        paper_bgcolor="#16213e",
        font=dict(color="white"),
        height=700,
        legend=dict(bgcolor="rgba(0,0,0,0.3)", bordercolor="white", borderwidth=1),
    )

    fig = go.Figure(data=edge_traces + node_traces, layout=layout)

    # Export HTML
    output_dir = _ensure_outputs_dir(client_name)
    html_path = str(output_dir / get_export_filename(client_name, "silo_diagram", "html"))
    fig.write_html(html_path)
    logger.info("SILO diagram saved: %s", html_path)

    return fig, html_path


def export_csv(
    recommendations_df: pd.DataFrame,
    client_name: str,
) -> str:
    """Export link recommendations to CSV."""
    output_dir = _ensure_outputs_dir(client_name)
    csv_path = str(output_dir / get_export_filename(client_name, "internal_links", "csv"))

    # Select and order columns for the export
    export_cols = [
        "source_url", "target_url", "anchor_text", "link_type",
        "priority", "reason", "silo_name", "implementation_status",
    ]
    available_cols = [c for c in export_cols if c in recommendations_df.columns]

    export_df = recommendations_df[available_cols].copy()

    # Map priority numbers to labels
    export_df["priority"] = export_df["priority"].map(PRIORITY_LABELS).fillna(export_df["priority"])

    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("CSV exported: %s (%d rows)", csv_path, len(export_df))
    return csv_path


def export_html_report(
    page_taxonomy_df: pd.DataFrame,
    recommendations_df: pd.DataFrame,
    clusters: dict,
    silo_structure: dict,
    client_name: str,
    gsc_property: str,
    run_date: str = None,
) -> str:
    """Generate consolidated per-page HTML report using Jinja2 template."""
    output_dir = _ensure_outputs_dir(client_name)
    html_path = str(output_dir / get_export_filename(client_name, "full_report", "html"))
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")

    # Build per-page report data
    page_reports = []
    for _, row in page_taxonomy_df.iterrows():
        url = row["url"]

        # Links this page should RECEIVE (incoming)
        incoming = recommendations_df[recommendations_df["target_url"] == url] if not recommendations_df.empty else pd.DataFrame()
        # Links this page should GIVE (outgoing)
        outgoing = recommendations_df[recommendations_df["source_url"] == url] if not recommendations_df.empty else pd.DataFrame()

        # Money page connection (if blog→money link exists)
        money_link = outgoing[outgoing["link_type"] == "blog_to_money"]
        money_page_rec = money_link.iloc[0].to_dict() if not money_link.empty else None

        page_reports.append({
            "url": url,
            "short_url": get_url_path(url),
            "page_type": row.get("page_type", ""),
            "page_type_label": PAGE_TYPE_LABELS.get(row.get("page_type", ""), "Unknown"),
            "page_type_color": PAGE_TYPE_COLORS.get(row.get("page_type", ""), "#999"),
            "cluster_label": row.get("cluster_label", "unassigned"),
            "silo_id": row.get("silo_id", ""),
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "opportunity_score": float(row.get("opportunity_score", 0)),
            "incoming_links": incoming.to_dict("records") if not incoming.empty else [],
            "outgoing_links": outgoing.to_dict("records") if not outgoing.empty else [],
            "money_page_rec": money_page_rec,
        })

    # Sort by page type (pillar first, then cluster, money, orphan)
    type_order = {"pillar": 0, "cluster_post": 1, "money_page": 2, "orphan_candidate": 3}
    page_reports.sort(key=lambda x: type_order.get(x["page_type"], 4))

    # Summary stats
    summary = {
        "client_name": client_name,
        "gsc_property": gsc_property,
        "run_date": run_date,
        "total_pages": len(page_taxonomy_df),
        "total_clusters": len(clusters),
        "total_silos": len(silo_structure),
        "total_recommendations": len(recommendations_df) if not recommendations_df.empty else 0,
        "p1_count": len(recommendations_df[recommendations_df["priority"] == 1]) if not recommendations_df.empty else 0,
        "p2_count": len(recommendations_df[recommendations_df["priority"] == 2]) if not recommendations_df.empty else 0,
        "p3_count": len(recommendations_df[recommendations_df["priority"] == 3]) if not recommendations_df.empty else 0,
        "orphan_count": len(page_taxonomy_df[page_taxonomy_df["page_type"] == "orphan_candidate"]),
        "type_counts": page_taxonomy_df["page_type"].value_counts().to_dict() if not page_taxonomy_df.empty else {},
    }

    # Render template
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("report_template.html")
    html_content = template.render(
        summary=summary,
        page_reports=page_reports,
        clusters=clusters,
        silo_structure=silo_structure,
        priority_labels=PRIORITY_LABELS,
        page_type_colors=PAGE_TYPE_COLORS,
        page_type_labels=PAGE_TYPE_LABELS,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info("HTML report saved: %s (%d pages)", html_path, len(page_reports))
    return html_path


def prepare_supabase_records(
    run_id: str,
    pages_df: pd.DataFrame,
    clusters: dict,
    page_taxonomy_df: pd.DataFrame,
    silo_structure: dict,
    recommendations_df: pd.DataFrame,
) -> dict[str, list[dict]]:
    """
    Prepare all data as lists of dicts ready for Supabase insertion.

    Returns:
        Dict with keys: gsc_pages, keyword_clusters, page_taxonomy, silo_structure, link_recommendations
    """
    gsc_pages = pages_df[
        ["url", "clicks", "impressions", "ctr", "position", "opportunity_score"]
    ].copy()
    gsc_pages["id"] = [str(uuid.uuid4()) for _ in range(len(gsc_pages))]
    gsc_pages_records = gsc_pages.to_dict("records")

    cluster_records = []
    for cluster_id, cluster in clusters.items():
        cluster_records.append({
            "id": cluster_id,
            "cluster_label": cluster.get("label", ""),
            "intent": cluster.get("intent", "informational"),
            "lsi_terms": cluster.get("lsi_terms", []),
            "entities": cluster.get("entities", []),
            "anchor_variants": cluster.get("anchor_variants", []),
            "query_count": cluster.get("query_count", 0),
        })

    taxonomy_cols = ["id", "url", "page_type", "cluster_id", "silo_id", "opportunity_score"]
    taxonomy_cols = [c for c in taxonomy_cols if c in page_taxonomy_df.columns]
    taxonomy_records = page_taxonomy_df[taxonomy_cols].to_dict("records")

    silo_records = []
    for silo_id, silo in silo_structure.items():
        silo_records.append({
            "id": silo_id,
            "silo_name": silo.get("silo_name", ""),
            "pillar_url": silo.get("pillar_url"),
            "cluster_post_count": silo.get("cluster_post_count", 0),
            "money_page_count": silo.get("money_page_count", 0),
        })

    rec_cols = ["id", "source_url", "target_url", "anchor_text", "link_type", "priority", "reason", "silo_id", "implementation_status"]
    rec_cols = [c for c in rec_cols if c in recommendations_df.columns]
    rec_records = recommendations_df[rec_cols].to_dict("records") if not recommendations_df.empty else []

    return {
        "gsc_pages": gsc_pages_records,
        "keyword_clusters": cluster_records,
        "page_taxonomy": taxonomy_records,
        "silo_structure": silo_records,
        "link_recommendations": rec_records,
    }
