# Internal Link Optimization Agent — Implementation Plan

## Context

An SEO agency tool that analyzes a client's Google Search Console data alongside a Business Profile document to produce a fully optimized internal linking strategy based on SILO architecture. The output gives the SEO practitioner a precise, prioritized action plan: which pages to link, how to link them, what anchor text to use, and why — all rooted in real organic performance data and business context.

**Core philosophy driving design decisions:**
- SILO architecture as default: pillar pages own topic clusters, cluster posts link bidirectionally to pillar
- "Link juice" flows from high-authority pages → underperforming pages in the same cluster
- All blog posts must connect to the most relevant money page (service/product/category)
- Business context (USP, ICP, pain points) shapes both cluster groupings and LSI/NLP anchor terms
- One-off per client, so outputs must be self-contained and portable

---

## Project Structure

```
Internal Link Optimization Agent/
├── app.py                          # Streamlit entry point
├── requirements.txt
├── .env.example
├── IMPLEMENTATION_PLAN.md          # Copy of this plan
├── src/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── gsc_fetcher.py          # Agent 1: GSC data extraction
│   │   ├── profile_parser.py       # Agent 2: Business profile parsing
│   │   ├── keyword_clusterer.py    # Agent 3: Semantic clustering + LSI expansion
│   │   ├── content_categorizer.py  # Agent 4: Page labeling + SILO structure
│   │   ├── link_recommender.py     # Agent 5: Link recommendations
│   │   └── output_generator.py     # Agent 6: Reports, diagrams, exports
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── openrouter.py           # OpenRouter API client (with retry + rate limit)
│   │   ├── supabase_client.py      # Supabase operations
│   │   ├── document_parser.py      # PDF/Word/Google Doc URL → plain text
│   │   └── helpers.py              # Shared utilities
│   └── config/
│       ├── __init__.py
│       └── settings.py             # Env vars, model names, constants
├── templates/
│   └── report_template.html        # Jinja2 template for per-page HTML reports
└── outputs/                        # Local export directory (gitignored)
    └── .gitkeep
```

---

## Phase 1: Project Foundation

### 1.1 — Configuration & Environment (`src/config/settings.py`, `.env.example`)

Define all environment variables and constants:

```
GOOGLE_SERVICE_ACCOUNT_JSON   # Path to service account JSON file
OPENROUTER_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_KEY
OPENROUTER_BASE_URL           # https://openrouter.ai/api/v1
MODEL_REASONING               # claude-sonnet-4-5 (complex AI tasks)
MODEL_FAST                    # google/gemini-2.0-flash (batch/bulk tasks)
GSC_DEFAULT_DAYS              # 90 (last 3 months)
MAX_PAGES_PER_BATCH           # 50 (for AI processing batches)
```

### 1.2 — Dependencies (`requirements.txt`)

Key packages:
- `streamlit` — UI
- `google-auth`, `google-api-python-client` — GSC API
- `pypdf2`, `python-docx` — document parsing
- `requests` — HTTP (Google Doc fetch, OpenRouter)
- `pandas`, `numpy` — data processing
- `networkx`, `plotly` — SILO visualization
- `supabase` — database client
- `jinja2` — HTML report templates
- `python-dotenv` — environment variables
- `tenacity` — retry logic for API calls

---

## Phase 2: Core Utilities

### 2.1 — OpenRouter Client (`src/utils/openrouter.py`)

Wraps OpenRouter REST API with:
- Configurable model selection (reasoning vs. fast)
- Automatic retry with exponential backoff (3 retries, 2x multiplier)
- JSON response extraction with fallback
- Token usage tracking (logged but not shown to user)
- Support for both single completions and batch calls

Pattern: `chat_completion(messages, model, response_format="json")` returns parsed dict.

### 2.2 — Document Parser (`src/utils/document_parser.py`)

Detects input type and extracts plain text:
- `.txt` / `.md` → read directly
- `.pdf` → pypdf2 page extraction
- `.docx` → python-docx paragraph extraction
- Google Doc URL (contains `docs.google.com`) → export as plain text via `?export=txt`
- Returns a normalized string for the profile parser agent

### 2.3 — Supabase Client (`src/utils/supabase_client.py`)

CRUD operations for all 6 tables. Handles:
- Upsert (not insert) to allow re-runs for same client
- Batch inserts for large datasets (pagination)
- `analysis_runs` as the foreign key anchor for all other tables

---

## Phase 3: The Six Agents

### Agent 1 — GSC Data Fetcher (`src/agents/gsc_fetcher.py`)

**Input:** Service account JSON path, GSC property URL, date range (days back)

**Process:**
1. Authenticate via service account credentials
2. Call `searchanalytics.query` with two separate queries:
   - **Query-level data**: dimensions `[query, page]`, metrics `[clicks, impressions, ctr, position]`, paginated 25,000 rows at a time
   - **Page-level data**: dimensions `[page]`, same metrics, paginated
3. Filter out branded queries (optional flag in UI)
4. Compute derived fields per page: `opportunity_score = impressions / (position * clicks + 1)` — higher score = underperforming relative to impression volume
5. Return two DataFrames: `queries_df`, `pages_df`

**Scale handling:** Pagination loop until `rowCount < pageSize`, handles 2000+ pages cleanly.

### Agent 2 — Business Profile Parser (`src/agents/profile_parser.py`)

**Input:** Raw text extracted by document parser

**Process:**
Call OpenRouter (claude-sonnet-4-5) with a structured extraction prompt.

**Extraction targets:**
- `business_nature`: What the business does (1-2 sentences)
- `usp`: Unique selling proposition
- `target_audience`: ICP description
- `pain_points`: List of customer problems solved
- `services`: List of service/product/category offerings with descriptions
- `industry_keywords`: Any explicit keywords/terms mentioned in the document
- `brand_name`: Business name

**Output:** Structured Python dict (`BusinessProfile` dataclass).

If document is missing fields, mark them as `null` — the system degrades gracefully (e.g., no money-page linking if no services defined).

### Agent 3 — Keyword Clusterer (`src/agents/keyword_clusterer.py`)

**Input:** `queries_df`, `BusinessProfile`

**Process (3 steps):**

**Step A — Pre-filter:** Remove queries with 0 clicks AND < 5 impressions. Deduplicate near-identical queries (e.g., "best seo agency" vs "best seo agencies" → same cluster).

**Step B — Semantic Clustering (AI):** Batch queries into groups of 200. For each batch, send to OpenRouter (gemini-2.0-flash for speed) with a prompt that:
- Groups queries into semantic clusters (topics)
- Names each cluster (the "topic label")
- Identifies the primary intent of each cluster: informational | commercial | transactional | navigational
- Maps each query to exactly one cluster

**Step C — LSI/NLP Expansion (AI):** For each cluster, using BusinessProfile context, generate:
- 5-10 LSI terms (semantically related phrases)
- 3-5 NLP entities (named entities, concepts relevant to the business)
- 3 anchor text variations (for use in link recommendations)

**Output:** `clusters` dict — `{cluster_id: {label, intent, queries[], lsi_terms[], entities[], anchor_variants[], page_assignments[]}}`

**Page assignment:** Each page in `pages_df` is assigned to its dominant cluster based on which queries it ranks for (from `queries_df` join).

### Agent 4 — Content Categorizer + SILO Builder (`src/agents/content_categorizer.py`)

**Input:** `pages_df` with cluster assignments, `BusinessProfile`, `clusters`

**Process:**

**Step A — Page Classification (AI):** Send batches of 50 pages (URL + top queries + cluster) to OpenRouter (claude-sonnet-4-5) with a prompt to classify each page as:
- `pillar` — comprehensive, high-authority topic overview (usually ranks for the broadest query in a cluster)
- `cluster_post` — depth content on a sub-topic within a cluster
- `money_page` — service, product, collection, or category page (revenue-driving)
- `orphan_candidate` — page with no clear cluster fit, low impressions, no internal link potential identified

**Step B — SILO Definition:** Using clusters + classifications:
- Each cluster gets one pillar page (or one is recommended if none exists)
- Cluster posts belong to their cluster's silo
- Money pages are mapped to the most relevant silo(s)

**Step C — Pillar Gap Detection:** If a cluster has no identified pillar page, flag it as "needs pillar creation" in the report.

**Output:**
- `page_taxonomy_df`: Page URL | Type | Cluster ID | Silo ID | Opportunity Score
- `silo_structure`: `{silo_id: {pillar_url, cluster_post_urls[], money_pages[], cluster_label}}`

### Agent 5 — Link Recommendation Engine (`src/agents/link_recommender.py`)

**Input:** `silo_structure`, `page_taxonomy_df`, `clusters`, `pages_df` (with authority/performance metrics)

**Process — Four recommendation types, generated by AI:**

**Type 1: Pillar ↔ Cluster Bidirectional Links**
Every cluster post must link to its silo's pillar. Every pillar must link to all cluster posts. Generate anchor text from cluster LSI terms.

**Type 2: Authority → Underperformer Boost Links**
For each cluster: take the top 3 pages by clicks → find bottom 3 by opportunity_score (high impressions, low clicks) in same cluster → recommend contextual links from high-authority to underperformer.

**Type 3: Blog → Money Page Connections**
For each cluster post and pillar, use AI (claude-sonnet-4-5) to identify the single most relevant money page and recommend a contextual link with business-aligned anchor text.

**Type 4: Orphan Page Integration**
For each orphan_candidate, use AI to find the most topically similar cluster and recommend 2-3 pages that should link to it.

**Recommendation schema per item:**
```
source_url | target_url | anchor_text | link_type | priority (1-3) | reason | silo_id
```

Priority scoring:
- P1: Missing pillar←→cluster links (structural necessity)
- P2: Authority boost links (performance gap)
- P3: Blog→money page + orphan fixes

**Output:** `recommendations_df` (all recommendations, sorted by priority)

### Agent 6 — Output Generator (`src/agents/output_generator.py`)

**Input:** All above outputs

**Deliverable 1: Visual SILO Diagram**
- Use `networkx` to build directed graph
- Nodes: pages, colored by type (pillar=gold, cluster=blue, money=green, orphan=red)
- Edges: recommended links, thickness by priority
- Render with `plotly` for interactivity (hover = URL + anchor text)
- Export as HTML (interactive) + PNG (static)

**Deliverable 2: CSV Export**
`internal_links_{client_name}_{date}.csv`
Columns: `source_url | target_url | anchor_text | link_type | priority | reason | silo_name | implementation_status`
(implementation_status defaults to "pending" — practitioner updates as they implement)

**Deliverable 3: Per-Page HTML Report**
For each page in `page_taxonomy_df`, generate a section:
- Page URL + type + silo membership
- Top queries it ranks for (from GSC)
- Links it should RECEIVE (with source URL + suggested anchor)
- Links it should GIVE (with target URL + suggested anchor)
- Relevant money page connection (if applicable)
Rendered via Jinja2 template into one consolidated HTML file.

**Deliverable 4: Supabase Storage**
Save all processed data across 6 tables. `analysis_run_id` (UUID) ties everything together.

---

## Phase 4: Streamlit App (`app.py`)

### UI Flow (7 steps with progress feedback)

```
Page 1: Setup
  - Client name input
  - Upload service account JSON (or path input)
  - GSC property dropdown (populated after auth)
  - Date range slider (default 90 days)
  - Business Profile upload (file uploader accepts .txt, .md, .pdf, .docx)
    OR Google Doc URL input
  - [Run Analysis] button

Page 2: Progress (live status)
  - Step 1/6: Fetching GSC data... ✓
  - Step 2/6: Parsing business profile... ✓
  - Step 3/6: Clustering keywords... (spinner)
  - Step 4/6: Categorizing content...
  - Step 5/6: Generating link recommendations...
  - Step 6/6: Building outputs...

Page 3: Results Dashboard
  - Summary metrics: total pages, total clusters, total recommendations, orphans found
  - SILO diagram (interactive plotly chart, embedded)
  - Tab 1: Link Recommendations (filterable table by priority/silo/type)
  - Tab 2: Page Taxonomy (all pages with their types and cluster)
  - Tab 3: Keyword Clusters (expandable list with LSI terms)
  - Tab 4: Per-Page Report (page selector → shows that page's full report)

Sidebar:
  - Export CSV button
  - Export HTML Report button
  - Export SILO diagram (PNG) button
  - "Saved to Supabase" confirmation with run ID
```

---

## Phase 5: Supabase Schema

Six tables, all with UUID PKs and `created_at` timestamps.

```sql
analysis_runs (id, client_name, gsc_property, date_range_days, created_at, status)

gsc_pages (id, run_id→analysis_runs, url, clicks, impressions, ctr, position, opportunity_score)

keyword_clusters (id, run_id, cluster_label, intent, lsi_terms[], entities[], anchor_variants[], query_count)

page_taxonomy (id, run_id, url, page_type, cluster_id→keyword_clusters, silo_id, opportunity_score)

silo_structure (id, run_id, silo_name, pillar_url, cluster_post_count, money_page_count)

link_recommendations (id, run_id, source_url, target_url, anchor_text, link_type, priority, reason, silo_id, implementation_status)
```

Migration file: `supabase/migrations/20260301_internal_link_agent_schema.sql`

---

## Build Order

1. `requirements.txt` + `.env.example`
2. `src/config/settings.py`
3. `src/utils/openrouter.py`
4. `src/utils/document_parser.py`
5. `src/utils/supabase_client.py`
6. `src/utils/helpers.py`
7. `src/agents/gsc_fetcher.py`
8. `src/agents/profile_parser.py`
9. `src/agents/keyword_clusterer.py`
10. `src/agents/content_categorizer.py`
11. `src/agents/link_recommender.py`
12. `src/agents/output_generator.py`
13. `templates/report_template.html`
14. `supabase/migrations/...sql`
15. `app.py` (Streamlit UI — last, wires everything together)
16. `IMPLEMENTATION_PLAN.md` copy in project root

---

## Verification Steps

1. **Unit test GSC fetcher**: Run against a test property with known data, verify row counts match GSC UI
2. **Document parser test**: Run each format (.txt, .pdf, .docx, Google Doc URL) through parser, verify clean text output
3. **Clustering validation**: Inspect cluster output — check that semantically related queries group together, LSI terms are relevant
4. **SILO structure check**: Verify every cluster_post has exactly one pillar assignment, no pages are uncategorized
5. **Recommendation completeness**: Every cluster_post should have at least 1 incoming link recommendation and 1 outgoing
6. **Export integrity**: Open CSV in Excel, verify all columns populated, no broken URLs
7. **Supabase check**: Query `link_recommendations` table, verify run_id FK links back to `analysis_runs`
8. **End-to-end run**: Full run on a 100-page client site, review SILO diagram matches expected content structure

---

## Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| Streamlit over pure script | Non-blocking, handles multiple formats via UI, portable across clients |
| OpenRouter over direct Anthropic SDK | Multi-model flexibility: fast/cheap for batches, powerful for reasoning tasks |
| Gemini Flash for clustering batches | GSC data can have 10,000+ queries — batch AI needs to be fast and cheap |
| Claude Sonnet for categorization + recommendations | These require nuanced reasoning about business context |
| networkx + plotly for SILO viz | Interactive hover, exportable, no external service dependency |
| Opportunity score formula | Surfaces pages with high impression volume but poor CTR/position — these are the underperformers that benefit most from internal link juice |
| P1/P2/P3 priority system | Gives practitioner a clear "fix this first" list rather than overwhelming flat CSV |
