import logging
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from src.config.settings import GSC_SCOPES, GSC_PAGE_SIZE
from src.utils.helpers import compute_opportunity_score, normalize_url

logger = logging.getLogger(__name__)


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_oauth_flow(client_id: str, client_secret: str, redirect_uri: str) -> Flow:
    """Create an OAuth2 Flow object from credentials."""
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=GSC_SCOPES,
        redirect_uri=redirect_uri,
    )


def get_auth_url(client_id: str, client_secret: str, redirect_uri: str) -> tuple[str, str, "Flow"]:
    """
    Generate the Google OAuth authorization URL.

    Returns:
        Tuple of (auth_url, state, flow) — the flow object must be stored in
        session state and passed back to exchange_code_for_credentials so the
        PKCE code_verifier is available for the token exchange.
    """
    flow = get_oauth_flow(client_id, client_secret, redirect_uri)
    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    return auth_url, state, flow


def exchange_code_for_credentials(
    code: str,
    flow: "Flow",
) -> dict:
    """
    Exchange an OAuth authorization code for credentials using the original flow.
    The same flow instance that generated the auth URL must be passed here so
    the PKCE code_verifier is intact.

    Returns:
        Credentials dict (serializable, suitable for st.session_state).
    """
    flow.fetch_token(code=code)
    return save_credentials(flow.credentials)


def save_credentials(credentials: Credentials) -> dict:
    """Serialize a Credentials object to a JSON-safe dict for session storage."""
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else GSC_SCOPES,
    }


def load_credentials(credentials_dict: dict) -> Credentials | None:
    """
    Reconstruct a Credentials object from a stored dict.
    Automatically refreshes if expired.

    Returns:
        Valid Credentials object, or None if refresh failed.
    """
    credentials = Credentials(
        token=credentials_dict.get("token"),
        refresh_token=credentials_dict.get("refresh_token"),
        token_uri=credentials_dict.get("token_uri", "https://accounts.google.com/o/oauth2/token"),
        client_id=credentials_dict.get("client_id"),
        client_secret=credentials_dict.get("client_secret"),
        scopes=credentials_dict.get("scopes", GSC_SCOPES),
    )

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            logger.info("GSC credentials refreshed successfully")
        except Exception as e:
            logger.warning("Failed to refresh GSC credentials: %s", e)
            return None

    return credentials


def build_service_from_credentials(credentials_dict: dict):
    """Build an authenticated GSC service from a stored credentials dict."""
    credentials = load_credentials(credentials_dict)
    if credentials is None:
        raise ValueError("GSC credentials are expired or invalid. Please reconnect with Google.")
    return build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


# ── GSC data functions ────────────────────────────────────────────────────────

def list_properties(credentials_dict: dict) -> list[str]:
    """
    List all GSC properties accessible by these OAuth credentials.
    Returns sorted list of property URLs.
    """
    service = build_service_from_credentials(credentials_dict)
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
    credentials_dict: dict,
    site_url: str,
    days_back: int = 90,
    filter_branded: bool = False,
    brand_name: str = "",
    progress_callback: Callable[[str], None] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch GSC search analytics data for a property.

    Args:
        credentials_dict: OAuth credentials dict (from session state)
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
    service = build_service_from_credentials(credentials_dict)

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
