-- ParaFast: preshift_checks table for Pre-Shift Checklist Agent
-- Run in Supabase SQL Editor: Dashboard -> SQL Editor -> New query

CREATE TABLE IF NOT EXISTS preshift_checks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  badge_number TEXT NOT NULL,
  check_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'GOOD',
  detail TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS preshift_checks_badge_idx ON preshift_checks(badge_number);
CREATE INDEX IF NOT EXISTS preshift_checks_status_idx ON preshift_checks(status);

-- Example seed data (optional - replace badge_number with real badge):
-- INSERT INTO preshift_checks (badge_number, check_type, status, detail) VALUES
--   ('12345', 'ACRC', 'BAD', '2 unfinished ACRCs overdue'),
--   ('12345', 'Vaccinations', 'BAD', 'Expired 2025-01-15'),
--   ('12345', 'Overtime', 'BAD', 'Pending approval'),
--   ('12345', 'Drivers License', 'GOOD', NULL);
