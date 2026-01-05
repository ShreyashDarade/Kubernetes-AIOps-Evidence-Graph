"""
Alert normalizer - converts various alert formats to standard Incident schema.
"""
from datetime import datetime, timezone
from typing import Any
import hashlib
import structlog

from src.models import IncidentCreate, IncidentSeverity, IncidentSource


logger = structlog.get_logger()


class AlertNormalizer:
    """Normalizes alerts from different sources to a standard format."""
    
    # Severity mapping
    SEVERITY_MAP = {
        # Alertmanager/Prometheus
        "critical": IncidentSeverity.CRITICAL,
        "high": IncidentSeverity.HIGH,
        "warning": IncidentSeverity.MEDIUM,
        "info": IncidentSeverity.INFO,
        "low": IncidentSeverity.LOW,
        # Grafana
        "alerting": IncidentSeverity.HIGH,
        "error": IncidentSeverity.HIGH,
        "warn": IncidentSeverity.MEDIUM,
    }
    
    @classmethod
    def normalize_alertmanager(
        cls, 
        alert: dict[str, Any],
        payload: dict[str, Any],
    ) -> IncidentCreate:
        """
        Normalize Alertmanager alert to IncidentCreate.
        
        Args:
            alert: Individual alert from alerts array
            payload: Full webhook payload
        """
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        # Extract key fields
        alertname = labels.get("alertname", "Unknown Alert")
        namespace = labels.get("namespace", "default")
        cluster = labels.get("cluster") or labels.get("kubernetes_cluster") or "default-cluster"
        service = labels.get("service") or labels.get("job") or labels.get("deployment")
        pod = labels.get("pod")
        
        # Determine severity
        severity_str = labels.get("severity", "warning").lower()
        severity = cls.SEVERITY_MAP.get(severity_str, IncidentSeverity.MEDIUM)
        
        # Build title
        if pod:
            title = f"{alertname}: {pod}"
        elif service:
            title = f"{alertname}: {service}"
        else:
            title = alertname
        
        # Description from annotations
        description = annotations.get("description") or annotations.get("summary") or ""
        
        # Parse start time
        starts_at_str = alert.get("startsAt")
        if starts_at_str:
            # Handle ISO format with Z suffix
            starts_at_str = starts_at_str.replace("Z", "+00:00")
            try:
                started_at = datetime.fromisoformat(starts_at_str)
            except ValueError:
                started_at = datetime.now(timezone.utc)
        else:
            started_at = datetime.now(timezone.utc)
        
        # Generate fingerprint for deduplication
        fingerprint = cls._generate_fingerprint(
            source="alertmanager",
            alertname=alertname,
            namespace=namespace,
            service=service or pod or "",
        )
        
        return IncidentCreate(
            fingerprint=fingerprint,
            title=title,
            description=description,
            severity=severity,
            source=IncidentSource.ALERTMANAGER,
            cluster=cluster,
            namespace=namespace,
            service=service,
            labels=labels,
            annotations=annotations,
            started_at=started_at,
        )
    
    @classmethod
    def normalize_grafana(
        cls,
        alert: dict[str, Any],
        payload: dict[str, Any],
    ) -> IncidentCreate:
        """
        Normalize Grafana alert to IncidentCreate.
        
        Args:
            alert: Individual alert from alerts array
            payload: Full webhook payload
        """
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        # Merge common labels/annotations
        labels = {**payload.get("commonLabels", {}), **labels}
        annotations = {**payload.get("commonAnnotations", {}), **annotations}
        
        # Extract key fields
        alertname = labels.get("alertname") or alert.get("alertname", "Grafana Alert")
        namespace = labels.get("namespace", "default")
        cluster = labels.get("cluster", "default-cluster")
        service = labels.get("service") or labels.get("grafana_folder")
        
        # Severity
        severity_str = labels.get("severity", "warning").lower()
        severity = cls.SEVERITY_MAP.get(severity_str, IncidentSeverity.MEDIUM)
        
        # Title and description
        title = annotations.get("summary") or alertname
        description = annotations.get("description", "")
        
        # Start time
        starts_at_str = alert.get("startsAt")
        if starts_at_str:
            starts_at_str = starts_at_str.replace("Z", "+00:00")
            try:
                started_at = datetime.fromisoformat(starts_at_str)
            except ValueError:
                started_at = datetime.now(timezone.utc)
        else:
            started_at = datetime.now(timezone.utc)
        
        fingerprint = cls._generate_fingerprint(
            source="grafana",
            alertname=alertname,
            namespace=namespace,
            service=service or "",
        )
        
        return IncidentCreate(
            fingerprint=fingerprint,
            title=title,
            description=description,
            severity=severity,
            source=IncidentSource.GRAFANA,
            cluster=cluster,
            namespace=namespace,
            service=service,
            labels=labels,
            annotations=annotations,
            started_at=started_at,
        )
    
    @classmethod
    def normalize_prometheus(
        cls,
        alert: dict[str, Any],
    ) -> IncidentCreate:
        """Normalize raw Prometheus alert."""
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        alertname = labels.get("alertname", "Prometheus Alert")
        namespace = labels.get("namespace", "default")
        cluster = labels.get("cluster", "default-cluster")
        service = labels.get("service") or labels.get("instance")
        
        severity_str = labels.get("severity", "warning").lower()
        severity = cls.SEVERITY_MAP.get(severity_str, IncidentSeverity.MEDIUM)
        
        fingerprint = cls._generate_fingerprint(
            source="prometheus",
            alertname=alertname,
            namespace=namespace,
            service=service or "",
        )
        
        return IncidentCreate(
            fingerprint=fingerprint,
            title=alertname,
            description=annotations.get("description", ""),
            severity=severity,
            source=IncidentSource.PROMETHEUS,
            cluster=cluster,
            namespace=namespace,
            service=service,
            labels=labels,
            annotations=annotations,
            started_at=datetime.now(timezone.utc),
        )
    
    @classmethod
    def _generate_fingerprint(
        cls,
        source: str,
        alertname: str,
        namespace: str,
        service: str,
    ) -> str:
        """Generate a unique fingerprint for deduplication."""
        key = f"{source}:{alertname}:{namespace}:{service}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]
