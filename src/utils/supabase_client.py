import logging
from typing import Any
from supabase import create_client, Client
from src.config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    """Get or create the Supabase client (singleton)."""
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment variables")
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def create_analysis_run(run_id: str, client_name: str, gsc_property: str, date_range_days: int) -> dict:
    """Insert a new analysis run record and return it."""
    client = get_client()
    data = {
        "id": run_id,
        "client_name": client_name,
        "gsc_property": gsc_property,
        "date_range_days": date_range_days,
        "status": "running",
    }
    result = client.table("analysis_runs").upsert(data).execute()
    logger.info("Created analysis run: %s for client: %s", run_id, client_name)
    return result.data[0] if result.data else data


def update_run_status(run_id: str, status: str) -> None:
    """Update the status of an analysis run."""
    client = get_client()
    client.table("analysis_runs").update({"status": status}).eq("id", run_id).execute()
    logger.info("Updated run %s status to: %s", run_id, status)


def save_gsc_pages(run_id: str, pages: list[dict]) -> None:
    """Batch insert GSC page data for a run."""
    if not pages:
        return
    client = get_client()
    batch_size = 500
    for i in range(0, len(pages), batch_size):
        batch = pages[i:i + batch_size]
        for row in batch:
            row["run_id"] = run_id
        client.table("gsc_pages").upsert(batch).execute()
    logger.info("Saved %d GSC pages for run %s", len(pages), run_id)


def save_keyword_clusters(run_id: str, clusters: list[dict]) -> None:
    """Save keyword cluster records for a run."""
    if not clusters:
        return
    client = get_client()
    for row in clusters:
        row["run_id"] = run_id
    client.table("keyword_clusters").upsert(clusters).execute()
    logger.info("Saved %d keyword clusters for run %s", len(clusters), run_id)


def save_page_taxonomy(run_id: str, pages: list[dict]) -> None:
    """Save page taxonomy (type + cluster assignments) for a run."""
    if not pages:
        return
    client = get_client()
    batch_size = 500
    for i in range(0, len(pages), batch_size):
        batch = pages[i:i + batch_size]
        for row in batch:
            row["run_id"] = run_id
        client.table("page_taxonomy").upsert(batch).execute()
    logger.info("Saved %d page taxonomy records for run %s", len(pages), run_id)


def save_silo_structure(run_id: str, silos: list[dict]) -> None:
    """Save SILO structure records for a run."""
    if not silos:
        return
    client = get_client()
    for row in silos:
        row["run_id"] = run_id
    client.table("silo_structure").upsert(silos).execute()
    logger.info("Saved %d silos for run %s", len(silos), run_id)


def save_link_recommendations(run_id: str, recommendations: list[dict]) -> None:
    """Batch insert link recommendations for a run."""
    if not recommendations:
        return
    client = get_client()
    batch_size = 500
    for i in range(0, len(recommendations), batch_size):
        batch = recommendations[i:i + batch_size]
        for row in batch:
            row["run_id"] = run_id
        client.table("link_recommendations").upsert(batch).execute()
    logger.info("Saved %d link recommendations for run %s", len(recommendations), run_id)


def get_previous_runs(client_name: str) -> list[dict]:
    """Fetch previous analysis runs for a client (for reference)."""
    client = get_client()
    result = (
        client.table("analysis_runs")
        .select("id, client_name, gsc_property, date_range_days, status, created_at")
        .eq("client_name", client_name)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    return result.data or []


def save_all_results(
    run_id: str,
    gsc_pages: list[dict],
    keyword_clusters: list[dict],
    page_taxonomy: list[dict],
    silo_structure: list[dict],
    link_recommendations: list[dict],
) -> None:
    """Save all analysis results to Supabase in sequence."""
    save_gsc_pages(run_id, gsc_pages)
    save_keyword_clusters(run_id, keyword_clusters)
    save_page_taxonomy(run_id, page_taxonomy)
    save_silo_structure(run_id, silo_structure)
    save_link_recommendations(run_id, link_recommendations)
    update_run_status(run_id, "completed")
    logger.info("All results saved for run %s", run_id)
