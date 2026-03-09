import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}

_FETCH_TIMEOUT = 12  # seconds per request
_MAX_CONTENT_CHARS = 1500  # cap passed to AI per source page


def _extract_paragraphs(html: str) -> str:
    """
    Extract meaningful body text from HTML.
    Removes nav, header, footer, sidebar, scripts, styles.
    Returns up to _MAX_CONTENT_CHARS of paragraph text.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strip noise elements first
    for tag in soup(
        ["script", "style", "nav", "header", "footer",
         "aside", "form", "noscript", "iframe"]
    ):
        tag.decompose()

    # Prefer main content container if identifiable
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(
            "div",
            class_=lambda c: c and any(
                x in (c if isinstance(c, str) else " ".join(c))
                for x in [
                    "content", "post-body", "entry-content",
                    "article-body", "blog-post", "page-content",
                ]
            ),
        )
        or soup.find("body")
        or soup
    )

    paragraphs = []
    for p in main.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if len(text) > 60:  # skip fragments / captions
            paragraphs.append(text)

    combined = "\n\n".join(paragraphs)
    return combined[:_MAX_CONTENT_CHARS]


def fetch_page_content(url: str) -> str:
    """
    Fetch a URL and return extracted paragraph text.
    Returns empty string on any failure (HTTP error, timeout, non-HTML, etc.).
    """
    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            logger.debug(
                "Page fetch %s returned HTTP %d", url, resp.status_code
            )
            return ""
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            logger.debug(
                "Page fetch %s is not HTML: %s", url, content_type
            )
            return ""
        return _extract_paragraphs(resp.text)
    except Exception as e:
        logger.debug("Page fetch failed for %s: %s", url, e)
        return ""


def fetch_pages_parallel(
    urls: list[str],
    max_workers: int = 10,
) -> dict[str, str]:
    """
    Fetch multiple URLs in parallel.

    Returns a dict {url: content_text} for successfully fetched pages only.
    Pages that fail or return empty content are omitted from the result.
    """
    if not urls:
        return {}

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_page_content, url): url for url in urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                content = future.result()
                if content:
                    results[url] = content
            except Exception as e:
                logger.debug("Fetch task failed for %s: %s", url, e)

    logger.info(
        "Page content fetched: %d/%d URLs successful",
        len(results),
        len(urls),
    )
    return results
