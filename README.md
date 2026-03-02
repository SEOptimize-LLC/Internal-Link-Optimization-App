# Internal Link Optimization Agent

A Streamlit app that uses Google Search Console data and a Business Profile document to generate a complete, AI-powered internal linking strategy based on SILO architecture.

## What It Does

1. **Fetches GSC organic data** — queries, pages, clicks, impressions, CTR, and position for the last 90 days (configurable)
2. **Parses your Business Profile** — extracts brand context, USP, ICP, pain points, and services/products to anchor all analysis
3. **Semantically clusters keywords** — groups queries into topic clusters; optionally enriches each cluster with DataForSEO search volume and keyword difficulty data so you can see both organic performance and traffic potential side by side
4. **Classifies every page** — labels pages as Pillar, Cluster Post, Money Page, or Orphan Candidate
5. **Builds SILO structure** — defines which pillar owns which cluster posts, flags content gaps
6. **Generates prioritized link recommendations** — 4 types across 3 priority levels, with contextual AI-generated anchor text per source→target pair
7. **Exports 3 deliverables** — Interactive SILO diagram, CSV action list, and a full per-page HTML report

## Link Recommendation Types

| Priority | Type | Logic |
| --- | --- | --- |
| P1 — Critical | Pillar ↔ Cluster | Bidirectional structural links within every SILO |
| P2 — High | Authority Boost | Top-click pages → high-impression/underperforming pages in same cluster |
| P3 — Recommended | Blog → Money Page | AI matches each content page to the most relevant service/product/category page |
| P3 — Recommended | Orphan Integration | AI finds source pages to link to isolated/orphaned content |

## Tech Stack

- **UI**: Streamlit
- **GSC**: Google Search Console API (OAuth2 — sign in with Google)
- **AI**: OpenRouter (model selectable — defaults to Claude Sonnet 4.6)
- **Keyword metrics**: DataForSEO (optional — search volume + keyword difficulty)
- **Storage**: Supabase (PostgreSQL) + local file exports
- **Visualization**: networkx + Plotly (interactive SILO diagram)
- **Document parsing**: PyPDF2, python-docx, requests (Google Docs)

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/SEOptimize-LLC/Internal-Link-Optimization-App.git
cd Internal-Link-Optimization-App
pip install -r requirements.txt
```

### 2. Create a Google OAuth2 Client ID

The app authenticates with Google Search Console via OAuth2 — no service account JSON needed.

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Select your project (or create one)
3. Go to **APIs & Services → Library** and enable the **Google Search Console API**
4. Go to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client ID**
6. Application type: **Web application**
7. Under **Authorized redirect URIs**, add:
   - `http://localhost:8501` (local development)
   - Your Streamlit Cloud URL if deploying (e.g. `https://your-app.streamlit.app`)
8. Click **Create** — copy the **Client ID** and **Client Secret**

### 3. Configure secrets

Copy the template and fill it in:

```bash
cp .streamlit/secrets_template.toml .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:

```toml
[google]
client_id = "your_google_oauth_client_id"
client_secret = "your_google_oauth_client_secret"
redirect_uri = "http://localhost:8501"   # or your Streamlit Cloud URL

OPENROUTER_API_KEY = "sk-or-your-key-here"

SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_SERVICE_KEY = "sb_secret_your-key-here"

# Optional — enables search volume + keyword difficulty enrichment
# Get credentials at https://app.dataforseo.com/api-dashboard
DATAFORSEO_LOGIN = "your_dataforseo_email@example.com"
DATAFORSEO_PASSWORD = "your_dataforseo_api_password"
```

> `secrets.toml` is gitignored and will never be committed. The template file (`secrets_template.toml`) is committed as a reference only.

#### Streamlit Cloud — Secrets tab

When deploying to Streamlit Cloud, add the same keys under **Settings → Secrets** in TOML format (no file upload needed — paste the content directly):

```toml
[google]
client_id = "..."
client_secret = "..."
redirect_uri = "https://your-app.streamlit.app"

OPENROUTER_API_KEY = "..."

SUPABASE_URL = "..."
SUPABASE_SERVICE_KEY = "..."

# Optional
DATAFORSEO_LOGIN = "..."
DATAFORSEO_PASSWORD = "..."
```

> DataForSEO credentials are optional. If omitted, keyword clustering still works — clusters just won't show monthly search volume or keyword difficulty scores.

### 4. Set up Supabase

Run the migration in your Supabase SQL editor:

```text
supabase/migrations/20260301_internal_link_agent_schema.sql
```

### 5. Run

```bash
streamlit run app.py
```

## Usage

### Step 1 — Setup

- Enter client name
- Click **Sign in with Google** → authorize access to Search Console → select property from the dropdown
- Set date range (default: 90 days) and optionally filter branded queries
- Upload the Business Profile document (`.txt`, `.md`, `.pdf`, `.docx`) or paste a Google Doc URL
- Select **Target Country** and **Language** — used for DataForSEO search volume and keyword difficulty lookup (only applies if DataForSEO credentials are configured; defaults to United States / English)

### Step 2 — Analysis

Analysis runs automatically across 6 steps with live progress indicators.

### Step 3 — Results Dashboard

- Interactive SILO diagram (hover for details)
- Filterable link recommendations table (by priority, SILO, and link type)
- Full page taxonomy with opportunity scores
- Keyword clusters sorted by total monthly search volume, with KD scores if DataForSEO is configured
- Per-page incoming/outgoing link plan with AI-generated anchor text

### Exports (sidebar)

- CSV — `source_url | target_url | anchor_text | link_type | priority | reason | silo_name | implementation_status`
- HTML report — per-page breakdown, dark-themed, filterable
- All results saved to Supabase with a unique `run_id`

## Business Profile Format

The Business Profile document should cover:

- Business name and what it does
- Unique Selling Proposition (USP)
- Target audience / Ideal Customer Profile (ICP)
- Customer pain points
- Services, products, or categories (with brief descriptions)
- Any industry-specific keywords or topics

Supported formats: `.txt`, `.md`, `.pdf`, `.docx`, or a public Google Doc URL.

## Project Structure

```text
├── app.py                          # Streamlit entry point
├── requirements.txt
├── .env.example
├── .streamlit/
│   └── secrets_template.toml       # Secrets template (copy to secrets.toml)
├── src/
│   ├── agents/
│   │   ├── gsc_fetcher.py          # Agent 1: GSC data extraction
│   │   ├── profile_parser.py       # Agent 2: Business profile parsing
│   │   ├── keyword_clusterer.py    # Agent 3: Semantic clustering + DataForSEO enrichment
│   │   ├── content_categorizer.py  # Agent 4: Page labeling + SILO structure
│   │   ├── link_recommender.py     # Agent 5: Link recommendations
│   │   └── output_generator.py     # Agent 6: Reports, diagrams, exports
│   ├── utils/
│   │   ├── openrouter.py           # OpenRouter API client
│   │   ├── dataforseo.py           # DataForSEO keyword metrics (search vol + KD)
│   │   ├── supabase_client.py      # Supabase operations
│   │   ├── document_parser.py      # Multi-format document parsing
│   │   └── helpers.py              # Shared utilities
│   └── config/
│       └── settings.py             # Environment variables + constants
├── templates/
│   └── report_template.html        # Jinja2 HTML report template
├── supabase/
│   └── migrations/
│       └── 20260301_internal_link_agent_schema.sql
└── outputs/                        # Local export directory (gitignored)
```

## Opportunity Score

Pages are ranked by an **opportunity score** that surfaces underperformers:

```text
opportunity_score = impressions / (max(position, 1) × clicks + 1)
```

High score = lots of impressions but poor CTR/position → ideal targets for link juice from high-authority pages.

## License

MIT
