"""
Metrics Evidence Collector.
Collects metrics from Prometheus for the incident.
"""
from datetime import datetime
from typing import Any, Optional
import httpx
import yaml
import structlog
from pathlib import Path

from src.models import Evidence, EvidenceType, EvidenceSource, CollectorResult, MetricEvidence
from src.services.collectors.base import BaseCollector
from src.config import settings


logger = structlog.get_logger()


class MetricsCollector(BaseCollector):
    """Collects metric evidence from Prometheus."""
    
    name = "metrics"
    
    def __init__(self, incident):
        super().__init__(incident)
        self.prometheus_url = settings.prometheus_url
        self.max_points = settings.max_metric_points
        self.queries = self._load_queries()
    
    def _load_queries(self) -> dict[str, list[dict]]:
        """Load PromQL queries from config."""
        queries_file = Path(__file__).parent.parent.parent / "config" / "promql_queries.yaml"
        
        try:
            with open(queries_file, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.warning("Failed to load PromQL queries config", error=str(e))
            return {}
    
    async def collect(self) -> CollectorResult:
        """Collect metrics from Prometheus."""
        evidence = []
        errors = []
        
        namespace = self.incident.namespace
        service_name = self.incident.service
        
        categories = self._determine_categories()
        
        for category in categories:
            category_queries = self.queries.get(category, [])
            
            for query_config in category_queries:
                try:
                    result = await self._execute_query(
                        query_config,
                        namespace=namespace,
                        service_name=service_name,
                    )
                    
                    if result:
                        evidence.append(result)
                        
                except Exception as e:
                    errors.append(f"Query {query_config.get('name')} failed: {e}")
        
        return CollectorResult(
            collector_name=self.name,
            success=len(errors) == 0,
            evidence=evidence,
            entities=[],
            relations=[],
            errors=errors,
        )
    
    def _determine_categories(self) -> list[str]:
        """Determine which query categories to run based on incident."""
        categories = ["deployment", "resource"]
        
        labels = self.incident.labels
        alertname = labels.get("alertname", "").lower()
        category = labels.get("category", "").lower()
        
        category_mapping = [
            (["crash", "restart"], "crashloop", "crashloop"),
            (["oom", "memory"], "oom", "oom"),
            (["error", "5xx"], "error_rate", "error_rate"),
            (["latency", "slow"], "latency", "latency"),
            (["node"], "node", "node"),
            (["hpa", "scaling"], "scaling", "hpa"),
        ]
        
        for keywords, cat_match, cat_name in category_mapping:
            if any(kw in alertname for kw in keywords) or category == cat_match:
                categories.append(cat_name)
        
        return list(set(categories))
    
    async def _execute_query(
        self,
        query_config: dict,
        namespace: str,
        service_name: Optional[str],
    ) -> Optional[Evidence]:
        """Execute a PromQL query and create evidence."""
        query_name = query_config.get("name", "unknown")
        query_template = query_config.get("query", "")
        description = query_config.get("description", "")
        
        query = self._substitute_query_template(query_template, namespace, service_name)
        
        results = await self._fetch_prometheus_data(query, query_name)
        if not results:
            return None
        
        metric_data = self._process_results(results)
        if not metric_data["values"]:
            return None
        
        signal_strength = self._calculate_signal_strength(metric_data, query_name)
        
        evidence_data = {
            "query_name": query_name,
            "query": query,
            "description": description,
            "series_count": len(results),
            "values": metric_data["values"],
            "current_value": metric_data.get("current_value"),
            "max_value": metric_data.get("max_value"),
            "min_value": metric_data.get("min_value"),
            "avg_value": metric_data.get("avg_value"),
            "is_anomalous": signal_strength > 0.7,
        }
        
        summary = self._build_metric_summary(description, metric_data)
        
        return self.create_evidence(
            evidence_type=EvidenceType.METRIC_SIGNAL.value,
            source=EvidenceSource.PROMETHEUS.value,
            entity_name=query_name,
            data=evidence_data,
            signal_strength=signal_strength,
            summary=summary,
        )
    
    def _substitute_query_template(
        self, 
        query_template: str, 
        namespace: str, 
        service_name: Optional[str]
    ) -> str:
        """Substitute template variables in query."""
        pod_prefix = service_name or ".*"
        query = query_template.replace("{{namespace}}", namespace)
        query = query.replace("{{pod_prefix}}", pod_prefix)
        query = query.replace("{{deployment}}", service_name or ".*")
        return query
    
    async def _fetch_prometheus_data(self, query: str, query_name: str) -> list:
        """Fetch data from Prometheus."""
        start_time = int(self.start_time.timestamp())
        end_time = int(self.end_time.timestamp())
        step = max(15, (end_time - start_time) // 100)
        
        url = f"{self.prometheus_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": start_time,
            "end": end_time,
            "step": step,
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("status") != "success":
                logger.warning("Prometheus query unsuccessful", query=query_name, response=data)
                return []
            
            return data.get("data", {}).get("result", [])
    
    def _build_metric_summary(self, description: str, metric_data: dict) -> str:
        """Build summary string for metric evidence."""
        summary = f"{description}: current={metric_data.get('current_value', 'N/A')}"
        max_val = metric_data.get("max_value")
        if max_val is not None:
            summary += f", max={max_val:.2f}"
        return summary
    
    def _process_results(self, results: list) -> dict[str, Any]:
        """Process Prometheus query results."""
        all_values = []
        
        for result in results:
            metric_labels = result.get("metric", {})
            values = result.get("values", [])
            
            for ts, val in values:
                parsed = self._parse_metric_value(ts, val, metric_labels)
                if parsed:
                    all_values.append(parsed)
        
        all_values.sort(key=lambda x: x["timestamp"])
        
        if len(all_values) > self.max_points:
            step = len(all_values) // self.max_points
            all_values = all_values[::step]
        
        return self._calculate_stats(all_values)
    
    def _parse_metric_value(
        self, 
        ts: float, 
        val: str, 
        labels: dict
    ) -> Optional[dict]:
        """Parse a single metric value."""
        try:
            numeric_val = float(val)
            if numeric_val != float('inf') and numeric_val != float('-inf'):
                return {
                    "timestamp": ts,
                    "value": numeric_val,
                    "labels": labels,
                }
        except (ValueError, TypeError):
            pass
        return None
    
    def _calculate_stats(self, all_values: list) -> dict[str, Any]:
        """Calculate statistics from metric values."""
        numeric_values = [v["value"] for v in all_values]
        
        return {
            "values": all_values[-50:],
            "current_value": numeric_values[-1] if numeric_values else None,
            "max_value": max(numeric_values) if numeric_values else None,
            "min_value": min(numeric_values) if numeric_values else None,
            "avg_value": sum(numeric_values) / len(numeric_values) if numeric_values else None,
        }
    
    def _calculate_signal_strength(self, metric_data: dict, query_name: str) -> float:
        """Calculate signal strength based on metric values."""
        current = metric_data.get("current_value")
        
        if current is None:
            return 0.3
        
        # Check each threshold category
        thresholds = [
            (self._check_restart_threshold, ["restart"]),
            (self._check_error_threshold, ["error", "5xx"]),
            (self._check_memory_threshold, ["memory", "usage"]),
            (self._check_latency_threshold, ["latency"]),
            (self._check_throttle_threshold, ["throttl"]),
            (self._check_oom_threshold, ["oom"]),
            (self._check_hpa_threshold, ["hpa"]),
        ]
        
        for check_fn, keywords in thresholds:
            if any(kw in query_name for kw in keywords):
                return check_fn(current, query_name)
        
        return 0.3
    
    def _check_restart_threshold(self, current: float, query_name: str) -> float:
        """Check restart count thresholds."""
        if current > 5:
            return 0.9
        if current > 2:
            return 0.7
        if current > 0:
            return 0.5
        return 0.3
    
    def _check_error_threshold(self, current: float, query_name: str) -> float:
        """Check error rate thresholds."""
        if current > 0.1:
            return 0.9
        if current > 0.05:
            return 0.8
        if current > 0.01:
            return 0.6
        return 0.3
    
    def _check_memory_threshold(self, current: float, query_name: str) -> float:
        """Check memory usage thresholds."""
        if current > 90:
            return 0.9
        if current > 80:
            return 0.7
        if current > 70:
            return 0.5
        return 0.3
    
    def _check_latency_threshold(self, current: float, query_name: str) -> float:
        """Check latency thresholds."""
        if current > 5:
            return 0.9
        if current > 2:
            return 0.7
        if current > 1:
            return 0.5
        return 0.3
    
    def _check_throttle_threshold(self, current: float, query_name: str) -> float:
        """Check CPU throttling thresholds."""
        if current > 0.5:
            return 0.8
        if current > 0.1:
            return 0.6
        return 0.3
    
    def _check_oom_threshold(self, current: float, query_name: str) -> float:
        """Check OOM thresholds."""
        if current > 0:
            return 0.95
        return 0.3
    
    def _check_hpa_threshold(self, current: float, query_name: str) -> float:
        """Check HPA thresholds."""
        if "max" in query_name and current == 1:
            return 0.8
        return 0.3
