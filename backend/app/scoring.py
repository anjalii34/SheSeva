"""Transparent, deterministic, rule-based scoring for enterprise suitability.

Rules (see master spec, Page 4):
- Ratings are 0-5, or None when unknown / not assessed. None is NEVER treated as 0.
- Only factors backed by usable evidence count. Proxy / unknown evidence is excluded.
- Score = 100 * sum(w_i * r_i / 5) / sum(w_i), over usable factors only (w as fractions).
  With full coverage this equals the spec formula: sum(weight% * rating / 5).
- Coverage (share of total weight with usable evidence) is reported separately.
- Coverage below the minimum -> status "insufficient_evidence", not rankable.
- The score is a readiness/suitability index, NOT a probability of success.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

METHOD_VERSION = "rule-based-v1.0"

FACTORS: dict[str, str] = {
    "market_demand": "Verified market demand",
    "raw_material": "Raw-material availability",
    "transport": "Transport and accessibility",
    "skills": "SHG skills and capacity",
    "infrastructure": "Infrastructure readiness",
    "financial": "Financial feasibility",
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "market_demand": 30.0,
    "raw_material": 20.0,
    "transport": 15.0,
    "skills": 15.0,
    "infrastructure": 10.0,
    "financial": 10.0,
}

# Evidence classes. Only these count as usable evidence for scoring.
EVIDENCE_CLASSES = ("verified", "official", "estimated", "proxy", "unknown")
USABLE_CLASSES = frozenset({"verified", "official", "estimated"})

DEFAULT_MIN_COVERAGE = 0.6
COVERAGE_WARNING_GAP = 0.2  # compare-warning if coverage differs by more than this


class ScoringError(ValueError):
    """Raised for invalid weights or ratings."""


@dataclass
class FactorInput:
    rating: float | None = None
    evidence_class: str = "unknown"


@dataclass
class FactorResult:
    factor: str
    label: str
    weight: float
    rating: float | None
    evidence_class: str
    status: str  # "scored" | "not_assessed" | "excluded_proxy"
    contribution: float | None  # points toward the 0-100 score, un-normalised
    explanation: str


@dataclass
class ScoreResult:
    method_version: str
    score: float | None
    coverage: float  # 0..1
    status: str  # "scored" | "insufficient_evidence"
    rankable: bool
    min_coverage: float
    factors: list[FactorResult] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    explanation: str = ""


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    if set(weights) != set(FACTORS):
        missing = set(FACTORS) - set(weights)
        extra = set(weights) - set(FACTORS)
        raise ScoringError(f"Weights must cover exactly the known factors (missing={sorted(missing)}, unknown={sorted(extra)})")
    for k, v in weights.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ScoringError(f"Weight for '{k}' must be a non-negative number")
    total = sum(weights.values())
    if abs(total - 100.0) > 1e-6:
        raise ScoringError(f"Weights must total 100, got {total:g}")
    return {k: float(v) for k, v in weights.items()}


def validate_rating(factor: str, rating: float | None) -> float | None:
    if rating is None:
        return None
    if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not math.isfinite(rating):
        raise ScoringError(f"Rating for '{factor}' must be a number between 0 and 5, or null for unknown")
    if rating < 0 or rating > 5:
        raise ScoringError(f"Rating for '{factor}' must be between 0 and 5, got {rating}")
    return float(rating)


def compute_score(
    inputs: dict[str, FactorInput],
    weights: dict[str, float] | None = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> ScoreResult:
    weights = validate_weights(weights or DEFAULT_WEIGHTS)
    if not 0 <= min_coverage <= 1:
        raise ScoringError("min_coverage must be between 0 and 1")
    unknown_factors = set(inputs) - set(FACTORS)
    if unknown_factors:
        raise ScoringError(f"Unknown factors: {sorted(unknown_factors)}")

    results: list[FactorResult] = []
    usable_weight = 0.0
    weighted_points = 0.0
    excluded: list[str] = []

    for factor, label in FACTORS.items():
        w = weights[factor]
        inp = inputs.get(factor, FactorInput())
        if inp.evidence_class not in EVIDENCE_CLASSES:
            raise ScoringError(f"Unknown evidence class '{inp.evidence_class}' for '{factor}'")
        rating = validate_rating(factor, inp.rating)

        if rating is None or inp.evidence_class == "unknown":
            results.append(FactorResult(factor, label, w, rating, inp.evidence_class, "not_assessed", None,
                                        "Not assessed: no usable evidence. Excluded from the score (not counted as 0)."))
            excluded.append(factor)
        elif inp.evidence_class not in USABLE_CLASSES:  # proxy
            results.append(FactorResult(factor, label, w, rating, inp.evidence_class, "excluded_proxy", None,
                                        "Proxy evidence only. Not treated as direct evidence, so excluded from the score."))
            excluded.append(factor)
        else:
            points = w * rating / 5.0
            usable_weight += w
            weighted_points += points
            note = f"{w:g}% x {rating:g}/5 = {points:.2f} points ({inp.evidence_class} evidence)."
            if inp.evidence_class == "estimated":
                note += " Estimated/derived: treat with caution."
            results.append(FactorResult(factor, label, w, rating, inp.evidence_class, "scored", points, note))

    coverage = usable_weight / 100.0
    if usable_weight == 0 or coverage < min_coverage:
        status, rankable, score = "insufficient_evidence", False, None
        expl = (f"Insufficient evidence: {coverage:.0%} of weighted factors have usable evidence "
                f"(minimum {min_coverage:.0%}). No score is given and the enterprise is not ranked.")
    else:
        score = round(weighted_points / usable_weight * 100.0, 2)
        status, rankable = "scored", True
        if excluded:
            names = ", ".join(FACTORS[f] for f in excluded)
            expl = (f"Partial evidence ({coverage:.0%} coverage). Score is normalised over assessed factors only; "
                    f"excluded: {names}.")
        else:
            expl = "All factors assessed with usable evidence (100% coverage)."
    return ScoreResult(METHOD_VERSION, score, round(coverage, 4), status, rankable, min_coverage, results, excluded, expl)


def coverage_warning(results: list[ScoreResult]) -> str | None:
    """Warn when scores being compared rest on materially different evidence coverage."""
    covs = [r.coverage for r in results if r.rankable]
    if len(covs) >= 2 and max(covs) - min(covs) > COVERAGE_WARNING_GAP:
        return ("Scores rest on materially different evidence coverage "
                f"({min(covs):.0%} to {max(covs):.0%}). Compare with caution.")
    return None
