import logging
import uuid
from typing import Callable

import pandas as pd

from src.agents.profile_parser import BusinessProfile
from src.config.settings import MAX_QUERIES_PER_CLUSTER_BATCH
from src.utils.helpers import chunk_list, deduplicate_queries
from src.utils.openrouter import chat_completion

logger = logging.getLogger(__name__)

CLUSTERING_SYSTEM_PROMPT = """You are an expert SEO keyword analyst specializing in semantic search clustering.
Group the provided queries into meaningful topic clusters.

Return a JSON object with this exact structure:
{
  "clusters": [
    {
      "label": "cluster topic name (3-6 words, descriptive)",
      "intent": "informational|commercial|transactional|navigational",
      "queries": ["query1", "query2", ...]
    }
  ]
}

Rules:
- Each query must appear in exactly one cluster
- Cluster labels should be descriptive topic names, not generic categories
- Group by semantic meaning and user intent, not just keyword overlap
- Aim for 5-20 clusters depending on the diversity of queries
- Use the business context to inform groupings
"""



def _cluster_batch(
    queries: list[str],
    business_context: str,
    batch_index: int,
) -> list[dict]:
    """Cluster a batch of queries using AI."""
    query_list = "\n".join(f"- {q}" for q in queries)

    messages = [
        {"role": "system", "content": CLUSTERING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Business context: {business_context}\n\n"
                f"Cluster these {len(queries)} queries:\n{query_list}"
            ),
        },
    ]

    result = chat_completion(
        messages=messages,
        use_fast_model=True,
        response_format="json",
        temperature=0.1,
    )

    clusters = result.get("clusters", [])
    logger.debug("Batch %d: %d queries → %d clusters", batch_index, len(queries), len(clusters))
    return clusters



def _merge_cross_batch_clusters(all_batch_clusters: list[list[dict]]) -> list[dict]:
    """
    Merge clusters from multiple batches. Uses AI to consolidate duplicate/overlapping clusters
    if there are multiple batches; otherwise returns the single batch directly.
    """
    if len(all_batch_clusters) == 1:
        return all_batch_clusters[0]

    # Flatten all clusters
    flat_clusters = []
    for batch in all_batch_clusters:
        flat_clusters.extend(batch)

    if len(flat_clusters) <= 30:
        return flat_clusters

    # For large datasets with many batches, merge similar clusters via AI
    cluster_summary = "\n".join(
        f"- {c['label']} ({c.get('intent', 'unknown')}): {', '.join(c.get('queries', [])[:3])}"
        for c in flat_clusters[:80]
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an SEO expert. Consolidate these topic clusters by merging near-duplicates. "
                "Return JSON: {\"merged_labels\": [{\"keep\": \"label\", \"merge_into\": \"label or null\"}]}"
            ),
        },
        {"role": "user", "content": f"Consolidate these clusters:\n{cluster_summary}"},
    ]

    try:
        merge_map_result = chat_completion(
            messages=messages, use_fast_model=True, response_format="json", temperature=0.1
        )
        merge_map = {
            item["keep"]: item["merge_into"]
            for item in merge_map_result.get("merged_labels", [])
            if item.get("merge_into")
        }

        # Apply merge map
        merged: dict[str, dict] = {}
        for cluster in flat_clusters:
            label = cluster["label"]
            target = merge_map.get(label, label)
            if target not in merged:
                merged[target] = {"label": target, "intent": cluster.get("intent", "informational"), "queries": []}
            merged[target]["queries"].extend(cluster.get("queries", []))

        return list(merged.values())
    except Exception as e:
        logger.warning("Cluster merge failed, returning flat clusters: %s", e)
        return flat_clusters


def cluster_keywords(
    queries_df: pd.DataFrame,
    profile: BusinessProfile,
    pages_df: pd.DataFrame,
    progress_callback: Callable[[str], None] = None,
) -> dict:
    """
    Cluster GSC queries into semantic topic groups.

    Args:
        queries_df: DataFrame with columns [query, page, clicks, impressions, ctr, position]
        profile: Parsed business profile
        pages_df: Page performance DataFrame
        progress_callback: Optional status update callback

    Returns:
        clusters dict: {cluster_id: {label, intent, queries[], page_assignments[], query_count}}
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    if queries_df.empty:
        logger.warning("No queries to cluster")
        return {}

    business_context = profile.to_context_string()

    # Deduplicate and sort queries by impressions
    unique_queries = (
        queries_df.groupby("query")["impressions"]
        .sum()
        .sort_values(ascending=False)
        .index.tolist()
    )
    unique_queries = deduplicate_queries(unique_queries)
    _progress(f"Clustering {len(unique_queries):,} unique queries...")

    # Split into batches
    batches = chunk_list(unique_queries, MAX_QUERIES_PER_CLUSTER_BATCH)
    _progress(f"Processing {len(batches)} batch(es) of up to {MAX_QUERIES_PER_CLUSTER_BATCH} queries each")

    all_batch_clusters = []
    for i, batch in enumerate(batches):
        _progress(f"Clustering batch {i + 1}/{len(batches)} ({len(batch)} queries)...")
        batch_clusters = _cluster_batch(batch, business_context, i)
        all_batch_clusters.append(batch_clusters)

    # Merge clusters from batches
    raw_clusters = _merge_cross_batch_clusters(all_batch_clusters)
    _progress(f"Identified {len(raw_clusters)} topic clusters")

    # --- Page assignments ---
    # For each page, find its dominant cluster based on which queries it ranks for
    page_query_map: dict[str, list[str]] = {}
    for _, row in queries_df.iterrows():
        page = row["page"]
        if page not in page_query_map:
            page_query_map[page] = []
        page_query_map[page].append(row["query"])

    # Build query → cluster lookup
    query_to_cluster: dict[str, str] = {}
    for cluster in raw_clusters:
        for q in cluster.get("queries", []):
            query_to_cluster[q.lower()] = cluster["label"]

    # Assign pages to their dominant cluster
    page_cluster_votes: dict[str, dict[str, int]] = {}
    for page, page_queries in page_query_map.items():
        votes: dict[str, int] = {}
        for q in page_queries:
            cluster_label = query_to_cluster.get(q.lower())
            if cluster_label:
                votes[cluster_label] = votes.get(cluster_label, 0) + 1
        if votes:
            page_cluster_votes[page] = votes

    # --- Build final clusters ---
    final_clusters: dict[str, dict] = {}

    for i, cluster in enumerate(raw_clusters):
        cluster_id = str(uuid.uuid4())
        label = cluster.get("label", f"Cluster {i + 1}")
        queries = cluster.get("queries", [])
        intent = cluster.get("intent", "informational")

        # Identify pages assigned to this cluster
        assigned_pages = [
            page for page, votes in page_cluster_votes.items()
            if max(votes, key=votes.get) == label
        ]

        final_clusters[cluster_id] = {
            "id": cluster_id,
            "label": label,
            "intent": intent,
            "queries": queries,
            "page_assignments": assigned_pages,
            "query_count": len(queries),
        }

    _progress(f"Keyword clustering complete: {len(final_clusters)} topic clusters")
    return final_clusters
