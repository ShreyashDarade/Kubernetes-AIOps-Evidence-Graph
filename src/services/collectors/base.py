"""
Base collector class for evidence collection.
All collectors inherit from this and implement the collect method.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import structlog
from prometheus_client import Histogram

from src.models import Incident, Evidence, CollectorResult
from src.config import settings


logger = structlog.get_logger()


# Metrics
COLLECTOR_DURATION = Histogram(
    "aiops_collector_duration_seconds",
    "Evidence collector duration",
    ["collector_name"]
)


class BaseCollector(ABC):
    """Base class for evidence collectors."""
    
    name: str = "base"
    
    def __init__(self, incident: Incident):
        self.incident = incident
        self.time_window_minutes = settings.evidence_time_window_minutes
        self.start_time = self._calculate_start_time()
        self.end_time = datetime.now(timezone.utc)
    
    def _calculate_start_time(self) -> datetime:
        """Calculate the start of the evidence collection time window."""
        return self.incident.started_at - timedelta(minutes=self.time_window_minutes)
    
    @abstractmethod
    async def collect(self) -> CollectorResult:
        """
        Collect evidence from the source.
        
        Returns:
            CollectorResult containing evidence, entities, and relations
        """
        pass
    
    async def run(self) -> CollectorResult:
        """Run the collector with metrics and error handling."""
        start = datetime.now(timezone.utc)
        
        try:
            with COLLECTOR_DURATION.labels(collector_name=self.name).time():
                result = await self.collect()
            
            result.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()
            
            logger.info(
                "Collector completed",
                collector=self.name,
                evidence_count=len(result.evidence),
                entities_count=len(result.entities),
                duration=result.duration_seconds,
            )
            
            return result
            
        except Exception as e:
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            
            logger.error(
                "Collector failed",
                collector=self.name,
                error=str(e),
                duration=duration,
            )
            
            return CollectorResult(
                collector_name=self.name,
                success=False,
                errors=[str(e)],
                duration_seconds=duration,
            )
    
    def create_evidence(
        self,
        evidence_type: str,
        source: str,
        entity_name: str,
        data: dict[str, Any],
        signal_strength: float = 0.5,
        summary: Optional[str] = None,
    ) -> Evidence:
        """Helper to create an evidence object."""
        from src.models import EvidenceType, EvidenceSource
        
        return Evidence(
            incident_id=self.incident.id,
            evidence_type=EvidenceType(evidence_type),
            source=EvidenceSource(source),
            entity_name=entity_name,
            entity_namespace=self.incident.namespace,
            data=data,
            signal_strength=signal_strength,
            summary=summary,
            time_window_start=self.start_time,
            time_window_end=self.end_time,
        )
