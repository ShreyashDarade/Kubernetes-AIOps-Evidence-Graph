"""
Rules Engine for deterministic RCA hypothesis generation.
Matches evidence patterns to known issue patterns.
"""
from typing import Any
import structlog
from uuid import uuid4

from src.models import Incident, Hypothesis, HypothesisCategory, HypothesisSource


logger = structlog.get_logger()


# Diagnosis rules - patterns that map to hypotheses
DIAGNOSIS_RULES = [
    {
        "id": "crashloop_recent_deploy",
        "name": "Bad Deployment - CrashLoop",
        "conditions": [
            {"type": "waiting_reason", "values": ["CrashLoopBackOff"]},
            {"type": "recent_deploy", "within_minutes": 30},
        ],
        "category": HypothesisCategory.BAD_DEPLOYMENT,
        "hypothesis": "Recent deployment caused application crash",
        "description": "The application started crash looping immediately after a deployment. The new code or configuration likely contains a bug that prevents startup.",
        "confidence_base": 0.90,
        "actions": [
            "rollback_deployment",
            "Check application logs for startup errors",
            "Review recent code changes in the deployment",
        ],
    },
    {
        "id": "crashloop_no_change",
        "name": "Runtime Error - CrashLoop",
        "conditions": [
            {"type": "waiting_reason", "values": ["CrashLoopBackOff"]},
            {"type": "no_recent_deploy", "within_minutes": 60},
        ],
        "category": HypothesisCategory.EXTERNAL_DEPENDENCY,
        "hypothesis": "Application crashing due to external dependency or data issue",
        "description": "The application is crash looping but there were no recent deployments. This suggests an issue with external dependencies, database state, or corrupted data.",
        "confidence_base": 0.75,
        "actions": [
            "restart_pod",
            "Check external service connectivity",
            "Verify database connections",
            "Review application logs for dependency errors",
        ],
    },
    {
        "id": "oom_killed",
        "name": "Memory Exhaustion",
        "conditions": [
            {"type": "terminated_reason", "values": ["OOMKilled"]},
        ],
        "category": HypothesisCategory.RESOURCE_EXHAUSTION,
        "hypothesis": "Container killed due to memory limit exceeded",
        "description": "The container was terminated because it exceeded its memory limit. This could be a memory leak, insufficient limits, or a sudden spike in memory usage.",
        "confidence_base": 0.95,
        "actions": [
            "Increase memory limits if appropriate",
            "Check for memory leaks in application",
            "Review memory usage patterns",
            "restart_deployment",
        ],
    },
    {
        "id": "oom_high_memory",
        "name": "Memory Pressure",
        "conditions": [
            {"type": "memory_usage_high", "threshold": 0.9},
        ],
        "category": HypothesisCategory.RESOURCE_EXHAUSTION,
        "hypothesis": "Container approaching memory limit",
        "description": "The container is using over 90% of its memory limit and is at risk of OOMKill. Memory limits may be too low or there's a memory leak.",
        "confidence_base": 0.80,
        "actions": [
            "Increase memory limits",
            "Investigate memory usage patterns",
            "Check for memory leaks",
        ],
    },
    {
        "id": "image_pull_failure",
        "name": "Image Pull Error",
        "conditions": [
            {"type": "waiting_reason", "values": ["ImagePullBackOff", "ErrImagePull", "ImageInspectError"]},
        ],
        "category": HypothesisCategory.CONFIGURATION_ERROR,
        "hypothesis": "Failed to pull container image",
        "description": "The container cannot start because the image cannot be pulled. This could be due to incorrect image tag, registry authentication issues, or network problems.",
        "confidence_base": 0.95,
        "actions": [
            "Verify image tag exists in registry",
            "Check imagePullSecrets configuration",
            "Verify registry authentication",
            "Check network connectivity to registry",
        ],
    },
    {
        "id": "node_failure_isolated",
        "name": "Node-Specific Issue",
        "conditions": [
            {"type": "multiple_pods_same_node", "threshold": 2},
            {"type": "node_unhealthy", "conditions": ["DiskPressure", "MemoryPressure", "PIDPressure", "NetworkUnavailable"]},
        ],
        "category": HypothesisCategory.INFRASTRUCTURE_ISSUE,
        "hypothesis": "Failures isolated to problematic node",
        "description": "Multiple pods are failing and they're all on the same node which has unhealthy conditions. The node infrastructure is the likely root cause.",
        "confidence_base": 0.85,
        "actions": [
            "cordon_node",
            "Migrate pods to healthy nodes",
            "Investigate node health",
            "Check node resource usage",
        ],
    },
    {
        "id": "hpa_maxed",
        "name": "Scaling Limit Reached",
        "conditions": [
            {"type": "hpa_at_max", "value": True},
            {"type": "latency_high", "threshold_ms": 1000},
        ],
        "category": HypothesisCategory.SCALING_ISSUE,
        "hypothesis": "HPA at maximum capacity with high latency",
        "description": "The Horizontal Pod Autoscaler is at maximum replicas but latency remains high. The service needs more capacity than currently configured.",
        "confidence_base": 0.80,
        "actions": [
            "scale_replicas",
            "Increase HPA max replicas",
            "Review resource requests/limits",
            "Consider adding nodes to cluster",
        ],
    },
    {
        "id": "readiness_probe_failing",
        "name": "Readiness Probe Failure",
        "conditions": [
            {"type": "pod_not_ready", "duration_seconds": 300},
            {"type": "readiness_probe_failing", "value": True},
        ],
        "category": HypothesisCategory.DEPENDENCY_FAILURE,
        "hypothesis": "Pods failing readiness probe",
        "description": "Pods are not becoming ready because the readiness probe is failing. This usually indicates the application cannot serve traffic due to dependency issues.",
        "confidence_base": 0.75,
        "actions": [
            "Check application health endpoints",
            "Verify database connections",
            "Check external service dependencies",
            "Review probe configuration",
        ],
    },
    {
        "id": "config_error",
        "name": "Configuration Error",
        "conditions": [
            {"type": "terminated_reason", "values": ["ContainerCannotRun", "CreateContainerConfigError"]},
        ],
        "category": HypothesisCategory.CONFIGURATION_ERROR,
        "hypothesis": "Container configuration error",
        "description": "The container cannot run due to a configuration issue such as missing volumes, invalid environment variables, or security context problems.",
        "confidence_base": 0.90,
        "actions": [
            "Check ConfigMap and Secret references",
            "Verify volume mounts",
            "Review container security context",
            "Check environment variable configurations",
        ],
    },
    {
        "id": "network_error",
        "name": "Network Connectivity Issue",
        "conditions": [
            {"type": "log_pattern", "patterns": ["connection refused", "connection reset", "timeout"]},
            {"type": "network_errors_high", "threshold": 10},
        ],
        "category": HypothesisCategory.NETWORK_ISSUE,
        "hypothesis": "Network connectivity problems",
        "description": "The application is experiencing network connectivity issues. This could be DNS problems, service mesh issues, or network policy restrictions.",
        "confidence_base": 0.70,
        "actions": [
            "Check DNS resolution",
            "Verify network policies",
            "Check service mesh configuration",
            "Test connectivity to external services",
        ],
    },
]


class RulesEngine:
    """Deterministic rules engine for RCA hypothesis generation."""
    
    def __init__(self):
        self.rules = DIAGNOSIS_RULES
    
    def generate_hypotheses(
        self,
        incident: Incident,
        evidence: list[dict],
    ) -> list[dict]:
        """Generate hypotheses by matching evidence against rules."""
        hypotheses = []
        
        signals = self._extract_signals(evidence)
        
        logger.debug(
            "Extracted signals",
            incident_id=str(incident.id),
            signals=list(signals.keys()),
        )
        
        for rule in self.rules:
            match_result = self._match_rule(rule, signals)
            
            if match_result["matched"]:
                hypothesis = self._create_hypothesis(incident, rule, match_result)
                hypotheses.append(hypothesis)
                
                logger.info(
                    "Rule matched",
                    rule_id=rule["id"],
                    confidence=hypothesis["confidence"],
                )
        
        hypotheses.sort(key=lambda x: x["confidence"], reverse=True)
        
        if not hypotheses:
            hypotheses.append(self._create_unknown_hypothesis(incident, signals))
        
        return hypotheses
    
    def _create_hypothesis(
        self, 
        incident: Incident, 
        rule: dict, 
        match_result: dict
    ) -> dict:
        """Create a hypothesis from a matched rule."""
        confidence = self._calculate_confidence(
            rule["confidence_base"],
            match_result["match_count"],
            match_result["evidence_strength"],
        )
        
        return {
            "id": str(uuid4()),
            "incident_id": str(incident.id),
            "category": rule["category"].value,
            "title": rule["name"],
            "description": rule["description"],
            "confidence": confidence,
            "rank": 0,
            "supporting_evidence_ids": match_result["evidence_ids"],
            "recommended_actions": rule["actions"],
            "generated_by": HypothesisSource.RULES_ENGINE.value,
            "rule_id": rule["id"],
            "support_count": match_result["match_count"],
            "signal_strength": match_result["evidence_strength"],
        }
    
    def _extract_signals(self, evidence: list[dict]) -> dict:
        """Extract signals from evidence for rule matching."""
        signals = self._init_signals()
        
        for ev in evidence:
            self._process_evidence_item(ev, signals)
        
        return signals
    
    def _init_signals(self) -> dict:
        """Initialize empty signals dict."""
        return {
            "waiting_reasons": set(),
            "terminated_reasons": set(),
            "log_patterns": set(),
            "has_recent_deploy": False,
            "has_image_change": False,
            "memory_usage_high": False,
            "cpu_throttling": False,
            "hpa_at_max": False,
            "node_issues": {},
            "restart_count": 0,
            "error_count": 0,
            "latency_high": False,
            "evidence_ids": [],
        }
    
    def _process_evidence_item(self, ev: dict, signals: dict) -> None:
        """Process a single evidence item and update signals."""
        ev_id = ev.get("id")
        ev_type = ev.get("evidence_type")
        data = ev.get("data", {})
        
        signals["evidence_ids"].append(ev_id)
        
        processors = {
            "kubernetes_pod": self._process_pod_evidence,
            "deploy_change": self._process_deploy_evidence,
            "image_change": self._process_image_evidence,
            "log_signal": self._process_log_evidence,
            "metric_signal": self._process_metric_evidence,
            "kubernetes_node": self._process_node_evidence,
        }
        
        processor = processors.get(ev_type)
        if processor:
            processor(data, signals)
    
    def _process_pod_evidence(self, data: dict, signals: dict) -> None:
        """Process pod evidence."""
        if data.get("waiting_reason"):
            signals["waiting_reasons"].add(data["waiting_reason"])
        if data.get("terminated_reason"):
            signals["terminated_reasons"].add(data["terminated_reason"])
        signals["restart_count"] = max(signals["restart_count"], data.get("restart_count", 0))
    
    def _process_deploy_evidence(self, data: dict, signals: dict) -> None:
        """Process deploy change evidence."""
        if data.get("is_recent_change"):
            signals["has_recent_deploy"] = True
    
    def _process_image_evidence(self, data: dict, signals: dict) -> None:
        """Process image change evidence."""
        if data.get("image_changed"):
            signals["has_image_change"] = True
    
    def _process_log_evidence(self, data: dict, signals: dict) -> None:
        """Process log signal evidence."""
        for pattern in data.get("patterns_found", []):
            signals["log_patterns"].add(pattern)
        signals["error_count"] += data.get("error_count", 0)
    
    def _process_metric_evidence(self, data: dict, signals: dict) -> None:
        """Process metric signal evidence."""
        query_name = data.get("query_name", "")
        
        if "memory" in query_name and data.get("is_anomalous"):
            current = data.get("current_value")
            if current and current > 90:
                signals["memory_usage_high"] = True
        
        if "hpa" in query_name and "max" in query_name and data.get("current_value") == 1:
            signals["hpa_at_max"] = True
        
        if "latency" in query_name and data.get("current_value", 0) > 1:
            signals["latency_high"] = True
    
    def _process_node_evidence(self, data: dict, signals: dict) -> None:
        """Process node evidence."""
        node_name = data.get("name")
        ready_status = data.get("conditions", {}).get("Ready", {}).get("status")
        if ready_status != "True":
            signals["node_issues"][node_name] = data.get("conditions", {})
    
    def _match_rule(self, rule: dict, signals: dict) -> dict:
        """Check if a rule matches the current signals."""
        matched_conditions = 0
        total_conditions = len(rule["conditions"])
        evidence_strength = 0.0
        
        for condition in rule["conditions"]:
            result = self._check_condition(condition, signals)
            if result["matched"]:
                matched_conditions += 1
                evidence_strength += result["strength"]
        
        matched = matched_conditions == total_conditions and total_conditions > 0
        
        return {
            "matched": matched,
            "match_count": matched_conditions,
            "evidence_ids": signals["evidence_ids"][:5],
            "evidence_strength": evidence_strength / max(total_conditions, 1),
        }
    
    def _check_condition(self, condition: dict, signals: dict) -> dict:
        """Check a single condition against signals."""
        cond_type = condition["type"]
        
        checks = {
            "waiting_reason": (
                lambda: bool(signals["waiting_reasons"] & set(condition.get("values", []))),
                0.9
            ),
            "terminated_reason": (
                lambda: bool(signals["terminated_reasons"] & set(condition.get("values", []))),
                0.9
            ),
            "recent_deploy": (lambda: signals["has_recent_deploy"], 0.8),
            "no_recent_deploy": (lambda: not signals["has_recent_deploy"], 0.6),
            "memory_usage_high": (lambda: signals["memory_usage_high"], 0.85),
            "hpa_at_max": (lambda: signals["hpa_at_max"], 0.75),
            "latency_high": (lambda: signals["latency_high"], 0.7),
            "log_pattern": (
                lambda: bool(signals["log_patterns"] & set(condition.get("patterns", []))),
                0.65
            ),
            "node_unhealthy": (lambda: bool(signals["node_issues"]), 0.8),
        }
        
        if cond_type in checks:
            check_fn, strength = checks[cond_type]
            if check_fn():
                return {"matched": True, "strength": strength}
        
        return {"matched": False, "strength": 0.0}
    
    def _calculate_confidence(
        self,
        base_confidence: float,
        match_count: int,
        evidence_strength: float,
    ) -> float:
        """Calculate final confidence score."""
        confidence = base_confidence * 0.6 + evidence_strength * 0.4
        
        if match_count > 2:
            confidence = min(confidence * 1.1, 0.99)
        
        return round(confidence, 3)
    
    def _create_unknown_hypothesis(self, incident: Incident, signals: dict) -> dict:
        """Create a hypothesis when no rules match."""
        return {
            "id": str(uuid4()),
            "incident_id": str(incident.id),
            "category": HypothesisCategory.UNKNOWN.value,
            "title": "Unknown Issue",
            "description": "No specific pattern matched. Manual investigation required.",
            "confidence": 0.3,
            "rank": 1,
            "supporting_evidence_ids": signals["evidence_ids"][:5],
            "recommended_actions": [
                "Review application logs",
                "Check recent deployments",
                "Verify external dependencies",
                "Escalate to engineering team",
            ],
            "generated_by": HypothesisSource.RULES_ENGINE.value,
            "rule_id": "unknown",
            "support_count": 0,
            "signal_strength": 0.0,
        }
