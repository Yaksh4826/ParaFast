-- ParaFast: voice preference per user (Section H - persona consistency)
-- Run in Supabase SQL Editor

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS voice_id TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS gender TEXT;
