"""
Logs Evidence Collector.
Collects logs from Loki for pods related to the incident.
"""
from datetime import datetime
from typing import Any, Optional
import re
import httpx
import structlog

from src.models import Evidence, EvidenceType, EvidenceSource, CollectorResult, LogEvidence
from src.services.collectors.base import BaseCollector
from src.config import settings


logger = structlog.get_logger()


# Common error patterns to detect
ERROR_PATTERNS = [
    (r"(?i)(error|err|exception|fail|failed|failure)", "error"),
    (r"(?i)(panic|fatal|critical)", "critical"),
    (r"(?i)(OOMKilled|out of memory|OutOfMemoryError)", "oom"),
    (r"(?i)(connection refused|connection reset|timeout|timed out)", "network"),
    (r"(?i)(permission denied|access denied|unauthorized|forbidden)", "auth"),
    (r"(?i)(no such file|not found|missing|does not exist)", "missing"),
    (r"(?i)(null pointer|nil pointer|NullPointerException|segfault)", "null_pointer"),
    (r"(?i)(cannot connect|unable to connect|connection failed)", "connection"),
    (r"(?i)(disk full|no space left|storage.*full)", "disk"),
    (r"(?i)(TLS|SSL|certificate|handshake)", "tls"),
]

# Stack trace patterns
STACK_TRACE_PATTERNS = [
    r"at\s+[\w.$]+\([\w.]+:\d+\)",  # Java
    r"File \"[^\"]+\", line \d+",   # Python
    r"goroutine \d+ \[.+\]:",       # Go
    r"\s+at\s+.+\s+\(.+:\d+:\d+\)",  # JavaScript/Node
]


class LogsCollector(BaseCollector):
    """Collects log evidence from Loki."""
    
    name = "logs"
    
    def __init__(self, incident):
        super().__init__(incident)
        self.loki_url = settings.loki_url
        self.max_lines = settings.max_log_lines
    
    async def collect(self) -> CollectorResult:
        """Collect logs from Loki."""
        evidence = []
        errors = []
        
        namespace = self.incident.namespace
        service_name = self.incident.service
        
        try:
            log_result = await self._query_logs(namespace, service_name)
            
            if log_result:
                analyzed = self._analyze_logs(log_result, service_name or "all")
                evidence.append(analyzed)
            
        except Exception as e:
            errors.append(f"Log collection failed: {e}")
            logger.error("Log collection failed", error=str(e))
        
        return CollectorResult(
            collector_name=self.name,
            success=len(errors) == 0,
            evidence=evidence,
            entities=[],
            relations=[],
            errors=errors,
        )
    
    async def _query_logs(
        self, 
        namespace: str, 
        service_name: Optional[str]
    ) -> list[dict[str, Any]]:
        """Query Loki for logs."""
        query = self._build_logql_query(namespace, service_name)
        
        start_ns = int(self.start_time.timestamp() * 1e9)
        end_ns = int(self.end_time.timestamp() * 1e9)
        
        url = f"{self.loki_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": self.max_lines,
            "direction": "backward",
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("status") != "success":
                logger.warning("Loki query unsuccessful", response=data)
                return []
            
            return self._flatten_log_entries(data)
    
    def _build_logql_query(self, namespace: str, service_name: Optional[str]) -> str:
        """Build LogQL query string."""
        if service_name:
            return f'{{namespace="{namespace}", app="{service_name}"}}'
        return f'{{namespace="{namespace}"}}'
    
    def _flatten_log_entries(self, data: dict) -> list[dict[str, Any]]:
        """Flatten Loki response into log entries."""
        results = data.get("data", {}).get("result", [])
        log_entries = []
        
        for stream in results:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                log_entries.append({
                    "timestamp": int(ts),
                    "line": line,
                    "labels": labels,
                })
        
        return log_entries
    
    def _analyze_logs(
        self, 
        log_entries: list[dict[str, Any]], 
        entity_name: str,
    ) -> Evidence:
        """Analyze logs and extract patterns."""
        analysis = self._extract_log_patterns(log_entries)
        signal_strength = self._calculate_log_signal_strength(analysis)
        
        log_data = {
            "total_lines": len(log_entries),
            "error_count": analysis["error_count"],
            "warning_count": analysis["warning_count"],
            "patterns_found": list(analysis["patterns_found"]),
            "sample_errors": analysis["sample_errors"],
            "stack_traces": analysis["stack_traces"],
            "time_range": {
                "start": self.start_time.isoformat(),
                "end": self.end_time.isoformat(),
            }
        }
        
        summary = self._build_log_summary(log_entries, analysis)
        
        return self.create_evidence(
            evidence_type=EvidenceType.LOG_SIGNAL.value,
            source=EvidenceSource.LOKI.value,
            entity_name=entity_name,
            data=log_data,
            signal_strength=signal_strength,
            summary=summary,
        )
    
    def _extract_log_patterns(self, log_entries: list[dict[str, Any]]) -> dict:
        """Extract patterns from log entries."""
        error_count = 0
        warning_count = 0
        patterns_found = set()
        stack_traces = []
        sample_errors = []
        
        for entry in log_entries:
            line = entry.get("line", "")
            
            matched = self._match_error_patterns(line, patterns_found, sample_errors)
            if matched == "error":
                error_count += 1
            elif matched == "warning":
                warning_count += 1
            
            self._match_stack_traces(line, stack_traces)
        
        return {
            "error_count": error_count,
            "warning_count": warning_count,
            "patterns_found": patterns_found,
            "sample_errors": sample_errors,
            "stack_traces": stack_traces,
        }
    
    def _match_error_patterns(
        self, 
        line: str, 
        patterns_found: set, 
        sample_errors: list
    ) -> Optional[str]:
        """Match error patterns in a log line."""
        for pattern, category in ERROR_PATTERNS:
            if re.search(pattern, line):
                patterns_found.add(category)
                if "error" in category or "critical" in category:
                    if len(sample_errors) < 10:
                        sample_errors.append(line[:500])
                    return "error"
                return "warning"
        return None
    
    def _match_stack_traces(self, line: str, stack_traces: list) -> None:
        """Match stack trace patterns in a log line."""
        if len(stack_traces) >= 5:
            return
        
        for st_pattern in STACK_TRACE_PATTERNS:
            if re.search(st_pattern, line):
                stack_traces.append(line[:1000])
                return
    
    def _calculate_log_signal_strength(self, analysis: dict) -> float:
        """Calculate signal strength from log analysis."""
        error_count = analysis["error_count"]
        warning_count = analysis["warning_count"]
        patterns_found = analysis["patterns_found"]
        
        signal_strength = 0.3
        
        if error_count > 10:
            signal_strength = 0.9
        elif error_count > 5:
            signal_strength = 0.8
        elif error_count > 0:
            signal_strength = 0.6
        elif warning_count > 10:
            signal_strength = 0.5
        
        if "oom" in patterns_found or "critical" in patterns_found:
            signal_strength = max(signal_strength, 0.95)
        
        return signal_strength
    
    def _build_log_summary(
        self, 
        log_entries: list[dict[str, Any]], 
        analysis: dict
    ) -> str:
        """Build summary string for log evidence."""
        summary = f"Analyzed {len(log_entries)} log lines: {analysis['error_count']} errors, {analysis['warning_count']} warnings"
        if analysis["patterns_found"]:
            summary += f". Patterns: {', '.join(analysis['patterns_found'])}"
        return summary
