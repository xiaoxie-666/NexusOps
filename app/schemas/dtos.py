from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.core.enums import (
    IncidentCategory,
    IncidentEnvironment,
    IncidentNoteType,
    IncidentSeverity,
    IncidentSource,
    IncidentStatus,
    KnowledgeSourceStatus,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class KnowledgeIngestRequest(ApiModel):
    source: str
    content: str


class KnowledgeIngestResponse(ApiModel):
    source: str
    chunks: int


def authority(role: str) -> dict[str, Any]:
    return {"authority": role}


class AiMessage(ApiModel):
    role: str
    content: str


# ---------------------------------------------------------------------------
# NexusOps phase-1 DTOs
# ---------------------------------------------------------------------------

class IncidentCreateRequest(ApiModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=20_000)
    serviceName: str = Field(min_length=1, max_length=128)
    environment: IncidentEnvironment = IncidentEnvironment.UNKNOWN
    category: IncidentCategory = IncidentCategory.UNKNOWN
    severity: IncidentSeverity = IncidentSeverity.P2
    source: IncidentSource = IncidentSource.MANUAL
    assignee: str = Field(default="unassigned", max_length=128)
    impactScope: str = Field(default="", max_length=8_000)


class IncidentUpdateRequest(ApiModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, min_length=5, max_length=20_000)
    serviceName: Optional[str] = Field(default=None, min_length=1, max_length=128)
    environment: Optional[IncidentEnvironment] = None
    category: Optional[IncidentCategory] = None
    severity: Optional[IncidentSeverity] = None
    assignee: Optional[str] = Field(default=None, max_length=128)
    impactScope: Optional[str] = Field(default=None, max_length=8_000)
    expectedVersion: Optional[int] = Field(default=None, ge=1)


class IncidentTransitionRequest(ApiModel):
    status: IncidentStatus
    reason: str = Field(default="", max_length=4_000)
    expectedVersion: Optional[int] = Field(default=None, ge=1)


class IncidentNoteCreateRequest(ApiModel):
    content: str = Field(min_length=1, max_length=10_000)
    noteType: IncidentNoteType = IncidentNoteType.COMMENT


class IncidentNoteResponse(ApiModel):
    id: int
    incidentId: int
    actorName: str
    noteType: str
    content: str
    createdAt: datetime


class IncidentStatusHistoryResponse(ApiModel):
    id: int
    incidentId: int
    fromStatus: Optional[str] = None
    toStatus: str
    reason: str
    actorName: str
    createdAt: datetime


class IncidentResponse(ApiModel):
    id: int
    incidentKey: str
    title: str
    description: str
    serviceName: str
    environment: str
    category: str
    severity: str
    status: str
    source: str
    reporterUsername: str
    assignee: str
    impactScope: str
    resolutionSummary: str
    version: int
    createdAt: datetime
    updatedAt: datetime
    acknowledgedAt: Optional[datetime] = None
    resolvedAt: Optional[datetime] = None
    closedAt: Optional[datetime] = None


class IncidentDetailResponse(IncidentResponse):
    notes: list[IncidentNoteResponse] = Field(default_factory=list)
    history: list[IncidentStatusHistoryResponse] = Field(default_factory=list)


class IncidentListResponse(ApiModel):
    items: list[IncidentResponse]
    total: int
    page: int
    pageSize: int


class KnowledgeSourceCreateRequest(ApiModel):
    sourceKey: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=2, max_length=200)
    url: Optional[AnyHttpUrl] = None
    versionHash: str = Field(min_length=8, max_length=64)
    status: KnowledgeSourceStatus = KnowledgeSourceStatus.ACTIVE
    reviewedAt: Optional[datetime] = None


class KnowledgeSourceStatusRequest(ApiModel):
    status: KnowledgeSourceStatus


class KnowledgeSourceResponse(ApiModel):
    id: int
    sourceKey: str
    name: str
    url: Optional[str] = None
    versionHash: str
    status: str
    reviewedAt: Optional[datetime] = None
    createdAt: datetime
    updatedAt: datetime



# ---------------------------------------------------------------------------
# NexusOps phase-2 Agent analysis DTOs
# ---------------------------------------------------------------------------

class IncidentAnalysisResponse(ApiModel):
    runId: str
    incidentId: int
    incidentKey: str
    status: str
    summary: str
    understanding: dict[str, Any] = Field(default_factory=dict)
    impactAssessment: dict[str, Any] = Field(default_factory=dict)
    retrievedContext: dict[str, Any] = Field(default_factory=dict)
    resolutionPlan: dict[str, Any] = Field(default_factory=dict)
    operationGuard: dict[str, Any] = Field(default_factory=dict)
    agentTrace: list[dict[str, Any]] = Field(default_factory=list)
    modelCalls: list[dict[str, Any]] = Field(default_factory=list)
    createdAt: datetime


class IncidentAnalysisRunSummary(ApiModel):
    runId: str
    incidentId: int
    incidentKey: str
    status: str
    summary: str
    createdAt: datetime


# ---------------------------------------------------------------------------
# Final NexusOps integrations, approvals and tool execution DTOs
# ---------------------------------------------------------------------------

class ApprovalCreateRequest(ApiModel):
    actionKey: str = Field(min_length=3, max_length=64)
    target: str = Field(min_length=1, max_length=256)
    justification: str = Field(min_length=8, max_length=8000)
    details: dict[str, Any] = Field(default_factory=dict)


class ApprovalReviewRequest(ApiModel):
    approved: bool
    reviewNote: str = Field(default="", max_length=4000)


class IncidentToolEnqueueRequest(ApiModel):
    toolName: str = Field(min_length=3, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotencyKey: Optional[str] = Field(default=None, max_length=128)


class AlertEventResponse(ApiModel):
    id: int
    source: str
    externalId: Optional[str] = None
    fingerprint: str
    alertName: str
    serviceName: str
    environment: str
    severity: str
    status: str
    summary: str
    description: str
    incidentId: Optional[int] = None
    occurrenceCount: int
    firstSeenAt: datetime
    lastSeenAt: datetime
    createdAt: datetime


class IncidentApprovalResponse(ApiModel):
    id: int
    incidentId: int
    requestedBy: str
    actionKey: str
    target: str
    justification: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: str
    reviewedBy: Optional[str] = None
    reviewNote: str = ""
    createdAt: datetime
    reviewedAt: Optional[datetime] = None
    executionMode: str = "MANUAL_ONLY"


class IncidentToolJobResponse(ApiModel):
    id: int
    incidentId: int
    createdBy: str
    toolName: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotencyKey: str
    status: str
    attempts: int
    maxAttempts: int
    runAfter: datetime
    lastError: str
    result: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime
    updatedAt: datetime


class IncidentToolAuditResponse(ApiModel):
    id: int
    incidentId: int
    jobId: Optional[int] = None
    actor: Optional[str] = None
    toolName: str
    decision: str
    status: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime


class IncidentNotificationResponse(ApiModel):
    id: int
    incidentId: int
    channel: str
    recipient: str
    subject: str
    message: str
    status: str
    providerMessage: str
    createdAt: datetime
