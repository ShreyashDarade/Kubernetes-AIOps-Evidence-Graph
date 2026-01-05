"""
Evidence models for the AIOps Evidence Graph Platform.
Represents collected evidence from Kubernetes, logs, metrics, and deployments.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class EvidenceType(str, Enum):
    """Types of evidence that can be collected."""
    # Kubernetes resources
    KUBERNETES_POD = "kubernetes_pod"
    KUBERNETES_DEPLOYMENT = "kubernetes_deployment"
    KUBERNETES_REPLICASET = "kubernetes_replicaset"
    KUBERNETES_EVENT = "kubernetes_event"
    KUBERNETES_NODE = "kubernetes_node"
    KUBERNETES_SERVICE = "kubernetes_service"
    KUBERNETES_CONFIGMAP = "kubernetes_configmap"
    KUBERNETES_HPA = "kubernetes_hpa"
    KUBERNETES_PVC = "kubernetes_pvc"
    
    # Signals
    LOG_SIGNAL = "log_signal"
    METRIC_SIGNAL = "metric_signal"
    
    # Changes
    DEPLOY_CHANGE = "deploy_change"
    CONFIG_CHANGE = "config_change"
    IMAGE_CHANGE = "image_change"
    
    # Dependencies
    DEPENDENCY_STATE = "dependency_state"
    NETWORK_TOPOLOGY = "network_topology"


class EvidenceSource(str, Enum):
    """Sources from which evidence is collected."""
    KUBERNETES_API = "kubernetes_api"
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    ARGOCD = "argocd"
    HELM = "helm"
    GIT = "git"
    KUBE_STATE_METRICS = "kube_state_metrics"


class LogLevel(str, Enum):
    """Log severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


class Evidence(BaseModel):
    """
    Evidence collected during incident investigation.
    
    Each piece of evidence is linked to an incident and contains
    raw data from various sources that help determine root cause.
    """
    id: UUID = Field(default_factory=uuid4, description="Unique evidence identifier")
    incident_id: UUID = Field(..., description="Associated incident ID")
    evidence_type: EvidenceType = Field(..., description="Type of evidence")
    source: EvidenceSource = Field(..., description="Source system")
    
    # Entity identification
    entity_name: str = Field(..., description="Name of the entity (pod, deployment, etc.)")
    entity_namespace: str = Field(..., description="Kubernetes namespace")
    entity_uid: Optional[str] = Field(None, description="Kubernetes UID if applicable")
    
    # Evidence data
    data: dict[str, Any] = Field(..., description="Raw evidence payload")
    summary: Optional[str] = Field(None, description="Human-readable summary")
    
    # Relevance scoring
    signal_strength: float = Field(
        default=0.5, 
        ge=0.0, 
        le=1.0, 
        description="Relevance score (0-1)"
    )
    is_anomaly: bool = Field(default=False, description="Whether this is anomalous")
    
    # Time context
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    time_window_start: Optional[datetime] = Field(None, description="Evidence time window start")
    time_window_end: Optional[datetime] = Field(None, description="Evidence time window end")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174001",
                "incident_id": "123e4567-e89b-12d3-a456-426614174000",
                "evidence_type": "kubernetes_pod",
                "source": "kubernetes_api",
                "entity_name": "api-server-7d4f5b6c8-xyz",
                "entity_namespace": "default",
                "data": {
                    "status": "CrashLoopBackOff",
                    "restarts": 5,
                    "last_termination_reason": "Error",
                    "exit_code": 1
                },
                "signal_strength": 0.9
            }
        }


class GraphEntity(BaseModel):
    """Entity to be stored in Neo4j graph database."""
    id: str = Field(..., description="Unique entity identifier")
    type: str = Field(..., description="Node label (Pod, Deployment, etc.)")
    properties: dict[str, Any] = Field(default_factory=dict, description="Node properties")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "pod:default:api-server-7d4f5b6c8-xyz",
                "type": "Pod",
                "properties": {
                    "name": "api-server-7d4f5b6c8-xyz",
                    "namespace": "default",
                    "status": "CrashLoopBackOff",
                    "restarts": 5
                }
            }
        }


class GraphRelation(BaseModel):
    """Relationship between entities in the graph."""
    source_id: str = Field(..., description="Source entity ID")
    target_id: str = Field(..., description="Target entity ID")
    relation_type: str = Field(..., description="Relationship label")
    properties: dict[str, Any] = Field(default_factory=dict, description="Relationship properties")
    
    class Config:
        json_schema_extra = {
            "example": {
                "source_id": "deployment:default:api-server",
                "target_id": "pod:default:api-server-7d4f5b6c8-xyz",
                "relation_type": "OWNS",
                "properties": {"created_at": "2026-01-05T05:00:00Z"}
            }
        }


class CollectorResult(BaseModel):
    """Result from an evidence collector."""
    collector_name: str = Field(..., description="Name of the collector")
    success: bool = Field(..., description="Whether collection succeeded")
    evidence: list[Evidence] = Field(default_factory=list, description="Collected evidence")
    entities: list[GraphEntity] = Field(default_factory=list, description="Graph entities")
    relations: list[GraphRelation] = Field(default_factory=list, description="Graph relationships")
    errors: list[str] = Field(default_factory=list, description="Collection errors")
    duration_seconds: float = Field(0.0, description="Collection duration")


class MetricDataPoint(BaseModel):
    """A single metric data point from Prometheus."""
    timestamp: datetime
    value: float
    labels: dict[str, str] = Field(default_factory=dict)


class MetricEvidence(BaseModel):
    """Metric evidence with time series data."""
    query: str = Field(..., description="PromQL query used")
    metric_name: str = Field(..., description="Metric name")
    data_points: list[MetricDataPoint] = Field(default_factory=list)
    current_value: Optional[float] = None
    threshold: Optional[float] = None
    is_above_threshold: bool = False


class LogEvidence(BaseModel):
    """Log evidence with extracted patterns."""
    pod_name: str
    container_name: str
    log_lines: list[dict[str, Any]] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    patterns_found: list[str] = Field(default_factory=list, description="Extracted error patterns")
    stack_traces: list[str] = Field(default_factory=list, description="Found stack traces")


class DeploymentChange(BaseModel):
    """Evidence of a deployment change."""
    deployment_name: str
    namespace: str
    change_type: str = Field(..., description="image_update, config_change, scale, rollback")
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_at: datetime
    changed_by: Optional[str] = None
    revision: int
