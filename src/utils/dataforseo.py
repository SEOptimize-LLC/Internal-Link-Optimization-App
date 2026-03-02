import base64
import logging

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config.settings import DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD

logger = logging.getLogger(__name__)

DATAFORSEO_BASE_URL = "https://api.dataforseo.com/v3"

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
    token = base64.b64encode(f"{DATAFORSEO_LOGIN}:{DATAFORSEO_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


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

    Args:
        keywords: List of keyword strings to look up
        location_code: DataForSEO location code (default 2840 = US)
        language_code: Language code (default "en")

    Returns:
        Dict mapping keyword (lowercase) → {search_volume, competition, cpc, keyword_difficulty}
        Returns empty dict if credentials are not configured or all calls fail.
    """
    if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
        logger.info("DataForSEO credentials not configured — skipping keyword metrics enrichment")
        return {}

    if not keywords:
        return {}

    results: dict[str, dict] = {}

    # ── Search volume (Google Ads) — max 700 per request ─────────────────────
    SV_BATCH = 700
    for i in range(0, len(keywords), SV_BATCH):
        batch = keywords[i : i + SV_BATCH]
        try:
            data = _post(
                "/keywords_data/google_ads/search_volume/live",
                [{"keywords": batch, "location_code": location_code, "language_code": language_code}],
            )
            for task in data.get("tasks", []):
                for item in task.get("result") or []:
                    kw = (item.get("keyword") or "").lower()
                    if kw:
                        results[kw] = {
                            "search_volume": item.get("search_volume") or 0,
                            "competition": round(item.get("competition") or 0.0, 2),
                            "cpc": round(item.get("cpc") or 0.0, 2),
                            "keyword_difficulty": 0,
                        }
        except Exception as e:
            logger.warning(
                "DataForSEO search volume batch %d/%d failed: %s",
                i // SV_BATCH + 1,
                -(-len(keywords) // SV_BATCH),
                e,
            )

    # ── Keyword difficulty — max 1000 per request ────────────────────────────
    KD_BATCH = 1000
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
                -(-len(keywords) // KD_BATCH),
                e,
            )

    logger.info(
        "DataForSEO metrics fetched: %d/%d keywords have data", len(results), len(keywords)
    )
    return results
