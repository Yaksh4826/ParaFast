-- ParaFast: form_drafts table for Scribe Agent drafts
-- Run in Supabase SQL Editor: Dashboard -> SQL Editor -> New query

-- Fix: form_drafts needs badge_number column.
-- If table has wrong schema, drop and recreate (loses existing drafts):
DROP TABLE IF EXISTS form_drafts;

CREATE TABLE form_drafts (
  badge_number TEXT PRIMARY KEY,
  content JSONB DEFAULT '{}',
  status TEXT DEFAULT 'pending',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS if needed (optional):
-- ALTER TABLE form_drafts ENABLE ROW LEVEL SECURITY;
