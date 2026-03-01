import io
import logging
import re
import requests

logger = logging.getLogger(__name__)


class DocumentParseError(Exception):
    pass


def _clean_text(text: str) -> str:
    """Normalize whitespace and remove excessive blank lines."""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_txt(file_bytes: bytes) -> str:
    """Parse plain text or markdown file."""
    try:
        return _clean_text(file_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return _clean_text(file_bytes.decode("latin-1"))


def parse_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using PyPDF2."""
    try:
        import PyPDF2
    except ImportError:
        raise DocumentParseError("PyPDF2 is not installed. Run: pip install PyPDF2")

    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)

    if not pages:
        raise DocumentParseError("Could not extract any text from PDF. The file may be image-based.")

    return _clean_text("\n\n".join(pages))


def parse_docx(file_bytes: bytes) -> str:
    """Extract text from Word document."""
    try:
        from docx import Document
    except ImportError:
        raise DocumentParseError("python-docx is not installed. Run: pip install python-docx")

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return _clean_text("\n\n".join(paragraphs))


def parse_google_doc_url(url: str) -> str:
    """
    Fetch a Google Doc as plain text by converting the URL to export format.
    Supports both /document/d/{id}/edit and /document/d/{id}/ formats.
    """
    # Extract document ID
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise DocumentParseError(f"Could not extract Google Doc ID from URL: {url}")

    doc_id = match.group(1)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    try:
        response = requests.get(export_url, timeout=30)
        response.raise_for_status()
        return _clean_text(response.text)
    except requests.RequestException as e:
        raise DocumentParseError(
            f"Failed to fetch Google Doc. Make sure the document is set to 'Anyone with the link can view'. Error: {e}"
        )


def parse_document(file_bytes: bytes = None, filename: str = "", url: str = "") -> str:
    """
    Auto-detect document type and extract plain text.

    Args:
        file_bytes: Raw file bytes (for uploaded files)
        filename: Original filename (used to detect type)
        url: Google Doc URL (alternative to file upload)

    Returns:
        Extracted plain text string
    """
    if url:
        if "docs.google.com" in url:
            logger.info("Parsing Google Doc from URL")
            return parse_google_doc_url(url)
        raise DocumentParseError("Only Google Docs URLs are supported. For other URLs, download the file and upload it.")

    if file_bytes is None:
        raise DocumentParseError("Either file_bytes or a Google Doc URL must be provided")

    name_lower = filename.lower()

    if name_lower.endswith(".pdf"):
        logger.info("Parsing PDF document: %s", filename)
        return parse_pdf(file_bytes)
    elif name_lower.endswith(".docx"):
        logger.info("Parsing Word document: %s", filename)
        return parse_docx(file_bytes)
    elif name_lower.endswith((".txt", ".md")):
        logger.info("Parsing text/markdown document: %s", filename)
        return parse_txt(file_bytes)
    else:
        # Try plain text as fallback
        logger.warning("Unknown file type '%s', attempting plain text parse", filename)
        return parse_txt(file_bytes)
