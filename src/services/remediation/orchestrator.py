"""
Remediation Orchestrator.
Coordinates remediation actions with policy evaluation and blast radius assessment.
"""
from datetime import datetime, timezone
from typing import Any, Optional
import structlog
from uuid import uuid4

from src.models import Incident, RemediationAction, ActionType, ActionRisk, ActionStatus, Environment
from src.services.policy.opa_client import OPAClient
from src.config import settings


logger = structlog.get_logger()


class RemediationOrchestrator:
    """Orchestrates remediation actions with safety controls."""
    
    # Action risk levels
    ACTION_RISKS = {
        ActionType.RESTART_POD: ActionRisk.LOW,
        ActionType.DELETE_POD: ActionRisk.LOW,
        ActionType.RESTART_DEPLOYMENT: ActionRisk.LOW,
        ActionType.SCALE_REPLICAS: ActionRisk.LOW,
        ActionType.ROLLBACK_DEPLOYMENT: ActionRisk.MEDIUM,
        ActionType.CORDON_NODE: ActionRisk.MEDIUM,
        ActionType.UNCORDON_NODE: ActionRisk.MEDIUM,
        ActionType.DRAIN_NODE: ActionRisk.HIGH,
        ActionType.UPDATE_CONFIGMAP: ActionRisk.HIGH,
        ActionType.UPDATE_RESOURCE_LIMITS: ActionRisk.HIGH,
        ActionType.UPDATE_HPA: ActionRisk.MEDIUM,
    }
    
    def __init__(self):
        self.opa_client = OPAClient()
    
    def calculate_blast_radius(self, incident: Incident) -> dict[str, Any]:
        """Calculate blast radius for potential remediation."""
        from kubernetes import client, config
        
        try:
            if settings.kubeconfig:
                config.load_kube_config(settings.kubeconfig)
            else:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()
            
            apps_v1 = client.AppsV1Api()
            
            # Get deployment info
            namespace = incident.namespace
            service_name = incident.service
            
            affected_pods = 0
            affected_deployments = 0
            
            if service_name:
                try:
                    deploy = apps_v1.read_namespaced_deployment(
                        name=service_name, 
                        namespace=namespace
                    )
                    affected_pods = deploy.spec.replicas or 1
                    affected_deployments = 1
                except client.ApiException:
                    pass
            
            # Environment multiplier
            env_multiplier = {
                "dev": 1.0,
                "staging": 2.0,
                "uat": 2.5,
                "prod": 5.0,
            }
            
            env = settings.app_env.lower()
            multiplier = env_multiplier.get(env, 3.0)
            
            # Base score
            base_score = affected_pods * 5 + affected_deployments * 10
            
            # Critical namespace boost
            critical_namespaces = {"default", "platform", "core-services"}
            if namespace in critical_namespaces:
                base_score *= 1.5
            
            final_score = min(base_score * multiplier, 100)
            
            return {
                "score": round(final_score, 2),
                "affected_pods": affected_pods,
                "affected_deployments": affected_deployments,
                "environment": env,
                "environment_multiplier": multiplier,
                "is_acceptable": final_score < settings.remediation_max_blast_radius,
            }
            
        except Exception as e:
            logger.error("Failed to calculate blast radius", error=str(e))
            return {
                "score": 100,  # Max score on error
                "error": str(e),
                "is_acceptable": False,
            }
    
    async def propose_action(
        self,
        incident: Incident,
        action_type: str,
        target_resource: str,
        parameters: Optional[dict] = None,
    ) -> RemediationAction:
        """Propose a remediation action."""
        # Determine action type enum
        try:
            action_enum = ActionType(action_type)
        except ValueError:
            action_enum = ActionType.ESCALATE_TO_HUMAN
        
        # Get risk level
        risk = self.ACTION_RISKS.get(action_enum, ActionRisk.HIGH)
        
        # Calculate blast radius
        blast_radius = await self.calculate_blast_radius(incident)
        
        # Determine environment
        env_map = {
            "development": Environment.DEV,
            "staging": Environment.STAGING,
            "uat": Environment.UAT,
            "production": Environment.PROD,
            "prod": Environment.PROD,
        }
        environment = env_map.get(settings.app_env.lower(), Environment.PROD)
        
        # Create idempotency key
        idempotency_key = f"{incident.id}_{action_type}_{target_resource}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        
        # Evaluate policy
        policy_result = await self.opa_client.evaluate_remediation(
            action_type=action_type,
            environment=settings.app_env,
            blast_radius_score=blast_radius["score"],
            namespace=incident.namespace,
            affected_replicas=blast_radius.get("affected_pods", 1),
        )
        
        # Determine if approval required
        requires_approval = policy_result.get("requires_approval", True)
        
        # Auto-approve in dev if configured
        if environment == Environment.DEV and settings.remediation_auto_approve_dev:
            requires_approval = False
        
        action = RemediationAction(
            incident_id=incident.id,
            idempotency_key=idempotency_key,
            action_type=action_enum,
            target_resource=target_resource,
            target_namespace=incident.namespace,
            target_cluster=incident.cluster,
            parameters=parameters or {},
            risk_level=risk,
            blast_radius_score=blast_radius["score"],
            affected_replicas=blast_radius.get("affected_pods", 0),
            environment=environment,
            status=ActionStatus.PROPOSED if policy_result["allow"] else ActionStatus.REJECTED,
            status_reason=policy_result.get("reason"),
            requires_approval=requires_approval,
        )
        
        logger.info(
            "Proposed remediation action",
            action_id=str(action.id),
            action_type=action_type,
            allowed=policy_result["allow"],
            requires_approval=requires_approval,
        )
        
        return action
