"""
Remediation Verifier.
Verifies that remediation actions were successful by checking metrics.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import structlog
import httpx
from kubernetes import client, config

from src.models import Incident
from src.config import settings


logger = structlog.get_logger()


class RemediationVerifier:
    """Verifies remediation success by checking metrics and state."""
    
    def __init__(self):
        self.prometheus_url = settings.prometheus_url
    
    async def verify(
        self,
        incident: Incident,
    ) -> dict[str, Any]:
        """Verify that remediation improved the situation."""
        namespace = incident.namespace
        service = incident.service
        
        try:
            error_rate = await self._check_error_rate(namespace, service)
            restart_rate = await self._check_restart_rate(namespace, service)
            pod_health = self._check_pod_health(namespace, service)
            
            metrics_improved = (
                error_rate.get("improved", False) or
                restart_rate.get("improved", False) or
                pod_health.get("healthy", False)
            )
            
            success = metrics_improved and pod_health.get("healthy", False)
            
            result = {
                "success": success,
                "metrics_improved": metrics_improved,
                "error_rate": error_rate,
                "restart_rate": restart_rate,
                "pod_health": pod_health,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            
            logger.info(
                "Verification complete",
                incident_id=str(incident.id),
                success=success,
                metrics_improved=metrics_improved,
            )
            
            return result
            
        except Exception as e:
            logger.error("Verification failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    async def _check_error_rate(self, namespace: str, service: Optional[str]) -> dict:
        """Check if error rate has decreased."""
        try:
            pod_filter = f', pod=~"{service}.*"' if service else ""
            query = f'sum(rate(http_requests_total{{namespace="{namespace}"{pod_filter}, status=~"5.."}}[5m])) / sum(rate(http_requests_total{{namespace="{namespace}"{pod_filter}}}[5m]))'
            
            current = await self._query_prometheus(query)
            
            query_before = f'sum(rate(http_requests_total{{namespace="{namespace}"{pod_filter}, status=~"5.."}}[5m] offset 15m)) / sum(rate(http_requests_total{{namespace="{namespace}"{pod_filter}}}[5m] offset 15m))'
            
            before = await self._query_prometheus(query_before)
            
            improved = self._is_metric_improved(current, before)
            
            return {
                "current": current,
                "before": before,
                "improved": improved,
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def _check_restart_rate(self, namespace: str, service: Optional[str]) -> dict:
        """Check if restart rate has decreased."""
        try:
            pod_prefix = service or ".*"
            
            query = f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{namespace}", pod=~"{pod_prefix}.*"}}[5m]))'
            
            current = await self._query_prometheus(query)
            
            query_before = f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{namespace}", pod=~"{pod_prefix}.*"}}[5m] offset 15m))'
            
            before = await self._query_prometheus(query_before)
            
            improved = current is not None and before is not None and current <= before
            
            return {
                "current": current,
                "before": before,
                "improved": improved,
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def _is_metric_improved(
        self, 
        current: Optional[float], 
        before: Optional[float]
    ) -> bool:
        """Check if metric has improved (decreased)."""
        return current is not None and before is not None and current < before
    
    def _check_pod_health(self, namespace: str, service: Optional[str]) -> dict:
        """Check if pods are healthy."""
        try:
            self._init_kube_config()
            core_v1 = client.CoreV1Api()
            
            label_selector = f"app={service}" if service else None
            
            pods = core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
            
            total = len(pods.items)
            healthy = sum(1 for pod in pods.items if self._is_pod_healthy(pod))
            
            return {
                "total": total,
                "healthy": healthy,
                "healthy_percentage": (healthy / total * 100) if total > 0 else 0,
                "all_healthy": healthy == total,
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def _init_kube_config(self) -> None:
        """Initialize Kubernetes config."""
        if settings.kubeconfig:
            config.load_kube_config(settings.kubeconfig)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
    
    def _is_pod_healthy(self, pod) -> bool:
        """Check if a pod is healthy."""
        if pod.status.phase != "Running":
            return False
        
        for cond in pod.status.conditions or []:
            if cond.type == "Ready" and cond.status != "True":
                return False
        
        return True
    
    async def _query_prometheus(self, query: str) -> Optional[float]:
        """Query Prometheus for a single value."""
        url = f"{self.prometheus_url}/api/v1/query"
        
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(url, params={"query": query})
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("status") != "success":
                return None
            
            results = data.get("data", {}).get("result", [])
            
            if not results:
                return None
            
            try:
                return float(results[0]["value"][1])
            except (IndexError, KeyError, ValueError):
                return None
