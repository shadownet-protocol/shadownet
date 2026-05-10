-- pgstore schema for the Shadownet reference SCA + SNS servers.
--
-- Apply automatically on Open(); also kept here as a human-readable reference
-- (e.g. for ops setting up read replicas, reviewing migrations, etc.).
--
-- All identifiers (table and column names) are unprefixed beyond `sca_` /
-- `sns_` so a single Postgres schema can host both servers without collision.

CREATE TABLE IF NOT EXISTS sca_sessions (
  id           TEXT        PRIMARY KEY,
  subject      TEXT        NOT NULL,
  level        TEXT        NOT NULL,
  method       TEXT        NOT NULL,
  state        TEXT        NOT NULL CHECK (state IN ('pending','ready','consumed','failed','expired')),
  next_kind    TEXT,
  next_url     TEXT,
  next_ttl     INTEGER,
  callback_url TEXT,
  created_at   TIMESTAMPTZ NOT NULL,
  ready_at     TIMESTAMPTZ,
  expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sca_sessions_subject ON sca_sessions(subject);

CREATE TABLE IF NOT EXISTS sca_credentials (
  jti               TEXT        PRIMARY KEY,
  issuer            TEXT        NOT NULL,
  subject           TEXT        NOT NULL,
  level             TEXT        NOT NULL,
  subject_type      TEXT        NOT NULL,
  jwt               TEXT        NOT NULL,
  status_list_id    TEXT        NOT NULL,
  status_list_index BIGINT      NOT NULL,
  issued_at         TIMESTAMPTZ NOT NULL,
  expires           TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sca_credentials_subject ON sca_credentials(subject);
CREATE UNIQUE INDEX IF NOT EXISTS sca_credentials_status_loc
  ON sca_credentials(status_list_id, status_list_index);

-- One row per status-list shard. The active shard is the one with the lowest
-- creation order whose next_index < size; rotation appends a fresh shard.
CREATE TABLE IF NOT EXISTS sca_status_lists (
  list_id    TEXT        PRIMARY KEY,
  size       BIGINT      NOT NULL,
  next_index BIGINT      NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Sparse representation: only revoked indices appear. Snapshot reconstructs
-- the bitstring; inserting twice for the same (list_id, idx) is a no-op
-- (idempotent revocation).
CREATE TABLE IF NOT EXISTS sca_revoked (
  list_id TEXT   NOT NULL REFERENCES sca_status_lists(list_id) ON DELETE CASCADE,
  idx     BIGINT NOT NULL,
  PRIMARY KEY (list_id, idx)
);

CREATE TABLE IF NOT EXISTS sns_records (
  local         TEXT        PRIMARY KEY,
  shadowname    TEXT        NOT NULL,
  did           TEXT        NOT NULL,
  endpoint      TEXT        NOT NULL,
  public_key    JSONB       NOT NULL,
  subject_type  TEXT        NOT NULL,
  ttl           INTEGER     NOT NULL,
  issued_at     TIMESTAMPTZ NOT NULL,
  tombstone     BOOLEAN     NOT NULL DEFAULT FALSE
);
