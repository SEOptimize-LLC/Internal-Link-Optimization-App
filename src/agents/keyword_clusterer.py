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
- Group by semantic meaning AND user intent — merge queries that share the same topic
- Aim for 3-8 clusters per batch — prefer broader, more inclusive groupings
- Only split into a separate cluster when user intent is clearly and meaningfully different
- Long-tail variations of the same core topic belong in the same cluster
- Use the business context to inform groupings
"""



def _cluster_batch(
    queries: list[str],
    business_context: str,
    batch_index: int,
    target_cluster_count: int = 0,
) -> list[dict]:
    """Cluster a batch of queries using AI."""
    query_list = "\n".join(f"- {q}" for q in queries)

    target_str = (
        f" Produce EXACTLY {target_cluster_count} clusters for this batch."
        if target_cluster_count > 0
        else ""
    )

    messages = [
        {"role": "system", "content": CLUSTERING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Business context: {business_context}\n\n"
                f"Cluster these {len(queries)} queries:{target_str}\n{query_list}"
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



def _merge_cross_batch_clusters(
    all_batch_clusters: list[list[dict]],
    total_keyword_count: int = 0,
) -> list[dict]:
    """
    Merge clusters from multiple batches. Uses AI to consolidate duplicate/overlapping
    clusters. Processes ALL clusters (not just the first N) and targets a sensible
    final count based on the total keyword volume.
    """
    if len(all_batch_clusters) == 1:
        return all_batch_clusters[0]

    # Flatten all clusters
    flat_clusters = []
    for batch in all_batch_clusters:
        flat_clusters.extend(batch)

    if len(flat_clusters) <= 20:
        return flat_clusters

    # Target cluster count: roughly 1 cluster per 80-100 keywords, capped at 60
    if total_keyword_count > 0:
        target_count = max(15, min(60, total_keyword_count // 90))
    else:
        target_count = max(15, len(flat_clusters) // 4)

    logger.info(
        "Merging %d raw clusters → target %d final clusters", len(flat_clusters), target_count
    )

    # Build cluster summary — include ALL clusters (in chunks if very large)
    cluster_lines = [
        f"- {c['label']} ({c.get('intent', 'unknown')}): "
        f"{', '.join(c.get('queries', [])[:3])}"
        for c in flat_clusters
    ]

    # If too many clusters to fit in one call, summarise in two passes
    CHUNK_SIZE = 120
    if len(cluster_lines) > CHUNK_SIZE:
        merged_partial: dict[str, dict] = {}
        for i in range(0, len(flat_clusters), CHUNK_SIZE):
            chunk = flat_clusters[i : i + CHUNK_SIZE]
            chunk_merged = _merge_cluster_chunk(chunk, target_count)
            for c in chunk_merged:
                label = c["label"]
                if label not in merged_partial:
                    merged_partial[label] = {
                        "label": label,
                        "intent": c.get("intent", "informational"),
                        "queries": [],
                    }
                merged_partial[label]["queries"].extend(c.get("queries", []))
        flat_clusters = list(merged_partial.values())
        # One final pass to hit the target count
        cluster_lines = [
            f"- {c['label']} ({c.get('intent', 'unknown')}): "
            f"{', '.join(c.get('queries', [])[:3])}"
            for c in flat_clusters
        ]

    cluster_summary = "\n".join(cluster_lines)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an SEO expert. Consolidate these topic clusters by merging "
                "near-duplicates and topically similar groups. "
                "Return JSON: {\"merged_labels\": "
                "[{\"keep\": \"label\", \"merge_into\": \"target label or null\"}]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Consolidate these {len(flat_clusters)} clusters into approximately "
                f"{target_count} final clusters. "
                "Merge aggressively — groups that cover the same broad topic should be one cluster.\n\n"
                f"{cluster_summary}"
            ),
        },
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

        merged: dict[str, dict] = {}
        for cluster in flat_clusters:
            label = cluster["label"]
            target = merge_map.get(label, label)
            if target not in merged:
                merged[target] = {
                    "label": target,
                    "intent": cluster.get("intent", "informational"),
                    "queries": [],
                }
            merged[target]["queries"].extend(cluster.get("queries", []))

        logger.info("Merged to %d final clusters", len(merged))
        return list(merged.values())
    except Exception as e:
        logger.warning("Cluster merge failed, returning flat clusters: %s", e)
        return flat_clusters


def _merge_cluster_chunk(clusters: list[dict], target_count: int) -> list[dict]:
    """Run a single merge pass on a subset of clusters."""
    cluster_summary = "\n".join(
        f"- {c['label']} ({c.get('intent', 'unknown')}): "
        f"{', '.join(c.get('queries', [])[:3])}"
        for c in clusters
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are an SEO expert. Consolidate these topic clusters by merging "
                "near-duplicates and topically similar groups. "
                "Return JSON: {\"merged_labels\": "
                "[{\"keep\": \"label\", \"merge_into\": \"target label or null\"}]}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Consolidate these {len(clusters)} clusters. "
                "Merge aggressively — similar topics should become one cluster.\n\n"
                f"{cluster_summary}"
            ),
        },
    ]
    try:
        result = chat_completion(
            messages=messages, use_fast_model=True, response_format="json", temperature=0.1
        )
        merge_map = {
            item["keep"]: item["merge_into"]
            for item in result.get("merged_labels", [])
            if item.get("merge_into")
        }
        merged: dict[str, dict] = {}
        for cluster in clusters:
            label = cluster["label"]
            target = merge_map.get(label, label)
            if target not in merged:
                merged[target] = {
                    "label": target,
                    "intent": cluster.get("intent", "informational"),
                    "queries": [],
                }
            merged[target]["queries"].extend(cluster.get("queries", []))
        return list(merged.values())
    except Exception as e:
        logger.warning("Chunk merge failed: %s", e)
        return clusters


def cluster_keywords(
    queries_df: pd.DataFrame,
    profile: BusinessProfile,
    pages_df: pd.DataFrame,
    location_code: int = 2840,
    language_code: str = "en",
    progress_callback: Callable[[str], None] = None,
) -> dict:
    """
    Cluster GSC queries into semantic topic groups, optionally enriched with
    DataForSEO search volume and keyword difficulty data.

    Args:
        queries_df: DataFrame with [query, page, clicks, impressions, ctr, position]
        profile: Parsed business profile
        pages_df: Page performance DataFrame
        location_code: DataForSEO location code (default 2840 = US)
        language_code: Language code for DataForSEO (default "en")
        progress_callback: Optional status update callback

    Returns:
        Tuple of:
          - clusters dict: {cluster_id: {label, intent, queries[],
                page_assignments[], query_count,
                total_search_volume, avg_difficulty}}
          - keyword_metrics dict: {query_lower: {search_volume, keyword_difficulty,
                competition, cpc}} — raw per-query DataForSEO data (empty if not configured)
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

    # ── DataForSEO enrichment (optional) ────────────────────────────────────
    keyword_metrics: dict[str, dict] = {}
    try:
        from src.utils.dataforseo import fetch_keyword_metrics
        from src.config.settings import DATAFORSEO_LOGIN
        if DATAFORSEO_LOGIN:
            _progress(
                f"Fetching search volume + keyword difficulty for "
                f"{len(unique_queries):,} queries via DataForSEO..."
            )
            keyword_metrics = fetch_keyword_metrics(
                unique_queries, location_code=location_code, language_code=language_code
            )
            found = sum(1 for k in unique_queries if k.lower() in keyword_metrics)
            _progress(f"DataForSEO: metrics found for {found:,}/{len(unique_queries):,} queries")
    except Exception as e:
        logger.warning("DataForSEO enrichment skipped: %s", e)

    # Sort queries: highest search_volume first (fall back to GSC impressions order)
    if keyword_metrics:
        unique_queries.sort(
            key=lambda q: keyword_metrics.get(q.lower(), {}).get("search_volume", 0),
            reverse=True,
        )

    # Split into batches
    batches = chunk_list(unique_queries, MAX_QUERIES_PER_CLUSTER_BATCH)
    _progress(
        f"Processing {len(batches)} batch(es) of up to "
        f"{MAX_QUERIES_PER_CLUSTER_BATCH} queries each"
    )

    # Compute global target cluster count, then derive per-batch target so the
    # model produces a predictable number of clusters regardless of batch count.
    global_target = max(15, min(60, len(unique_queries) // 90))
    num_batches = len(batches)
    per_batch_target = max(3, min(8, round(global_target / num_batches)))
    logger.info(
        "Cluster targets — global: %d, per-batch: %d (%d batches)",
        global_target, per_batch_target, num_batches,
    )

    all_batch_clusters = []
    for i, batch in enumerate(batches):
        _progress(f"Clustering batch {i + 1}/{len(batches)} ({len(batch)} queries)...")
        batch_clusters = _cluster_batch(batch, business_context, i, per_batch_target)
        all_batch_clusters.append(batch_clusters)

    # Merge clusters from batches
    raw_clusters = _merge_cross_batch_clusters(
        all_batch_clusters, total_keyword_count=len(unique_queries)
    )

    # Safety net: if merge still produced far too many clusters, force one more pass
    if len(raw_clusters) > global_target * 1.5:
        logger.warning(
            "Post-merge has %d clusters (target %d) — running final consolidation",
            len(raw_clusters), global_target,
        )
        raw_clusters = _merge_cluster_chunk(raw_clusters, global_target)

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

        # Aggregate DataForSEO metrics across all queries in the cluster
        volumes = [
            keyword_metrics.get(q.lower(), {}).get("search_volume", 0)
            for q in queries
        ]
        difficulties = [
            keyword_metrics.get(q.lower(), {}).get("keyword_difficulty", 0)
            for q in queries
            if keyword_metrics.get(q.lower(), {}).get("keyword_difficulty", 0) > 0
        ]
        total_search_volume = sum(volumes)
        avg_difficulty = round(sum(difficulties) / len(difficulties), 1) if difficulties else 0

        final_clusters[cluster_id] = {
            "id": cluster_id,
            "label": label,
            "intent": intent,
            "queries": queries,
            "page_assignments": assigned_pages,
            "query_count": len(queries),
            "total_search_volume": total_search_volume,
            "avg_difficulty": avg_difficulty,
        }

    _progress(f"Keyword clustering complete: {len(final_clusters)} topic clusters")
    return final_clusters, keyword_metrics
