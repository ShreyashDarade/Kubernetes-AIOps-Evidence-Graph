"""
Regression tests guarding against a class of bug found across the workflow
activities: several service methods were defined as plain `def` while every
caller invoked them with `await`, which raises `TypeError: object ... can't
be used in 'await' expression'` the first time the workflow actually runs.

These tests just assert the methods stay coroutine functions so the mismatch
can't silently come back.
"""
import inspect

from src.services.integrations.slack_client import JiraClient
from src.services.rca.rules_engine import RulesEngine
from src.services.remediation.executor import RemediationExecutor
from src.services.remediation.orchestrator import RemediationOrchestrator


def test_rules_engine_generate_hypotheses_is_async():
    assert inspect.iscoroutinefunction(RulesEngine.generate_hypotheses)


def test_orchestrator_calculate_blast_radius_is_async():
    assert inspect.iscoroutinefunction(RemediationOrchestrator.calculate_blast_radius)


def test_executor_execute_is_async():
    assert inspect.iscoroutinefunction(RemediationExecutor.execute)


def test_jira_client_create_incident_ticket_is_async():
    assert inspect.iscoroutinefunction(JiraClient.create_incident_ticket)
