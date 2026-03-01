import json
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.config.settings import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_REASONING, MODEL_FAST

logger = logging.getLogger(__name__)


class OpenRouterError(Exception):
    pass


def _extract_json(text: str) -> dict:
    """Extract JSON from a response string, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (``` markers)
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object or array within the text
        import re
        json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if json_match:
            return json.loads(json_match.group(1))
        raise OpenRouterError(f"Could not extract JSON from response: {text[:200]}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException, OpenRouterError)),
    reraise=True,
)
def chat_completion(
    messages: list[dict],
    model: str = None,
    use_fast_model: bool = False,
    response_format: str = "json",
    temperature: float = 0.1,
) -> dict | str:
    """
    Call OpenRouter chat completions API.

    Args:
        messages: List of {"role": "...", "content": "..."} dicts
        model: Override model ID (defaults to MODEL_REASONING or MODEL_FAST)
        use_fast_model: If True, use the fast/cheap model instead of reasoning model
        response_format: "json" to parse response as JSON, "text" to return raw string
        temperature: Sampling temperature (low = more deterministic)

    Returns:
        Parsed dict if response_format="json", raw string otherwise
    """
    if not OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not set in environment variables")

    selected_model = model or (MODEL_FAST if use_fast_model else MODEL_REASONING)

    payload = {
        "model": selected_model,
        "messages": messages,
        "temperature": temperature,
    }

    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://internal-link-optimizer.local",
        "X-Title": "Internal Link Optimization Agent",
    }

    response = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120,
    )

    if response.status_code != 200:
        logger.error("OpenRouter API error %s: %s", response.status_code, response.text[:500])
        raise OpenRouterError(f"API returned {response.status_code}: {response.text[:200]}")

    data = response.json()

    # Log token usage
    usage = data.get("usage", {})
    logger.debug(
        "OpenRouter usage — model: %s, prompt: %s, completion: %s",
        selected_model,
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
    )

    content = data["choices"][0]["message"]["content"]

    if response_format == "json":
        return _extract_json(content)

    return content


def batch_chat_completion(
    batch_messages: list[list[dict]],
    model: str = None,
    use_fast_model: bool = False,
    response_format: str = "json",
) -> list[dict | str]:
    """
    Run multiple chat completions sequentially, returning a list of results.
    Each item in batch_messages is a full messages list for one call.
    """
    results = []
    for messages in batch_messages:
        result = chat_completion(
            messages=messages,
            model=model,
            use_fast_model=use_fast_model,
            response_format=response_format,
        )
        results.append(result)
    return results
