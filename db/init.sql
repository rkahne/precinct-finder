-- Precinct Leader Finder — Database Schema
-- Run once: psql -U precinct_user -d precinctdb -f db/init.sql

-- Leader counts per precinct (populated by scripts/process_data.py)
CREATE TABLE IF NOT EXISTS precincts (
    precinct_code         VARCHAR(20) PRIMARY KEY,
    leg_dist              VARCHAR(20),
    unique_leaders        INTEGER DEFAULT 0,
    has_enough_leaders    BOOLEAN DEFAULT FALSE,
    updated_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Leader interest form submissions
CREATE TABLE IF NOT EXISTS submissions (
    id                  SERIAL PRIMARY KEY,
    first_name          VARCHAR(100) NOT NULL,
    last_name           VARCHAR(100) NOT NULL,
    email               VARCHAR(200) NOT NULL,
    phone               VARCHAR(50),
    precinct_code       VARCHAR(20),
    leg_dist            VARCHAR(20),
    message             TEXT,
    ip_address          INET,
    user_agent          TEXT,
    submitted_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    exported_to_sheets  BOOLEAN DEFAULT FALSE
);

-- Address searches (logged after a successful precinct match)
CREATE TABLE IF NOT EXISTS searches (
    id               SERIAL PRIMARY KEY,
    address_input    TEXT,
    matched_address  TEXT,
    precinct_code    VARCHAR(20),
    leg_dist         VARCHAR(20),
    lat              DOUBLE PRECISION,
    lon              DOUBLE PRECISION,
    ip_address       INET,
    user_agent       TEXT,
    searched_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Page visits (logged on each load of the index page)
CREATE TABLE IF NOT EXISTS page_visits (
    id          SERIAL PRIMARY KEY,
    ip_address  INET,
    user_agent  TEXT,
    referrer    TEXT,
    visited_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_submissions_precinct   ON submissions (precinct_code);
CREATE INDEX IF NOT EXISTS idx_submissions_submitted  ON submissions (submitted_at);
CREATE INDEX IF NOT EXISTS idx_searches_precinct      ON searches (precinct_code);
CREATE INDEX IF NOT EXISTS idx_searches_at            ON searches (searched_at);
CREATE INDEX IF NOT EXISTS idx_page_visits_at         ON page_visits (visited_at);
