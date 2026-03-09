import base64
import logging
from datetime import datetime, timedelta

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config.settings import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD

logger = logging.getLogger(__name__)

DATAFORSEO_BASE_URL = "https://api.dataforseo.com/v3"


def _get_credentials() -> tuple[str, str]:
    """
    Resolve DataForSEO credentials at call time.

    Priority:
    1. st.secrets flat keys:       DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD
    2. st.secrets nested section:  [dataforseo] login / password
    3. Module-level constants:     read from environment variables at import time

    Supports all Streamlit Cloud secrets formats and local .env files.
    """
    login = DATAFORSEO_LOGIN
    password = DATAFORSEO_PASSWORD
    try:
        import streamlit as st
        if not login:
            login = st.secrets.get("DATAFORSEO_LOGIN", "")
        if not password:
            password = st.secrets.get("DATAFORSEO_PASSWORD", "")
        # Try nested [dataforseo] section
        if not login or not password:
            dfs = st.secrets.get("dataforseo", {})
            if not login:
                login = dfs.get("login", "")
            if not password:
                password = dfs.get("password", "")
    except Exception:
        pass
    return login, password

# Common location codes for the UI selector
LOCATION_OPTIONS = {
    "United States": 2840,
    "United Kingdom": 2826,
    "Canada": 2124,
    "Australia": 2036,
    "Ireland": 2372,
    "New Zealand": 2554,
    "South Africa": 2710,
    "India": 2356,
    "Germany": 2276,
    "France": 2250,
    "Spain": 2724,
    "Netherlands": 2528,
    "Brazil": 2076,
    "Mexico": 2484,
}


def _auth_header() -> dict:
    login, password = _get_credentials()
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _last_30_days() -> tuple[str, str]:
    """Return (date_from, date_to) for the previous 30 days as YYYY-MM-DD strings."""
    today = datetime.utcnow().date()
    date_to = today - timedelta(days=1)
    date_from = today - timedelta(days=30)
    return date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _post(endpoint: str, payload: list) -> dict:
    response = requests.post(
        f"{DATAFORSEO_BASE_URL}{endpoint}",
        headers=_auth_header(),
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    return response.json()


def fetch_keyword_metrics(
    keywords: list[str],
    location_code: int = 2840,
    language_code: str = "en",
) -> dict[str, dict]:
    """
    Fetch search volume and keyword difficulty from DataForSEO.

    Search volume is the average of:
      1. Google Ads search volume (location + language filtered, last 30 days)
      2. Clickstream global search volume (last 30 days)
    If only one source returns data for a keyword, that value is used as-is.

    Args:
        keywords: List of keyword strings to look up
        location_code: DataForSEO location code (default 2840 = US)
        language_code: Language code (default "en")

    Returns:
        Dict mapping keyword (lowercase) → {search_volume, competition, cpc, keyword_difficulty}
        Returns empty dict if credentials are not configured or all calls fail.
    """
    login, password = _get_credentials()
    if not login or not password:
        logger.info("DataForSEO credentials not configured — skipping keyword metrics enrichment")
        return {}

    if not keywords:
        return {}

    date_from, date_to = _last_30_days()
    BATCH = 1000

    # ── 1. Google Ads search volume (location-specific, last 30 days) ─────────
    google_sv: dict[str, int] = {}
    google_meta: dict[str, dict] = {}  # competition + cpc come from Google Ads only
    total_sv_batches = -(-len(keywords) // BATCH)

    for i in range(0, len(keywords), BATCH):
        batch = keywords[i : i + BATCH]
        try:
            data = _post(
                "/keywords_data/google_ads/search_volume/live",
                [{
                    "keywords": batch,
                    "location_code": location_code,
                    "language_code": language_code,
                    "date_from": date_from,
                    "date_to": date_to,
                }],
            )
            for task in data.get("tasks", []):
                for item in task.get("result") or []:
                    kw = (item.get("keyword") or "").lower()
                    if kw:
                        google_sv[kw] = item.get("search_volume") or 0
                        google_meta[kw] = {
                            "competition": round(item.get("competition") or 0.0, 2),
                            "cpc": round(item.get("cpc") or 0.0, 2),
                        }
        except Exception as e:
            logger.warning(
                "DataForSEO Google Ads SV batch %d/%d failed: %s",
                i // BATCH + 1,
                total_sv_batches,
                e,
            )

    # ── 2. Clickstream global search volume (last 30 days) ───────────────────
    clickstream_sv: dict[str, int] = {}
    total_cs_batches = -(-len(keywords) // BATCH)

    for i in range(0, len(keywords), BATCH):
        batch = keywords[i : i + BATCH]
        try:
            data = _post(
                "/keywords_data/clickstream_data/global_search_volume/live",
                [{
                    "keywords": batch,
                    "date_from": date_from,
                    "date_to": date_to,
                }],
            )
            for task in data.get("tasks", []):
                for item in task.get("result") or []:
                    kw = (item.get("keyword") or "").lower()
                    if kw:
                        clickstream_sv[kw] = item.get("search_volume") or 0
        except Exception as e:
            logger.warning(
                "DataForSEO Clickstream SV batch %d/%d failed: %s",
                i // BATCH + 1,
                total_cs_batches,
                e,
            )

    # ── 3. Average the two search volume sources per keyword ──────────────────
    results: dict[str, dict] = {}
    all_sv_keywords = set(google_sv.keys()) | set(clickstream_sv.keys())

    for kw in all_sv_keywords:
        g = google_sv.get(kw, 0)
        c = clickstream_sv.get(kw, 0)

        if g and c:
            avg_sv = round((g + c) / 2)
        else:
            avg_sv = g or c  # use whichever is non-zero

        meta = google_meta.get(kw, {"competition": 0.0, "cpc": 0.0})
        results[kw] = {
            "search_volume": avg_sv,
            "competition": meta["competition"],
            "cpc": meta["cpc"],
            "keyword_difficulty": 0,
        }

    logger.info(
        "Search volume sources — Google Ads: %d keywords, Clickstream: %d keywords, merged: %d keywords",
        len(google_sv),
        len(clickstream_sv),
        len(results),
    )

    # ── 4. Keyword difficulty — max 1000 per request ──────────────────────────
    KD_BATCH = 1000
    total_kd_batches = -(-len(keywords) // KD_BATCH)

    for i in range(0, len(keywords), KD_BATCH):
        batch = keywords[i : i + KD_BATCH]
        try:
            data = _post(
                "/dataforseo_labs/google/bulk_keyword_difficulty/live",
                [{"keywords": batch, "location_code": location_code, "language_code": language_code}],
            )
            for task in data.get("tasks", []):
                for item in task.get("result") or []:
                    kw = (item.get("keyword") or "").lower()
                    kd = item.get("keyword_difficulty") or 0
                    if kw:
                        if kw in results:
                            results[kw]["keyword_difficulty"] = kd
                        else:
                            results[kw] = {
                                "search_volume": 0,
                                "competition": 0.0,
                                "cpc": 0.0,
                                "keyword_difficulty": kd,
                            }
        except Exception as e:
            logger.warning(
                "DataForSEO keyword difficulty batch %d/%d failed: %s",
                i // KD_BATCH + 1,
                total_kd_batches,
                e,
            )

    logger.info(
        "DataForSEO metrics fetched: %d/%d keywords have data", len(results), len(keywords)
    )
    return results
