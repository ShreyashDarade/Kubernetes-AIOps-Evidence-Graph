"""
Remediation Executor.
Executes approved remediation actions against Kubernetes.
"""
from datetime import datetime, timezone
from typing import Any
import structlog
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from src.models import Incident, ActionType
from src.config import settings


logger = structlog.get_logger()

# Constants
ERROR_NO_DEPLOYMENT_NAME = "No deployment name specified"


class RemediationExecutor:
    """Executes remediation actions against Kubernetes."""
    
    def __init__(self):
        self._init_client()
    
    def _init_client(self):
        """Initialize Kubernetes client."""
        try:
            if settings.kubeconfig:
                config.load_kube_config(settings.kubeconfig)
            else:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config()
            
            self.core_v1 = client.CoreV1Api()
            self.apps_v1 = client.AppsV1Api()
            
        except Exception as e:
            logger.error("Failed to initialize Kubernetes client", error=str(e))
            raise
    
    def execute(
        self,
        incident: Incident,
        action_type: str,
        parameters: dict = None,
    ) -> dict[str, Any]:
        """Execute a remediation action."""
        parameters = parameters or {}
        
        action_handlers = {
            "restart_pod": self._restart_pod,
            ActionType.RESTART_POD.value: self._restart_pod,
            "restart_deployment": self._restart_deployment,
            ActionType.RESTART_DEPLOYMENT.value: self._restart_deployment,
            "rollback_deployment": self._rollback_deployment,
            ActionType.ROLLBACK_DEPLOYMENT.value: self._rollback_deployment,
            "scale_replicas": self._scale_replicas,
            ActionType.SCALE_REPLICAS.value: self._scale_replicas,
            "cordon_node": self._cordon_node,
            ActionType.CORDON_NODE.value: self._cordon_node,
        }
        
        try:
            handler = action_handlers.get(action_type)
            if handler:
                if action_type == "cordon_node" or action_type == ActionType.CORDON_NODE.value:
                    return handler(parameters)
                return handler(incident, parameters)
            
            return {
                "success": False,
                "error": f"Unknown action type: {action_type}",
            }
                
        except Exception as e:
            logger.error("Action execution failed", action_type=action_type, error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    def _restart_pod(self, incident: Incident, parameters: dict) -> dict:
        """Restart a pod by deleting it (assuming deployment-managed)."""
        namespace = incident.namespace
        pod_name = parameters.get("pod_name")
        
        if not pod_name:
            pod_name = self._find_unhealthy_pod(incident)
        
        if not pod_name:
            return {"success": False, "error": "No pods found"}
        
        try:
            self.core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
            )
            
            logger.info("Deleted pod", pod=pod_name, namespace=namespace)
            
            return {
                "success": True,
                "action": "delete_pod",
                "pod": pod_name,
                "namespace": namespace,
            }
            
        except ApiException as e:
            return {"success": False, "error": str(e)}
    
    def _find_unhealthy_pod(self, incident: Incident) -> str | None:
        """Find an unhealthy pod for the incident's service."""
        namespace = incident.namespace
        label_selector = f"app={incident.service}" if incident.service else None
        
        pods = self.core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
        
        if not pods.items:
            return None
        
        # Find first unhealthy pod
        for pod in pods.items:
            if pod.status.phase != "Running":
                return pod.metadata.name
        
        # Default to first pod
        return pods.items[0].metadata.name
    
    def _restart_deployment(self, incident: Incident, parameters: dict) -> dict:
        """Restart deployment using rollout restart."""
        namespace = incident.namespace
        deployment_name = parameters.get("deployment_name") or incident.service
        
        if not deployment_name:
            return {"success": False, "error": ERROR_NO_DEPLOYMENT_NAME}
        
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            }
            
            self.apps_v1.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=patch,
            )
            
            logger.info("Restarted deployment", deployment=deployment_name, namespace=namespace)
            
            return {
                "success": True,
                "action": "restart_deployment",
                "deployment": deployment_name,
                "namespace": namespace,
            }
            
        except ApiException as e:
            return {"success": False, "error": str(e)}
    
    def _rollback_deployment(self, incident: Incident, parameters: dict) -> dict:
        """Rollback deployment to previous revision."""
        namespace = incident.namespace
        deployment_name = parameters.get("deployment_name") or incident.service
        # Note: revision parameter could be used for specific revision rollback
        
        if not deployment_name:
            return {"success": False, "error": ERROR_NO_DEPLOYMENT_NAME}
        
        try:
            deploy = self.apps_v1.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
            )
            
            rs_list = self.apps_v1.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=f"app={deployment_name}",
            )
            
            rs_sorted = sorted(
                rs_list.items,
                key=lambda x: int(x.metadata.annotations.get(
                    "deployment.kubernetes.io/revision", "0"
                )),
                reverse=True
            )
            
            if len(rs_sorted) < 2:
                return {"success": False, "error": "No previous revision available"}
            
            previous_rs = rs_sorted[1]
            deploy.spec.template = previous_rs.spec.template
            
            self.apps_v1.replace_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=deploy,
            )
            
            logger.info(
                "Rolled back deployment",
                deployment=deployment_name,
                namespace=namespace,
                to_revision=previous_rs.metadata.annotations.get(
                    "deployment.kubernetes.io/revision"
                ),
            )
            
            return {
                "success": True,
                "action": "rollback_deployment",
                "deployment": deployment_name,
                "namespace": namespace,
            }
            
        except ApiException as e:
            return {"success": False, "error": str(e)}
    
    def _scale_replicas(self, incident: Incident, parameters: dict) -> dict:
        """Scale deployment replicas."""
        namespace = incident.namespace
        deployment_name = parameters.get("deployment_name") or incident.service
        replicas = parameters.get("replicas")
        
        if not deployment_name:
            return {"success": False, "error": ERROR_NO_DEPLOYMENT_NAME}
        
        if replicas is None:
            replicas = self._get_current_replicas_plus_one(namespace, deployment_name)
        
        try:
            patch = {"spec": {"replicas": replicas}}
            
            self.apps_v1.patch_namespaced_deployment_scale(
                name=deployment_name,
                namespace=namespace,
                body=patch,
            )
            
            logger.info(
                "Scaled deployment",
                deployment=deployment_name,
                namespace=namespace,
                replicas=replicas,
            )
            
            return {
                "success": True,
                "action": "scale_replicas",
                "deployment": deployment_name,
                "namespace": namespace,
                "replicas": replicas,
            }
            
        except ApiException as e:
            return {"success": False, "error": str(e)}
    
    def _get_current_replicas_plus_one(self, namespace: str, deployment_name: str) -> int:
        """Get current replica count + 1."""
        deploy = self.apps_v1.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
        )
        return (deploy.spec.replicas or 1) + 1
    
    def _cordon_node(self, parameters: dict) -> dict:
        """Cordon a node to prevent new pods from scheduling."""
        node_name = parameters.get("node_name")
        
        if not node_name:
            return {"success": False, "error": "No node name specified"}
        
        try:
            patch = {"spec": {"unschedulable": True}}
            
            self.core_v1.patch_node(
                name=node_name,
                body=patch,
            )
            
            logger.info("Cordoned node", node=node_name)
            
            return {
                "success": True,
                "action": "cordon_node",
                "node": node_name,
            }
            
        except ApiException as e:
            return {"success": False, "error": str(e)}
