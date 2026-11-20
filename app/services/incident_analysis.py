from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.events import (
    AgentArtifact,
    AgentEvent,
    AgentEventType,
    AgentTask,
    CollaborationBlackboard,
    PRIORITY_ORDER,
    TaskPriority,
)
from app.agents.ops_agents import (
    ImpactAssessmentAgent,
    IncidentContextAgent,
    IncidentUnderstandingAgent,
    OperationGuardAgent,
    OpsAgentServices,
    ResolutionAgent,
)
from app.agents.registry import AgentCapability, AgentRegistry
from app.core.config import Settings
from app.models.entities import IncidentAgentRun, IncidentRecord, UserAccount
from app.schemas.dtos import IncidentAnalysisResponse
from app.services.knowledge import KnowledgeService
from app.services.incident_context_memory import IncidentContextMemory
from app.services.model_router import ModelRouter


logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """Incident coordinator that owns the shared Blackboard and acceptance gate."""

    name = "CoordinatorAgent"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.max_rounds = max(1, int(settings.incident_analysis_max_rounds))
        self.max_claims_per_round = max(1, int(settings.agent_max_claims_per_round))
        self.max_claims_per_agent = max(1, int(settings.agent_max_claims_per_agent))
        self.min_confidence = float(settings.incident_analysis_min_confidence)

    def root_task(self, incident: IncidentRecord) -> AgentTask:
        return AgentTask(
            id="task:incident-understanding",
            title="Understand reported incident",
            description=f"{incident.incident_key}: {incident.title}\n{incident.description}",
            priority=TaskPriority.CRITICAL if incident.severity in {"P0", "P1"} else TaskPriority.HIGH,
            required_capabilities=frozenset({AgentCapability.INCIDENT_UNDERSTANDING.value}),
            created_by=self.name,
            metadata={"kind": "understanding"},
        )

    def run(self, incident: IncidentRecord, registry: AgentRegistry, board: CollaborationBlackboard) -> CollaborationBlackboard:
        root = self.root_task(incident)
        board = board.add_task(root).append_event(
            AgentEvent(type=AgentEventType.TASK_CREATED, actor=self.name, task_id=root.id, message=root.title)
        )
        board = board.append_event(
            AgentEvent(type=AgentEventType.TURN_STARTED, actor=self.name, message="incident analysis published to shared CollaborationBlackboard")
        )
        claim_counts: dict[str, int] = {}

        for round_number in range(1, self.max_rounds + 1):
            board = board.append_event(
                AgentEvent(type=AgentEventType.ROUND_STARTED, actor=self.name, message=f"round={round_number}", metadata={"round": round_number})
            )
            board = self._derive_tasks(board)
            if board.final_artifact_id:
                return board
            candidates = self._claim_candidates(board, registry, claim_counts)
            if not candidates:
                break

            for task, candidate in candidates:
                current_task = board.tasks.get(task.id, task)
                board = board.update_task(current_task.claim(candidate.agent.profile.name)).append_event(
                    AgentEvent(
                        type=AgentEventType.TASK_CLAIMED,
                        actor=candidate.agent.profile.name,
                        task_id=current_task.id,
                        message=candidate.decision.reason,
                        metadata={"confidence": candidate.decision.confidence},
                    )
                )
                try:
                    result = candidate.agent.act(current_task, board)
                    board = board.apply_turn_result(current_task, candidate.agent.profile.name, result)
                except Exception as exc:
                    board = board.update_task(current_task.reopen()).append_event(
                        AgentEvent(
                            type=AgentEventType.AGENT_FAILED,
                            actor=candidate.agent.profile.name,
                            task_id=current_task.id,
                            message=f"{type(exc).__name__}: {str(exc)[:240]}",
                        )
                    )
                claim_counts[candidate.agent.profile.name] = claim_counts.get(candidate.agent.profile.name, 0) + 1

            board = self._derive_tasks(board)
            board = self._try_accept_final(board)
            if board.final_artifact_id:
                return board

        return board.append_event(
            AgentEvent(
                type=AgentEventType.BUDGET_EXHAUSTED,
                actor=self.name,
                message="incident analysis budget exhausted before all specialist artifacts were accepted",
            )
        )

    def _derive_tasks(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        understanding = board.latest_artifact("incident_understanding")
        impact = board.latest_artifact("impact_assessment")
        context = board.latest_artifact("incident_context")
        resolution = board.latest_artifact("resolution_plan")

        if understanding:
            board = self._ensure_task(
                board,
                AgentTask(
                    id="task:impact-assessment",
                    title="Assess incident impact",
                    description="Estimate severity and blast radius.",
                    priority=TaskPriority.HIGH,
                    required_capabilities=frozenset({AgentCapability.IMPACT_ASSESSMENT.value}),
                    created_by=self.name,
                    metadata={"kind": "impact"},
                ),
            )
            board = self._ensure_task(
                board,
                AgentTask(
                    id="task:retrieve-context",
                    title="Retrieve operational context",
                    description="Find relevant runbooks and historical knowledge.",
                    priority=TaskPriority.HIGH,
                    required_capabilities=frozenset({AgentCapability.INCIDENT_CONTEXT.value}),
                    created_by=self.name,
                    metadata={"kind": "context"},
                ),
            )
        if understanding and impact and context:
            board = self._ensure_task(
                board,
                AgentTask(
                    id="task:resolution-plan",
                    title="Generate diagnosis and resolution plan",
                    description="Build testable hypotheses and prioritized checks.",
                    priority=TaskPriority.HIGH,
                    required_capabilities=frozenset({AgentCapability.RESOLUTION.value}),
                    created_by=self.name,
                    metadata={"kind": "resolution"},
                ),
            )
        if resolution:
            board = self._ensure_task(
                board,
                AgentTask(
                    id="task:operation-guard",
                    title="Review operational safety",
                    description="Classify actions by reversibility and production risk.",
                    priority=TaskPriority.CRITICAL,
                    required_capabilities=frozenset({AgentCapability.OPERATION_GUARD.value}),
                    created_by=self.name,
                    metadata={"kind": "guard"},
                ),
            )
        return board

    def _ensure_task(self, board: CollaborationBlackboard, task: AgentTask) -> CollaborationBlackboard:
        if task.id in board.tasks or any(item.status.value == "CLOSED" and item.id == task.id for item in board.tasks.values()):
            return board
        return board.add_task(task).append_event(
            AgentEvent(type=AgentEventType.TASK_CREATED, actor=self.name, task_id=task.id, message=task.title)
        )

    def _claim_candidates(self, board: CollaborationBlackboard, registry: AgentRegistry, claim_counts: dict[str, int]):
        choices = []
        for task in board.open_tasks():
            for candidate in registry.candidate_decisions_for(task, board):
                if claim_counts.get(candidate.agent.profile.name, 0) >= self.max_claims_per_agent:
                    continue
                choices.append((task, candidate))
        choices.sort(
            key=lambda pair: (
                PRIORITY_ORDER[pair[0].priority],
                pair[1].decision.confidence,
                pair[1].agent.profile.name,
            ),
            reverse=True,
        )
        selected = []
        selected_agents: set[str] = set()
        for task, candidate in choices:
            name = candidate.agent.profile.name
            if name in selected_agents:
                continue
            selected.append((task, candidate))
            selected_agents.add(name)
            if len(selected) >= self.max_claims_per_round:
                break
        return selected

    def _try_accept_final(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        if board.final_artifact_id:
            return board
        understanding = board.latest_artifact("incident_understanding")
        impact = board.latest_artifact("impact_assessment")
        context = board.latest_artifact("incident_context")
        resolution = board.latest_artifact("resolution_plan")
        guard = board.latest_artifact("operation_guard")
        if not all((understanding, impact, context, resolution, guard)):
            return board
        confidence = min(item.confidence for item in (understanding, impact, context, resolution, guard))
        if confidence < self.min_confidence:
            return board.append_event(
                AgentEvent(type=AgentEventType.BUDGET_EXHAUSTED, actor=self.name, message="final confidence below acceptance threshold", metadata={"confidence": confidence})
            )
        artifact = AgentArtifact(
            id=f"CoordinatorAgent:incident-analysis:{uuid.uuid4().hex[:10]}",
            owner=self.name,
            kind="incident_analysis",
            confidence=confidence,
            payload={
                "understanding": understanding.payload,
                "impactAssessment": impact.payload,
                "retrievedContext": context.payload,
                "resolutionPlan": resolution.payload,
                "operationGuard": guard.payload,
            },
            metadata={"acceptance": "specialist artifacts complete and operation guard reviewed"},
        )
        board = board.add_artifact(artifact)
        return board.accept_final(artifact.id, self.name, "Accepted after impact, context, resolution and operation-safety review")


class IncidentAgentRuntimeService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def analyze(self, incident_id: int, actor: UserAccount) -> IncidentAnalysisResponse:
        incident = self.db.query(IncidentRecord).filter(IncidentRecord.id == incident_id).first()
        if incident is None:
            raise HTTPException(404, "Incident not found")
        if not {"ROLE_ADMIN", "ROLE_ENGINEER", "ROLE_ONCALL"}.intersection(set(actor.roles)):
            raise HTTPException(403, "Incident operator role required")

        memory = IncidentContextMemory(self.db, self.settings)
        # MySQL remains authoritative. A Redis outage must not fail an incident run.
        try:
            previous_context = memory.load(incident)
        except Exception as exc:
            logger.warning("Incident context restore skipped: %s", type(exc).__name__)
            self.db.rollback()
            previous_context = None
        model_router = ModelRouter(self.settings)
        knowledge = KnowledgeService(self.db, self.settings)
        services = OpsAgentServices(
            db=self.db,
            settings=self.settings,
            incident=incident,
            actor=actor,
            knowledge=knowledge,
            model_router=model_router,
            prior_context=previous_context,
        )
        agents = [
            IncidentUnderstandingAgent(services),
            ImpactAssessmentAgent(services),
            IncidentContextAgent(services),
            ResolutionAgent(services),
            OperationGuardAgent(services),
        ]
        registry = AgentRegistry(agents)
        board = CollaborationBlackboard(
            turn_id=uuid.uuid4().hex,
            user_id=actor.id,
            session_id=f"incident:{incident.id}",
            user_input=f"{incident.title}\n{incident.description}\n{incident.impact_scope}",
            model_input=f"{incident.title}\n{incident.description}\n{incident.impact_scope}",
        )
        board = CoordinatorAgent(self.settings).run(incident, registry, board)
        final = board.accepted_artifact() or board.latest_artifact("incident_analysis")
        payload = final.payload if final else {}
        understanding = payload.get("understanding") or _artifact_payload(board, "incident_understanding")
        impact = payload.get("impactAssessment") or _artifact_payload(board, "impact_assessment")
        context = payload.get("retrievedContext") or _artifact_payload(board, "incident_context")
        resolution = payload.get("resolutionPlan") or _artifact_payload(board, "resolution_plan")
        guard = payload.get("operationGuard") or _artifact_payload(board, "operation_guard")
        status = "COMPLETED" if board.final_artifact_id else "DEGRADED"
        summary = _build_summary(incident, impact, resolution, guard, status)
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        run_id = uuid.uuid4().hex
        row = IncidentAgentRun(
            run_public_id=run_id,
            incident_id=incident.id,
            initiated_by_user_id=actor.id,
            status=status,
            summary=summary,
            understanding_json=_json(understanding),
            impact_json=_json(impact),
            context_json=_json(context),
            resolution_json=_json(resolution),
            guard_json=_json(guard),
            agent_events_json=_json(board.events),
            agent_tasks_json=_json(list(board.tasks.values())),
            model_calls_json=_json(model_router.calls),
            created_at=created_at,
            completed_at=created_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        try:
            memory.save(incident, run_id, summary, understanding, impact, context)
        except Exception as exc:
            # Preserve the persisted analysis even if optional snapshot storage fails.
            logger.exception("Incident context snapshot persistence failed: %s", exc)
            self.db.rollback()
        return self._to_response(row, incident.incident_key)

    def latest(self, incident_id: int, actor: UserAccount) -> IncidentAnalysisResponse:
        incident = self.db.query(IncidentRecord).filter(IncidentRecord.id == incident_id).first()
        if incident is None:
            raise HTTPException(404, "Incident not found")
        if not {"ROLE_ADMIN", "ROLE_ENGINEER", "ROLE_ONCALL"}.intersection(set(actor.roles)):
            raise HTTPException(403, "Incident operator role required")
        row = (
            self.db.query(IncidentAgentRun)
            .filter(IncidentAgentRun.incident_id == incident_id)
            .order_by(IncidentAgentRun.created_at.desc(), IncidentAgentRun.id.desc())
            .first()
        )
        if row is None:
            raise HTTPException(404, "No analysis run found for this incident")
        return self._to_response(row, incident.incident_key)

    @staticmethod
    def _to_response(row: IncidentAgentRun, incident_key: str) -> IncidentAnalysisResponse:
        return IncidentAnalysisResponse(
            runId=row.run_public_id,
            incidentId=row.incident_id,
            incidentKey=incident_key,
            status=row.status,
            summary=row.summary,
            understanding=_loads(row.understanding_json),
            impactAssessment=_loads(row.impact_json),
            retrievedContext=_loads(row.context_json),
            resolutionPlan=_loads(row.resolution_json),
            operationGuard=_loads(row.guard_json),
            agentTrace=_loads(row.agent_events_json),
            modelCalls=_loads(row.model_calls_json),
            createdAt=row.created_at,
        )


def _artifact_payload(board: CollaborationBlackboard, kind: str) -> dict[str, Any]:
    artifact = board.latest_artifact(kind)
    return artifact.payload if artifact else {}


def _build_summary(incident: IncidentRecord, impact: dict, resolution: dict, guard: dict, status: str) -> str:
    severity = impact.get("suggestedSeverity", incident.severity)
    hypotheses = len(resolution.get("rootCauseHypotheses", []))
    actions = len(resolution.get("recommendedActions", []))
    guard_decision = guard.get("decision", "NOT_REVIEWED")
    if status != "COMPLETED":
        return f"{incident.incident_key} 的多 Agent 研判未完整完成，当前结果仅供参考，需要人工补充排查。"
    return (
        f"{incident.incident_key}（{incident.service_name}）研判完成：建议优先级 {severity}，"
        f"形成 {hypotheses} 个待验证根因假设和 {actions} 项排查建议；操作安全审查结果为 {guard_decision}。"
        "根因尚未被证实，所有生产变更均需人工审批，系统未执行任何命令。"
    )


def _json(value: Any) -> str:
    return json.dumps(_to_jsonable(value), ensure_ascii=False, default=str)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_to_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    return value


def _loads(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
