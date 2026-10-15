import pytest

from app.scoring import (DEFAULT_WEIGHTS, FACTORS, FactorInput, ScoringError, compute_score,
                         coverage_warning, validate_weights)


def full(rating, cls="verified"):
    return {f: FactorInput(rating, cls) for f in FACTORS}


def test_full_marks_is_100():
    r = compute_score(full(5))
    assert r.score == 100.0 and r.coverage == 1.0 and r.status == "scored"


def test_formula_matches_spec():
    ratings = {"market_demand": 4, "raw_material": 3, "transport": 5, "skills": 2, "infrastructure": 1, "financial": 4}
    inputs = {k: FactorInput(v, "official") for k, v in ratings.items()}
    expected = sum(DEFAULT_WEIGHTS[k] * v / 5 for k, v in ratings.items())
    assert compute_score(inputs).score == pytest.approx(expected)


def test_unknown_is_not_zero():
    inputs = full(4)
    inputs["financial"] = FactorInput(None, "unknown")
    r = compute_score(inputs)
    assert r.coverage == 0.9
    assert r.score == 80.0  # 4/5 on every assessed factor; unknown did not drag it down
    fin = next(f for f in r.factors if f.factor == "financial")
    assert fin.status == "not_assessed" and fin.contribution is None
    assert "financial" in r.excluded


def test_proxy_not_treated_as_direct_evidence():
    inputs = full(5)
    inputs["market_demand"] = FactorInput(5, "proxy")
    r = compute_score(inputs)
    assert r.coverage == 0.7
    assert next(f for f in r.factors if f.factor == "market_demand").status == "excluded_proxy"


def test_insufficient_evidence_not_ranked():
    inputs = {"market_demand": FactorInput(5, "verified")}  # only 30% coverage
    r = compute_score(inputs)
    assert r.status == "insufficient_evidence" and r.score is None and not r.rankable


def test_empty_inputs_insufficient():
    r = compute_score({})
    assert r.status == "insufficient_evidence" and r.coverage == 0


def test_zero_rating_with_evidence_is_real_zero():
    inputs = full(4)
    inputs["transport"] = FactorInput(0, "verified")
    r = compute_score(inputs)
    assert next(f for f in r.factors if f.factor == "transport").contribution == 0
    assert r.score < 80


@pytest.mark.parametrize("bad", [-1, 5.1, float("nan"), "3", True])
def test_invalid_rating_rejected(bad):
    with pytest.raises(ScoringError):
        compute_score({"market_demand": FactorInput(bad, "verified")})


def test_weights_must_total_100():
    w = dict(DEFAULT_WEIGHTS, market_demand=31)
    with pytest.raises(ScoringError):
        validate_weights(w)
    with pytest.raises(ScoringError):
        validate_weights({"market_demand": 100})
    with pytest.raises(ScoringError):
        validate_weights(dict(DEFAULT_WEIGHTS, skills=-5, market_demand=50))


def test_custom_weights_change_score():
    inputs = full(3)
    inputs["market_demand"] = FactorInput(5, "verified")
    base = compute_score(inputs).score
    w = dict(DEFAULT_WEIGHTS, market_demand=50, raw_material=0, transport=0, skills=15, infrastructure=10, financial=25)
    assert compute_score(inputs, w).score > base


def test_deterministic():
    assert compute_score(full(3)) == compute_score(full(3))


def test_coverage_warning():
    a = compute_score(full(4))
    partial = full(4)
    for f in ("transport", "skills", "infrastructure"):
        partial[f] = FactorInput(None, "unknown")
    b = compute_score(partial)  # 60% coverage
    assert coverage_warning([a, b]) is not None
    assert coverage_warning([a, a]) is None
