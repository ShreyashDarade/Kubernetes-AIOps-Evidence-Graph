"""
Deploy Diff Evidence Collector.
Collects deployment history and recent changes.
"""
from datetime import datetime, timezone
from typing import Any, Optional
import structlog
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from src.models import (
    Evidence, EvidenceType, EvidenceSource, 
    CollectorResult, GraphEntity, GraphRelation, DeploymentChange
)
from src.services.collectors.base import BaseCollector
from src.config import settings


logger = structlog.get_logger()


class DeployDiffCollector(BaseCollector):
    """Collects deployment change evidence."""
    
    name = "deploy_diff"
    
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
            
            self.apps_v1 = client.AppsV1Api()
            self.core_v1 = client.CoreV1Api()
            
        except Exception as e:
            logger.error("Failed to initialize Kubernetes client", error=str(e))
            raise
    
    async def collect(self) -> CollectorResult:
        """Collect deployment change evidence."""
        evidence = []
        entities = []
        relations = []
        errors = []
        
        namespace = self.incident.namespace
        service_name = self.incident.service
        
        # Collect deployment history
        try:
            deploy_result = self._collect_deployment_history(namespace, service_name)
            evidence.extend(deploy_result["evidence"])
            entities.extend(deploy_result["entities"])
            relations.extend(deploy_result["relations"])
        except Exception as e:
            errors.append(f"Deployment history collection failed: {e}")
        
        # Collect ReplicaSet history
        try:
            rs_result = self._collect_replicaset_history(namespace, service_name)
            evidence.extend(rs_result["evidence"])
            entities.extend(rs_result["entities"])
        except Exception as e:
            errors.append(f"ReplicaSet history collection failed: {e}")
        
        # Collect ConfigMap changes
        try:
            cm_result = self._collect_configmap_changes(namespace)
            evidence.extend(cm_result["evidence"])
            entities.extend(cm_result["entities"])
        except Exception as e:
            errors.append(f"ConfigMap collection failed: {e}")
        
        return CollectorResult(
            collector_name=self.name,
            success=len(errors) == 0,
            evidence=evidence,
            entities=entities,
            relations=relations,
            errors=errors,
        )
    
    def _collect_deployment_history(
        self,
        namespace: str,
        service_name: Optional[str]
    ) -> dict[str, Any]:
        """Collect deployment rollout history."""
        evidence = []
        entities = []
        relations = []
        
        try:
            deployments = self.apps_v1.list_namespaced_deployment(namespace=namespace)
        except ApiException as e:
            logger.error("Failed to list deployments", error=str(e))
            return {"evidence": [], "entities": [], "relations": []}
        
        for deploy in deployments.items:
            deploy_name = deploy.metadata.name
            
            # Skip if not matching service
            if service_name and service_name not in deploy_name:
                continue
            
            result = self._process_deployment(deploy, namespace, deploy_name)
            evidence.extend(result["evidence"])
            entities.extend(result["entities"])
            relations.extend(result["relations"])
        
        return {"evidence": evidence, "entities": entities, "relations": relations}
    
    def _process_deployment(
        self, 
        deploy, 
        namespace: str, 
        deploy_name: str
    ) -> dict[str, Any]:
        """Process a single deployment and create evidence."""
        evidence = []
        entities = []
        relations = []
        
        current_revision = deploy.metadata.annotations.get(
            "deployment.kubernetes.io/revision", "0"
        )
        generation = deploy.metadata.generation
        observed_generation = deploy.status.observed_generation
        creation_ts = deploy.metadata.creation_timestamp
        
        current_images = self._extract_images(deploy)
        is_recent, change_age = self._check_recency(creation_ts)
        
        change_data = {
            "deployment_name": deploy_name,
            "namespace": namespace,
            "current_revision": current_revision,
            "generation": generation,
            "observed_generation": observed_generation,
            "current_images": current_images,
            "creation_timestamp": creation_ts.isoformat() if creation_ts else None,
            "is_recent_change": is_recent,
            "change_age_minutes": change_age,
            "strategy": deploy.spec.strategy.type if deploy.spec.strategy else None,
            "replicas": deploy.spec.replicas,
        }
        
        signal_strength = self._calculate_signal_strength(is_recent, change_age, generation, observed_generation)
        summary = self._build_summary(deploy_name, current_revision, is_recent, change_age)
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.DEPLOY_CHANGE.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=deploy_name,
            data=change_data,
            signal_strength=signal_strength,
            summary=summary,
        )
        evidence.append(ev)
        
        # Create graph entities for recent changes
        if is_recent:
            entity, rels = self._create_change_entities(
                namespace, deploy_name, current_revision, current_images, creation_ts
            )
            entities.append(entity)
            relations.extend(rels)
        
        return {"evidence": evidence, "entities": entities, "relations": relations}
    
    def _extract_images(self, deploy) -> list[dict]:
        """Extract container images from deployment."""
        if deploy.spec.template.spec.containers:
            return [
                {"name": c.name, "image": c.image}
                for c in deploy.spec.template.spec.containers
            ]
        return []
    
    def _check_recency(self, creation_ts) -> tuple[bool, Optional[float]]:
        """Check if change is recent."""
        if not creation_ts:
            return False, None
        
        creation_time = creation_ts.replace(tzinfo=None)
        if creation_time >= self.start_time.replace(tzinfo=None):
            change_age = (datetime.now(timezone.utc).replace(tzinfo=None) - creation_time).total_seconds() / 60
            return True, change_age
        return False, None
    
    def _calculate_signal_strength(
        self, 
        is_recent: bool, 
        change_age: Optional[float],
        generation: int,
        observed_generation: int
    ) -> float:
        """Calculate signal strength based on change recency."""
        if is_recent:
            if change_age and change_age < 30:
                return 0.95
            return 0.85
        if generation != observed_generation:
            return 0.7
        return 0.3
    
    def _build_summary(
        self, 
        deploy_name: str, 
        revision: str, 
        is_recent: bool, 
        change_age: Optional[float]
    ) -> str:
        """Build evidence summary string."""
        summary = f"Deployment {deploy_name}: revision {revision}"
        if is_recent:
            if change_age:
                summary += f" (changed {change_age:.0f}m ago)"
            else:
                summary += " (recently changed)"
        return summary
    
    def _create_change_entities(
        self,
        namespace: str,
        deploy_name: str,
        revision: str,
        images: list[dict],
        creation_ts
    ) -> tuple[GraphEntity, list[GraphRelation]]:
        """Create graph entities for a change event."""
        entity = GraphEntity(
            id=f"change:deployment:{namespace}:{deploy_name}:{revision}",
            type="ChangeEvent",
            properties={
                "type": "deployment_update",
                "deployment": deploy_name,
                "namespace": namespace,
                "revision": revision,
                "images": [img["image"] for img in images],
                "changed_at": creation_ts.isoformat() if creation_ts else None,
            }
        )
        
        relations = [
            GraphRelation(
                source_id=f"deployment:{namespace}:{deploy_name}",
                target_id=entity.id,
                relation_type="HAS_RECENT_CHANGE",
            ),
            GraphRelation(
                source_id=f"incident:{self.incident.id}",
                target_id=entity.id,
                relation_type="CORRELATES_WITH",
            ),
        ]
        
        return entity, relations
    
    def _collect_replicaset_history(
        self,
        namespace: str,
        service_name: Optional[str]
    ) -> dict[str, Any]:
        """Collect ReplicaSet history for image changes."""
        evidence = []
        entities = []
        
        try:
            replicasets = self.apps_v1.list_namespaced_replica_set(namespace=namespace)
        except ApiException as e:
            logger.error("Failed to list replicasets", error=str(e))
            return {"evidence": [], "entities": []}
        
        rs_by_deployment = self._group_replicasets_by_deployment(replicasets, service_name)
        
        for deploy_name, rs_list in rs_by_deployment.items():
            if len(rs_list) < 2:
                continue
            
            ev = self._create_replicaset_evidence(deploy_name, rs_list)
            if ev:
                evidence.append(ev)
        
        return {"evidence": evidence, "entities": entities}
    
    def _group_replicasets_by_deployment(
        self, 
        replicasets, 
        service_name: Optional[str]
    ) -> dict[str, list]:
        """Group ReplicaSets by their owner deployment."""
        rs_by_deployment = {}
        
        for rs in replicasets.items:
            self._add_replicaset_to_group(rs, service_name, rs_by_deployment)
        
        return rs_by_deployment
    
    def _add_replicaset_to_group(
        self,
        rs,
        service_name: Optional[str],
        rs_by_deployment: dict
    ) -> None:
        """Add a ReplicaSet to its deployment group."""
        if not rs.metadata.owner_references:
            return
        
        deploy_name = self._get_deployment_owner(rs, service_name)
        if not deploy_name:
            return
        
        if deploy_name not in rs_by_deployment:
            rs_by_deployment[deploy_name] = []
        
        rs_by_deployment[deploy_name].append(self._extract_replicaset_info(rs))
    
    def _get_deployment_owner(self, rs, service_name: Optional[str]) -> Optional[str]:
        """Get the deployment owner name if it matches the service filter."""
        for owner in rs.metadata.owner_references:
            if owner.kind != "Deployment":
                continue
            
            deploy_name = owner.name
            if service_name and service_name not in deploy_name:
                continue
            
            return deploy_name
        return None
    
    def _extract_replicaset_info(self, rs) -> dict:
        """Extract relevant info from a ReplicaSet."""
        containers = rs.spec.template.spec.containers
        return {
            "name": rs.metadata.name,
            "revision": rs.metadata.annotations.get(
                "deployment.kubernetes.io/revision", "0"
            ),
            "replicas": rs.status.replicas or 0,
            "available_replicas": rs.status.available_replicas or 0,
            "images": [c.image for c in containers] if containers else [],
            "created_at": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
        }
    
    def _create_replicaset_evidence(
        self, 
        deploy_name: str, 
        rs_list: list
    ) -> Optional[Evidence]:
        """Create evidence for ReplicaSet history."""
        rs_list.sort(key=lambda x: int(x["revision"]), reverse=True)
        
        current = rs_list[0]
        previous = rs_list[1] if len(rs_list) > 1 else None
        
        new_images = current["images"]
        old_images = previous["images"] if previous else []
        image_changed = old_images != new_images
        
        change_data = {
            "deployment": deploy_name,
            "current_revision": current["revision"],
            "previous_revision": previous["revision"] if previous else None,
            "current_images": new_images,
            "previous_images": old_images,
            "image_changed": image_changed,
            "revision_count": len(rs_list),
        }
        
        signal_strength = 0.85 if image_changed else 0.5
        
        summary = f"ReplicaSet history for {deploy_name}: {len(rs_list)} revisions"
        if image_changed:
            summary += " (image changed)"
        
        return self.create_evidence(
            evidence_type=EvidenceType.IMAGE_CHANGE.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=deploy_name,
            data=change_data,
            signal_strength=signal_strength,
            summary=summary,
        )
    
    def _collect_configmap_changes(self, namespace: str) -> dict[str, Any]:
        """Collect ConfigMap information."""
        evidence = []
        entities = []
        
        try:
            configmaps = self.core_v1.list_namespaced_config_map(namespace=namespace)
        except ApiException as e:
            logger.error("Failed to list configmaps", error=str(e))
            return {"evidence": [], "entities": []}
        
        for cm in configmaps.items:
            result = self._process_configmap(cm, namespace)
            if result:
                evidence.append(result["evidence"])
                entities.append(result["entity"])
        
        return {"evidence": evidence, "entities": entities}
    
    def _process_configmap(self, cm, namespace: str) -> Optional[dict]:
        """Process a single ConfigMap."""
        # Skip system configmaps
        if cm.metadata.name.startswith("kube-"):
            return None
        
        creation_ts = cm.metadata.creation_timestamp
        if not creation_ts:
            return None
        
        creation_time = creation_ts.replace(tzinfo=None)
        start_time = self.start_time.replace(tzinfo=None) if self.start_time.tzinfo else self.start_time
        
        if creation_time < start_time:
            return None
        
        cm_data = {
            "name": cm.metadata.name,
            "namespace": namespace,
            "keys": list(cm.data.keys()) if cm.data else [],
            "created_at": creation_ts.isoformat(),
            "resource_version": cm.metadata.resource_version,
        }
        
        ev = self.create_evidence(
            evidence_type=EvidenceType.CONFIG_CHANGE.value,
            source=EvidenceSource.KUBERNETES_API.value,
            entity_name=cm.metadata.name,
            data=cm_data,
            signal_strength=0.6,
            summary=f"ConfigMap {cm.metadata.name} recently modified",
        )
        
        entity = GraphEntity(
            id=f"configmap:{namespace}:{cm.metadata.name}",
            type="ConfigMap",
            properties={
                "name": cm.metadata.name,
                "namespace": namespace,
                "keys": list(cm.data.keys()) if cm.data else [],
            }
        )
        
        return {"evidence": ev, "entity": entity}
