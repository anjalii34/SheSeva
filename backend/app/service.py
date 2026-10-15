from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .scoring import (DEFAULT_MIN_COVERAGE, DEFAULT_WEIGHTS, FACTORS, METHOD_VERSION, FactorInput, ScoreResult,
                      compute_score)


def active_config(db: Session) -> m.ScoringConfig:
    cfg = db.scalars(select(m.ScoringConfig).where(m.ScoringConfig.active).order_by(m.ScoringConfig.id.desc())).first()
    if cfg is None:
        cfg = m.ScoringConfig(weights=DEFAULT_WEIGHTS, min_coverage=DEFAULT_MIN_COVERAGE,
                              method_version=METHOD_VERSION, change_note="default")
        db.add(cfg)
        db.flush()
    return cfg


def score_assessment(a: m.Assessment, cfg: m.ScoringConfig) -> ScoreResult:
    inputs = {f.factor: FactorInput(f.rating, f.evidence_class) for f in a.factors}
    return compute_score(inputs, cfg.weights, cfg.min_coverage)


def apply_score(a: m.Assessment, cfg: m.ScoringConfig) -> ScoreResult:
    r = score_assessment(a, cfg)
    a.score, a.coverage, a.status = r.score, r.coverage, r.status
    a.method_version, a.config_id, a.explanation = r.method_version, cfg.id, r.explanation
    return r


def serialize_result(r: ScoreResult, a: m.Assessment) -> dict:
    srcs = {f.factor: f for f in a.factors}
    factors = []
    for fr in r.factors:
        row = srcs.get(fr.factor)
        factors.append({
            "factor": fr.factor, "label": fr.label, "weight": fr.weight, "rating": fr.rating,
            "evidence_class": fr.evidence_class, "status": fr.status, "contribution": fr.contribution,
            "explanation": fr.explanation, "note": row.note if row else "",
            "source": {"id": row.source.id, "name": row.source.name, "is_demo": row.source.is_demo}
            if row and row.source else None,
        })
    return {"score": r.score, "coverage": r.coverage, "status": r.status, "rankable": r.rankable,
            "method_version": r.method_version, "min_coverage": r.min_coverage,
            "explanation": r.explanation, "factors": factors}


def upsert_assessment(db: Session, cluster_id: int, enterprise_type_id: int, ratings: list[dict],
                      is_demo: bool = False) -> m.Assessment:
    cfg = active_config(db)
    a = db.scalars(select(m.Assessment).where(m.Assessment.cluster_id == cluster_id,
                                              m.Assessment.enterprise_type_id == enterprise_type_id)).first()
    if a is None:
        a = m.Assessment(cluster_id=cluster_id, enterprise_type_id=enterprise_type_id, config_id=cfg.id,
                         coverage=0, status="insufficient_evidence", method_version=METHOD_VERSION, is_demo=is_demo)
        db.add(a)
        db.flush()
    existing = {f.factor: f for f in a.factors}
    for r in ratings:
        assert r["factor"] in FACTORS
        f = existing.get(r["factor"])
        if f is None:
            f = m.FactorRating(assessment_id=a.id, factor=r["factor"])
            a.factors.append(f)
        f.rating, f.evidence_class = r.get("rating"), r.get("evidence_class", "unknown")
        f.source_id, f.note = r.get("source_id"), r.get("note", "")
    db.flush()
    apply_score(a, cfg)
    return a
