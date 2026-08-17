-- Ledger: provenance-tracked, revocable agent memory on CockroachDB
-- Run with: python -m app.init_db   (or paste into `ccloud cluster sql ledger`)

-- ---------------------------------------------------------------------------
-- sources: where a memory came from. trust_tier is the routing dimension.
--   3 = operator / system    (trusted config)
--   2 = the user themselves
--   1 = tool output          (API responses, DB reads)
--   0 = untrusted content    (web pages, uploaded docs, third-party text)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        STRING NOT NULL,               -- user | tool_output | web_page | document
    label       STRING NOT NULL,               -- human-readable origin
    uri         STRING,                        -- URL / file path, nullable
    trust_tier  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,                   -- set when the source is repudiated
    CONSTRAINT trust_tier_range CHECK (trust_tier BETWEEN 0 AND 3)
);

-- ---------------------------------------------------------------------------
-- memories: the retrievable set. A row here is, by construction, something the
-- screening gate let through inside the same transaction that wrote it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    STRING NOT NULL DEFAULT 'default',
    content     STRING NOT NULL,
    embedding   VECTOR(1024) NOT NULL,
    source_id   UUID NOT NULL REFERENCES sources(id),
    trust_tier  INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,                   -- NULL = live; non-NULL = tombstoned
    revoked_reason STRING
);

-- The distributed vector index. This is CockroachDB feature #1.
CREATE VECTOR INDEX IF NOT EXISTS memories_embedding_idx ON memories (embedding);

-- Supports the hybrid predicate half of the retrieval query.
CREATE INDEX IF NOT EXISTS memories_live_idx
    ON memories (agent_id, trust_tier) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS memories_source_idx ON memories (source_id);

-- ---------------------------------------------------------------------------
-- memory_edges: derivation graph. If B was written using A as context, then
-- revoking A's source cascades to B. This is what makes revocation complete
-- rather than shallow.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_edges (
    memory_id    UUID NOT NULL REFERENCES memories(id),
    derived_from UUID NOT NULL REFERENCES memories(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, derived_from)
);

CREATE INDEX IF NOT EXISTS memory_edges_from_idx ON memory_edges (derived_from);

-- ---------------------------------------------------------------------------
-- quarantine: writes the gate rejected. Never retrievable; kept for audit and
-- for showing the operator what was attempted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quarantine (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    STRING NOT NULL DEFAULT 'default',
    content     STRING NOT NULL,
    source_id   UUID NOT NULL REFERENCES sources(id),
    verdict     JSONB NOT NULL,                -- {score, rules[], rationale}
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- audit_log: append-only. Every write, screening verdict, retrieval and
-- revocation lands here in the SAME transaction as the action it records.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    STRING NOT NULL DEFAULT 'default',
    action      STRING NOT NULL,               -- write_admitted | write_quarantined | retrieve | revoke
    subject_id  UUID,                          -- memory / source / quarantine row
    detail      JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log (created_at DESC);
