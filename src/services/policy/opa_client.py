"""
OPA Policy Client.
Evaluates remediation policies using Open Policy Agent.
"""
from datetime import datetime, timezone
from typing import Any, Optional
import httpx
import structlog

from src.config import settings


logger = structlog.get_logger()


class OPAClient:
    """Client for Open Policy Agent policy evaluation."""
    
    def __init__(self):
        self.opa_url = settings.opa_url
        self.policy_path = settings.opa_policy_path
    
    async def evaluate_remediation(
        self,
        action_type: str,
        environment: str,
        blast_radius_score: float,
        namespace: str,
        affected_replicas: int = 1,
    ) -> dict[str, Any]:
        """
        Evaluate remediation policy.
        
        Returns:
            {
                "allow": bool,
                "requires_approval": bool,
                "deny_reasons": list[str],
                "reason": str
            }
        """
        now = datetime.now(timezone.utc)
        
        input_data = {
            "action_type": action_type,
            "environment": environment,
            "blast_radius_score": blast_radius_score,
            "namespace": namespace,
            "affected_replicas": affected_replicas,
            "current_hour": now.hour,
            "is_weekend": now.weekday() >= 5,
            "freeze_active": False,  # Could be set by config
        }
        
        try:
            result = await self._query_opa(input_data)
            
            allow = result.get("allow", False)
            requires_approval = result.get("requires_approval", True)
            deny_reasons = result.get("deny", [])
            
            reason = self._build_reason(allow, deny_reasons)
            
            logger.info(
                "Policy evaluation complete",
                action_type=action_type,
                environment=environment,
                allow=allow,
                requires_approval=requires_approval,
            )
            
            return {
                "allow": allow,
                "requires_approval": requires_approval,
                "deny_reasons": deny_reasons,
                "reason": reason,
            }
            
        except Exception as e:
            logger.error("OPA evaluation failed", error=str(e))
            # Fail closed - deny by default
            return {
                "allow": False,
                "requires_approval": True,
                "deny_reasons": [f"Policy evaluation error: {e}"],
                "reason": f"Policy evaluation error: {e}",
            }
    
    def _build_reason(self, allow: bool, deny_reasons: list) -> str:
        """Build reason string from policy result."""
        if allow:
            return "allowed"
        if deny_reasons:
            return "; ".join(deny_reasons)
        return "policy denied"
    
    async def _query_opa(self, input_data: dict) -> dict:
        """Query OPA for policy decision."""
        url = f"{self.opa_url}{self.policy_path}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json={"input": input_data},
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get("result", {})
    
    async def check_health(self) -> bool:
        """Check if OPA is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.opa_url}/health")
                return response.status_code == 200
        except Exception:
            return False
