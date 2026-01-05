"""
Alert Ingestion Service - FastAPI application for receiving alerts.
Handles webhooks from Alertmanager, Grafana, and Prometheus.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
import structlog
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import hashlib
import json

from src.config import settings
from src.database import init_database, close_database, check_database_connection
from src.database.neo4j import Neo4jConnection, GraphService
from src.models import (
    Incident, IncidentCreate, IncidentSeverity, IncidentSource, IncidentStatus,
)
from src.services.ingestion.normalizer import AlertNormalizer
from src.services.ingestion.deduplicator import AlertDeduplicator


logger = structlog.get_logger()

# Prometheus metrics
ALERTS_RECEIVED = Counter(
    "aiops_alerts_received_total",
    "Total number of alerts received",
    ["source", "severity"]
)
ALERTS_DEDUPLICATED = Counter(
    "aiops_alerts_deduplicated_total",
    "Total number of alerts deduplicated"
)
INCIDENTS_CREATED = Counter(
    "aiops_incidents_created_total",
    "Total number of incidents created",
    ["severity"]
)
WEBHOOK_LATENCY = Histogram(
    "aiops_webhook_latency_seconds",
    "Webhook processing latency",
    ["source"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting AIOps Ingestion Service")
    await init_database()
    await GraphService.init_constraints()
    yield
    # Shutdown
    logger.info("Shutting down AIOps Ingestion Service")
    await close_database()
    await Neo4jConnection.close()


app = FastAPI(
    title="Kubernetes AIOps Evidence Graph",
    description="Production-ready AIOps platform for automated incident detection, RCA, and remediation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health checks
@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "service": "aiops-ingestion"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness check including dependencies."""
    checks = {
        "postgres": await check_database_connection(),
        "neo4j": await Neo4jConnection.verify_connectivity(),
    }
    
    all_healthy = all(checks.values())
    status_code = 200 if all_healthy else 503
    
    return JSONResponse(
        content={"status": "ready" if all_healthy else "not_ready", "checks": checks},
        status_code=status_code,
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# Alertmanager webhook
@app.post("/api/v1/webhooks/alertmanager")
async def alertmanager_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive alerts from Alertmanager.
    
    Expected format:
    {
        "receiver": "...",
        "status": "firing" | "resolved",
        "alerts": [
            {
                "status": "firing",
                "labels": {...},
                "annotations": {...},
                "startsAt": "...",
                "endsAt": "..."
            }
        ]
    }
    """
    with WEBHOOK_LATENCY.labels(source="alertmanager").time():
        try:
            payload = await request.json()
            logger.info("Received Alertmanager webhook", alert_count=len(payload.get("alerts", [])))
            
            incidents = []
            
            for alert in payload.get("alerts", []):
                if alert.get("status") != "firing":
                    continue
                
                # Track metric
                severity = alert.get("labels", {}).get("severity", "warning")
                ALERTS_RECEIVED.labels(source="alertmanager", severity=severity).inc()
                
                # Normalize alert to incident
                incident_data = AlertNormalizer.normalize_alertmanager(alert, payload)
                
                # Check for duplicates
                is_duplicate, _ = await AlertDeduplicator.check_duplicate(
                    incident_data.fingerprint
                )
                
                if is_duplicate:
                    ALERTS_DEDUPLICATED.inc()
                    logger.debug("Alert deduplicated", fingerprint=incident_data.fingerprint)
                    continue
                
                # Create incident
                incident = await create_incident(incident_data)
                incidents.append(incident)
                
                INCIDENTS_CREATED.labels(severity=incident.severity.value).inc()
                
                # Trigger workflow in background
                background_tasks.add_task(
                    trigger_incident_workflow,
                    incident,
                )
            
            return {
                "status": "accepted",
                "incidents_created": len(incidents),
                "incident_ids": [str(i.id) for i in incidents],
            }
            
        except Exception as e:
            logger.error("Error processing Alertmanager webhook", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))


# Grafana webhook
@app.post("/api/v1/webhooks/grafana")
async def grafana_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive alerts from Grafana.
    
    Expected format (Grafana Alerting):
    {
        "receiver": "...",
        "status": "firing" | "resolved",
        "alerts": [...],
        "commonLabels": {...},
        "commonAnnotations": {...}
    }
    """
    with WEBHOOK_LATENCY.labels(source="grafana").time():
        try:
            payload = await request.json()
            logger.info("Received Grafana webhook", status=payload.get("status"))
            
            if payload.get("status") != "firing":
                return {"status": "ignored", "reason": "not_firing"}
            
            incidents = []
            
            for alert in payload.get("alerts", []):
                if alert.get("status") != "firing":
                    continue
                
                severity = alert.get("labels", {}).get("severity", "warning")
                ALERTS_RECEIVED.labels(source="grafana", severity=severity).inc()
                
                # Normalize
                incident_data = AlertNormalizer.normalize_grafana(alert, payload)
                
                # Deduplicate
                is_duplicate, _ = await AlertDeduplicator.check_duplicate(
                    incident_data.fingerprint
                )
                
                if is_duplicate:
                    ALERTS_DEDUPLICATED.inc()
                    continue
                
                # Create incident
                incident = await create_incident(incident_data)
                incidents.append(incident)
                
                INCIDENTS_CREATED.labels(severity=incident.severity.value).inc()
                
                background_tasks.add_task(trigger_incident_workflow, incident)
            
            return {
                "status": "accepted",
                "incidents_created": len(incidents),
            }
            
        except Exception as e:
            logger.error("Error processing Grafana webhook", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))


# Manual incident creation
@app.post("/api/v1/incidents")
async def create_manual_incident(
    incident_data: IncidentCreate,
    background_tasks: BackgroundTasks,
):
    """Create an incident manually."""
    # Check duplicate
    is_duplicate, existing_id = await AlertDeduplicator.check_duplicate(
        incident_data.fingerprint
    )
    
    if is_duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Incident with fingerprint already exists: {existing_id}",
        )
    
    incident = await create_incident(incident_data)
    INCIDENTS_CREATED.labels(severity=incident.severity.value).inc()
    
    background_tasks.add_task(trigger_incident_workflow, incident)
    
    return incident


# Get incident by ID
@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(incident_id: str):
    """Get incident details by ID."""
    from src.database import get_session
    from sqlalchemy import text
    
    async with get_session() as session:
        result = await session.execute(
            text("SELECT * FROM incidents WHERE id = :id"),
            {"id": incident_id}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found")
        
        return dict(row._mapping)


# Get incident evidence graph
@app.get("/api/v1/incidents/{incident_id}/graph")
async def get_incident_graph(incident_id: str, depth: int = 3):
    """Get the evidence graph for an incident."""
    graph = await GraphService.get_incident_graph(incident_id, depth)
    return graph


# List incidents
@app.get("/api/v1/incidents")
async def list_incidents(
    status: str = None,
    severity: str = None,
    namespace: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """List incidents with optional filters."""
    from src.database import get_session
    from sqlalchemy import text
    
    query = "SELECT * FROM incidents WHERE 1=1"
    params = {}
    
    if status:
        query += " AND status = :status"
        params["status"] = status
    if severity:
        query += " AND severity = :severity"
        params["severity"] = severity
    if namespace:
        query += " AND namespace = :namespace"
        params["namespace"] = namespace
    
    query += " ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    
    async with get_session() as session:
        result = await session.execute(text(query), params)
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def create_incident(incident_data: IncidentCreate) -> Incident:
    """Create an incident in the database."""
    from src.database import get_session
    from sqlalchemy import text
    
    incident = Incident(
        fingerprint=incident_data.fingerprint,
        title=incident_data.title,
        description=incident_data.description,
        severity=incident_data.severity,
        source=incident_data.source,
        cluster=incident_data.cluster,
        namespace=incident_data.namespace,
        service=incident_data.service,
        labels=incident_data.labels,
        annotations=incident_data.annotations,
        started_at=incident_data.started_at,
    )
    
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO incidents (id, fingerprint, title, description, severity, status, 
                    source, cluster, namespace, service, labels, annotations, started_at, created_at, updated_at)
                VALUES (:id, :fingerprint, :title, :description, :severity, :status,
                    :source, :cluster, :namespace, :service, :labels, :annotations, :started_at, :created_at, :updated_at)
            """),
            {
                "id": str(incident.id),
                "fingerprint": incident.fingerprint,
                "title": incident.title,
                "description": incident.description,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "source": incident.source.value,
                "cluster": incident.cluster,
                "namespace": incident.namespace,
                "service": incident.service,
                "labels": json.dumps(incident.labels),
                "annotations": json.dumps(incident.annotations),
                "started_at": incident.started_at,
                "created_at": incident.created_at,
                "updated_at": incident.updated_at,
            }
        )
    
    logger.info(
        "Created incident",
        incident_id=str(incident.id),
        title=incident.title,
        severity=incident.severity.value,
    )
    
    return incident


async def trigger_incident_workflow(incident: Incident) -> None:
    """Trigger the incident workflow in Temporal."""
    try:
        from temporalio.client import Client
        
        client = await Client.connect(settings.temporal_address)
        
        await client.start_workflow(
            "IncidentWorkflow",
            incident.model_dump(mode="json"),
            id=f"incident-{incident.id}",
            task_queue=settings.temporal_task_queue,
        )
        
        logger.info(
            "Started incident workflow",
            incident_id=str(incident.id),
            workflow_id=f"incident-{incident.id}",
        )
    except Exception as e:
        logger.error(
            "Failed to start incident workflow",
            incident_id=str(incident.id),
            error=str(e),
        )
