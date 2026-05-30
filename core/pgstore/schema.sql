-- SPDX-License-Identifier: MIT
-- pgstore schema for Shadownet v0.2 Provider + Issuer reference servers.
--
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT
-- EXISTS) so applySchema is safe to run on every Open. Migrations are
-- forward-only; breaking schema changes ship in a new pgstore minor with a
-- migration script.

-- ── Provider ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS provider_records (
    local           TEXT        PRIMARY KEY,
    shadow_pk       TEXT        NOT NULL,
    a2a_url         TEXT        NOT NULL,
    display_name    TEXT        NOT NULL DEFAULT '',
    description     TEXT        NOT NULL DEFAULT '',
    version         TEXT        NOT NULL DEFAULT '1.0.0',
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);

-- ── Issuer: credentials ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS issuer_credentials (
    idempotency_key  TEXT        PRIMARY KEY,
    jws              TEXT        NOT NULL,
    iss              TEXT        NOT NULL,
    sub              TEXT        NOT NULL,
    org              TEXT        NOT NULL,
    epoch            BIGINT      NOT NULL,
    idx              BIGINT      NOT NULL,
    issued_at        TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issuer_credentials_sub ON issuer_credentials(sub);

-- ── Issuer: pending ceremonies ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS issuer_pendings (
    handle_id          TEXT        PRIMARY KEY,
    idempotency_key    TEXT        NOT NULL UNIQUE,
    iss                TEXT        NOT NULL,
    aud                TEXT        NOT NULL,
    kind               TEXT        NOT NULL,
    org                TEXT        NOT NULL,
    subject_pub        TEXT        NOT NULL,
    status             SMALLINT    NOT NULL,
    next_url           TEXT        NOT NULL DEFAULT '',
    reason             TEXT        NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL,
    ceremony_expiry    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issuer_pendings_status ON issuer_pendings(status);

-- ── Issuer: status epochs + revocations ───────────────────────────────

CREATE TABLE IF NOT EXISTS issuer_epochs (
    number                    BIGINT      PRIMARY KEY,
    max_indices               BIGINT      NOT NULL,
    next_idx                  BIGINT      NOT NULL,
    opened_at                 TIMESTAMPTZ NOT NULL,
    closed_at                 TIMESTAMPTZ,
    last_issued_expires_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS issuer_revocations (
    epoch       BIGINT      NOT NULL,
    idx         BIGINT      NOT NULL,
    revoked_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (epoch, idx)
);
