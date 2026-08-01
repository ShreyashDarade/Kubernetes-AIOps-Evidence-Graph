"""Tests for core pydantic models."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.models import Incident, IncidentSeverity, IncidentSource, IncidentStatus


def test_incident_defaults_status_open():
    incident = Incident(
        fingerprint="fp",
        title="title",
        severity=IncidentSeverity.LOW,
        source=IncidentSource.MANUAL,
        cluster="c",
        namespace="ns",
        started_at=datetime.now(UTC),
    )

    assert incident.status == IncidentStatus.OPEN
    assert incident.id is not None


def test_incident_requires_severity():
    with pytest.raises(ValidationError):
        Incident(
            fingerprint="fp",
            title="title",
            source=IncidentSource.MANUAL,
            cluster="c",
            namespace="ns",
            started_at=datetime.now(UTC),
        )


def test_incident_two_instances_get_distinct_ids():
    kwargs = dict(
        fingerprint="fp",
        title="title",
        severity=IncidentSeverity.LOW,
        source=IncidentSource.MANUAL,
        cluster="c",
        namespace="ns",
        started_at=datetime.now(UTC),
    )

    assert Incident(**kwargs).id != Incident(**kwargs).id
