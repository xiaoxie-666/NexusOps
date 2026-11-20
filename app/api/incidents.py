import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.enums import KnowledgeSourceStatus
from app.core.security import current_user, require_admin, require_incident_editor, require_incident_operator
from app.models.entities import UserAccount
from app.schemas.dtos import (
    AiMessage,
    IncidentAnalysisResponse,
    IncidentCreateRequest,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentNoteCreateRequest,
    IncidentNoteResponse,
    IncidentTransitionRequest,
    IncidentUpdateRequest,
    KnowledgeSourceCreateRequest,
    KnowledgeSourceResponse,
    KnowledgeSourceStatusRequest,
)
from app.services.incidents import IncidentService
from app.services.incident_analysis import IncidentAgentRuntimeService
from app.services.incident_context_memory import IncidentContextMemory
from app.models.entities import IncidentRecord
from app.services.model_router import ModelRouter, ModelStreamInterrupted

router = APIRouter(tags=["NexusOps Incidents"])


@router.post("/api/incidents", response_model=IncidentDetailResponse, status_code=201)
def create_incident(
    request: IncidentCreateRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # Authenticated requesters may report an incident; operational updates are
    # restricted to engineers, on-call users, and administrators.
    if not get_settings().incident_allow_requester_create and not set(user.roles).intersection(
        {"ROLE_ADMIN", "ROLE_ENGINEER", "ROLE_ONCALL"}
    ):
        raise HTTPException(403, "Incident creation is disabled for requester role")
    return IncidentService(db).create(request, user)


@router.get("/api/incidents", response_model=IncidentListResponse)
def list_incidents(
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    status: str | None = None,
    severity: str | None = None,
    service_name: str | None = Query(default=None, alias="serviceName"),
    keyword: str | None = None,
):
    max_page_size = get_settings().incident_max_page_size
    if page_size > max_page_size:
        raise HTTPException(422, f"pageSize must be <= {max_page_size}")
    return IncidentService(db).list(user, page, page_size, status, severity, service_name, keyword)


@router.get("/api/incidents/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(
    incident_id: int,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).detail(incident_id, user)


@router.patch("/api/incidents/{incident_id}", response_model=IncidentDetailResponse)
def update_incident(
    incident_id: int,
    request: IncidentUpdateRequest,
    user: Annotated[UserAccount, Depends(require_incident_editor)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).update(incident_id, request, user)


@router.post("/api/incidents/{incident_id}/transition", response_model=IncidentDetailResponse)
def transition_incident(
    incident_id: int,
    request: IncidentTransitionRequest,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).transition(incident_id, request, user)


@router.post("/api/incidents/{incident_id}/notes", response_model=IncidentNoteResponse, status_code=201)
def add_incident_note(
    incident_id: int,
    request: IncidentNoteCreateRequest,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).add_note(incident_id, request, user)


@router.get("/api/admin/knowledge-sources", response_model=list[KnowledgeSourceResponse])
def list_knowledge_sources(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).list_knowledge_sources()


@router.post("/api/admin/knowledge-sources", response_model=KnowledgeSourceResponse, status_code=201)
def create_knowledge_source(
    request: KnowledgeSourceCreateRequest,
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).create_knowledge_source(request)


@router.patch("/api/admin/knowledge-sources/{source_id}/status", response_model=KnowledgeSourceResponse)
def update_knowledge_source_status(
    source_id: int,
    request: KnowledgeSourceStatusRequest,
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentService(db).set_knowledge_source_status(source_id, request.status.value)


@router.get("/api/incidents/{incident_id}/context/snapshots")
def list_incident_context_snapshots(
    incident_id: int,
    _: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    if db.get(IncidentRecord, incident_id) is None:
        raise HTTPException(404, "Incident not found")
    return IncidentContextMemory(db, get_settings()).versions(incident_id)


@router.post("/api/incidents/{incident_id}/context/snapshots/{version}/restore")
def restore_incident_context_snapshot(
    incident_id: int,
    version: int,
    _: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    incident = db.get(IncidentRecord, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    try:
        context = IncidentContextMemory(db, get_settings()).restore_version(incident, version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"restored": True, "version": context["version"], "runId": context["runId"]}


@router.post("/api/incidents/{incident_id}/analyze", response_model=IncidentAnalysisResponse)
def analyze_incident(
    incident_id: int,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    """Run the NexusOps event-driven multi-Agent incident analysis."""
    return IncidentAgentRuntimeService(db, get_settings()).analyze(incident_id, user)


@router.get("/api/incidents/{incident_id}/analysis/latest", response_model=IncidentAnalysisResponse)
def latest_incident_analysis(
    incident_id: int,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    return IncidentAgentRuntimeService(db, get_settings()).latest(incident_id, user)


@router.post("/api/incidents/{incident_id}/analyze/stream")
async def analyze_incident_stream(
    incident_id: int,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    """Run analysis, then stream its operator-facing summary using safe model fallback."""
    settings = get_settings()
    analysis = IncidentAgentRuntimeService(db, settings).analyze(incident_id, user)
    messages = [
        AiMessage(
            role="system",
            content=(
                "NEXUSOPS_ANALYSIS_SUMMARY\n"
                "你是研发运维故障研判助手。用简洁中文总结故障优先级、研判结果和下一步建议。"
                "明确根因是假设而非事实；不得声称已经执行命令；生产变更必须人工审批。"
            ),
        ),
        AiMessage(
            role="user",
            content=json.dumps(
                {
                    "incident": {
                        "incidentKey": analysis.incidentKey,
                        "title": analysis.understanding.get("title", ""),
                        "serviceName": analysis.understanding.get("serviceName", ""),
                        "severity": analysis.understanding.get("reportedSeverity", ""),
                    },
                    "impact": analysis.impactAssessment,
                    "resolution": analysis.resolutionPlan,
                    "guard": analysis.operationGuard,
                },
                ensure_ascii=False,
            ),
        ),
    ]
    model_router = ModelRouter(settings)

    async def event_stream():
        yield _sse("meta", {"runId": analysis.runId, "incidentId": incident_id, "type": "meta"})
        try:
            async for token in model_router.stream("ResolutionAgent", messages):
                yield _sse("token", {"runId": analysis.runId, "content": token, "type": "token"})
        except ModelStreamInterrupted as exc:
            # Once a token has reached the client, do not append a second model's answer.
            yield _sse("error", {"runId": analysis.runId, "partial": True, "message": str(exc), "type": "error"})
        except Exception as exc:
            yield _sse("error", {"runId": analysis.runId, "partial": False, "message": f"Summary stream unavailable: {type(exc).__name__}", "type": "error"})
        yield _sse("done", {"runId": analysis.runId, "type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
