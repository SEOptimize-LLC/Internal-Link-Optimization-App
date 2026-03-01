import logging
import uuid
from typing import Callable

import pandas as pd

from src.agents.profile_parser import BusinessProfile
from src.config.settings import MODEL_REASONING
from src.utils.helpers import chunk_list, truncate_url
from src.utils.openrouter import chat_completion

logger = logging.getLogger(__name__)

MONEY_PAGE_LINK_PROMPT = """You are an expert SEO strategist focused on lead generation and conversion optimization.

For each blog/content page provided, identify the SINGLE most relevant money page (service, product, or category page) it should link to.

Return a JSON object:
{
  "recommendations": [
    {
      "source_url": "the blog/content page URL",
      "target_url": "the most relevant money page URL",
      "anchor_text": "natural, contextual anchor text (5-8 words)",
      "reasoning": "why this connection makes business sense"
    }
  ]
}

Rules:
- Only use money page URLs from the provided list
- Choose the MOST relevant money page based on topical alignment and business context
- Anchor text must be natural and contextual — never use "click here" or generic phrases
- Anchor text should reflect what the service/product actually does
- If no money page is clearly relevant to a content page, omit it from recommendations
"""

ORPHAN_INTEGRATION_PROMPT = """You are an expert SEO architect specializing in site structure optimization.

For each orphan page provided, identify 2-3 existing pages that should link TO it to integrate it into the site structure.

Return a JSON object:
{
  "recommendations": [
    {
      "orphan_url": "the orphan page URL",
      "source_url": "page that should link to the orphan",
      "anchor_text": "natural anchor text for the link",
      "reasoning": "why this page should link to the orphan"
    }
  ]
}

Rules:
- Only use existing page URLs from the provided candidates list
- Choose contextually relevant source pages
- Each orphan should get 2-3 incoming link recommendations
- Anchor text must be descriptive and relevant
"""

ANCHOR_TEXT_SYSTEM_PROMPT = """You are an expert SEO content strategist specializing in contextual internal linking.

For each source→target page pair, generate natural anchor text that:
- Fits naturally into the source page's topic context
- Accurately describes what the target page covers
- Reads like a human writer would use it (not keyword-stuffed)
- Is 3-7 words long

Return a JSON object:
{
  "anchors": [
    {
      "source_url": "...",
      "target_url": "...",
      "anchor_text": "natural contextual anchor text here"
    }
  ]
}

Rules:
- Never use generic phrases like "click here", "learn more", "read this", or "this article"
- Anchor text must reflect the target page's specific topic
- Consider the source page's topic so the phrasing flows naturally in context
- One anchor suggestion per source→target pair
"""


def _generate_pillar_cluster_links(
    silo_structure: dict,
    clusters: dict,
) -> list[dict]:
    """
    Generate bidirectional pillar ↔ cluster post links (Type 1 - P1 priority).
    These are structural necessities, generated deterministically without AI.
    Anchor text is a placeholder (cluster label); enriched by _enrich_anchor_texts later.
    """
    recommendations = []

    cluster_id_to_label: dict[str, str] = {
        cid: c.get("label", "") for cid, c in clusters.items()
    }

    for silo_id, silo in silo_structure.items():
        pillar_url = silo.get("pillar_url")
        cluster_posts = silo.get("cluster_post_urls", [])
        cluster_id = silo.get("cluster_id", "")
        silo_name = silo.get("silo_name", "")
        cluster_label = cluster_id_to_label.get(cluster_id, silo_name)

        if not pillar_url or not cluster_posts:
            continue

        for post_url in cluster_posts:
            # Cluster post → Pillar (placeholder anchor; enriched later)
            recommendations.append({
                "id": str(uuid.uuid4()),
                "source_url": post_url,
                "target_url": pillar_url,
                "anchor_text": cluster_label,
                "link_type": "cluster_to_pillar",
                "priority": 1,
                "reason": f"Cluster post links back to pillar page for '{silo_name}' SILO structure",
                "silo_id": silo_id,
                "silo_name": silo_name,
                "implementation_status": "pending",
            })

            # Pillar → Cluster post (placeholder anchor; enriched later)
            recommendations.append({
                "id": str(uuid.uuid4()),
                "source_url": pillar_url,
                "target_url": post_url,
                "anchor_text": cluster_label,
                "link_type": "pillar_to_cluster",
                "priority": 1,
                "reason": f"Pillar page links to cluster post to distribute authority within '{silo_name}' SILO",
                "silo_id": silo_id,
                "silo_name": silo_name,
                "implementation_status": "pending",
            })

    logger.info("Generated %d P1 pillar↔cluster link recommendations", len(recommendations))
    return recommendations


def _generate_authority_boost_links(
    page_taxonomy_df: pd.DataFrame,
    silo_structure: dict,
    clusters: dict,
) -> list[dict]:
    """
    Generate authority → underperformer boost links (Type 2 - P2 priority).
    Top 3 pages by clicks → bottom 3 by opportunity score within same cluster.
    """
    recommendations = []

    for silo_id, silo in silo_structure.items():
        cluster_id = silo.get("cluster_id", "")
        silo_name = silo.get("silo_name", "")
        cluster = clusters.get(cluster_id, {})

        # Get all pages in this cluster
        silo_urls = set(silo.get("cluster_post_urls", []) + ([silo.get("pillar_url")] if silo.get("pillar_url") else []))
        if len(silo_urls) < 2:
            continue

        silo_pages = page_taxonomy_df[page_taxonomy_df["url"].isin(silo_urls)].copy()
        if len(silo_pages) < 2:
            continue

        # Top 3 authority pages (highest clicks)
        top_authority = silo_pages.nlargest(3, "clicks")
        # Bottom 3 underperformers (highest opportunity score = most impression/position gap)
        top_opportunity = silo_pages[silo_pages["clicks"] < silo_pages["clicks"].median()].nlargest(3, "opportunity_score")

        cluster_label = cluster.get("label", silo_name)

        for _, authority_row in top_authority.iterrows():
            for _, opportunity_row in top_opportunity.iterrows():
                # Don't link a page to itself
                if authority_row["url"] == opportunity_row["url"]:
                    continue
                # Don't create duplicate pillar↔cluster links (already in Type 1)
                if (
                    authority_row["page_type"] == "pillar" and opportunity_row["page_type"] == "cluster_post"
                ) or (
                    authority_row["page_type"] == "cluster_post" and opportunity_row["page_type"] == "pillar"
                ):
                    continue

                recommendations.append({
                    "id": str(uuid.uuid4()),
                    "source_url": authority_row["url"],
                    "target_url": opportunity_row["url"],
                    "anchor_text": cluster_label,
                    "link_type": "authority_boost",
                    "priority": 2,
                    "reason": (
                        f"High-authority page ({authority_row['clicks']:,.0f} clicks) boosts "
                        f"underperforming page (opportunity score: {opportunity_row['opportunity_score']:.2f}) "
                        f"within '{silo_name}' cluster"
                    ),
                    "silo_id": silo_id,
                    "silo_name": silo_name,
                    "implementation_status": "pending",
                })

    logger.info("Generated %d P2 authority boost link recommendations", len(recommendations))
    return recommendations


def _generate_blog_to_money_links(
    page_taxonomy_df: pd.DataFrame,
    silo_structure: dict,
    profile: BusinessProfile,
    progress_callback: Callable[[str], None] = None,
) -> list[dict]:
    """
    Generate blog/content → money page links (Type 3 - P3 priority) using AI.
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)

    money_pages = page_taxonomy_df[page_taxonomy_df["page_type"] == "money_page"]["url"].tolist()
    content_pages = page_taxonomy_df[
        page_taxonomy_df["page_type"].isin(["pillar", "cluster_post"])
    ].copy()

    if not money_pages or content_pages.empty:
        logger.info("No money pages or content pages found for blog→money linking")
        return []

    # Build money page descriptions from profile services
    money_page_descriptions = []
    for mp_url in money_pages[:15]:
        # Try to match to a service from profile
        matched_service = next(
            (s for s in profile.services if s.get("url_hint", "").lower() in mp_url.lower()),
            None,
        )
        desc = matched_service["description"] if matched_service else "service/product page"
        money_page_descriptions.append(f"{mp_url} ({desc})")

    money_pages_str = "\n".join(money_page_descriptions)
    business_context = profile.to_context_string()

    # Batch content pages
    content_page_list = content_pages[["url", "cluster_label"]].to_dict("records")
    batches = chunk_list(content_page_list, 20)
    _progress(f"Generating blog→money page links for {len(content_page_list)} content pages...")

    all_recommendations = []

    for i, batch in enumerate(batches):
        _progress(f"Blog→money links batch {i + 1}/{len(batches)}...")

        content_pages_str = "\n".join(
            f"- {p['url']} (topic: {p.get('cluster_label', 'general')})" for p in batch
        )

        messages = [
            {"role": "system", "content": MONEY_PAGE_LINK_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Business context: {business_context}\n\n"
                    f"Available money pages:\n{money_pages_str}\n\n"
                    f"Content pages to link from:\n{content_pages_str}"
                ),
            },
        ]

        result = chat_completion(
            messages=messages,
            model=MODEL_REASONING,
            response_format="json",
            temperature=0.2,
        )

        # Build URL to silo lookup
        url_to_silo = {}
        url_to_silo_name = {}
        for silo_id, silo in silo_structure.items():
            for url in silo.get("cluster_post_urls", []) + [silo.get("pillar_url", "")]:
                if url:
                    url_to_silo[url] = silo_id
                    url_to_silo_name[url] = silo.get("silo_name", "")

        for rec in result.get("recommendations", []):
            source = rec.get("source_url", "")
            target = rec.get("target_url", "")
            if source and target and target in money_pages:
                all_recommendations.append({
                    "id": str(uuid.uuid4()),
                    "source_url": source,
                    "target_url": target,
                    "anchor_text": rec.get("anchor_text", ""),
                    "link_type": "blog_to_money",
                    "priority": 3,
                    "reason": rec.get("reasoning", "Blog post links to relevant service/product page"),
                    "silo_id": url_to_silo.get(source, ""),
                    "silo_name": url_to_silo_name.get(source, ""),
                    "implementation_status": "pending",
                })

    logger.info("Generated %d P3 blog→money page link recommendations", len(all_recommendations))
    return all_recommendations


def _generate_orphan_links(
    page_taxonomy_df: pd.DataFrame,
    clusters: dict,
    progress_callback: Callable[[str], None] = None,
) -> list[dict]:
    """
    Generate links to integrate orphan pages (Type 4 - P3 priority).
    """
    orphans = page_taxonomy_df[page_taxonomy_df["page_type"] == "orphan_candidate"]["url"].tolist()
    if not orphans:
        logger.info("No orphan candidates found")
        return []

    # Build candidate source pages (top pages by clicks, excluding orphans)
    candidate_pages = (
        page_taxonomy_df[page_taxonomy_df["page_type"] != "orphan_candidate"]
        .nlargest(30, "clicks")["url"]
        .tolist()
    )

    if not candidate_pages:
        return []

    candidates_str = "\n".join(f"- {url}" for url in candidate_pages[:20])
    orphans_str = "\n".join(f"- {url}" for url in orphans[:10])

    messages = [
        {"role": "system", "content": ORPHAN_INTEGRATION_PROMPT},
        {
            "role": "user",
            "content": (
                f"Orphan pages needing integration:\n{orphans_str}\n\n"
                f"Candidate source pages:\n{candidates_str}"
            ),
        },
    ]

    result = chat_completion(
        messages=messages,
        model=MODEL_REASONING,
        response_format="json",
        temperature=0.2,
    )

    recommendations = []
    for rec in result.get("recommendations", []):
        if rec.get("source_url") and rec.get("orphan_url"):
            recommendations.append({
                "id": str(uuid.uuid4()),
                "source_url": rec["source_url"],
                "target_url": rec["orphan_url"],
                "anchor_text": rec.get("anchor_text", ""),
                "link_type": "orphan_integration",
                "priority": 3,
                "reason": rec.get("reasoning", "Integrating orphan page into site structure"),
                "silo_id": "",
                "silo_name": "Orphan Integration",
                "implementation_status": "pending",
            })

    logger.info("Generated %d orphan integration link recommendations", len(recommendations))
    return recommendations


def _enrich_anchor_texts(
    recommendations: list[dict],
    clusters: dict,
    page_taxonomy_df: pd.DataFrame,
    profile: BusinessProfile,
    progress_callback: Callable[[str], None] = None,
) -> list[dict]:
    """
    Replace placeholder anchor text on P1/P2 links with AI-generated contextual anchors.
    Each source→target pair gets anchor text informed by both pages' cluster topics
    and the business profile, so phrasing is specific and natural.
    """
    if not recommendations:
        return recommendations

    # Build URL → cluster_id lookup from taxonomy
    url_to_cluster_id: dict[str, str] = {}
    if not page_taxonomy_df.empty and "cluster_id" in page_taxonomy_df.columns:
        for _, row in page_taxonomy_df.iterrows():
            if row.get("cluster_id"):
                url_to_cluster_id[row["url"]] = str(row["cluster_id"])

    business_context = profile.to_context_string()
    batches = chunk_list(recommendations, 25)
    enriched: list[dict] = []

    for i, batch in enumerate(batches):
        if progress_callback:
            progress_callback(f"Generating contextual anchor text: batch {i + 1}/{len(batches)}...")

        pairs_text = []
        for j, rec in enumerate(batch, 1):
            src_cid = url_to_cluster_id.get(rec["source_url"], "")
            tgt_cid = url_to_cluster_id.get(rec["target_url"], "")
            src_cluster = clusters.get(src_cid, {})
            tgt_cluster = clusters.get(tgt_cid, {})

            src_label = src_cluster.get("label", rec.get("silo_name", ""))
            tgt_label = tgt_cluster.get("label", rec.get("silo_name", ""))
            src_queries = ", ".join(src_cluster.get("queries", [])[:3])
            tgt_queries = ", ".join(tgt_cluster.get("queries", [])[:3])

            src_context = f"{src_label}" + (f" (e.g. {src_queries})" if src_queries else "")
            tgt_context = f"{tgt_label}" + (f" (e.g. {tgt_queries})" if tgt_queries else "")

            pairs_text.append(
                f"{j}. source_url: {rec['source_url']}\n"
                f"   source_topic: {src_context}\n"
                f"   target_url: {rec['target_url']}\n"
                f"   target_topic: {tgt_context}"
            )

        messages = [
            {"role": "system", "content": ANCHOR_TEXT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Business context: {business_context}\n\n"
                    f"Generate anchor text for {len(batch)} link pairs:\n\n"
                    + "\n\n".join(pairs_text)
                ),
            },
        ]

        try:
            result = chat_completion(
                messages=messages,
                use_fast_model=True,
                response_format="json",
                temperature=0.2,
            )
            anchor_map = {
                (a["source_url"], a["target_url"]): a["anchor_text"]
                for a in result.get("anchors", [])
                if a.get("source_url") and a.get("target_url") and a.get("anchor_text")
            }
        except Exception as e:
            logger.warning("Anchor text enrichment batch %d failed: %s", i + 1, e)
            anchor_map = {}

        for rec in batch:
            key = (rec["source_url"], rec["target_url"])
            if key in anchor_map:
                rec = {**rec, "anchor_text": anchor_map[key]}
            enriched.append(rec)

    logger.info("Anchor text enrichment complete: %d links updated", len(enriched))
    return enriched


def generate_link_recommendations(
    silo_structure: dict,
    page_taxonomy_df: pd.DataFrame,
    clusters: dict,
    profile: BusinessProfile,
    progress_callback: Callable[[str], None] = None,
) -> pd.DataFrame:
    """
    Generate all four types of internal link recommendations.

    Args:
        silo_structure: SILO structure dict from content_categorizer
        page_taxonomy_df: Page taxonomy DataFrame
        clusters: Keyword clusters dict
        profile: Business profile
        progress_callback: Optional status callback

    Returns:
        recommendations_df sorted by priority
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    all_recommendations = []

    _progress("Generating P1: Pillar ↔ Cluster bidirectional links...")
    p1_recs = _generate_pillar_cluster_links(silo_structure, clusters)

    _progress("Generating P2: Authority boost links...")
    p2_recs = _generate_authority_boost_links(page_taxonomy_df, silo_structure, clusters)

    _progress(f"Generating contextual anchor text for {len(p1_recs) + len(p2_recs)} P1/P2 links...")
    enriched_recs = _enrich_anchor_texts(
        p1_recs + p2_recs, clusters, page_taxonomy_df, profile, progress_callback
    )
    all_recommendations.extend(enriched_recs)

    _progress("Generating P3: Blog → Money page connections...")
    all_recommendations.extend(
        _generate_blog_to_money_links(page_taxonomy_df, silo_structure, profile, progress_callback)
    )

    _progress("Generating P3: Orphan page integration...")
    all_recommendations.extend(
        _generate_orphan_links(page_taxonomy_df, clusters, progress_callback)
    )

    if not all_recommendations:
        logger.warning("No link recommendations generated")
        return pd.DataFrame()

    recommendations_df = pd.DataFrame(all_recommendations)
    recommendations_df = recommendations_df.sort_values(["priority", "silo_name"]).reset_index(drop=True)

    # Deduplicate: same source→target pair keeps highest priority
    recommendations_df = (
        recommendations_df.sort_values("priority")
        .drop_duplicates(subset=["source_url", "target_url"], keep="first")
        .reset_index(drop=True)
    )

    priority_counts = recommendations_df["priority"].value_counts().sort_index().to_dict()
    _progress(
        f"Total recommendations: {len(recommendations_df)} — "
        f"P1: {priority_counts.get(1, 0)}, P2: {priority_counts.get(2, 0)}, P3: {priority_counts.get(3, 0)}"
    )

    return recommendations_df
