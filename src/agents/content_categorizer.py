import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd

from src.agents.profile_parser import BusinessProfile
from src.config.settings import (
    MAX_PAGES_PER_BATCH,
    MAX_PARALLEL_WORKERS,
    MODEL_REASONING,
)
from src.utils.helpers import chunk_list, get_url_path
from src.utils.openrouter import chat_completion

logger = logging.getLogger(__name__)

CLASSIFICATION_SYSTEM_PROMPT = """You are an expert SEO content strategist specializing in site architecture and topical authority.

Classify each page into one of these types:
- "pillar": Comprehensive, broad topic overview page. Highest authority content on a topic. Ranks for head/broad terms.
- "cluster_post": In-depth content on a specific sub-topic within a broader topic. Supports a pillar.
- "money_page": Revenue-driving page — service page, product page, collection page, or category page. Not primarily a blog post.
- "orphan_candidate": Page with no clear topical fit, very low engagement, or that doesn't belong to any identifiable cluster.

Return a JSON object with this exact structure:
{
  "pages": [
    {
      "url": "the exact URL provided",
      "page_type": "pillar|cluster_post|money_page|orphan_candidate",
      "reasoning": "one sentence explanation"
    }
  ]
}

Rules:
- Every URL in the input must appear exactly once in the output
- Use business context to identify money pages (services, products, collections)
- Homepage is typically a pillar or money page
- Blog/article pages are pillar or cluster_post based on breadth
- /services/, /products/, /shop/, /category/ paths are money_page signals
"""

SILO_BUILDER_SYSTEM_PROMPT = """You are an expert SEO architect specializing in SILO site structure.

Given a list of pages with their types and topic cluster assignments, define the SILO structure.
A SILO groups related content under one pillar page, with all cluster posts linking bidirectionally to the pillar.

Return a JSON object:
{
  "silos": [
    {
      "silo_name": "descriptive silo name",
      "pillar_url": "URL of the pillar page",
      "cluster_post_urls": ["url1", "url2"],
      "money_page_urls": ["url1"],
      "cluster_label": "the topic cluster label this silo is based on",
      "pillar_gap": false
    }
  ],
  "pillar_gaps": [
    {
      "cluster_label": "topic needing a pillar",
      "recommended_pillar_title": "suggested title for new pillar content"
    }
  ]
}

Rules:
- Each cluster should have exactly one SILO
- If no pillar page exists for a cluster, set pillar_url to null and pillar_gap to true
- Money pages can appear in multiple silos if they are relevant to multiple topics
- Orphan candidates are excluded from silos
"""


def _classify_page_batch(
    pages_batch: list[dict],
    business_context: str,
    clusters_summary: str,
) -> list[dict]:
    """Classify a batch of pages using AI."""
    pages_list = "\n".join(
        f"- URL: {p['url']} | Top queries: {', '.join(p.get('top_queries', [])[:3])} | Cluster: {p.get('cluster_label', 'unassigned')}"
        for p in pages_batch
    )

    messages = [
        {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Business context: {business_context}\n\n"
                f"Available topic clusters: {clusters_summary}\n\n"
                f"Classify these pages:\n{pages_list}"
            ),
        },
    ]

    result = chat_completion(
        messages=messages,
        model=MODEL_REASONING,
        response_format="json",
        temperature=0.1,
    )

    return result.get("pages", [])


def categorize_content(
    pages_df: pd.DataFrame,
    profile: BusinessProfile,
    clusters: dict,
    progress_callback: Callable[[str], None] = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Classify all pages and build SILO structure.

    Args:
        pages_df: DataFrame with page performance data + cluster assignments
        profile: Business profile
        clusters: Clusters dict from keyword_clusterer
        progress_callback: Optional status callback

    Returns:
        Tuple of (page_taxonomy_df, silo_structure dict)
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    if pages_df.empty:
        logger.warning("No pages to categorize")
        return pd.DataFrame(), {}

    business_context = profile.to_context_string()
    clusters_summary = "; ".join(
        f"{c['label']} ({c['intent']})" for c in list(clusters.values())[:20]
    )

    # Build cluster label lookup: page URL → cluster label
    page_to_cluster: dict[str, str] = {}
    page_to_cluster_id: dict[str, str] = {}
    for cluster_id, cluster in clusters.items():
        for page_url in cluster.get("page_assignments", []):
            page_to_cluster[page_url] = cluster["label"]
            page_to_cluster_id[page_url] = cluster_id

    # Prepare pages for classification
    pages_for_classification = []
    for _, row in pages_df.iterrows():
        url = row["url"]
        pages_for_classification.append({
            "url": url,
            "top_queries": row.get("top_queries", []) if isinstance(row.get("top_queries"), list) else [],
            "cluster_label": page_to_cluster.get(url, "unassigned"),
            "cluster_id": page_to_cluster_id.get(url, ""),
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "opportunity_score": row.get("opportunity_score", 0),
        })

    # Classify in batches
    batches = chunk_list(pages_for_classification, MAX_PAGES_PER_BATCH)
    _progress(f"Classifying {len(pages_for_classification):,} pages in {len(batches)} batch(es)...")

    all_classifications: dict[str, dict] = {}
    _progress(
        f"Classifying {len(batches)} batch(es) in parallel "
        f"({MAX_PARALLEL_WORKERS} workers)..."
    )
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
        futures = {
            executor.submit(
                _classify_page_batch, batch, business_context, clusters_summary
            ): batch
            for batch in batches
        }
        completed = 0
        for future in as_completed(futures):
            try:
                classified = future.result()
                for item in classified:
                    if item.get("url"):
                        all_classifications[item["url"]] = item
            except Exception as e:
                logger.warning("Page classification batch failed: %s", e)
            completed += 1
            _progress(
                f"Classifying: {completed}/{len(batches)} batches complete..."
            )

    # Build taxonomy DataFrame
    taxonomy_records = []
    for page_info in pages_for_classification:
        url = page_info["url"]
        classification = all_classifications.get(url, {})
        page_type = classification.get("page_type", "cluster_post")

        taxonomy_records.append({
            "id": str(uuid.uuid4()),
            "url": url,
            "page_type": page_type,
            "cluster_id": page_info["cluster_id"],
            "cluster_label": page_info["cluster_label"],
            "opportunity_score": page_info["opportunity_score"],
            "clicks": page_info["clicks"],
            "impressions": page_info["impressions"],
            "reasoning": classification.get("reasoning", ""),
        })

    page_taxonomy_df = pd.DataFrame(taxonomy_records)

    type_counts = page_taxonomy_df["page_type"].value_counts().to_dict()
    _progress(f"Classification complete: {type_counts}")

    # --- Build SILO structure ---
    _progress("Building SILO structure...")

    # Group pages by cluster for SILO definition
    cluster_pages: dict[str, dict] = {}
    for _, row in page_taxonomy_df.iterrows():
        cluster_label = row["cluster_label"]
        if cluster_label == "unassigned":
            continue
        if cluster_label not in cluster_pages:
            cluster_pages[cluster_label] = {
                "pillar": None,
                "cluster_posts": [],
                "money_pages": [],
            }
        page_type = row["page_type"]
        if page_type == "pillar":
            # Use the highest-click pillar if multiple exist
            if cluster_pages[cluster_label]["pillar"] is None:
                cluster_pages[cluster_label]["pillar"] = row["url"]
        elif page_type == "cluster_post":
            cluster_pages[cluster_label]["cluster_posts"].append(row["url"])
        elif page_type == "money_page":
            cluster_pages[cluster_label]["money_pages"].append(row["url"])

    # Build structured silo list for AI refinement
    silo_input = []
    for cluster_label, pages in cluster_pages.items():
        silo_input.append({
            "cluster_label": cluster_label,
            "pillar_url": pages["pillar"],
            "cluster_post_urls": pages["cluster_posts"][:20],  # Limit for prompt
            "money_page_urls": pages["money_pages"][:5],
        })

    # Use AI to finalize SILO structure and detect gaps
    cluster_id_lookup = {c["label"]: cid for cid, c in clusters.items()}

    messages = [
        {"role": "system", "content": SILO_BUILDER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Business context: {business_context}\n\n"
                f"Define the SILO structure from these topic groups:\n"
                f"{str(silo_input)}"
            ),
        },
    ]

    silo_result = chat_completion(
        messages=messages,
        model=MODEL_REASONING,
        response_format="json",
        temperature=0.1,
    )

    # Build final silo_structure dict
    silo_structure: dict[str, dict] = {}
    for silo in silo_result.get("silos", []):
        silo_id = str(uuid.uuid4())
        cluster_label = silo.get("cluster_label", "")

        silo_structure[silo_id] = {
            "id": silo_id,
            "silo_name": silo.get("silo_name", cluster_label),
            "pillar_url": silo.get("pillar_url"),
            "cluster_post_urls": silo.get("cluster_post_urls", []),
            "money_page_urls": silo.get("money_page_urls", []),
            "cluster_label": cluster_label,
            "cluster_id": cluster_id_lookup.get(cluster_label, ""),
            "pillar_gap": silo.get("pillar_gap", False),
            "cluster_post_count": len(silo.get("cluster_post_urls", [])),
            "money_page_count": len(silo.get("money_page_urls", [])),
        }

    # Update page_taxonomy_df with silo_id
    url_to_silo: dict[str, str] = {}
    for silo_id, silo in silo_structure.items():
        if silo.get("pillar_url"):
            url_to_silo[silo["pillar_url"]] = silo_id
        for url in silo.get("cluster_post_urls", []):
            url_to_silo[url] = silo_id
        for url in silo.get("money_page_urls", []):
            url_to_silo[url] = silo_id

    page_taxonomy_df["silo_id"] = page_taxonomy_df["url"].map(url_to_silo).fillna("")

    pillar_gaps = silo_result.get("pillar_gaps", [])
    if pillar_gaps:
        _progress(f"Detected {len(pillar_gaps)} content gap(s) — clusters missing a pillar page")

    _progress(f"SILO structure complete: {len(silo_structure)} silos defined")
    return page_taxonomy_df, silo_structure
