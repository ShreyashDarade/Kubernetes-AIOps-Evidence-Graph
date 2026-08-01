"""Shared pytest fixtures."""
from datetime import UTC, datetime

import pytest

from src.models import Incident, IncidentSeverity, IncidentSource


@pytest.fixture
def incident() -> Incident:
    """A baseline incident used across unit tests."""
    return Incident(
        fingerprint="test-fingerprint",
        title="Pod CrashLoopBackOff: api-server",
        description="Pod api-server is crash looping",
        severity=IncidentSeverity.CRITICAL,
        source=IncidentSource.ALERTMANAGER,
        cluster="test-cluster",
        namespace="default",
        service="api-server",
        started_at=datetime.now(UTC),
    )


def make_pod_evidence(
    evidence_id: str = "ev-1",
    waiting_reason: str | None = None,
    terminated_reason: str | None = None,
    restart_count: int = 0,
    node_name: str = "node-1",
    phase: str = "Running",
    conditions: list[dict] | None = None,
) -> dict:
    """Build a kubernetes_pod evidence dict as produced by KubernetesCollector."""
    return {
        "id": evidence_id,
        "evidence_type": "kubernetes_pod",
        "data": {
            "name": "api-server-abc123",
            "namespace": "default",
            "phase": phase,
            "node_name": node_name,
            "restart_count": restart_count,
            "waiting_reason": waiting_reason,
            "terminated_reason": terminated_reason,
            "conditions": conditions or [],
        },
    }


def make_deploy_evidence(evidence_id: str = "ev-deploy", is_recent_change: bool = True) -> dict:
    return {
        "id": evidence_id,
        "evidence_type": "deploy_change",
        "data": {"is_recent_change": is_recent_change},
    }


def make_node_evidence(evidence_id: str = "ev-node", node_name: str = "node-1", ready: bool = False) -> dict:
    return {
        "id": evidence_id,
        "evidence_type": "kubernetes_node",
        "data": {
            "name": node_name,
            "conditions": {"Ready": {"status": "True" if ready else "False"}},
        },
    }


def make_log_evidence(evidence_id: str = "ev-log", patterns_found: list[str] | None = None, error_count: int = 0) -> dict:
    return {
        "id": evidence_id,
        "evidence_type": "log_signal",
        "data": {
            "patterns_found": patterns_found or [],
            "error_count": error_count,
        },
    }
