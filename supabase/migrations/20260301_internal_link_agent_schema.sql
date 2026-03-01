-- Internal Link Optimization Agent Schema
-- Migration: 20260301_internal_link_agent_schema

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Table 1: analysis_runs
-- Anchor table for all other records in a run
-- ============================================================
CREATE TABLE IF NOT EXISTS analysis_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_name TEXT NOT NULL,
    gsc_property TEXT NOT NULL,
    date_range_days INTEGER NOT NULL DEFAULT 90,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_client ON analysis_runs(client_name);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_created ON analysis_runs(created_at DESC);

-- ============================================================
-- Table 2: gsc_pages
-- Raw GSC performance data per page per run
-- ============================================================
CREATE TABLE IF NOT EXISTS gsc_pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    clicks INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    ctr FLOAT DEFAULT 0,
    position FLOAT DEFAULT 0,
    opportunity_score FLOAT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gsc_pages_run ON gsc_pages(run_id);
CREATE INDEX IF NOT EXISTS idx_gsc_pages_clicks ON gsc_pages(run_id, clicks DESC);
CREATE INDEX IF NOT EXISTS idx_gsc_pages_opportunity ON gsc_pages(run_id, opportunity_score DESC);

-- ============================================================
-- Table 3: keyword_clusters
-- Semantic clusters with LSI terms and anchor variants
-- ============================================================
CREATE TABLE IF NOT EXISTS keyword_clusters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    cluster_label TEXT NOT NULL,
    intent TEXT DEFAULT 'informational' CHECK (intent IN ('informational', 'commercial', 'transactional', 'navigational')),
    lsi_terms JSONB DEFAULT '[]',
    entities JSONB DEFAULT '[]',
    anchor_variants JSONB DEFAULT '[]',
    query_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_keyword_clusters_run ON keyword_clusters(run_id);

-- ============================================================
-- Table 4: page_taxonomy
-- Page classification (type) and SILO membership
-- ============================================================
CREATE TABLE IF NOT EXISTS page_taxonomy (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    page_type TEXT NOT NULL CHECK (page_type IN ('pillar', 'cluster_post', 'money_page', 'orphan_candidate')),
    cluster_id UUID REFERENCES keyword_clusters(id) ON DELETE SET NULL,
    silo_id UUID,  -- References silo_structure.id (set after silo creation)
    opportunity_score FLOAT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_page_taxonomy_run ON page_taxonomy(run_id);
CREATE INDEX IF NOT EXISTS idx_page_taxonomy_type ON page_taxonomy(run_id, page_type);
CREATE INDEX IF NOT EXISTS idx_page_taxonomy_silo ON page_taxonomy(silo_id);

-- ============================================================
-- Table 5: silo_structure
-- Defined SILOs: pillar + cluster posts + money pages
-- ============================================================
CREATE TABLE IF NOT EXISTS silo_structure (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    silo_name TEXT NOT NULL,
    pillar_url TEXT,
    cluster_post_count INTEGER DEFAULT 0,
    money_page_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_silo_structure_run ON silo_structure(run_id);

-- ============================================================
-- Table 6: link_recommendations
-- All internal link recommendations with priority and status
-- ============================================================
CREATE TABLE IF NOT EXISTS link_recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    anchor_text TEXT,
    link_type TEXT CHECK (link_type IN ('pillar_to_cluster', 'cluster_to_pillar', 'authority_boost', 'blog_to_money', 'orphan_integration')),
    priority INTEGER NOT NULL CHECK (priority IN (1, 2, 3)),
    reason TEXT,
    silo_id UUID,
    implementation_status TEXT DEFAULT 'pending' CHECK (implementation_status IN ('pending', 'implemented', 'skipped')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_link_recs_run ON link_recommendations(run_id);
CREATE INDEX IF NOT EXISTS idx_link_recs_priority ON link_recommendations(run_id, priority);
CREATE INDEX IF NOT EXISTS idx_link_recs_source ON link_recommendations(run_id, source_url);
CREATE INDEX IF NOT EXISTS idx_link_recs_status ON link_recommendations(implementation_status);

-- ============================================================
-- RLS Policies (enable RLS on all tables)
-- ============================================================
ALTER TABLE analysis_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE gsc_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE keyword_clusters ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_taxonomy ENABLE ROW LEVEL SECURITY;
ALTER TABLE silo_structure ENABLE ROW LEVEL SECURITY;
ALTER TABLE link_recommendations ENABLE ROW LEVEL SECURITY;

-- Service role bypass (used by the app's service key)
CREATE POLICY "Service role has full access" ON analysis_runs FOR ALL USING (true);
CREATE POLICY "Service role has full access" ON gsc_pages FOR ALL USING (true);
CREATE POLICY "Service role has full access" ON keyword_clusters FOR ALL USING (true);
CREATE POLICY "Service role has full access" ON page_taxonomy FOR ALL USING (true);
CREATE POLICY "Service role has full access" ON silo_structure FOR ALL USING (true);
CREATE POLICY "Service role has full access" ON link_recommendations FOR ALL USING (true);

-- ============================================================
-- Auto-update updated_at trigger
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_analysis_runs_updated_at BEFORE UPDATE ON analysis_runs FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_link_recs_updated_at BEFORE UPDATE ON link_recommendations FOR EACH ROW EXECUTE FUNCTION update_updated_at();
