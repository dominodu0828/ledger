"""FastAPI surface. Serves the demo UI and the JSON API."""

import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, memory

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Ledger",
    description="Provenance-tracked, revocable agent memory on CockroachDB + AWS",
    version="0.1.0",
)


class SourceIn(BaseModel):
    kind: str
    label: str
    trust_tier: int
    uri: str | None = None


class WriteIn(BaseModel):
    content: str
    source_id: str
    agent_id: str = "default"
    derived_from: list[str] | None = None
    bypass_screen: bool = False


class IngestIn(BaseModel):
    text: str
    source_id: str
    agent_id: str = "default"
    bypass_screen: bool = False


class AskIn(BaseModel):
    question: str
    agent_id: str = "default"
    min_trust: int = 0


class RevokeIn(BaseModel):
    source_id: str
    reason: str
    agent_id: str = "default"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/stats")
def api_stats(agent_id: str = "default"):
    return memory.stats(agent_id)


@app.get("/api/sources")
def api_sources():
    return memory.list_sources()


@app.post("/api/sources")
def api_create_source(body: SourceIn):
    sid = memory.register_source(body.kind, body.label, body.trust_tier, body.uri)
    return {"id": sid}


@app.post("/api/write")
def api_write(body: WriteIn):
    try:
        r = memory.write(
            body.content,
            body.source_id,
            agent_id=body.agent_id,
            derived_from=body.derived_from,
            bypass_screen=body.bypass_screen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "admitted": r.admitted,
        "memory_id": r.memory_id,
        "quarantine_id": r.quarantine_id,
        "verdict": r.verdict,
    }


@app.post("/api/ingest")
def api_ingest(body: IngestIn):
    try:
        return agent.ingest(
            body.text, body.source_id, body.agent_id, bypass_screen=body.bypass_screen
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/recall")
def api_recall(
    q: str,
    agent_id: str = "default",
    min_trust: int = 0,
    limit: int = 5,
    as_of: str | None = None,
):
    try:
        hits = memory.recall(q, agent_id, min_trust, limit, as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        {
            "id": h.id,
            "content": h.content,
            "trust_tier": h.trust_tier,
            "score": h.score,
            "source": h.source_label,
        }
        for h in hits
    ]


@app.post("/api/ask")
def api_ask(body: AskIn):
    return agent.ask(body.question, body.agent_id, body.min_trust)


@app.post("/api/revoke")
def api_revoke(body: RevokeIn):
    try:
        return memory.revoke_source(body.source_id, body.reason, body.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/quarantine")
def api_quarantine(agent_id: str = "default"):
    return memory.quarantined(agent_id)


@app.get("/api/audit")
def api_audit(agent_id: str = "default", limit: int = 100):
    return memory.audit(agent_id, limit)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
