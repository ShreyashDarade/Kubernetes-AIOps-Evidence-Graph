"""
Remediation action models for the AIOps Evidence Graph Platform.
Represents proposed, approved, and executed remediation actions with verification.
"""
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Types of remediation actions."""
    # Pod-level
    RESTART_POD = "restart_pod"
    DELETE_POD = "delete_pod"

    # Deployment-level
    RESTART_DEPLOYMENT = "restart_deployment"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    SCALE_REPLICAS = "scale_replicas"

    # Node-level
    CORDON_NODE = "cordon_node"
    DRAIN_NODE = "drain_node"
    UNCORDON_NODE = "uncordon_node"

    # Configuration
    UPDATE_CONFIGMAP = "update_configmap"
    UPDATE_RESOURCE_LIMITS = "update_resource_limits"
    UPDATE_HPA = "update_hpa"

    # Network
    RESTART_SERVICE = "restart_service"

    # Manual
    ESCALATE_TO_HUMAN = "escalate_to_human"
    CREATE_TICKET = "create_ticket"


class ActionRisk(str, Enum):
    """Risk levels for remediation actions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(str, Enum):
    """Status states for remediation actions."""
    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"


class Environment(str, Enum):
    """Deployment environments."""
    DEV = "dev"
    STAGING = "staging"
    UAT = "uat"
    PROD = "prod"


class RemediationAction(BaseModel):
    """
    A remediation action proposed or executed for an incident.
    
    Actions go through a lifecycle: proposed -> pending_approval -> approved/rejected -> executing -> completed/failed
    """
    id: UUID = Field(default_factory=uuid4, description="Unique action identifier")
    incident_id: UUID = Field(..., description="Associated incident ID")
    hypothesis_id: UUID | None = Field(None, description="Associated hypothesis ID")

    # Idempotency
    idempotency_key: str = Field(
        ...,
        description="Unique key: incident_id + action_type + target + version"
    )

    # Action details
    action_type: ActionType = Field(..., description="Type of remediation action")
    target_resource: str = Field(..., description="Target resource name")
    target_namespace: str = Field(..., description="Target namespace")
    target_cluster: str | None = Field(None, description="Target cluster")

    # Parameters
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters"
    )

    # Risk assessment
    risk_level: ActionRisk = Field(..., description="Risk level")
    blast_radius_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Blast radius score (0-100)"
    )
    affected_replicas: int = Field(0, description="Number of replicas affected")
    environment: Environment = Field(default=Environment.DEV)

    # Status
    status: ActionStatus = Field(default=ActionStatus.PROPOSED)
    status_reason: str | None = Field(None, description="Reason for current status")

    # Approval
    requires_approval: bool = Field(default=True)
    approved_by: str | None = Field(None, description="Approver identifier")
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None

    # Execution
    executed_at: datetime | None = None
    completed_at: datetime | None = None
    execution_result: dict[str, Any] | None = None
    error_message: str | None = None

    # Rollback
    can_rollback: bool = Field(default=False)
    rollback_action_id: UUID | None = None

    # Audit
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(default="system")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174003",
                "incident_id": "123e4567-e89b-12d3-a456-426614174000",
                "hypothesis_id": "123e4567-e89b-12d3-a456-426614174002",
                "idempotency_key": "123e4567_rollback_deployment_api-server_v1",
                "action_type": "rollback_deployment",
                "target_resource": "api-server",
                "target_namespace": "default",
                "risk_level": "medium",
                "blast_radius_score": 25.0,
                "affected_replicas": 3,
                "environment": "prod",
                "status": "pending_approval",
                "requires_approval": True
            }
        }


class VerificationResult(BaseModel):
    """
    Result of verifying a remediation action's effectiveness.
    
    After executing an action, we verify metrics improved and the incident is resolved.
    """
    id: UUID = Field(default_factory=uuid4)
    action_id: UUID = Field(..., description="Associated action ID")
    incident_id: UUID = Field(..., description="Associated incident ID")

    # Outcome
    success: bool = Field(..., description="Whether remediation was successful")
    metrics_improved: bool = Field(..., description="Whether metrics returned to normal")

    # Metrics comparison
    error_rate_before: float | None = None
    error_rate_after: float | None = None
    latency_p99_before: float | None = None
    latency_p99_after: float | None = None
    restart_count_before: int | None = None
    restart_count_after: int | None = None

    # Kubernetes state
    pods_healthy_before: int | None = None
    pods_healthy_after: int | None = None

    # Details
    verification_details: dict[str, Any] = Field(default_factory=dict)
    verification_notes: str | None = None

    # Timing
    verification_started_at: datetime = Field(default_factory=datetime.utcnow)
    verified_at: datetime = Field(default_factory=datetime.utcnow)
    wait_duration_seconds: int = Field(0, description="Time waited before verification")


class BlastRadiusAssessment(BaseModel):
    """Assessment of an action's potential blast radius."""
    action_type: ActionType
    target_resource: str
    target_namespace: str
    environment: Environment

    # Impact metrics
    affected_pods: int = 0
    affected_services: int = 0
    affected_deployments: int = 0
    affected_users_estimate: int | None = None

    # Scoring
    base_score: float = 0.0
    environment_multiplier: float = 1.0
    criticality_multiplier: float = 1.0
    final_score: float = 0.0

    # Recommendations
    is_acceptable: bool = True
    requires_approval: bool = False
    risk_level: ActionRisk = ActionRisk.LOW
    warnings: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    """Request for action approval (e.g., Slack message)."""
    action_id: UUID
    incident_id: UUID
    incident_title: str
    action_type: ActionType
    target_resource: str
    target_namespace: str
    risk_level: ActionRisk
    blast_radius_score: float
    hypothesis_summary: str
    evidence_summary: str
    recommended_by: str = "AIOps Platform"
    approval_deadline: datetime | None = None
    slack_message_ts: str | None = None
    slack_channel: str | None = None


class ApprovalResponse(BaseModel):
    """Response to an approval request."""
    action_id: UUID
    approved: bool
    responder: str
    responded_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str | None = None


class ActionCreate(BaseModel):
    """Schema for creating a remediation action."""
    incident_id: UUID
    hypothesis_id: UUID | None = None
    action_type: ActionType
    target_resource: str
    target_namespace: str
    target_cluster: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    environment: Environment = Environment.DEV


class ActionUpdate(BaseModel):
    """Schema for updating action status."""
    status: ActionStatus | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    execution_result: dict[str, Any] | None = None
    error_message: str | None = None
