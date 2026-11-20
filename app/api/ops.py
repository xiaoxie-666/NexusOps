from __future__ import annotations

import hmac
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import current_user, require_admin, require_approval_reviewer, require_incident_operator
from app.models.entities import (
    AlertEvent,
    IncidentApproval,
    IncidentNotification,
    IncidentRecord,
    IncidentToolAudit,
    IncidentToolJob,
    IncidentNote,
    UserAccount,
)
from app.schemas.dtos import (
    ApprovalCreateRequest,
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    authority,
    ApprovalReviewRequest,
    AlertEventResponse,
    IncidentApprovalResponse,
    IncidentNotificationResponse,
    IncidentToolAuditResponse,
    IncidentToolEnqueueRequest,
    IncidentToolJobResponse,
)
from app.services.alert_ingestion import AlertIngestionService
from app.services.incident_tooling import ALLOWED_TOOL_NAMES, IncidentToolService
from app.services.knowledge import KnowledgeService
from app.services.model_router import ModelRouter
from app.agents.factory import agent_framework_status

router = APIRouter(tags=["NexusOps Operations"])


@router.get("/actuator/health")
def health(db: Annotated[Session, Depends(get_db)]):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "Database readiness check failed") from exc
    return {"status": "UP", "service": "nexusops", "database": "UP", "redis": "OPTIONAL_CONTEXT_CACHE_WITH_MYSQL_FALLBACK"}


@router.get("/actuator/health/live")
def health_live():
    return {"status": "UP", "service": "nexusops"}


@router.get("/actuator/health/ready")
def health_ready(db: Annotated[Session, Depends(get_db)]):
    db.execute(text("SELECT 1"))
    return {"status": "UP", "database": "UP"}


@router.get("/api/profile")
def profile(user: Annotated[UserAccount, Depends(current_user)]):
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "roles": [authority(role) for role in user.roles],
    }


@router.get("/api/agent/status")
def agent_status(user: Annotated[UserAccount, Depends(current_user)]):
    settings = get_settings()
    provider = settings.ai_provider.lower()
    model = settings.ollama_model if provider == "ollama" else settings.openai_model if provider == "openai" else "mock"
    return {
        "project": "NexusOps",
        "provider": provider,
        "model": model,
        "realModelEnabled": provider in {"ollama", "openai"},
        "agentFramework": agent_framework_status(settings),
        "modelRouter": ModelRouter(settings).route_status(),
        "agents": [
            {"name": "CoordinatorAgent", "status": "READY", "description": "共享黑板、任务认领、预算管理与最终结果采纳"},
            {"name": "IncidentUnderstandingAgent", "status": "READY", "description": "提取故障现象、服务、环境和关键异常信号"},
            {"name": "ImpactAssessmentAgent", "status": "READY", "description": "评估故障影响范围并提出优先级建议"},
            {"name": "IncidentContextAgent", "status": "READY", "description": "通过 Hybrid RAG 检索有效运维知识和 Runbook 证据"},
            {"name": "ResolutionAgent", "status": "READY", "description": "结合证据生成待验证根因假设和只读排查建议"},
            {"name": "OperationGuardAgent", "status": "READY", "description": "审查建议的风险等级、审批要求和禁止操作"},
        ],
        "runtimeHarness": {"name": "NexusOpsIncidentAgentRuntime", "status": "READY"},
        "loop": {"type": "event-driven-multi-agent", "maxSteps": settings.incident_analysis_max_rounds},
        "collaboration": {"scheduler": "claim-based", "state": "append-only-blackboard", "finalAcceptance": "CoordinatorAgent accepts after OperationGuard review"},
    }


@router.post("/api/admin/knowledge")
def ingest_knowledge(
    request: KnowledgeIngestRequest,
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    chunks = KnowledgeService(db, get_settings()).ingest(request.source, request.content)
    return KnowledgeIngestResponse(source=request.source, chunks=chunks)


@router.get("/api/admin/knowledge/status")
def knowledge_status(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return KnowledgeService(db, get_settings()).status()


@router.post("/api/admin/knowledge/rebuild-vector")
def rebuild_knowledge_vector(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        indexed = KnowledgeService(db, get_settings()).rebuild_vector_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"indexedChunks": indexed}


@router.post("/api/admin/knowledge/backup")
def backup_knowledge_vector(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        snapshot = KnowledgeService(db, get_settings()).backup_vector_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"snapshot": snapshot}


@router.post("/api/admin/knowledge/file")
async def ingest_knowledge_file(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Knowledge file must be smaller than 20 MB")
    filename = file.filename or "uploaded-knowledge.txt"
    if not filename.lower().endswith((".pdf", ".md", ".markdown", ".txt")):
        raise HTTPException(415, "Supported file types: PDF, Markdown, TXT")
    chunks = KnowledgeService(db, get_settings()).ingest_file(filename, data)
    return KnowledgeIngestResponse(source=filename, chunks=chunks)


def _user_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(UserAccount, user_id)
    return user.display_name or user.username if user else None


def _loads(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _approval_response(db: Session, row: IncidentApproval) -> IncidentApprovalResponse:
    return IncidentApprovalResponse(
        id=row.id,
        incidentId=row.incident_id,
        requestedBy=_user_name(db, row.requested_by_user_id) or "unknown",
        actionKey=row.action_key,
        target=row.target,
        justification=row.justification,
        details=_loads(row.payload_json),
        status=row.status,
        reviewedBy=_user_name(db, row.reviewed_by_user_id),
        reviewNote=row.review_note,
        createdAt=row.created_at,
        reviewedAt=row.reviewed_at,
        executionMode="MANUAL_ONLY",
    )


def _job_response(db: Session, row: IncidentToolJob) -> IncidentToolJobResponse:
    return IncidentToolJobResponse(
        id=row.id,
        incidentId=row.incident_id,
        createdBy=_user_name(db, row.created_by_user_id) or "unknown",
        toolName=row.tool_name,
        payload=_loads(row.payload_json),
        idempotencyKey=row.idempotency_key,
        status=row.status,
        attempts=row.attempts,
        maxAttempts=row.max_attempts,
        runAfter=row.run_after,
        lastError=row.last_error,
        result=_loads(row.result_json),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


@router.get("/api/dashboard/summary")
def dashboard_summary(
    _: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_start = datetime(now.year, now.month, now.day)
    active_statuses = ["NEW", "TRIAGED", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATED"]
    total = db.query(IncidentRecord).count()
    active = db.query(IncidentRecord).filter(IncidentRecord.status.in_(active_statuses)).count()
    critical = db.query(IncidentRecord).filter(
        IncidentRecord.severity.in_(["P0", "P1"]), IncidentRecord.status.in_(active_statuses)
    ).count()
    created_today = db.query(IncidentRecord).filter(IncidentRecord.created_at >= day_start).count()
    status_rows = db.query(IncidentRecord.status, func.count(IncidentRecord.id)).group_by(IncidentRecord.status).all()
    severity_rows = db.query(IncidentRecord.severity, func.count(IncidentRecord.id)).group_by(IncidentRecord.severity).all()
    alert_firing = db.query(AlertEvent).filter(AlertEvent.status == "FIRING").count()
    pending_approvals = db.query(IncidentApproval).filter(IncidentApproval.status == "PENDING").count()
    pending_jobs = db.query(IncidentToolJob).filter(IncidentToolJob.status.in_(["PENDING", "RUNNING"])).count()
    daily = []
    for offset in range(6, -1, -1):
        start = day_start - timedelta(days=offset)
        end = start + timedelta(days=1)
        count = db.query(IncidentRecord).filter(
            IncidentRecord.created_at >= start, IncidentRecord.created_at < end
        ).count()
        daily.append({"date": start.strftime("%Y-%m-%d"), "count": count})
    recent = db.query(IncidentRecord).order_by(IncidentRecord.created_at.desc()).limit(8).all()
    return {
        "generatedAt": now.isoformat(),
        "metrics": {
            "totalIncidents": total,
            "activeIncidents": active,
            "criticalIncidents": critical,
            "createdToday": created_today,
            "firingAlerts": alert_firing,
            "pendingApprovals": pending_approvals,
            "pendingToolJobs": pending_jobs,
        },
        "byStatus": {key: value for key, value in status_rows},
        "bySeverity": {key: value for key, value in severity_rows},
        "dailyTrend": daily,
        "recentIncidents": [
            {
                "id": row.id,
                "incidentKey": row.incident_key,
                "title": row.title,
                "serviceName": row.service_name,
                "severity": row.severity,
                "status": row.status,
                "environment": row.environment,
                "createdAt": row.created_at,
            }
            for row in recent
        ],
    }


@router.get("/api/alerts", response_model=list[AlertEventResponse])
def list_alerts(
    _: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    query = db.query(AlertEvent)
    if status:
        query = query.filter(AlertEvent.status == status.upper())
    rows = query.order_by(AlertEvent.last_seen_at.desc()).limit(limit).all()
    return [
        AlertEventResponse(
            id=row.id, source=row.source, externalId=row.external_id, fingerprint=row.fingerprint,
            alertName=row.alert_name, serviceName=row.service_name, environment=row.environment,
            severity=row.severity, status=row.status, summary=row.summary, description=row.description,
            incidentId=row.incident_id, occurrenceCount=row.occurrence_count,
            firstSeenAt=row.first_seen_at, lastSeenAt=row.last_seen_at, createdAt=row.created_at,
        ) for row in rows
    ]


@router.post("/api/webhooks/alerts/{source}")
async def ingest_alert_webhook(
    source: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: str | None = Header(default=None, alias="X-NexusOps-Webhook-Token"),
):
    settings = get_settings()
    if settings.alert_webhook_token:
        if not token or not hmac.compare_digest(token, settings.alert_webhook_token):
            raise HTTPException(401, "Invalid webhook token")
    elif settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(503, "ALERT_WEBHOOK_TOKEN must be configured in production")
    source = source.strip().lower()
    if source not in {"alertmanager", "prometheus", "grafana", "generic"}:
        raise HTTPException(404, "Unsupported webhook source")
    raw = await request.body()
    if len(raw) > settings.alert_webhook_max_payload_bytes:
        raise HTTPException(413, "Webhook payload is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Webhook body must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "Webhook body must be a JSON object")
    return AlertIngestionService(db).ingest(source, payload)


@router.post("/api/webhooks/alertmanager")
async def ingest_alertmanager_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: str | None = Header(default=None, alias="X-NexusOps-Webhook-Token"),
):
    settings = get_settings()
    if settings.alert_webhook_token:
        if not token or not hmac.compare_digest(token, settings.alert_webhook_token):
            raise HTTPException(401, "Invalid webhook token")
    elif settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(503, "ALERT_WEBHOOK_TOKEN must be configured in production")
    raw = await request.body()
    if len(raw) > settings.alert_webhook_max_payload_bytes:
        raise HTTPException(413, "Webhook payload is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Webhook body must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "Webhook body must be a JSON object")
    return AlertIngestionService(db).ingest("alertmanager", payload)


@router.post("/api/incidents/{incident_id}/approvals", response_model=IncidentApprovalResponse, status_code=201)
def create_approval(
    incident_id: int,
    request: ApprovalCreateRequest,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        row = IncidentToolService(db).create_approval(
            incident_id, user, request.actionKey, request.target, request.justification, request.details
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _approval_response(db, row)


@router.get("/api/admin/approvals", response_model=list[IncidentApprovalResponse])
def list_approvals(
    _: Annotated[UserAccount, Depends(require_approval_reviewer)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    query = db.query(IncidentApproval)
    if status:
        query = query.filter(IncidentApproval.status == status.upper())
    rows = query.order_by(IncidentApproval.created_at.desc()).limit(limit).all()
    return [_approval_response(db, row) for row in rows]


@router.post("/api/admin/approvals/{approval_id}/review", response_model=IncidentApprovalResponse)
def review_approval(
    approval_id: int,
    request: ApprovalReviewRequest,
    user: Annotated[UserAccount, Depends(require_approval_reviewer)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(IncidentApproval, approval_id)
    if row is None:
        raise HTTPException(404, "Approval request not found")
    if row.status != "PENDING":
        raise HTTPException(409, "Approval request has already been reviewed")
    if row.requested_by_user_id == user.id:
        raise HTTPException(403, "The requester cannot review their own approval request")
    if not request.approved and len(request.reviewNote.strip()) < 4:
        raise HTTPException(422, "A rejection reason of at least 4 characters is required")
    updated = db.query(IncidentApproval).filter(
        IncidentApproval.id == approval_id,
        IncidentApproval.status == "PENDING",
    ).update(
        {
            IncidentApproval.status: "APPROVED" if request.approved else "REJECTED",
            IncidentApproval.reviewed_by_user_id: user.id,
            IncidentApproval.review_note: request.reviewNote.strip(),
            IncidentApproval.reviewed_at: datetime.now(timezone.utc).replace(tzinfo=None),
        },
        synchronize_session=False,
    )
    if updated != 1:
        db.rollback()
        raise HTTPException(409, "Approval request was already reviewed by another operator")
    db.refresh(row)
    db.add(
        IncidentNote(
            incident_id=row.incident_id,
            actor_user_id=user.id,
            actor_name=user.display_name or user.username,
            note_type="SYSTEM",
            content=(
                f"人工审批结果：{row.action_key} -> {row.status}。目标：{row.target}。"
                f"审批备注：{row.review_note or '无'}。"
                "注意：审批仅记录授权决定，不会触发或执行生产变更。"
            ),
        )
    )
    db.add(
        IncidentToolAudit(
            incident_id=row.incident_id,
            job_id=None,
            actor_user_id=user.id,
            tool_name=row.action_key,
            decision=row.status,
            status="MANUAL_APPROVAL_RECORDED",
            reason="Approval is recorded for an external authorized operator; NexusOps does not execute production changes.",
            payload_json=json.dumps({"target": row.target, "justification": row.justification}, ensure_ascii=False),
            result_json=json.dumps({"status": row.status, "execution": "MANUAL_ONLY"}, ensure_ascii=False),
        )
    )
    db.commit()
    db.refresh(row)
    return _approval_response(db, row)


@router.post("/api/incidents/{incident_id}/tools", response_model=IncidentToolJobResponse, status_code=202)
def enqueue_incident_tool(
    incident_id: int,
    request: IncidentToolEnqueueRequest,
    user: Annotated[UserAccount, Depends(require_incident_operator)],
    db: Annotated[Session, Depends(get_db)],
):
    if request.toolName not in ALLOWED_TOOL_NAMES:
        raise HTTPException(422, f"Tool must be one of: {', '.join(sorted(ALLOWED_TOOL_NAMES))}")
    try:
        row = IncidentToolService(db).enqueue(
            incident_id, user, request.toolName, request.payload, request.idempotencyKey
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _job_response(db, row)


@router.get("/api/admin/tool-jobs", response_model=list[IncidentToolJobResponse])
def list_incident_tool_jobs(
    _: Annotated[UserAccount, Depends(require_approval_reviewer)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    query = db.query(IncidentToolJob)
    if status:
        query = query.filter(IncidentToolJob.status == status.upper())
    rows = query.order_by(IncidentToolJob.created_at.desc()).limit(limit).all()
    return [_job_response(db, row) for row in rows]


@router.post("/api/admin/tool-jobs/{job_id}/retry", response_model=IncidentToolJobResponse)
def retry_incident_tool_job(
    job_id: int,
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(IncidentToolJob, job_id)
    if row is None:
        raise HTTPException(404, "Tool job not found")
    if row.status not in {"DEAD", "FAILED"}:
        raise HTTPException(409, "Only DEAD or FAILED jobs can be retried")
    row.status = "PENDING"
    row.attempts = 0
    row.run_after = datetime.now(timezone.utc).replace(tzinfo=None)
    row.last_error = "Manually retried by administrator"
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(
        IncidentToolAudit(
            incident_id=row.incident_id,
            job_id=row.id,
            actor_user_id=user.id,
            tool_name=row.tool_name,
            decision="ADMIN_RETRY",
            status="QUEUED",
            reason="Administrator manually re-queued a dead-letter job",
            payload_json=row.payload_json,
            result_json="{}",
        )
    )
    db.commit()
    db.refresh(row)
    return _job_response(db, row)


@router.get("/api/admin/tool-audits", response_model=list[IncidentToolAuditResponse])
def list_incident_tool_audits(
    _: Annotated[UserAccount, Depends(require_approval_reviewer)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = db.query(IncidentToolAudit).order_by(IncidentToolAudit.created_at.desc()).limit(limit).all()
    return [
        IncidentToolAuditResponse(
            id=row.id, incidentId=row.incident_id, jobId=row.job_id,
            actor=_user_name(db, row.actor_user_id), toolName=row.tool_name,
            decision=row.decision, status=row.status, reason=row.reason,
            payload=_loads(row.payload_json), result=_loads(row.result_json), createdAt=row.created_at,
        ) for row in rows
    ]


@router.get("/api/admin/exports/incident-ledger")
def download_incident_ledger(_: Annotated[UserAccount, Depends(require_admin)]):
    settings = get_settings()
    path = Path(settings.excel_path)
    if not path.is_absolute():
        path = settings.project_root / path
    if not path.exists():
        raise HTTPException(404, "Incident ledger has not been generated yet")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="nexusops-incident-ledger.xlsx",
    )


@router.get("/api/admin/notifications", response_model=list[IncidentNotificationResponse])
def list_incident_notifications(
    _: Annotated[UserAccount, Depends(require_approval_reviewer)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = db.query(IncidentNotification).order_by(IncidentNotification.created_at.desc()).limit(limit).all()
    return [
        IncidentNotificationResponse(
            id=row.id, incidentId=row.incident_id, channel=row.channel,
            recipient=row.recipient, subject=row.subject, message=row.message,
            status=row.status, providerMessage=row.provider_message, createdAt=row.created_at,
        ) for row in rows
    ]


@router.get("/api/ops/skills")
def list_ops_skills(_: Annotated[UserAccount, Depends(require_incident_operator)]):
    from app.services.ops_skills import OpsSkillLibrary

    return [
        {"name": item.name, "description": item.description, "categories": list(item.categories)}
        for item in OpsSkillLibrary.list_skills()
    ]


@router.get("/api/admin/ops/knowledge-manifest")
def ops_knowledge_manifest(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.entities import KnowledgeChunk, KnowledgeSource

    sources = db.query(KnowledgeSource).order_by(KnowledgeSource.source_key.asc()).all()
    return [
        {
            "id": row.id,
            "sourceKey": row.source_key,
            "name": row.name,
            "url": row.url,
            "origin": "BUILT_IN_RUNBOOK" if row.source_key.startswith("ops:") else "UPLOADED_OR_ADMIN",
            "status": row.status,
            "versionHash": row.version_hash,
            "reviewedAt": row.reviewed_at,
            "updatedAt": row.updated_at,
            "chunkCount": db.query(KnowledgeChunk).filter(KnowledgeChunk.source == row.source_key).count(),
        }
        for row in sources
    ]
