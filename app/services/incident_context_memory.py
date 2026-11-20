"""Incident-scoped context memory: MySQL is authoritative; Redis is an optional TTL cache.

Never treat old model conclusions as established facts. Cache entries are validated
against current incident fields and active Runbook versions before reuse.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import IncidentContextSnapshot, IncidentRecord, KnowledgeSource

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _clip(value: Any, maximum: int) -> str:
    s = str(value or "").strip()
    return s if len(s) <= maximum else s[: max(0, maximum - 1)] + "…"


def estimated_tokens(value: Any) -> int:
    """Conservative *estimate*, not a tokenizer-specific token count."""
    s = value if isinstance(value, str) else _json(value)
    # Chinese ideographs and other non-ASCII chars often use more tokens.
    n_nonascii = sum(ord(ch) > 127 for ch in s)
    return n_nonascii + (len(s) - n_nonascii + 2) // 3


def bound_resolution_context(payload: dict[str, Any], budget: int) -> tuple[dict[str, Any], dict[str, int]]:
    """Bound an incident prompt without losing evidence source/chunk attribution."""
    budget = max(384, int(budget))
    result = json.loads(_json(payload))  # never mutate caller-owned objects
    before = estimated_tokens(result)
    if before <= budget:
        return result, {"estimatedBefore": before, "estimatedAfter": before, "budget": budget}

    # Historical context is less important than current incident facts and citations.
    if "previousContext" in result:
        previous = result["previousContext"]
        if isinstance(previous, dict):
            previous["summary"] = _clip(previous.get("summary"), 160)
            previous["keyFacts"] = [_clip(x, 100) for x in previous.get("keyFacts", [])[:3]]
            previous["sources"] = list(previous.get("sources", []))[:3]

    for limit in (320, 220, 140, 90, 48, 0):
        for item in result.get("evidence", []):
            item["excerpt"] = _clip(item.get("excerpt"), limit)
        if estimated_tokens(result) <= budget:
            break

    for field in ("symptomSummary", "title"):
        value = result.get("incident", {})
        if field in value:
            value[field] = _clip(value[field], 240 if field == "symptomSummary" else 100)
    for field in ("impactScope",):
        value = result.get("impact", {})
        if field in value:
            value[field] = _clip(value[field], 140)
    if estimated_tokens(result) > budget:
        result.pop("previousContext", None)
    if estimated_tokens(result) > budget:
        result.get("impact", {}).pop("assessmentReasons", None)
    while estimated_tokens(result) > budget and len(result.get("evidence", [])) > 1:
        result["evidence"].pop()  # first/best evidence is preserved
    # Constant fields might exceed a tiny user-specified budget; never discard
    # the incident key or first evidence reference merely to reach an artificial bound.
    after = estimated_tokens(result)
    return result, {"estimatedBefore": before, "estimatedAfter": after, "budget": budget}


class IncidentContextMemory:
    def __init__(self, db: Session, settings: Settings, redis_client: Any = None):
        self.db = db
        self.settings = settings
        self._redis_client = redis_client
        self._redis_initialized = redis_client is not None

    def _redis(self):
        if not self.settings.incident_context_cache_enabled:
            return None
        if not self._redis_initialized:
            self._redis_initialized = True
            try:
                import redis
                self._redis_client = redis.Redis.from_url(
                    self.settings.redis_url,
                    socket_connect_timeout=self.settings.redis_socket_timeout_seconds,
                    socket_timeout=self.settings.redis_socket_timeout_seconds,
                    decode_responses=True,
                )
            except Exception as exc:
                logger.warning("Incident context Redis unavailable: %s", type(exc).__name__)
                self._redis_client = None
        return self._redis_client

    @staticmethod
    def _cache_key(incident_id: int) -> str:
        return f"nexusops:incident-context:{int(incident_id)}"

    def incident_fingerprint(self, incident: IncidentRecord) -> str:
        return _digest({
            "id": incident.id, "version": incident.version,
            "title": incident.title, "description": incident.description,
            "service": incident.service_name, "status": incident.status,
            "category": incident.category, "severity": incident.severity,
            "environment": incident.environment, "impactScope": incident.impact_scope,
        })

    def knowledge_fingerprint(self) -> str:
        sources = (
            self.db.query(KnowledgeSource)
            .order_by(KnowledgeSource.source_key.asc())
            .all()
        )
        return _digest([(x.source_key, x.status, x.version_hash, str(x.reviewed_at)) for x in sources])

    def _read_cache(self, incident_id: int) -> dict[str, Any] | None:
        client = self._redis()
        if client is None:
            return None
        try:
            value = client.get(self._cache_key(incident_id))
            return json.loads(value) if value else None
        except Exception as exc:
            logger.warning("Incident context Redis read skipped: %s", type(exc).__name__)
            return None

    def _write_cache(self, incident_id: int, data: dict[str, Any]) -> None:
        client = self._redis()
        if client is None:
            return
        try:
            client.setex(
                self._cache_key(incident_id),
                max(30, int(self.settings.incident_context_cache_ttl_seconds)),
                _json(data),
            )
        except Exception as exc:
            logger.warning("Incident context Redis write skipped: %s", type(exc).__name__)

    def invalidate(self, incident_id: int) -> None:
        client = self._redis()
        if client is not None:
            try:
                client.delete(self._cache_key(incident_id))
            except Exception as exc:
                logger.warning("Incident context Redis invalidation skipped: %s", type(exc).__name__)

    @staticmethod
    def _to_context(row: IncidentContextSnapshot) -> dict[str, Any]:
        return {
            "version": row.version,
            "snapshotId": row.id,
            "runId": row.run_public_id,
            "summary": row.summary,
            "keyFacts": json.loads(row.key_facts_json or "[]"),
            "sources": json.loads(row.evidence_sources_json or "[]"),
            "incidentFingerprint": row.incident_fingerprint,
            "knowledgeFingerprint": row.knowledge_fingerprint,
        }

    def load(self, incident: IncidentRecord) -> dict[str, Any] | None:
        incident_hash = self.incident_fingerprint(incident)
        knowledge_hash = self.knowledge_fingerprint()
        cached = self._read_cache(incident.id)
        if cached and (cached.get("incidentFingerprint"), cached.get("knowledgeFingerprint")) == (incident_hash, knowledge_hash):
            return cached
        if cached:
            self.invalidate(incident.id)
        row = (
            self.db.query(IncidentContextSnapshot)
            .filter(
                IncidentContextSnapshot.incident_id == incident.id,
                IncidentContextSnapshot.incident_fingerprint == incident_hash,
                IncidentContextSnapshot.knowledge_fingerprint == knowledge_hash,
            )
            .order_by(IncidentContextSnapshot.version.desc())
            .first()
        )
        if row is None:
            return None
        restored = self._to_context(row)
        self._write_cache(incident.id, restored)
        return restored

    def versions(self, incident_id: int) -> list[dict[str, Any]]:
        return [{"version": row.version, "runId": row.run_public_id, "createdAt": row.created_at.isoformat()}
                for row in self.db.query(IncidentContextSnapshot)
                .filter(IncidentContextSnapshot.incident_id == incident_id)
                .order_by(IncidentContextSnapshot.version.desc()).all()]

    def restore_version(self, incident: IncidentRecord, version: int) -> dict[str, Any]:
        """Restore a compatible historical version into Redis, reject stale versions."""
        row = (
            self.db.query(IncidentContextSnapshot)
            .filter(IncidentContextSnapshot.incident_id == incident.id,
                    IncidentContextSnapshot.version == int(version))
            .first()
        )
        if row is None:
            raise ValueError("Snapshot version not found")
        if row.incident_fingerprint != self.incident_fingerprint(incident) or row.knowledge_fingerprint != self.knowledge_fingerprint():
            raise ValueError("Snapshot is stale; incident or Runbook revision changed")
        context = self._to_context(row)
        self._write_cache(incident.id, context)
        return context

    def save(self, incident: IncidentRecord, run_id: str, summary: str,
             understanding: dict[str, Any], impact: dict[str, Any], context: dict[str, Any]) -> IncidentContextSnapshot:
        existing = (self.db.query(IncidentContextSnapshot)
                    .filter(IncidentContextSnapshot.run_public_id == run_id).first())
        if existing:
            return existing
        # Serialize per-incident snapshot version creation under a MySQL row lock.
        self.db.query(IncidentRecord).filter(IncidentRecord.id == incident.id).with_for_update().first()
        latest_version = (self.db.query(func.max(IncidentContextSnapshot.version))
                          .filter(IncidentContextSnapshot.incident_id == incident.id).scalar()) or 0
        facts = [
            _clip(understanding.get("symptomSummary"), 240),
            _clip(impact.get("impactScope"), 160),
            _clip("priority: " + str(impact.get("suggestedSeverity") or incident.severity), 60),
        ]
        refs = [{"source": _clip(x.get("source"), 160), "chunkId": str(x.get("chunkId", ""))}
                for x in context.get("evidence", [])[:4] if x.get("source")]
        row = IncidentContextSnapshot(
            incident_id=incident.id,
            run_public_id=run_id,
            version=latest_version + 1,
            incident_fingerprint=self.incident_fingerprint(incident),
            knowledge_fingerprint=self.knowledge_fingerprint(),
            summary=_clip(summary, self.settings.incident_context_summary_max_chars),
            key_facts_json=_json([x for x in facts if x]),
            evidence_sources_json=_json(refs),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        self._write_cache(incident.id, self._to_context(row))
        return row
