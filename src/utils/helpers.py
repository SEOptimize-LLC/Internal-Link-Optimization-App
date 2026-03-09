import fnmatch
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse


def generate_run_id() -> str:
    """Generate a unique analysis run ID."""
    return str(uuid.uuid4())


def sanitize_client_name(name: str) -> str:
    """Convert client name to a safe filename prefix."""
    return re.sub(r"[^\w\-]", "_", name.strip().lower())


def get_export_filename(client_name: str, suffix: str, extension: str) -> str:
    """Generate a timestamped export filename."""
    safe_name = sanitize_client_name(client_name)
    date_str = datetime.now().strftime("%Y%m%d")
    return f"{safe_name}_{suffix}_{date_str}.{extension}"


def normalize_url(url: str) -> str:
    """Normalize URL: lowercase, strip trailing slash, remove query params and fragments."""
    url = url.lower().strip()
    parsed = urlparse(url)
    # Reconstruct without query string or fragment
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return normalized.rstrip("/")


def get_url_path(url: str) -> str:
    """Extract just the path from a URL for display."""
    parsed = urlparse(url)
    return parsed.path or "/"


def truncate_url(url: str, max_length: int = 60) -> str:
    """Truncate a URL for display purposes."""
    if len(url) <= max_length:
        return url
    return "..." + url[-(max_length - 3):]


def chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks of a given size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def deduplicate_queries(queries: list[str]) -> list[str]:
    """
    Remove near-duplicate queries (e.g., 'best seo agency' vs 'best seo agencies').
    Uses simple stemming via trailing 's' removal for deduplication.
    """
    seen = set()
    deduped = []
    for q in queries:
        # Normalize: lowercase, strip, remove trailing 's' for comparison
        key = q.lower().strip().rstrip("s")
        if key not in seen:
            seen.add(key)
            deduped.append(q)
    return deduped


def compute_opportunity_score(impressions: float, position: float, clicks: float) -> float:
    """
    Compute opportunity score: higher = more underperforming relative to impression volume.
    Formula: impressions / (position * clicks + 1)
    Pages with high impressions but poor CTR/position score highest.
    """
    if impressions == 0:
        return 0.0
    return round(impressions / (max(position, 1) * clicks + 1), 4)


def format_pct(value: float) -> str:
    """Format a float as a percentage string."""
    return f"{value * 100:.1f}%"


def format_number(value: float) -> str:
    """Format a number with comma separators."""
    return f"{int(value):,}"


def parse_exclusion_patterns(text: str) -> list[str]:
    """
    Parse newline-separated URL exclusion patterns from a text field.
    Skips blank lines and comment lines (starting with #).
    """
    patterns = []
    for line in text.splitlines():
        p = line.strip()
        if p and not p.startswith("#"):
            patterns.append(p)
    return patterns


def url_matches_exclusions(url: str, patterns: list[str]) -> bool:
    """
    Return True if the URL matches any exclusion pattern.

    Each pattern is tried as a regex first; if the pattern is not valid
    regex (e.g. bare wildcards like ``/jobs/*``), it falls back to
    fnmatch glob matching (case-insensitive).

    Examples that work out of the box:
      - ``https://example.com/jobs/*``      → glob wildcard
      - ``/appointment/.*``                 → regex substring match
      - ``.*\\.webp$``                      → regex suffix match
      - ``/fr_ca/``                         → regex substring match
    """
    for pattern in patterns:
        try:
            if re.search(pattern, url):
                return True
        except re.error:
            # Not valid regex — treat as glob wildcard
            if fnmatch.fnmatch(url.lower(), pattern.lower()):
                return True
    return False
