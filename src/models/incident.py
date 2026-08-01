"""
Incident model for the AIOps Evidence Graph Platform.
Represents an incident triggered by alerts from Prometheus/Alertmanager/Grafana.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IncidentSeverity(str, Enum):
    """Severity levels for incidents."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IncidentStatus(str, Enum):
    """Status states for incident lifecycle."""
    OPEN = "open"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSource(str, Enum):
    """Sources of incident alerts."""
    ALERTMANAGER = "alertmanager"
    GRAFANA = "grafana"
    PROMETHEUS = "prometheus"
    MANUAL = "manual"
    SYNTHETIC = "synthetic"


class Incident(BaseModel):
    """
    Core incident model representing an active or historical incident.
    
    This is the central entity that links all evidence, hypotheses, 
    and remediation actions together.
    """
    id: UUID = Field(default_factory=uuid4, description="Unique incident identifier")
    fingerprint: str = Field(..., description="Deduplication key based on alert labels")
    title: str = Field(..., max_length=500, description="Human-readable incident title")
    description: str | None = Field(None, description="Detailed incident description")
    severity: IncidentSeverity = Field(..., description="Incident severity level")
    status: IncidentStatus = Field(default=IncidentStatus.OPEN, description="Current incident status")
    source: IncidentSource = Field(..., description="Source system that generated the alert")

    # Kubernetes context
    cluster: str = Field(..., description="Kubernetes cluster name")
    namespace: str = Field(..., description="Kubernetes namespace")
    service: str | None = Field(None, description="Affected service name")

    # Metadata
    labels: dict[str, str] = Field(default_factory=dict, description="Alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Alert annotations")

    # Timestamps
    started_at: datetime = Field(..., description="When the incident started")
    acknowledged_at: datetime | None = Field(None, description="When incident was acknowledged")
    resolved_at: datetime | None = Field(None, description="When incident was resolved")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Record creation time")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update time")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "fingerprint": "pod_crashloop_default_api-server",
                "title": "Pod CrashLoopBackOff: api-server",
                "description": "Pod api-server-7d4f5b6c8-xyz in namespace default is crash looping",
                "severity": "critical",
                "status": "investigating",
                "source": "alertmanager",
                "cluster": "production-us-east-1",
                "namespace": "default",
                "service": "api-server",
                "labels": {
                    "alertname": "PodCrashLooping",
                    "pod": "api-server-7d4f5b6c8-xyz",
                    "namespace": "default"
                },
                "started_at": "2026-01-05T05:00:00Z"
            }
        }


class IncidentCreate(BaseModel):
    """Schema for creating a new incident."""
    fingerprint: str
    title: str
    description: str | None = None
    severity: IncidentSeverity
    source: IncidentSource
    cluster: str
    namespace: str
    service: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    started_at: datetime


class IncidentUpdate(BaseModel):
    """Schema for updating an existing incident."""
    title: str | None = None
    description: str | None = None
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class IncidentSummary(BaseModel):
    """Lightweight incident summary for listings."""
    id: UUID
    fingerprint: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus
    cluster: str
    namespace: str
    service: str | None
    started_at: datetime

    class Config:
        from_attributes = True
