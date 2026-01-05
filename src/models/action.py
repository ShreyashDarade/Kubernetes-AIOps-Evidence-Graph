"""
Remediation action models for the AIOps Evidence Graph Platform.
Represents proposed, approved, and executed remediation actions with verification.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


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
    hypothesis_id: Optional[UUID] = Field(None, description="Associated hypothesis ID")
    
    # Idempotency
    idempotency_key: str = Field(
        ..., 
        description="Unique key: incident_id + action_type + target + version"
    )
    
    # Action details
    action_type: ActionType = Field(..., description="Type of remediation action")
    target_resource: str = Field(..., description="Target resource name")
    target_namespace: str = Field(..., description="Target namespace")
    target_cluster: Optional[str] = Field(None, description="Target cluster")
    
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
    status_reason: Optional[str] = Field(None, description="Reason for current status")
    
    # Approval
    requires_approval: bool = Field(default=True)
    approved_by: Optional[str] = Field(None, description="Approver identifier")
    approved_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    
    # Execution
    executed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    execution_result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    
    # Rollback
    can_rollback: bool = Field(default=False)
    rollback_action_id: Optional[UUID] = None
    
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
    error_rate_before: Optional[float] = None
    error_rate_after: Optional[float] = None
    latency_p99_before: Optional[float] = None
    latency_p99_after: Optional[float] = None
    restart_count_before: Optional[int] = None
    restart_count_after: Optional[int] = None
    
    # Kubernetes state
    pods_healthy_before: Optional[int] = None
    pods_healthy_after: Optional[int] = None
    
    # Details
    verification_details: dict[str, Any] = Field(default_factory=dict)
    verification_notes: Optional[str] = None
    
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
    affected_users_estimate: Optional[int] = None
    
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
    approval_deadline: Optional[datetime] = None
    slack_message_ts: Optional[str] = None
    slack_channel: Optional[str] = None


class ApprovalResponse(BaseModel):
    """Response to an approval request."""
    action_id: UUID
    approved: bool
    responder: str
    responded_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None


class ActionCreate(BaseModel):
    """Schema for creating a remediation action."""
    incident_id: UUID
    hypothesis_id: Optional[UUID] = None
    action_type: ActionType
    target_resource: str
    target_namespace: str
    target_cluster: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    environment: Environment = Environment.DEV


class ActionUpdate(BaseModel):
    """Schema for updating action status."""
    status: Optional[ActionStatus] = None
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    execution_result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
