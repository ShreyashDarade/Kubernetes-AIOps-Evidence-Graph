"""
Tests for remediation verification.

verify() previously read pod_health["healthy"] (an int pod count) as if it
were a boolean, instead of pod_health["all_healthy"]. That made "success" and
"metrics_improved" evaluate truthy whenever at least one pod was healthy,
even if the rest of the fleet was still crashing.
"""
from unittest.mock import AsyncMock

import pytest

from src.services.remediation.verifier import RemediationVerifier


@pytest.fixture
def verifier() -> RemediationVerifier:
    return RemediationVerifier()


async def test_verify_fails_when_pods_still_unhealthy(verifier, incident, monkeypatch):
    monkeypatch.setattr(verifier, "_check_error_rate", AsyncMock(return_value={"improved": False}))
    monkeypatch.setattr(verifier, "_check_restart_rate", AsyncMock(return_value={"improved": False}))
    monkeypatch.setattr(
        verifier,
        "_check_pod_health",
        lambda namespace, service: {"total": 3, "healthy": 1, "all_healthy": False},
    )

    result = await verifier.verify(incident)

    assert result["metrics_improved"] is False
    assert result["success"] is False


async def test_verify_succeeds_when_all_pods_healthy_and_metric_improved(verifier, incident, monkeypatch):
    monkeypatch.setattr(verifier, "_check_error_rate", AsyncMock(return_value={"improved": True}))
    monkeypatch.setattr(verifier, "_check_restart_rate", AsyncMock(return_value={"improved": False}))
    monkeypatch.setattr(
        verifier,
        "_check_pod_health",
        lambda namespace, service: {"total": 3, "healthy": 3, "all_healthy": True},
    )

    result = await verifier.verify(incident)

    assert result["metrics_improved"] is True
    assert result["success"] is True


async def test_verify_does_not_succeed_on_improved_metric_alone(verifier, incident, monkeypatch):
    """metrics_improved alone (e.g. error rate down) must not mark success if pods aren't all healthy."""
    monkeypatch.setattr(verifier, "_check_error_rate", AsyncMock(return_value={"improved": True}))
    monkeypatch.setattr(verifier, "_check_restart_rate", AsyncMock(return_value={"improved": False}))
    monkeypatch.setattr(
        verifier,
        "_check_pod_health",
        lambda namespace, service: {"total": 3, "healthy": 2, "all_healthy": False},
    )

    result = await verifier.verify(incident)

    assert result["metrics_improved"] is True
    assert result["success"] is False
