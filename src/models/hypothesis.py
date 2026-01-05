"""
Hypothesis models for the AIOps Evidence Graph Platform.
Represents RCA hypotheses with confidence scores and evidence references.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class HypothesisCategory(str, Enum):
    """Categories of root cause hypotheses."""
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    BAD_DEPLOYMENT = "bad_deployment"
    CONFIGURATION_ERROR = "configuration_error"
    DEPENDENCY_FAILURE = "dependency_failure"
    INFRASTRUCTURE_ISSUE = "infrastructure_issue"
    NETWORK_ISSUE = "network_issue"
    SCALING_ISSUE = "scaling_issue"
    SECURITY_ISSUE = "security_issue"
    EXTERNAL_DEPENDENCY = "external_dependency"
    DATA_ISSUE = "data_issue"
    UNKNOWN = "unknown"


class HypothesisSource(str, Enum):
    """Source that generated the hypothesis."""
    RULES_ENGINE = "rules_engine"
    LLM = "llm"
    HYBRID = "hybrid"
    MANUAL = "manual"


class Hypothesis(BaseModel):
    """
    A root cause hypothesis generated during incident analysis.
    
    Hypotheses are ranked by confidence and linked to supporting evidence.
    """
    id: UUID = Field(default_factory=uuid4, description="Unique hypothesis identifier")
    incident_id: UUID = Field(..., description="Associated incident ID")
    
    # Classification
    category: HypothesisCategory = Field(..., description="Root cause category")
    title: str = Field(..., max_length=500, description="Hypothesis title")
    description: str = Field(..., description="Detailed explanation")
    
    # Confidence and ranking
    confidence: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Confidence score (0-1)"
    )
    rank: int = Field(..., ge=1, description="Ranking among hypotheses (1 = most likely)")
    
    # Evidence links
    supporting_evidence_ids: list[UUID] = Field(
        default_factory=list, 
        description="IDs of supporting evidence"
    )
    contradicting_evidence_ids: list[UUID] = Field(
        default_factory=list, 
        description="IDs of contradicting evidence"
    )
    
    # Scoring breakdown
    support_count: int = Field(0, description="Number of supporting evidence items")
    recency_weight: float = Field(0.0, description="Weight from recent changes")
    scope_weight: float = Field(0.0, description="Weight from scope (pod vs service)")
    signal_strength: float = Field(0.0, description="Combined signal strength")
    
    # Actions
    recommended_actions: list[str] = Field(
        default_factory=list, 
        description="Recommended remediation actions"
    )
    
    # Explanation
    why_not_notes: Optional[str] = Field(
        None, 
        description="Explanation for lower-ranked alternate hypotheses"
    )
    reasoning: Optional[str] = Field(
        None, 
        description="Detailed reasoning chain"
    )
    
    # Metadata
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    generated_by: HypothesisSource = Field(..., description="Source of hypothesis")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174002",
                "incident_id": "123e4567-e89b-12d3-a456-426614174000",
                "category": "bad_deployment",
                "title": "Recent Deployment Caused Runtime Crash",
                "description": "The api-server pod started crash looping immediately after a deployment update. The new image version contains a bug that causes the application to exit with code 1.",
                "confidence": 0.85,
                "rank": 1,
                "supporting_evidence_ids": [
                    "123e4567-e89b-12d3-a456-426614174001"
                ],
                "recommended_actions": [
                    "Rollback deployment to previous version",
                    "Review application logs for error details",
                    "Check image tag and build pipeline"
                ],
                "generated_by": "rules_engine"
            }
        }


class DiagnosisRule(BaseModel):
    """A deterministic diagnosis rule for pattern matching."""
    id: str = Field(..., description="Unique rule identifier")
    name: str = Field(..., description="Human-readable rule name")
    description: Optional[str] = None
    
    # Conditions
    conditions: list[dict] = Field(..., description="Conditions to match")
    
    # Output
    hypothesis_template: str = Field(..., description="Hypothesis description template")
    category: HypothesisCategory = Field(..., description="Hypothesis category")
    confidence_base: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Base confidence score"
    )
    
    # Actions
    recommended_actions: list[str] = Field(default_factory=list)
    
    # Priority
    priority: int = Field(default=50, description="Rule priority (higher = checked first)")
    enabled: bool = Field(default=True)


class RCAResult(BaseModel):
    """Complete RCA result for an incident."""
    incident_id: UUID
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    top_hypothesis: Optional[Hypothesis] = None
    evidence_summary: str = Field("", description="Summary of all evidence")
    analysis_duration_seconds: float = 0.0
    rules_matched: list[str] = Field(default_factory=list)
    llm_used: bool = False
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class HypothesisCreate(BaseModel):
    """Schema for creating a hypothesis."""
    incident_id: UUID
    category: HypothesisCategory
    title: str
    description: str
    confidence: float
    rank: int
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    generated_by: HypothesisSource


class HypothesisFeedback(BaseModel):
    """User feedback on a hypothesis."""
    hypothesis_id: UUID
    was_correct: bool
    actual_root_cause: Optional[str] = None
    feedback_notes: Optional[str] = None
    submitted_by: str
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
