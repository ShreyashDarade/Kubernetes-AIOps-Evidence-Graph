"""
Hypothesis Ranker.
Ranks hypotheses by confidence and evidence support.
"""
import structlog

logger = structlog.get_logger()


class HypothesisRanker:
    """Ranks and prioritizes RCA hypotheses."""
    
    def rank(self, hypotheses: list[dict]) -> list[dict]:
        """
        Rank hypotheses by multiple factors.
        
        Factors considered:
        - Base confidence score
        - Evidence support count
        - Evidence signal strength
        - Recency of related changes
        - Category priority (some issues are more critical)
        """
        if not hypotheses:
            return []
        
        # Category priority weights
        category_weights = {
            "resource_exhaustion": 1.2,  # OOM is critical
            "bad_deployment": 1.15,      # Recent deploy is likely cause
            "configuration_error": 1.1,
            "infrastructure_issue": 1.05,
            "dependency_failure": 1.0,
            "network_issue": 0.95,
            "scaling_issue": 0.9,
            "security_issue": 0.85,
            "external_dependency": 0.8,
            "data_issue": 0.75,
            "unknown": 0.5,
        }
        
        ranked = []
        
        for h in hypotheses:
            # Base score from confidence
            score = h.get("confidence", 0.5)
            
            # Apply category weight
            category = h.get("category", "unknown")
            category_weight = category_weights.get(category, 1.0)
            score *= category_weight
            
            # Boost for evidence support
            support_count = h.get("support_count", 0)
            if support_count > 0:
                score *= 1 + (min(support_count, 5) * 0.05)
            
            # Boost for signal strength
            signal_strength = h.get("signal_strength", 0)
            score *= 1 + (signal_strength * 0.2)
            
            # Store final score
            h["final_score"] = round(score, 4)
            ranked.append(h)
        
        # Sort by final score descending
        ranked.sort(key=lambda x: x["final_score"], reverse=True)
        
        # Assign ranks
        for i, h in enumerate(ranked):
            h["rank"] = i + 1
        
        logger.info(
            "Hypotheses ranked",
            count=len(ranked),
            top_category=ranked[0].get("category") if ranked else None,
            top_score=ranked[0].get("final_score") if ranked else None,
        )
        
        return ranked
