"""
Kubernetes Evidence Collector.
Collects pod, deployment, replicaset, events, node, and HPA information.
"""
from datetime import datetime, timezone
from typing import Any, Optional
import structlog
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from src.models import (
    Evidence, EvidenceType, EvidenceSource,
    CollectorResult, GraphEntity, GraphRelation,
)
from src.services.collectors.base import BaseCollector
from src.config import settings


logger = structlog.get_logger()


class KubernetesCollector(BaseCollector):
    """Collects evidence from Kubernetes API."""
    
    name = "kubernetes"
    
    def __init__(self, incident):
        super().__init__(incident)
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
            self.autoscaling_v1 = client.AutoscalingV1Api()
            
        except Exception as e:
            logger.error("Failed to initialize Kubernetes client", error=str(e))
            raise
    
    async def collect(self) -> CollectorResult:
        """Collect Kubernetes evidence."""
        evidence = []
        entities = []
        relations = []
        errors = []
        
        namespace = self.incident.namespace
        service_name = self.incident.service
        
        # Collect from all sources
        collectors = [
            ("pods", lambda: self._collect_pods(namespace, service_name)),
            ("deployments", lambda: self._collect_deployments(namespace, service_name)),
            ("events", lambda: self._collect_events(namespace)),
            ("nodes", lambda: self._collect_nodes()),
            ("hpa", lambda: self._collect_hpa(namespace)),
        ]
        
        for name, collector_fn in collectors:
            try:
                result = collector_fn()
                evidence.extend(result.get("evidence", []))
                entities.extend(result.get("entities", []))
                relations.extend(result.get("relations", []))
            except Exception as e:
                errors.append(f"{name} collection failed: {e}")
        
        # Create incident entity
        entities.append(self._create_incident_entity(namespace))
        
        return CollectorResult(
            collector_name=self.name,
            success=len(errors) == 0,
            evidence=evidence,
            entities=entities,
            relations=relations,
            errors=errors,
        )
    
    def _create_incident_entity(self, namespace: str) -> GraphEntity:
        """Create incident graph entity."""
        return GraphEntity(
            id=f"incident:{self.incident.id}",
            type="Incident",
            properties={
                "id": str(self.incident.id),
                "title": self.incident.title,
                "severity": self.incident.severity.value,
                "namespace": namespace,
                "started_at": self.incident.started_at.isoformat(),
            }
        )
    
    def _collect_pods(
        self, 
        namespace: str, 
        service_name: Optional[str]
    ) -> dict[str, Any]:
        """Collect pod information."""
        evidence = []
        entities = []
        relations = []
        
        label_selector = f"app={service_name}" if service_name else None
        
        try:
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
        except ApiException as e:
            logger.error("Failed to list pods", error=str(e))
            return {"evidence": [], "entities": [], "relations": []}
        
        for pod in pods.items:
            result = self._process_pod(pod, namespace)
            evidence.append(result["evidence"])
            entities.append(result["entity"])
            relations.extend(result["relations"])
        
        return {"evidence": evidence, "entities": entities, "relations": relations}
    
    def _process_pod(self, pod, namespace: str) -> dict[str, Any]:
        """Process a single pod."""
        pod_name = pod.metadata.name
        pod_uid = pod.metadata.uid
        phase = pod.status.phase
        
        conditions = self._extract_pod_conditions(pod)
        container_info = self._extract_container_info(pod)
        resources = self._extract_resources(pod)
        
        signal_strength = self._calculate_pod_signal_strength(
            container_info["waiting_reason"],
            container_info["terminated_reason"],
            container_info["restart_count"],
            phase
        )
        
        pod_data = {
            "name": pod_name,
            "namespace": namespace,
            "phase": phase,
            "node_name": pod.spec.node_name,
            "restart_count": container_info["restart_count"],
            "waiting_reason": container_info["waiting_reason"],
            "terminated_reason": container_info["terminated_reason"],
            "conditions": conditions,
            "container_statuses": container_info["statuses"],
            "resources": resources,
            "labels": dict(pod.metadata.labels or {}),
            "created_at": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
        }
        
        summary = self._build_pod_summary(pod_name, phase, container_info)
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.KUBERNETES_POD.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=pod_name,
            data=pod_data,
            signal_strength=signal_strength,
            summary=summary,
        )
        
        entity = GraphEntity(
            id=f"pod:{namespace}:{pod_name}",
            type="Pod",
            properties={
                "name": pod_name,
                "namespace": namespace,
                "uid": pod_uid,
                "phase": phase,
                "restart_count": container_info["restart_count"],
                "waiting_reason": container_info["waiting_reason"],
                "node_name": pod.spec.node_name,
            }
        )
        
        relations = self._create_pod_relations(pod, namespace, pod_name)
        
        return {"evidence": ev, "entity": entity, "relations": relations}
    
    def _extract_pod_conditions(self, pod) -> list[dict]:
        """Extract pod conditions."""
        if not pod.status.conditions:
            return []
        return [
            {"type": c.type, "status": c.status, "reason": c.reason}
            for c in pod.status.conditions
        ]
    
    def _extract_container_info(self, pod) -> dict[str, Any]:
        """Extract container status information."""
        statuses = []
        restart_count = 0
        waiting_reason = None
        terminated_reason = None
        
        if not pod.status.container_statuses:
            return {
                "statuses": statuses,
                "restart_count": restart_count,
                "waiting_reason": waiting_reason,
                "terminated_reason": terminated_reason,
            }
        
        for cs in pod.status.container_statuses:
            restart_count += cs.restart_count
            status_info = {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
            }
            
            if cs.state.waiting:
                waiting_reason = cs.state.waiting.reason
                status_info["waiting"] = {
                    "reason": cs.state.waiting.reason,
                    "message": cs.state.waiting.message,
                }
            
            if cs.state.terminated:
                terminated_reason = cs.state.terminated.reason
                status_info["terminated"] = {
                    "reason": cs.state.terminated.reason,
                    "exit_code": cs.state.terminated.exit_code,
                }
            
            if cs.last_state and cs.last_state.terminated:
                status_info["last_terminated"] = {
                    "reason": cs.last_state.terminated.reason,
                    "exit_code": cs.last_state.terminated.exit_code,
                }
            
            statuses.append(status_info)
        
        return {
            "statuses": statuses,
            "restart_count": restart_count,
            "waiting_reason": waiting_reason,
            "terminated_reason": terminated_reason,
        }
    
    def _extract_resources(self, pod) -> dict:
        """Extract resource info from pod."""
        resources = {}
        if not pod.spec.containers:
            return resources
        
        for container in pod.spec.containers:
            if container.resources:
                resources[container.name] = {
                    "requests": container.resources.requests,
                    "limits": container.resources.limits,
                }
        return resources
    
    def _calculate_pod_signal_strength(
        self,
        waiting_reason: Optional[str],
        terminated_reason: Optional[str],
        restart_count: int,
        phase: str
    ) -> float:
        """Calculate signal strength for pod."""
        if waiting_reason in ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"]:
            return 0.95
        if terminated_reason == "OOMKilled":
            return 0.95
        if restart_count > 3:
            return 0.8
        if phase != "Running":
            return 0.7
        return 0.3
    
    def _build_pod_summary(self, pod_name: str, phase: str, container_info: dict) -> str:
        """Build summary string for pod."""
        summary = f"Pod {pod_name}: {phase}"
        if container_info["waiting_reason"]:
            summary += f" (waiting: {container_info['waiting_reason']})"
        if container_info["restart_count"] > 0:
            summary += f", {container_info['restart_count']} restarts"
        return summary
    
    def _create_pod_relations(self, pod, namespace: str, pod_name: str) -> list[GraphRelation]:
        """Create graph relations for pod."""
        relations = []
        
        if pod.spec.node_name:
            relations.append(GraphRelation(
                source_id=f"pod:{namespace}:{pod_name}",
                target_id=f"node:{pod.spec.node_name}",
                relation_type="SCHEDULED_ON",
            ))
        
        relations.append(GraphRelation(
            source_id=f"incident:{self.incident.id}",
            target_id=f"pod:{namespace}:{pod_name}",
            relation_type="AFFECTS",
        ))
        
        return relations
    
    def _collect_deployments(
        self, 
        namespace: str,
        service_name: Optional[str]
    ) -> dict[str, Any]:
        """Collect deployment information."""
        evidence = []
        entities = []
        relations = []
        
        try:
            deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
        except ApiException as e:
            logger.error("Failed to list deployments", error=str(e))
            return {"evidence": [], "entities": [], "relations": []}
        
        for deploy in deployments.items:
            if service_name and service_name not in deploy.metadata.name:
                continue
            
            result = self._process_deployment(deploy, namespace)
            evidence.append(result["evidence"])
            entities.append(result["entity"])
        
        return {"evidence": evidence, "entities": entities, "relations": relations}
    
    def _process_deployment(self, deploy, namespace: str) -> dict[str, Any]:
        """Process a single deployment."""
        deploy_name = deploy.metadata.name
        
        replicas = deploy.status.replicas or 0
        ready_replicas = deploy.status.ready_replicas or 0
        unavailable_replicas = deploy.status.unavailable_replicas or 0
        
        conditions = self._extract_deploy_conditions(deploy)
        images = [c.image for c in deploy.spec.template.spec.containers] if deploy.spec.template.spec.containers else []
        
        deploy_data = {
            "name": deploy_name,
            "namespace": namespace,
            "replicas": replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": deploy.status.available_replicas or 0,
            "unavailable_replicas": unavailable_replicas,
            "conditions": conditions,
            "images": images,
            "generation": deploy.metadata.generation,
            "observed_generation": deploy.status.observed_generation,
            "strategy": deploy.spec.strategy.type if deploy.spec.strategy else None,
        }
        
        signal_strength = self._calculate_deploy_signal_strength(
            unavailable_replicas, ready_replicas, replicas
        )
        
        summary = f"Deployment {deploy_name}: {ready_replicas}/{replicas} ready"
        if unavailable_replicas > 0:
            summary += f", {unavailable_replicas} unavailable"
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.KUBERNETES_DEPLOYMENT.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=deploy_name,
            data=deploy_data,
            signal_strength=signal_strength,
            summary=summary,
        )
        
        entity = GraphEntity(
            id=f"deployment:{namespace}:{deploy_name}",
            type="Deployment",
            properties={
                "name": deploy_name,
                "namespace": namespace,
                "replicas": replicas,
                "ready_replicas": ready_replicas,
                "unavailable_replicas": unavailable_replicas,
            }
        )
        
        return {"evidence": ev, "entity": entity}
    
    def _extract_deploy_conditions(self, deploy) -> list[dict]:
        """Extract deployment conditions."""
        if not deploy.status.conditions:
            return []
        return [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in deploy.status.conditions
        ]
    
    def _calculate_deploy_signal_strength(
        self,
        unavailable_replicas: int,
        ready_replicas: int,
        replicas: int
    ) -> float:
        """Calculate signal strength for deployment."""
        if unavailable_replicas > 0:
            return 0.8
        if ready_replicas < replicas:
            return 0.7
        return 0.3
    
    def _collect_events(self, namespace: str) -> dict[str, Any]:
        """Collect Kubernetes events."""
        evidence = []
        entities = []
        
        try:
            events = self.core_v1.list_namespaced_event(namespace=namespace, limit=100)
        except ApiException as e:
            logger.error("Failed to list events", error=str(e))
            return {"evidence": [], "entities": []}
        
        for event in events.items:
            ev = self._process_event(event)
            if ev:
                evidence.append(ev)
        
        return {"evidence": evidence, "entities": entities}
    
    def _process_event(self, event) -> Optional[Evidence]:
        """Process a single event."""
        event_time = event.last_timestamp or event.event_time
        if not event_time:
            return None
        
        start_time = self.start_time.replace(tzinfo=None) if self.start_time.tzinfo else self.start_time
        if event_time.replace(tzinfo=None) < start_time:
            return None
        
        if event.type not in ["Warning", "Normal"]:
            return None
        
        event_data = {
            "type": event.type,
            "reason": event.reason,
            "message": event.message,
            "involved_object": {
                "kind": event.involved_object.kind,
                "name": event.involved_object.name,
                "namespace": event.involved_object.namespace,
            },
            "count": event.count,
            "first_timestamp": event.first_timestamp.isoformat() if event.first_timestamp else None,
            "last_timestamp": event_time.isoformat(),
        }
        
        signal_strength = self._calculate_event_signal_strength(event)
        summary = f"Event: {event.reason} - {event.message[:100]}"
        
        return self.create_evidence(
            evidence_type=EvidenceType.KUBERNETES_EVENT.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=event.involved_object.name,
            data=event_data,
            signal_strength=signal_strength,
            summary=summary,
        )
    
    def _calculate_event_signal_strength(self, event) -> float:
        """Calculate signal strength for event."""
        if event.type != "Warning":
            return 0.4
        if event.reason in ["FailedScheduling", "FailedMount", "BackOff", "Unhealthy", "Failed"]:
            return 0.9
        return 0.7
    
    def _collect_nodes(self) -> dict[str, Any]:
        """Collect node information."""
        evidence = []
        entities = []
        relations = []
        
        try:
            nodes = self.core_v1.list_node()
        except ApiException as e:
            logger.error("Failed to list nodes", error=str(e))
            return {"evidence": [], "entities": [], "relations": []}
        
        for node in nodes.items:
            result = self._process_node(node)
            if result:
                evidence.append(result["evidence"])
                entities.append(result["entity"])
        
        return {"evidence": evidence, "entities": entities, "relations": relations}
    
    def _process_node(self, node) -> Optional[dict[str, Any]]:
        """Process a single node."""
        node_name = node.metadata.name
        conditions, is_healthy = self._extract_node_conditions(node)
        
        # Only include unhealthy nodes
        if is_healthy:
            return None
        
        node_data = {
            "name": node_name,
            "conditions": conditions,
            "allocatable": dict(node.status.allocatable or {}),
            "capacity": dict(node.status.capacity or {}),
            "node_info": {
                "kernel_version": node.status.node_info.kernel_version if node.status.node_info else None,
                "kubelet_version": node.status.node_info.kubelet_version if node.status.node_info else None,
            }
        }
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.KUBERNETES_NODE.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=node_name,
            data=node_data,
            signal_strength=0.9,
            summary=f"Node {node_name}: unhealthy",
        )
        
        entity = GraphEntity(
            id=f"node:{node_name}",
            type="Node",
            properties={"name": node_name, "ready": False}
        )
        
        return {"evidence": ev, "entity": entity}
    
    def _extract_node_conditions(self, node) -> tuple[dict, bool]:
        """Extract node conditions and check health."""
        conditions = {}
        is_healthy = True
        
        for condition in node.status.conditions or []:
            conditions[condition.type] = {
                "status": condition.status,
                "reason": condition.reason,
                "message": condition.message,
            }
            if condition.type == "Ready" and condition.status != "True":
                is_healthy = False
            if condition.type in ["MemoryPressure", "DiskPressure", "PIDPressure"] and condition.status == "True":
                is_healthy = False
        
        return conditions, is_healthy
    
    def _collect_hpa(self, namespace: str) -> dict[str, Any]:
        """Collect HPA information."""
        evidence = []
        entities = []
        
        try:
            hpas = self.autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(namespace=namespace)
        except ApiException as e:
            logger.error("Failed to list HPAs", error=str(e))
            return {"evidence": [], "entities": []}
        
        for hpa in hpas.items:
            result = self._process_hpa(hpa, namespace)
            evidence.append(result["evidence"])
            entities.append(result["entity"])
        
        return {"evidence": evidence, "entities": entities}
    
    def _process_hpa(self, hpa, namespace: str) -> dict[str, Any]:
        """Process a single HPA."""
        hpa_name = hpa.metadata.name
        current_replicas = hpa.status.current_replicas or 0
        max_replicas = hpa.spec.max_replicas
        
        hpa_data = {
            "name": hpa_name,
            "namespace": namespace,
            "current_replicas": current_replicas,
            "desired_replicas": hpa.status.desired_replicas or 0,
            "min_replicas": hpa.spec.min_replicas or 1,
            "max_replicas": max_replicas,
            "target_ref": {
                "kind": hpa.spec.scale_target_ref.kind,
                "name": hpa.spec.scale_target_ref.name,
            },
            "current_cpu_utilization": hpa.status.current_cpu_utilization_percentage,
            "target_cpu_utilization": hpa.spec.target_cpu_utilization_percentage,
        }
        
        at_max = current_replicas >= max_replicas
        signal_strength = 0.8 if at_max else 0.3
        
        summary = f"HPA {hpa_name}: {current_replicas}/{max_replicas} replicas"
        if at_max:
            summary += " (at max!)"
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.KUBERNETES_HPA.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=hpa_name,
            data=hpa_data,
            signal_strength=signal_strength,
            summary=summary,
        )
        
        entity = GraphEntity(
            id=f"hpa:{namespace}:{hpa_name}",
            type="HPA",
            properties={
                "name": hpa_name,
                "current_replicas": current_replicas,
                "max_replicas": max_replicas,
                "at_max": at_max,
            }
        )
        
        return {"evidence": ev, "entity": entity}
