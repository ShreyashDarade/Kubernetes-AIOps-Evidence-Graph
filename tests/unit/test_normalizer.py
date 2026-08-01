"""Tests for alert normalization."""
from src.models import IncidentSeverity, IncidentSource
from src.services.ingestion.normalizer import AlertNormalizer


def test_normalize_alertmanager_maps_core_fields():
    alert = {
        "status": "firing",
        "labels": {
            "alertname": "PodCrashLooping",
            "namespace": "prod",
            "cluster": "us-east-1",
            "service": "api-server",
            "severity": "critical",
        },
        "annotations": {"summary": "Pod is crash looping"},
        "startsAt": "2026-01-05T05:00:00Z",
    }
    payload = {"receiver": "aiops", "status": "firing", "alerts": [alert]}

    incident = AlertNormalizer.normalize_alertmanager(alert, payload)

    assert incident.title == "PodCrashLooping: api-server"
    assert incident.severity == IncidentSeverity.CRITICAL
    assert incident.source == IncidentSource.ALERTMANAGER
    assert incident.namespace == "prod"
    assert incident.cluster == "us-east-1"


def test_normalize_alertmanager_same_labels_produce_same_fingerprint():
    alert = {
        "labels": {"alertname": "PodCrashLooping", "namespace": "prod", "service": "api-server"},
        "annotations": {},
    }
    payload = {}

    first = AlertNormalizer.normalize_alertmanager(alert, payload)
    second = AlertNormalizer.normalize_alertmanager(alert, payload)

    assert first.fingerprint == second.fingerprint


def test_normalize_alertmanager_different_service_produces_different_fingerprint():
    payload = {}
    alert_a = {"labels": {"alertname": "PodCrashLooping", "namespace": "prod", "service": "api-server"}, "annotations": {}}
    alert_b = {"labels": {"alertname": "PodCrashLooping", "namespace": "prod", "service": "worker"}, "annotations": {}}

    a = AlertNormalizer.normalize_alertmanager(alert_a, payload)
    b = AlertNormalizer.normalize_alertmanager(alert_b, payload)

    assert a.fingerprint != b.fingerprint


def test_normalize_grafana_merges_common_labels():
    alert = {"labels": {"severity": "alerting"}, "annotations": {}}
    payload = {
        "commonLabels": {"alertname": "HighLatency", "namespace": "prod"},
        "commonAnnotations": {"summary": "Latency is high"},
    }

    incident = AlertNormalizer.normalize_grafana(alert, payload)

    assert incident.title == "Latency is high"
    assert incident.namespace == "prod"
    assert incident.severity == IncidentSeverity.HIGH


def test_normalize_alertmanager_defaults_when_fields_missing():
    incident = AlertNormalizer.normalize_alertmanager({"labels": {}, "annotations": {}}, {})

    assert incident.title == "Unknown Alert"
    assert incident.namespace == "default"
    assert incident.severity == IncidentSeverity.MEDIUM
