-- Migration: expand submissions table to match updated interest form
-- Run once on the droplet:
--   psql -U precinct_user -d precinctdb -f /opt/precinct-finder/db/migrate_add_fields.sql

ALTER TABLE submissions
  ADD COLUMN IF NOT EXISTS legal_first_name     VARCHAR(100),
  ADD COLUMN IF NOT EXISTS preferred_first_name VARCHAR(100),
  ADD COLUMN IF NOT EXISTS legal_middle_name    VARCHAR(100),
  ADD COLUMN IF NOT EXISTS legal_last_name      VARCHAR(100),
  ADD COLUMN IF NOT EXISTS street_address       VARCHAR(200),
  ADD COLUMN IF NOT EXISTS city                 VARCHAR(100),
  ADD COLUMN IF NOT EXISTS state                VARCHAR(50),
  ADD COLUMN IF NOT EXISTS zip_code             VARCHAR(20),
  ADD COLUMN IF NOT EXISTS birthdate            DATE,
  ADD COLUMN IF NOT EXISTS is_democrat          BOOLEAN;

-- Grant access to the app user on any new columns
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO precinct_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO precinct_user;
