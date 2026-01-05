"""
Temporal Activities for Incident Workflow.
These are the individual tasks executed by the workflow.
"""
from datetime import datetime, timezone
from typing import Any
import json
import structlog
from temporalio import activity

from src.config import settings
from src.models import Incident, Evidence, CollectorResult
from src.database import get_session, GraphService
from src.services.collectors import (
    KubernetesCollector,
    LogsCollector,
    MetricsCollector,
    DeployDiffCollector,
)


logger = structlog.get_logger()


@activity.defn
async def collect_all_evidence(incident_data: dict) -> dict:
    """Collect evidence from all sources in parallel."""
    incident = Incident(**incident_data)
    
    results = {
        "total_evidence": 0,
        "evidence": [],
        "entities": [],
        "relations": [],
        "errors": [],
    }
    
    collectors = [
        KubernetesCollector(incident),
        LogsCollector(incident),
        MetricsCollector(incident),
        DeployDiffCollector(incident),
    ]
    
    for collector in collectors:
        try:
            result = await collector.run()
            
            # Aggregate results
            results["evidence"].extend([e.model_dump(mode="json") for e in result.evidence])
            results["entities"].extend([e.model_dump(mode="json") for e in result.entities])
            results["relations"].extend([r.model_dump(mode="json") for r in result.relations])
            results["errors"].extend(result.errors)
            results["total_evidence"] += len(result.evidence)
            
        except Exception as e:
            logger.error(f"Collector {collector.name} failed", error=str(e))
            results["errors"].append(f"{collector.name}: {e}")
    
    # Store evidence in database
    async with get_session() as session:
        from sqlalchemy import text
        
        for ev in results["evidence"]:
            await session.execute(
                text("""
                    INSERT INTO evidence (id, incident_id, evidence_type, source, 
                        entity_name, entity_namespace, data, signal_strength, collected_at)
                    VALUES (:id, :incident_id, :evidence_type, :source,
                        :entity_name, :entity_namespace, :data, :signal_strength, :collected_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": ev["id"],
                    "incident_id": str(incident.id),
                    "evidence_type": ev["evidence_type"],
                    "source": ev["source"],
                    "entity_name": ev["entity_name"],
                    "entity_namespace": ev["entity_namespace"],
                    "data": json.dumps(ev["data"]),
                    "signal_strength": ev["signal_strength"],
                    "collected_at": datetime.now(timezone.utc),
                }
            )
    
    logger.info(
        "Evidence collection complete",
        incident_id=str(incident.id),
        total_evidence=results["total_evidence"],
    )
    
    return results


@activity.defn
async def build_evidence_graph(data: dict) -> dict:
    """Build the evidence graph in Neo4j."""
    incident_data = data["incident"]
    evidence_data = data["evidence"]
    
    from src.models import GraphEntity, GraphRelation
    
    entities = [GraphEntity(**e) for e in evidence_data.get("entities", [])]
    relations = [GraphRelation(**r) for r in evidence_data.get("relations", [])]
    
    # Create entities
    entity_count = await GraphService.create_entities_batch(entities)
    
    # Create relations
    relation_count = await GraphService.create_relations_batch(relations)
    
    logger.info(
        "Evidence graph built",
        incident_id=incident_data.get("id"),
        nodes=entity_count,
        edges=relation_count,
    )
    
    return {
        "node_count": entity_count,
        "edge_count": relation_count,
    }


@activity.defn
async def generate_hypotheses(data: dict) -> list[dict]:
    """Generate RCA hypotheses."""
    incident_data = data["incident"]
    evidence_data = data["evidence"]
    
    from src.services.rca.rules_engine import RulesEngine
    from src.services.rca.llm_summarizer import LLMSummarizer
    
    incident = Incident(**incident_data)
    
    # Run rules engine
    rules_engine = RulesEngine()
    hypotheses = await rules_engine.generate_hypotheses(
        incident=incident,
        evidence=evidence_data.get("evidence", []),
    )
    
    # Enhance with LLM if configured
    if settings.llm_provider and hypotheses:
        try:
            summarizer = LLMSummarizer()
            hypotheses = await summarizer.enhance_hypotheses(
                hypotheses=hypotheses,
                evidence=evidence_data.get("evidence", []),
            )
        except Exception as e:
            logger.warning("LLM enhancement failed, using rules-only", error=str(e))
    
    logger.info(
        "Hypotheses generated",
        incident_id=str(incident.id),
        count=len(hypotheses),
    )
    
    return hypotheses


@activity.defn
async def rank_hypotheses(hypotheses: list[dict]) -> list[dict]:
    """Rank hypotheses by confidence."""
    from src.services.rca.hypothesis_ranker import HypothesisRanker
    
    ranker = HypothesisRanker()
    ranked = ranker.rank(hypotheses)
    
    return ranked


@activity.defn
async def generate_runbook(data: dict) -> dict:
    """Generate a runbook for the incident."""
    from src.services.runbook.generator import RunbookGenerator
    
    incident_data = data["incident"]
    hypotheses = data["hypotheses"]
    
    incident = Incident(**incident_data)
    
    generator = RunbookGenerator()
    runbook = await generator.generate(
        incident=incident,
        hypotheses=hypotheses,
    )
    
    return runbook


@activity.defn
async def calculate_blast_radius(incident_data: dict) -> dict:
    """Calculate blast radius for the incident."""
    from src.services.remediation.orchestrator import RemediationOrchestrator
    
    incident = Incident(**incident_data)
    orchestrator = RemediationOrchestrator()
    
    blast_radius = await orchestrator.calculate_blast_radius(incident)
    
    return blast_radius


@activity.defn
async def evaluate_remediation_policy(data: dict) -> dict:
    """Evaluate remediation policy using OPA."""
    from src.services.policy.opa_client import OPAClient
    
    incident_data = data["incident"]
    hypotheses = data["hypotheses"]
    blast_radius = data["blast_radius"]
    
    incident = Incident(**incident_data)
    
    # Get top hypothesis
    top_hypothesis = hypotheses[0] if hypotheses else None
    if not top_hypothesis:
        return {"allowed": False, "reason": "No hypothesis available"}
    
    # Determine proposed action
    proposed_action = None
    if top_hypothesis.get("recommended_actions"):
        proposed_action = top_hypothesis["recommended_actions"][0]
    
    if not proposed_action:
        return {"allowed": False, "reason": "No action recommended"}
    
    # Evaluate policy
    opa_client = OPAClient()
    policy_result = await opa_client.evaluate_remediation(
        action_type=proposed_action,
        environment=settings.app_env,
        blast_radius_score=blast_radius.get("score", 100),
        namespace=incident.namespace,
    )
    
    return {
        "allowed": policy_result.get("allow", False),
        "requires_approval": policy_result.get("requires_approval", True),
        "proposed_action": proposed_action,
        "reason": policy_result.get("reason"),
    }


@activity.defn
async def request_approval(data: dict) -> dict:
    """Request approval for remediation action."""
    # For now, auto-approve in dev environment
    if settings.app_env == "development":
        return {"approved": True, "approver": "auto_dev"}
    
    # In production, this would send Slack message and wait
    # For demo purposes, we'll implement a simple approval
    from src.services.integrations.slack_client import SlackClient
    
    try:
        slack = SlackClient()
        approval = await slack.request_approval(
            incident=data["incident"],
            action=data["action"],
            blast_radius=data["blast_radius"],
        )
        return approval
    except Exception as e:
        logger.warning("Slack approval failed, defaulting to manual", error=str(e))
        return {"approved": False, "reason": f"Approval system unavailable: {e}"}


@activity.defn
async def execute_remediation(data: dict) -> dict:
    """Execute the remediation action."""
    from src.services.remediation.executor import RemediationExecutor
    
    incident_data = data["incident"]
    action = data["action"]
    
    incident = Incident(**incident_data)
    
    executor = RemediationExecutor()
    result = await executor.execute(
        incident=incident,
        action_type=action,
    )
    
    return result


@activity.defn
async def verify_remediation(data: dict) -> dict:
    """Verify that remediation was successful."""
    from src.services.remediation.verifier import RemediationVerifier
    
    incident_data = data["incident"]
    
    incident = Incident(**incident_data)
    
    verifier = RemediationVerifier()
    result = await verifier.verify(
        incident=incident,
    )
    
    return result


@activity.defn
async def create_ticket(data: dict) -> dict:
    """Create a Jira ticket for the incident."""
    incident_data = data["incident"]
    hypotheses = data["hypotheses"]
    runbook = data["runbook"]
    
    if not settings.jira_url:
        logger.info("Jira not configured, skipping ticket creation")
        return {"ticket_id": None}
    
    from src.services.integrations.jira_client import JiraClient
    
    jira = JiraClient()
    ticket = await jira.create_incident_ticket(
        incident=incident_data,
        hypotheses=hypotheses,
        runbook=runbook,
    )
    
    return ticket


@activity.defn
async def close_incident(data: dict) -> dict:
    """Close the incident and update status."""
    incident_data = data["incident"]
    result = data["result"]
    
    async with get_session() as session:
        from sqlalchemy import text
        
        status = "resolved" if result.get("verification_success") else "closed"
        
        await session.execute(
            text("""
                UPDATE incidents 
                SET status = :status, 
                    resolved_at = :resolved_at,
                    updated_at = :updated_at
                WHERE id = :id
            """),
            {
                "id": incident_data.get("id"),
                "status": status,
                "resolved_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
    
    logger.info(
        "Incident closed",
        incident_id=incident_data.get("id"),
        status=status,
    )
    
    return {"status": status}
