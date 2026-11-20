from __future__ import annotations

import json
from typing import Any

from app.core.bootstrap import create_schema
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import AlertEvent, IncidentApproval, IncidentNote, IncidentRecord, IncidentStatusHistory, IncidentToolAudit
from app.services.knowledge import KnowledgeService

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - dependency is installed in release image
    raise RuntimeError("Install the mcp dependency from requirements.txt to run the NexusOps MCP server") from exc

mcp = FastMCP("nexusops-operations")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def _limit(value: int, maximum: int = 100) -> int:
    return max(1, min(int(value), maximum))


@mcp.tool()
def nexusops_list_incidents(status: str = "", severity: str = "", limit: int = 20) -> str:
    """List recent NexusOps incidents. Read-only; does not change incident state."""
    create_schema()
    db = SessionLocal()
    try:
        query = db.query(IncidentRecord)
        if status:
            query = query.filter(IncidentRecord.status == status.upper())
        if severity:
            query = query.filter(IncidentRecord.severity == severity.upper())
        rows = query.order_by(IncidentRecord.created_at.desc()).limit(_limit(limit)).all()
        return _dump([
            {
                "id": row.id, "incidentKey": row.incident_key, "title": row.title,
                "serviceName": row.service_name, "environment": row.environment,
                "category": row.category, "severity": row.severity, "status": row.status,
                "assignee": row.assignee, "createdAt": row.created_at,
            }
            for row in rows
        ])
    finally:
        db.close()


@mcp.tool()
def nexusops_get_incident(incident_id: int) -> str:
    """Get one incident with its recent notes and status history. Read-only."""
    create_schema()
    db = SessionLocal()
    try:
        row = db.get(IncidentRecord, incident_id)
        if row is None:
            return _dump({"error": "incident_not_found", "incidentId": incident_id})
        notes = db.query(IncidentNote).filter(IncidentNote.incident_id == incident_id).order_by(IncidentNote.created_at.desc()).limit(30).all()
        history = db.query(IncidentStatusHistory).filter(IncidentStatusHistory.incident_id == incident_id).order_by(IncidentStatusHistory.created_at.desc()).limit(30).all()
        return _dump({
            "id": row.id, "incidentKey": row.incident_key, "title": row.title,
            "description": row.description, "serviceName": row.service_name,
            "environment": row.environment, "category": row.category,
            "severity": row.severity, "status": row.status, "assignee": row.assignee,
            "impactScope": row.impact_scope, "createdAt": row.created_at,
            "notes": [{"actor": item.actor_name, "type": item.note_type, "content": item.content, "createdAt": item.created_at} for item in notes],
            "history": [{"from": item.from_status, "to": item.to_status, "reason": item.reason, "actor": item.actor_name, "createdAt": item.created_at} for item in history],
        })
    finally:
        db.close()


@mcp.tool()
def nexusops_list_alerts(status: str = "", limit: int = 30) -> str:
    """List recent monitoring alerts and linked incident IDs. Read-only."""
    create_schema()
    db = SessionLocal()
    try:
        query = db.query(AlertEvent)
        if status:
            query = query.filter(AlertEvent.status == status.upper())
        rows = query.order_by(AlertEvent.last_seen_at.desc()).limit(_limit(limit)).all()
        return _dump([
            {
                "id": row.id, "source": row.source, "alertName": row.alert_name,
                "serviceName": row.service_name, "environment": row.environment,
                "severity": row.severity, "status": row.status,
                "summary": row.summary, "incidentId": row.incident_id,
                "occurrenceCount": row.occurrence_count, "lastSeenAt": row.last_seen_at,
            }
            for row in rows
        ])
    finally:
        db.close()


@mcp.tool()
def nexusops_search_knowledge(query: str, top_k: int = 4) -> str:
    """Search active operational runbooks with hybrid RAG. Read-only."""
    create_schema()
    db = SessionLocal()
    try:
        service = KnowledgeService(db, get_settings())
        rows = service.retrieve(query, top_k=_limit(top_k, 12))
        return _dump([
            {"source": row.source, "chunkId": row.chunk_id, "score": row.score, "content": row.content}
            for row in rows
        ])
    finally:
        db.close()


@mcp.tool()
def nexusops_list_approvals(status: str = "PENDING", limit: int = 30) -> str:
    """List human approval records. Read-only; approval decisions are made in the NexusOps workbench."""
    create_schema()
    db = SessionLocal()
    try:
        query = db.query(IncidentApproval)
        if status:
            query = query.filter(IncidentApproval.status == status.upper())
        rows = query.order_by(IncidentApproval.created_at.desc()).limit(_limit(limit)).all()
        return _dump([
            {
                "id": row.id, "incidentId": row.incident_id, "actionKey": row.action_key,
                "target": row.target, "justification": row.justification,
                "status": row.status, "requestedByUserId": row.requested_by_user_id,
                "reviewedByUserId": row.reviewed_by_user_id,
                "createdAt": row.created_at, "reviewedAt": row.reviewed_at,
                "executionMode": "MANUAL_ONLY",
            }
            for row in rows
        ])
    finally:
        db.close()


@mcp.tool()
def nexusops_list_tool_audits(incident_id: int = 0, limit: int = 50) -> str:
    """List tool authorization and execution audit records. Read-only."""
    create_schema()
    db = SessionLocal()
    try:
        query = db.query(IncidentToolAudit)
        if incident_id > 0:
            query = query.filter(IncidentToolAudit.incident_id == incident_id)
        rows = query.order_by(IncidentToolAudit.created_at.desc()).limit(_limit(limit)).all()
        return _dump([
            {
                "id": row.id, "incidentId": row.incident_id, "jobId": row.job_id,
                "toolName": row.tool_name, "decision": row.decision,
                "status": row.status, "reason": row.reason, "createdAt": row.created_at,
            }
            for row in rows
        ])
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
