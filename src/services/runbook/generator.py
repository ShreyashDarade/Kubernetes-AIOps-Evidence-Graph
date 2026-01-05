"""
Runbook Generator.
Generates actionable runbooks with commands and dashboard links.
"""
from datetime import datetime, timezone
from typing import Any
import json
import structlog
from uuid import uuid4

from src.models import Incident
from src.config import settings
from src.database import get_session


logger = structlog.get_logger()


class RunbookGenerator:
    """Generates runbooks for incident investigation and remediation."""
    
    # Command templates by action type
    COMMAND_TEMPLATES = {
        "restart_pod": [
            "kubectl delete pod {pod_name} -n {namespace}",
            "kubectl get pods -n {namespace} -w",
        ],
        "restart_deployment": [
            "kubectl rollout restart deployment/{deployment} -n {namespace}",
            "kubectl rollout status deployment/{deployment} -n {namespace}",
        ],
        "rollback_deployment": [
            "kubectl rollout history deployment/{deployment} -n {namespace}",
            "kubectl rollout undo deployment/{deployment} -n {namespace}",
            "kubectl rollout status deployment/{deployment} -n {namespace}",
        ],
        "scale_replicas": [
            "kubectl scale deployment/{deployment} --replicas={replicas} -n {namespace}",
            "kubectl get pods -n {namespace} -l app={deployment}",
        ],
        "investigate_logs": [
            "kubectl logs -n {namespace} -l app={service} --tail=100",
            "kubectl logs -n {namespace} -l app={service} --previous --tail=100",
        ],
        "investigate_events": [
            "kubectl get events -n {namespace} --sort-by=.lastTimestamp",
            "kubectl describe pod -n {namespace} -l app={service}",
        ],
        "investigate_resources": [
            "kubectl top pods -n {namespace}",
            "kubectl describe nodes",
        ],
    }
    
    # PromQL queries for investigation
    INVESTIGATION_QUERIES = {
        "crashloop": [
            {
                "name": "Restart count",
                "query": 'increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[1h])',
            },
            {
                "name": "Container states",
                "query": 'kube_pod_container_status_waiting_reason{{namespace="{namespace}"}}',
            },
        ],
        "oom": [
            {
                "name": "Memory usage",
                "query": 'container_memory_usage_bytes{{namespace="{namespace}"}} / container_spec_memory_limit_bytes{{namespace="{namespace}"}}',
            },
        ],
        "error_rate": [
            {
                "name": "HTTP error rate",
                "query": 'sum(rate(http_requests_total{{namespace="{namespace}", status=~"5.."}}[5m])) / sum(rate(http_requests_total{{namespace="{namespace}"}}[5m]))',
            },
        ],
        "latency": [
            {
                "name": "P99 latency",
                "query": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{namespace="{namespace}"}}[5m])) by (le))',
            },
        ],
    }
    
    async def generate(
        self,
        incident: Incident,
        hypotheses: list[dict],
    ) -> dict[str, Any]:
        """Generate a runbook for the incident."""
        top_hypothesis = hypotheses[0] if hypotheses else {}
        category = top_hypothesis.get("category", "unknown")
        
        # Generate sections
        commands = self._generate_commands(incident, hypotheses)
        queries = self._generate_queries(incident, category)
        dashboard_links = self._generate_dashboard_links(incident)
        steps = self._generate_investigation_steps(hypotheses)
        
        runbook = {
            "id": str(uuid4()),
            "incident_id": str(incident.id),
            "title": f"Runbook: {incident.title}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "top_hypothesis": top_hypothesis.get("title"),
            "sections": {
                "summary": self._generate_summary(incident, hypotheses),
                "immediate_actions": top_hypothesis.get("recommended_actions", [])[:3],
                "investigation_commands": commands,
                "prometheus_queries": queries,
                "dashboard_links": dashboard_links,
                "investigation_steps": steps,
            }
        }
        
        # Store in database
        await self._store_runbook(runbook, incident)
        
        logger.info(
            "Generated runbook",
            incident_id=str(incident.id),
            runbook_id=runbook["id"],
        )
        
        return runbook
    
    def _generate_summary(self, incident: Incident, hypotheses: list[dict]) -> str:
        """Generate incident summary."""
        if not hypotheses:
            return f"Incident in {incident.namespace}/{incident.service or 'unknown'}"
        
        top = hypotheses[0]
        return f"""
**Incident**: {incident.title}
**Severity**: {incident.severity.value}
**Namespace**: {incident.namespace}
**Service**: {incident.service or 'N/A'}

**Top Hypothesis** (confidence: {top.get('confidence', 0):.0%}):
{top.get('description', 'No description available')}
"""
    
    def _generate_commands(
        self, 
        incident: Incident, 
        hypotheses: list[dict]
    ) -> list[dict]:
        """Generate kubectl commands."""
        commands = []
        namespace = incident.namespace
        service = incident.service or ""
        
        # Always include investigation commands
        for cmd in self.COMMAND_TEMPLATES["investigate_logs"]:
            commands.append({
                "description": "View recent logs",
                "command": cmd.format(namespace=namespace, service=service),
            })
        
        for cmd in self.COMMAND_TEMPLATES["investigate_events"]:
            commands.append({
                "description": "View recent events",
                "command": cmd.format(namespace=namespace, service=service),
            })
        
        # Add remediation commands based on hypotheses
        if hypotheses:
            actions = hypotheses[0].get("recommended_actions", [])
            for action in actions:
                if action.startswith("kubectl"):
                    commands.append({
                        "description": "Recommended action",
                        "command": action,
                    })
                elif action in self.COMMAND_TEMPLATES:
                    for cmd in self.COMMAND_TEMPLATES[action]:
                        commands.append({
                            "description": f"Execute: {action}",
                            "command": cmd.format(
                                namespace=namespace,
                                deployment=service,
                                service=service,
                                pod_name=f"{service}-xxx",
                                replicas=3,
                            ),
                        })
        
        return commands
    
    def _generate_queries(self, incident: Incident, category: str) -> list[dict]:
        """Generate PromQL queries for investigation."""
        queries = []
        namespace = incident.namespace
        
        # Get category-specific queries
        category_queries = self.INVESTIGATION_QUERIES.get(category, [])
        
        for q in category_queries:
            queries.append({
                "name": q["name"],
                "query": q["query"].format(namespace=namespace),
            })
        
        # Always include general health queries
        queries.append({
            "name": "Pod restarts",
            "query": f'increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[30m])',
        })
        
        return queries
    
    def _generate_dashboard_links(self, incident: Incident) -> list[dict]:
        """Generate Grafana dashboard links."""
        grafana_url = settings.grafana_url
        namespace = incident.namespace
        service = incident.service or ""
        
        return [
            {
                "name": "Kubernetes Overview",
                "url": f"{grafana_url}/d/kubernetes-overview?var-namespace={namespace}",
            },
            {
                "name": "Pod Resources",
                "url": f"{grafana_url}/d/pod-resources?var-namespace={namespace}&var-pod={service}",
            },
            {
                "name": "Application Metrics",
                "url": f"{grafana_url}/d/application-metrics?var-namespace={namespace}&var-service={service}",
            },
            {
                "name": "Logs Explorer",
                "url": f"{grafana_url}/explore?orgId=1&left=%5B%22now-1h%22,%22now%22,%22Loki%22,%7B%22expr%22:%22%7Bnamespace%3D%5C%22{namespace}%5C%22%7D%22%7D%5D",
            },
        ]
    
    def _generate_investigation_steps(self, hypotheses: list[dict]) -> list[str]:
        """Generate step-by-step investigation guide."""
        steps = [
            "1. Review the incident summary and top hypothesis",
            "2. Check the investigation commands section for relevant kubectl commands",
            "3. Execute the log inspection commands to identify specific errors",
            "4. Review Prometheus queries for metric anomalies",
            "5. Open the relevant Grafana dashboards for visual analysis",
        ]
        
        if hypotheses:
            category = hypotheses[0].get("category", "")
            
            if category == "bad_deployment":
                steps.extend([
                    "6. Check recent deployments with: kubectl rollout history",
                    "7. If recent deployment is the cause, consider rollback",
                ])
            elif category == "resource_exhaustion":
                steps.extend([
                    "6. Check resource limits and requests",
                    "7. Review memory/CPU graphs for leak patterns",
                ])
            elif category == "dependency_failure":
                steps.extend([
                    "6. Check connectivity to external dependencies",
                    "7. Verify DNS resolution and network policies",
                ])
        
        steps.append("8. Execute remediation if root cause is confirmed")
        steps.append("9. Monitor metrics to verify improvement")
        
        return steps
    
    async def _store_runbook(self, runbook: dict, incident: Incident) -> None:
        """Store runbook in database."""
        from sqlalchemy import text
        
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO runbooks (id, incident_id, title, content, commands, dashboard_links, generated_at)
                    VALUES (:id, :incident_id, :title, :content, :commands, :dashboard_links, :generated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": runbook["id"],
                    "incident_id": str(incident.id),
                    "title": runbook["title"],
                    "content": json.dumps(runbook["sections"]),
                    "commands": json.dumps(runbook["sections"]["investigation_commands"]),
                    "dashboard_links": json.dumps(runbook["sections"]["dashboard_links"]),
                    "generated_at": datetime.now(timezone.utc),
                }
            )
