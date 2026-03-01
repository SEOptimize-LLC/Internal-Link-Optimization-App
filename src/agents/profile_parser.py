import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from src.utils.openrouter import chat_completion
from src.config.settings import MODEL_REASONING

logger = logging.getLogger(__name__)


@dataclass
class BusinessProfile:
    brand_name: str = ""
    business_nature: str = ""
    usp: str = ""
    target_audience: str = ""
    pain_points: list[str] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)  # [{name, description, url_hint}]
    industry_keywords: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_context_string(self) -> str:
        """Format profile as a concise context string for AI prompts."""
        lines = [
            f"Business: {self.brand_name}",
            f"Nature: {self.business_nature}",
            f"USP: {self.usp}",
            f"Target Audience: {self.target_audience}",
            f"Pain Points: {', '.join(self.pain_points[:5])}",
            f"Services/Products: {', '.join([s.get('name', '') for s in self.services[:10]])}",
            f"Industry Keywords: {', '.join(self.industry_keywords[:15])}",
        ]
        return "\n".join(line for line in lines if line.split(": ", 1)[-1].strip())


EXTRACTION_SYSTEM_PROMPT = """You are an expert SEO strategist and business analyst.
Extract structured business information from the provided document.
Return a JSON object with EXACTLY these fields — use null for missing information, never guess or fabricate.

Required fields:
- brand_name: string (business name)
- business_nature: string (1-2 sentence description of what the business does)
- usp: string (unique selling proposition — what makes them different)
- target_audience: string (ideal customer profile / ICP description)
- pain_points: array of strings (customer problems this business solves)
- services: array of objects, each with:
    - name: string (service/product/category name)
    - description: string (brief description)
    - url_hint: string (likely URL slug, e.g., "/services/seo" — your best guess based on name)
- industry_keywords: array of strings (domain-specific terms, topics, jargon from the document)
"""


def parse_business_profile(raw_text: str) -> BusinessProfile:
    """
    Use AI to extract structured business information from raw document text.

    Args:
        raw_text: Plain text extracted from the business profile document

    Returns:
        BusinessProfile dataclass with extracted information
    """
    if not raw_text or not raw_text.strip():
        logger.warning("Empty business profile text provided, returning default profile")
        return BusinessProfile()

    # Truncate to avoid token limits while keeping the most important content
    truncated_text = raw_text[:8000] if len(raw_text) > 8000 else raw_text

    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Extract structured business information from this document:\n\n{truncated_text}",
        },
    ]

    logger.info("Parsing business profile with AI...")

    result = chat_completion(
        messages=messages,
        model=MODEL_REASONING,
        response_format="json",
        temperature=0.1,
    )

    # Build BusinessProfile from AI response, with safe fallbacks
    profile = BusinessProfile(
        brand_name=result.get("brand_name") or "",
        business_nature=result.get("business_nature") or "",
        usp=result.get("usp") or "",
        target_audience=result.get("target_audience") or "",
        pain_points=result.get("pain_points") or [],
        services=result.get("services") or [],
        industry_keywords=result.get("industry_keywords") or [],
        raw_text=raw_text,
    )

    # Validate service objects have required keys
    cleaned_services = []
    for svc in profile.services:
        if isinstance(svc, dict) and svc.get("name"):
            cleaned_services.append({
                "name": svc.get("name", ""),
                "description": svc.get("description", ""),
                "url_hint": svc.get("url_hint", ""),
            })
    profile.services = cleaned_services

    logger.info(
        "Business profile extracted: brand=%s, services=%d, keywords=%d",
        profile.brand_name,
        len(profile.services),
        len(profile.industry_keywords),
    )

    return profile
