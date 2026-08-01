"""
Tests for the deterministic RCA rules engine.

Several rules previously referenced condition types that were never
implemented in RulesEngine._check_condition, which made them permanently
unmatchable regardless of evidence. These tests pin the fixed behavior.
"""
import pytest

from src.services.rca.rules_engine import RulesEngine
from tests.conftest import (
    make_deploy_evidence,
    make_log_evidence,
    make_node_evidence,
    make_pod_evidence,
)


@pytest.fixture
def engine() -> RulesEngine:
    return RulesEngine()


async def test_crashloop_with_recent_deploy_matches_bad_deployment(engine, incident):
    evidence = [
        make_pod_evidence(waiting_reason="CrashLoopBackOff", restart_count=5),
        make_deploy_evidence(is_recent_change=True),
    ]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    assert hypotheses[0]["rule_id"] == "crashloop_recent_deploy"
    assert hypotheses[0]["category"] == "bad_deployment"


async def test_oom_killed_matches_resource_exhaustion(engine, incident):
    evidence = [make_pod_evidence(terminated_reason="OOMKilled")]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    rule_ids = {h["rule_id"] for h in hypotheses}
    assert "oom_killed" in rule_ids


async def test_no_evidence_yields_unknown_hypothesis(engine, incident):
    hypotheses = await engine.generate_hypotheses(incident, evidence=[])

    assert len(hypotheses) == 1
    assert hypotheses[0]["category"] == "unknown"
    assert hypotheses[0]["confidence"] == 0.3


async def test_multiple_pods_failing_on_same_node_matches_node_failure(engine, incident):
    """Regression test: 'multiple_pods_same_node' had no condition handler."""
    evidence = [
        make_pod_evidence(evidence_id="ev-1", waiting_reason="CrashLoopBackOff", node_name="node-1"),
        make_pod_evidence(evidence_id="ev-2", restart_count=3, node_name="node-1"),
        make_node_evidence(node_name="node-1", ready=False),
    ]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    rule_ids = {h["rule_id"] for h in hypotheses}
    assert "node_failure_isolated" in rule_ids


async def test_readiness_probe_failure_matches_dependency_failure(engine, incident):
    """Regression test: 'pod_not_ready' / 'readiness_probe_failing' had no condition handler."""
    evidence = [
        make_pod_evidence(
            phase="Running",
            conditions=[{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
        ),
    ]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    rule_ids = {h["rule_id"] for h in hypotheses}
    assert "readiness_probe_failing" in rule_ids


async def test_network_errors_match_network_issue(engine, incident):
    """Regression test: 'network_errors_high' had no condition handler, and the
    log_pattern values didn't correspond to any category logs_collector emits."""
    evidence = [make_log_evidence(patterns_found=["network"], error_count=15)]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    rule_ids = {h["rule_id"] for h in hypotheses}
    assert "network_error" in rule_ids


async def test_hypotheses_sorted_by_confidence_descending(engine, incident):
    evidence = [
        make_pod_evidence(terminated_reason="OOMKilled"),
        make_log_evidence(patterns_found=["network"], error_count=15),
    ]

    hypotheses = await engine.generate_hypotheses(incident, evidence)

    confidences = [h["confidence"] for h in hypotheses]
    assert confidences == sorted(confidences, reverse=True)
