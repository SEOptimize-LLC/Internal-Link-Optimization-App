import logging
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

from src.config.settings import GSC_SCOPES, GSC_PAGE_SIZE
from src.utils.helpers import compute_opportunity_score, normalize_url

logger = logging.getLogger(__name__)


def build_service(service_account_json_path: str):
    """Build authenticated GSC service from service account JSON file."""
    credentials = service_account.Credentials.from_service_account_file(
        service_account_json_path,
        scopes=GSC_SCOPES,
    )
    return build("searchconsole", "v1", credentials=credentials)


def list_properties(service_account_json_path: str) -> list[str]:
    """
    List all GSC properties accessible by this service account.
    Returns sorted list of property URLs.
    """
    service = build_service(service_account_json_path)
    result = service.sites().list().execute()
    sites = result.get("siteEntry", [])
    return sorted([s["siteUrl"] for s in sites])


def _fetch_paginated(
    service,
    site_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int = GSC_PAGE_SIZE,
) -> list[dict]:
    """Fetch all rows for a given dimensions request, handling pagination."""
    all_rows = []
    start_row = 0

    while True:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
        }

        response = (
            service.searchanalytics()
            .query(siteUrl=site_url, body=body)
            .execute()
        )

        rows = response.get("rows", [])
        all_rows.extend(rows)

        if len(rows) < row_limit:
            break

        start_row += row_limit
        logger.debug("Fetched %d rows so far for dimensions %s", len(all_rows), dimensions)

    return all_rows


def fetch_gsc_data(
    service_account_json_path: str,
    site_url: str,
    days_back: int = 90,
    filter_branded: bool = False,
    brand_name: str = "",
    progress_callback: Callable[[str], None] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch GSC search analytics data for a property.

    Args:
        service_account_json_path: Path to service account JSON
        site_url: GSC property URL (e.g., https://example.com/)
        days_back: Number of days of data to fetch
        filter_branded: If True, remove queries containing brand_name
        brand_name: Brand name to filter (used if filter_branded=True)
        progress_callback: Optional callable for progress status updates

    Returns:
        Tuple of (queries_df, pages_df) DataFrames
    """
    def _progress(msg: str):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    _progress(f"Connecting to GSC for {site_url}")
    service = build_service(service_account_json_path)

    # --- Query + Page level data ---
    _progress(f"Fetching query+page data ({start_date} to {end_date})...")
    raw_query_rows = _fetch_paginated(
        service, site_url, start_date, end_date,
        dimensions=["query", "page"]
    )
    _progress(f"Retrieved {len(raw_query_rows):,} query-page rows")

    # --- Page level data (aggregated) ---
    _progress("Fetching page-level aggregate data...")
    raw_page_rows = _fetch_paginated(
        service, site_url, start_date, end_date,
        dimensions=["page"]
    )
    _progress(f"Retrieved {len(raw_page_rows):,} unique pages")

    # --- Build queries DataFrame ---
    query_records = []
    for row in raw_query_rows:
        keys = row.get("keys", [])
        query = keys[0] if len(keys) > 0 else ""
        page = keys[1] if len(keys) > 1 else ""
        query_records.append({
            "query": query,
            "page": normalize_url(page),
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0.0),
            "position": row.get("position", 100.0),
        })

    queries_df = pd.DataFrame(query_records)

    # Filter branded queries
    if filter_branded and brand_name:
        brand_lower = brand_name.lower()
        before = len(queries_df)
        queries_df = queries_df[~queries_df["query"].str.lower().str.contains(brand_lower, na=False)]
        _progress(f"Filtered {before - len(queries_df)} branded queries")

    # Remove zero-engagement queries
    queries_df = queries_df[~((queries_df["clicks"] == 0) & (queries_df["impressions"] < 5))]

    # --- Build pages DataFrame ---
    page_records = []
    for row in raw_page_rows:
        page = normalize_url(row.get("keys", [""])[0])
        impressions = row.get("impressions", 0)
        clicks = row.get("clicks", 0)
        position = row.get("position", 100.0)
        page_records.append({
            "url": page,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": row.get("ctr", 0.0),
            "position": position,
            "opportunity_score": compute_opportunity_score(impressions, position, clicks),
        })

    pages_df = pd.DataFrame(page_records)

    # Add top query per page to pages_df
    if not queries_df.empty and not pages_df.empty:
        top_queries = (
            queries_df.sort_values("impressions", ascending=False)
            .groupby("page")["query"]
            .apply(lambda x: list(x.head(5)))
            .reset_index()
            .rename(columns={"query": "top_queries", "page": "url"})
        )
        pages_df = pages_df.merge(top_queries, on="url", how="left")
        pages_df["top_queries"] = pages_df["top_queries"].apply(
            lambda x: x if isinstance(x, list) else []
        )

    _progress(
        f"GSC data ready: {len(queries_df):,} queries across {len(pages_df):,} pages"
    )

    return queries_df, pages_df
