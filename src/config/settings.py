import os
from dotenv import load_dotenv

load_dotenv()

# Google Search Console — OAuth2
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8501")

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# AI Models
MODEL_REASONING = os.getenv("MODEL_REASONING", "anthropic/claude-sonnet-4-5")
MODEL_FAST = os.getenv("MODEL_FAST", "google/gemini-2.0-flash")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Analysis defaults
GSC_DEFAULT_DAYS = int(os.getenv("GSC_DEFAULT_DAYS", "90"))
MAX_PAGES_PER_BATCH = int(os.getenv("MAX_PAGES_PER_BATCH", "50"))
MAX_QUERIES_PER_CLUSTER_BATCH = int(os.getenv("MAX_QUERIES_PER_CLUSTER_BATCH", "200"))

# GSC API
GSC_PAGE_SIZE = 25000  # Max rows per API request
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# Output
OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "outputs")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "templates")

# Link priority labels
PRIORITY_LABELS = {1: "P1 - Critical", 2: "P2 - High", 3: "P3 - Recommended"}

# Page type labels
PAGE_TYPES = ["pillar", "cluster_post", "money_page", "orphan_candidate"]

# Link type labels
LINK_TYPES = ["pillar_to_cluster", "cluster_to_pillar", "authority_boost", "blog_to_money", "orphan_integration"]
