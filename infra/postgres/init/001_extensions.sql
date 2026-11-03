-- Runs once, on first start of an empty data volume.
-- Schema itself is owned by Alembic migrations (Phase 1), not this file.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() for ledger ids
