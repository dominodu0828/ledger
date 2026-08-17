"""The memory layer itself: write, retrieve, revoke, replay.

Every mutating function here does its whole job in one CockroachDB transaction.
That is the load-bearing claim of this project — not that we screen inbound
content (any harness can do that), but that screening, embedding, the write and
the audit record commit atomically, so there is no window in which a poisoned
memory is retrievable.
"""

import json
import uuid
from dataclasses import dataclass

from . import bedrock, config, db, screen


@dataclass
class WriteResult:
    admitted: bool
    memory_id: str | None
    quarantine_id: str | None
    verdict: dict


@dataclass
class Recall:
    id: str
    content: str
    trust_tier: int
    score: float
    source_label: str


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def register_source(kind: str, label: str, trust_tier: int, uri: str | None = None) -> str:
    with db.tx() as cur:
        cur.execute(
            """
            INSERT INTO sources (kind, label, uri, trust_tier)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (kind, label, uri, trust_tier),
        )
        return str(cur.fetchone()[0])


def ensure_source(kind: str, label: str, trust_tier: int, uri: str | None = None) -> str:
    """Return the id of a live source with this label, creating it if absent.

    MCP clients address sources by name, not UUID, so repeated calls from the
    same channel must land on one source row — otherwise revoking that channel
    would only revoke the slice written since the last restart.
    """
    with db.read() as cur:
        cur.execute(
            "SELECT id FROM sources WHERE label = %s AND revoked_at IS NULL LIMIT 1",
            (label,),
        )
        row = cur.fetchone()
    if row is not None:
        return str(row[0])
    return register_source(kind, label, trust_tier, uri)


def list_sources() -> list[dict]:
    with db.read() as cur:
        cur.execute(
            """
            SELECT s.id, s.kind, s.label, s.uri, s.trust_tier, s.revoked_at,
                   count(m.id) FILTER (WHERE m.revoked_at IS NULL) AS live_memories
            FROM sources s
            LEFT JOIN memories m ON m.source_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at
            """
        )
        return [
            {
                "id": str(r[0]),
                "kind": r[1],
                "label": r[2],
                "uri": r[3],
                "trust_tier": r[4],
                "revoked": r[5] is not None,
                "live_memories": r[6],
            }
            for r in cur.fetchall()
        ]


# ---------------------------------------------------------------------------
# Write path — screen, embed, insert, audit. One transaction.
# ---------------------------------------------------------------------------


@db.retry_on_serialization()
def write(
    content: str,
    source_id: str,
    agent_id: str = "default",
    derived_from: list[str] | None = None,
    bypass_screen: bool = False,
) -> WriteResult:
    """Attempt to commit `content` to the retrievable memory set.

    `bypass_screen=True` reproduces the unprotected baseline — it exists so the
    demo can show the same attack landing when the gate is off. It is not a
    production affordance.
    """
    with db.read() as cur:
        cur.execute("SELECT trust_tier FROM sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown source {source_id}")
    trust_tier = row[0]

    if bypass_screen:
        verdict = screen.Verdict(
            admitted=True, score=0.0, rules=[], rationale="SCREENING BYPASSED (baseline)"
        )
    else:
        verdict = screen.screen(content, trust_tier)

    # Embedding is computed before the transaction opens: it is a network call,
    # and holding a database transaction open across one is a production
    # anti-pattern. The atomicity that matters is screen-verdict + write +
    # audit, all of which happen below.
    vector = bedrock.embed(content) if verdict.admitted else None

    with db.tx() as cur:
        if not verdict.admitted:
            cur.execute(
                """
                INSERT INTO quarantine (agent_id, content, source_id, verdict)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (agent_id, content, source_id, json.dumps(verdict.as_json())),
            )
            qid = str(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO audit_log (agent_id, action, subject_id, detail)
                VALUES (%s, 'write_quarantined', %s, %s)
                """,
                (agent_id, qid, json.dumps(verdict.as_json())),
            )
            return WriteResult(False, None, qid, verdict.as_json())

        cur.execute(
            f"""
            INSERT INTO memories (agent_id, content, embedding, source_id, trust_tier)
            VALUES (%s, %s, %s::VECTOR({config.EMBED_DIM}), %s, %s)
            RETURNING id
            """,
            (agent_id, content, db.to_vector(vector), source_id, trust_tier),
        )
        mid = str(cur.fetchone()[0])

        for parent in derived_from or []:
            cur.execute(
                """
                INSERT INTO memory_edges (memory_id, derived_from)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (mid, parent),
            )

        cur.execute(
            """
            INSERT INTO audit_log (agent_id, action, subject_id, detail)
            VALUES (%s, 'write_admitted', %s, %s)
            """,
            (
                agent_id,
                mid,
                json.dumps(
                    {**verdict.as_json(), "derived_from": derived_from or []}
                ),
            ),
        )
        return WriteResult(True, mid, None, verdict.as_json())


# ---------------------------------------------------------------------------
# Read path — hybrid vector + predicate, strongly consistent.
# ---------------------------------------------------------------------------


def recall(
    query: str,
    agent_id: str = "default",
    min_trust: int = 0,
    limit: int = 5,
    as_of: str | None = None,
) -> list[Recall]:
    """Semantic recall over the LIVE memory set only.

    The `revoked_at IS NULL` predicate and the vector ordering are evaluated in
    the same query — a pure vector store would have to filter after the fact,
    with no consistency guarantee between the two steps.

    `as_of` uses CockroachDB time travel to answer "what would this query have
    returned at time T" — the audit surface for a post-incident review.
    """
    vector = db.to_vector(bedrock.embed(query))
    clause = db.as_of_clause(as_of)

    # The AS OF SYSTEM TIME clause is statement-scoped: it appears once, after
    # the last join and before WHERE. The vector literal is cast explicitly so
    # the planner resolves `<->` against VECTOR rather than an unknown text type.
    sql = f"""
        SELECT m.id, m.content, m.trust_tier,
               m.embedding <-> %s::VECTOR({config.EMBED_DIM}) AS distance,
               s.label
        FROM memories m
        JOIN sources s ON s.id = m.source_id{clause}
        WHERE m.agent_id = %s
          AND m.revoked_at IS NULL
          AND m.trust_tier >= %s
        ORDER BY m.embedding <-> %s::VECTOR({config.EMBED_DIM})
        LIMIT %s
    """
    with db.read() as cur:
        cur.execute(sql, (vector, agent_id, min_trust, vector, limit))
        rows = cur.fetchall()

    if not as_of:
        with db.tx() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (agent_id, action, detail)
                VALUES (%s, 'retrieve', %s)
                """,
                (
                    agent_id,
                    json.dumps(
                        {
                            "query": query[:500],
                            "min_trust": min_trust,
                            "hits": [str(r[0]) for r in rows],
                        }
                    ),
                ),
            )

    return [
        Recall(
            id=str(r[0]),
            content=r[1],
            trust_tier=r[2],
            score=_cosine_from_l2(float(r[3])),
            source_label=r[4],
        )
        for r in rows
    ]


def _cosine_from_l2(distance: float) -> float:
    """Convert the `<->` L2 distance back to cosine similarity.

    Titan is called with normalize=true, so every stored vector is unit length,
    and for unit vectors |a-b|^2 = 2 - 2(a.b) — i.e. cos = 1 - d^2/2.

    Reporting `1 - d` instead (as an earlier version did) is not a rescaling,
    it is wrong: a genuine 0.71-similarity match renders as 0.24, and anything
    past d=1 goes negative. That number is on screen in the demo, so it has to
    mean what a reader assumes it means.
    """
    return round(1.0 - (distance * distance) / 2.0, 4)


# ---------------------------------------------------------------------------
# Revocation — one transaction, cascading through the derivation graph.
# ---------------------------------------------------------------------------


@db.retry_on_serialization()
def revoke_source(source_id: str, reason: str, agent_id: str = "default") -> dict:
    """Repudiate a source and every memory that descends from it.

    The recursive CTE walks `memory_edges` transitively, so a memory the agent
    *wrote itself* while reasoning over poisoned input is invalidated too. All
    of it lands in one transaction: there is no moment where half the lineage
    is revoked and half is still retrievable.
    """
    with db.tx() as cur:
        cur.execute(
            "UPDATE sources SET revoked_at = now() WHERE id = %s RETURNING label",
            (source_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown source {source_id}")
        label = row[0]

        cur.execute(
            """
            WITH RECURSIVE tainted (id) AS (
                SELECT id FROM memories
                WHERE source_id = %s AND revoked_at IS NULL
              UNION
                SELECT e.memory_id
                FROM memory_edges e
                JOIN tainted t ON e.derived_from = t.id
            )
            UPDATE memories
            SET revoked_at = now(), revoked_reason = %s
            WHERE id IN (SELECT id FROM tainted) AND revoked_at IS NULL
            RETURNING id
            """,
            (source_id, reason),
        )
        revoked = [str(r[0]) for r in cur.fetchall()]

        cur.execute(
            """
            INSERT INTO audit_log (agent_id, action, subject_id, detail)
            VALUES (%s, 'revoke', %s, %s)
            """,
            (
                agent_id,
                source_id,
                json.dumps(
                    {"reason": reason, "source_label": label, "revoked_memories": revoked}
                ),
            ),
        )

    return {"source_id": source_id, "source_label": label, "revoked_count": len(revoked),
            "revoked_memory_ids": revoked}


# ---------------------------------------------------------------------------
# Inspection surfaces
# ---------------------------------------------------------------------------


def quarantined(agent_id: str = "default", limit: int = 50) -> list[dict]:
    with db.read() as cur:
        cur.execute(
            """
            SELECT q.id, q.content, q.verdict, q.created_at, s.label
            FROM quarantine q JOIN sources s ON s.id = q.source_id
            WHERE q.agent_id = %s
            ORDER BY q.created_at DESC LIMIT %s
            """,
            (agent_id, limit),
        )
        return [
            {
                "id": str(r[0]),
                "content": r[1],
                "verdict": r[2],
                "created_at": r[3].isoformat(),
                "source_label": r[4],
            }
            for r in cur.fetchall()
        ]


def audit(agent_id: str = "default", limit: int = 100) -> list[dict]:
    with db.read() as cur:
        cur.execute(
            """
            SELECT id, action, subject_id, detail, created_at
            FROM audit_log WHERE agent_id = %s
            ORDER BY created_at DESC LIMIT %s
            """,
            (agent_id, limit),
        )
        return [
            {
                "id": str(r[0]),
                "action": r[1],
                "subject_id": str(r[2]) if r[2] else None,
                "detail": r[3],
                "created_at": r[4].isoformat(),
            }
            for r in cur.fetchall()
        ]


def stats(agent_id: str = "default") -> dict:
    with db.read() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM memories WHERE agent_id = %s AND revoked_at IS NULL),
              (SELECT count(*) FROM memories WHERE agent_id = %s AND revoked_at IS NOT NULL),
              (SELECT count(*) FROM quarantine WHERE agent_id = %s),
              (SELECT count(*) FROM audit_log WHERE agent_id = %s)
            """,
            (agent_id, agent_id, agent_id, agent_id),
        )
        live, revoked, quar, events = cur.fetchone()
    return {
        "live_memories": live,
        "revoked_memories": revoked,
        "quarantined": quar,
        "audit_events": events,
    }
