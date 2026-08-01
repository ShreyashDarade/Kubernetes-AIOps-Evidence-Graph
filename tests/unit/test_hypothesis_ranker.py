"""Tests for hypothesis ranking."""
from src.services.rca.hypothesis_ranker import HypothesisRanker


def make_hypothesis(category: str, confidence: float, support_count: int = 0, signal_strength: float = 0.0) -> dict:
    return {
        "category": category,
        "confidence": confidence,
        "support_count": support_count,
        "signal_strength": signal_strength,
    }


def test_rank_empty_list_returns_empty():
    assert HypothesisRanker().rank([]) == []


def test_rank_assigns_sequential_ranks():
    hypotheses = [
        make_hypothesis("unknown", 0.3),
        make_hypothesis("resource_exhaustion", 0.9),
    ]

    ranked = HypothesisRanker().rank(hypotheses)

    assert [h["rank"] for h in ranked] == [1, 2]


def test_higher_category_weight_can_overtake_higher_raw_confidence():
    # resource_exhaustion (weight 1.2) at 0.7 confidence should outrank
    # external_dependency (weight 0.8) at 0.75 confidence.
    hypotheses = [
        make_hypothesis("external_dependency", 0.75),
        make_hypothesis("resource_exhaustion", 0.70),
    ]

    ranked = HypothesisRanker().rank(hypotheses)

    assert ranked[0]["category"] == "resource_exhaustion"


def test_evidence_support_boosts_score():
    low_support = make_hypothesis("unknown", 0.5, support_count=0)
    high_support = make_hypothesis("unknown", 0.5, support_count=5)

    ranked = HypothesisRanker().rank([low_support, high_support])

    assert ranked[0]["support_count"] == 5
    assert ranked[0]["final_score"] > ranked[1]["final_score"]
