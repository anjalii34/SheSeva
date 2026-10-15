import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m
from .db import Base, engine, get_db
from .scoring import (DEFAULT_MIN_COVERAGE, EVIDENCE_CLASSES, FACTORS, METHOD_VERSION, ScoringError,
                      coverage_warning, validate_weights)
from .service import active_config, apply_score, score_assessment, serialize_result, upsert_assessment

log = logging.getLogger("lge")
@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Livelihood Gap Engine API", version="0.1.0", lifespan=lifespan,
              description="Planning and readiness assessment. Scores are NOT probabilities of success.")

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH"],
                   allow_headers=["*"])


# ---------- auth: simple API-key roles (MVP). Keys come from env, never from code. ----------
def _role(x_api_key: str | None) -> str | None:
    if not x_api_key:
        return None
    if os.getenv("LGE_ADMIN_KEY") and x_api_key == os.getenv("LGE_ADMIN_KEY"):
        return "admin"
    if os.getenv("LGE_ANALYST_KEY") and x_api_key == os.getenv("LGE_ANALYST_KEY"):
        return "analyst"
    return None


def require_analyst(x_api_key: str | None = Header(default=None)) -> str:
    role = _role(x_api_key)
    if role is None:
        raise HTTPException(401, "Missing or invalid API key")
    return role


def require_admin(x_api_key: str | None = Header(default=None)) -> str:
    role = _role(x_api_key)
    if role is None:
        raise HTTPException(401, "Missing or invalid API key")
    if role != "admin":
        raise HTTPException(403, "Administrator role required")
    return role


def audit(db: Session, role: str, action: str, detail: str = ""):
    db.add(m.AuditLog(actor=role, action=action, detail=detail[:1000]))


@app.exception_handler(ScoringError)
async def scoring_error(_: Request, exc: ScoringError):
    return JSONResponse(status_code=422, content={"error": {"code": "invalid_scoring_input", "message": str(exc)}})


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": f"http_{exc.status_code}", "message": exc.detail}})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Internal server error"}})


# ---------- schemas ----------
class RatingIn(BaseModel):
    factor: Literal["market_demand", "raw_material", "transport", "skills", "infrastructure", "financial"]
    rating: float | None = Field(default=None, ge=0, le=5, description="null = unknown, never 0")
    evidence_class: Literal["verified", "official", "estimated", "proxy", "unknown"] = "unknown"
    source_id: int | None = None
    note: str = Field(default="", max_length=1000)


class AssessmentIn(BaseModel):
    cluster_id: int
    enterprise_type_id: int
    ratings: list[RatingIn]


class ClusterIn(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=120)
    state: str = Field(min_length=2, max_length=80)
    district: str = Field(min_length=2, max_length=80)
    block: str = Field(min_length=2, max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class IndicatorIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    value: str = Field(min_length=1, max_length=80)
    unit: str = Field(default="", max_length=40)
    year: str = Field(default="", max_length=20)
    group: str = Field(default="", max_length=40)
    source_id: int = Field(description="Every indicator must cite an evidence source")


class GapPatch(BaseModel):
    status: Literal["available", "partial", "missing", "unknown", "needs_verification"] | None = None
    basis: Literal["observed", "officially_reported", "estimated", "inferred", "none"] | None = None
    evidence: str | None = Field(default=None, max_length=2000)
    next_action: str | None = Field(default=None, max_length=1000)
    stakeholder: str | None = Field(default=None, max_length=120)


class SourceIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    organisation: str = ""
    reference: str = ""
    category: str = ""
    geographic_scope: str = ""
    observed_on: date | None = None
    accessed_on: date | None = None
    licence: str = ""
    limitations: str = ""
    verification_status: Literal["verified", "official", "estimated", "proxy", "unknown"] = "unknown"


class ConfigIn(BaseModel):
    weights: dict[str, float]
    min_coverage: float = Field(default=DEFAULT_MIN_COVERAGE, ge=0, le=1)
    confirm: bool = Field(description="Must be true: confirms the weight change")
    note: str = Field(default="", max_length=300)


# ---------- helpers ----------
def get_or_404(db: Session, model, id_: int, what: str):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{what} {id_} not found")
    return obj


def cluster_out(c: m.Cluster):
    return {"id": c.id, "code": c.code, "name": c.name, "state": c.state, "district": c.district, "block": c.block,
            "lat": c.lat, "lon": c.lon, "is_demo": c.is_demo}


def source_out(s: m.EvidenceSource):
    return {"id": s.id, "name": s.name, "organisation": s.organisation, "reference": s.reference,
            "category": s.category, "geographic_scope": s.geographic_scope,
            "observed_on": s.observed_on, "accessed_on": s.accessed_on, "licence": s.licence,
            "limitations": s.limitations, "verification_status": s.verification_status, "is_demo": s.is_demo}


def gap_out(g: m.Gap, et: m.EnterpriseType | None = None):
    return {"id": g.id, "cluster_id": g.cluster_id, "enterprise_type_id": g.enterprise_type_id,
            "enterprise_name": et.name if et else None, "category": g.category, "status": g.status,
            "basis": g.basis, "evidence": g.evidence, "last_checked": g.last_checked,
            "implication": g.implication, "next_action": g.next_action, "stakeholder": g.stakeholder,
            "is_demo": g.is_demo}


def assessment_out(db: Session, a: m.Assessment, with_detail: bool = True):
    cfg = db.get(m.ScoringConfig, a.config_id)
    out = {"id": a.id, "cluster_id": a.cluster_id, "enterprise_type_id": a.enterprise_type_id,
           "enterprise_name": a.enterprise_type.name, "category": a.enterprise_type.category,
           "score": a.score, "coverage": a.coverage, "status": a.status, "method_version": a.method_version,
           "explanation": a.explanation, "is_demo": a.is_demo, "updated_at": a.updated_at}
    if with_detail:
        # recompute from stored inputs against the config used, so the breakdown always matches the score
        out["detail"] = serialize_result(score_assessment(a, cfg), a)
        out["requirements"] = a.enterprise_type.requirements
        out["risks"] = a.enterprise_type.risks
        out["validation_steps"] = a.enterprise_type.validation_steps
    return out


# ---------- endpoints ----------
@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "method_version": METHOD_VERSION}


@app.get("/", include_in_schema=False)
def root():
    return {"name": "Livelihood Gap Engine API", "docs": "/docs", "health": "/health"}


@app.get("/api/clusters")
def list_clusters(db: Session = Depends(get_db)):
    return {"items": [cluster_out(c) for c in db.scalars(select(m.Cluster).order_by(m.Cluster.name))]}


@app.get("/api/clusters/{cluster_id}")
def get_cluster(cluster_id: int, db: Session = Depends(get_db)):
    return cluster_out(get_or_404(db, m.Cluster, cluster_id, "Cluster"))


@app.post("/api/clusters", status_code=201)
def create_cluster(body: ClusterIn, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    c = m.Cluster(**body.model_dump(), is_demo=False)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A cluster with this code already exists")
    # every enterprise type starts as 'not assessed' so nothing is silently scored
    for e in db.scalars(select(m.EnterpriseType)):
        upsert_assessment(db, c.id, e.id, [{"factor": f, "rating": None, "evidence_class": "unknown"} for f in FACTORS])
    audit(db, role, "cluster.create", c.code)
    db.commit()
    return cluster_out(c)


@app.get("/api/clusters/{cluster_id}/indicators")
def cluster_indicators(cluster_id: int, db: Session = Depends(get_db)):
    get_or_404(db, m.Cluster, cluster_id, "Cluster")
    rows = db.scalars(select(m.Indicator).where(m.Indicator.cluster_id == cluster_id).order_by(m.Indicator.group, m.Indicator.id))
    return {"items": [{"id": i.id, "group": i.group, "name": i.name, "value": i.value, "unit": i.unit, "year": i.year,
                       "is_demo": i.is_demo, "source": source_out(i.source) if i.source else None} for i in rows]}


@app.post("/api/clusters/{cluster_id}/indicators", status_code=201)
def add_indicator(cluster_id: int, body: IndicatorIn, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    get_or_404(db, m.Cluster, cluster_id, "Cluster")
    get_or_404(db, m.EvidenceSource, body.source_id, "Evidence source")
    i = m.Indicator(cluster_id=cluster_id, **body.model_dump(), is_demo=False)
    db.add(i)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This indicator already exists for the cluster")
    audit(db, role, "indicator.create", f"cluster={cluster_id} {body.name}")
    db.commit()
    return {"id": i.id}


@app.get("/api/auth/check")
def auth_check(role: str = Depends(require_analyst)):
    return {"role": role}


@app.get("/api/enterprise-types")
def list_enterprise_types(db: Session = Depends(get_db)):
    return {"items": [{"id": e.id, "code": e.code, "name": e.name, "category": e.category}
                      for e in db.scalars(select(m.EnterpriseType).order_by(m.EnterpriseType.name))]}


@app.get("/api/clusters/{cluster_id}/recommendations")
def recommendations(cluster_id: int, db: Session = Depends(get_db)):
    c = get_or_404(db, m.Cluster, cluster_id, "Cluster")
    rows = db.scalars(select(m.Assessment).where(m.Assessment.cluster_id == cluster_id)).all()
    items = [assessment_out(db, a) for a in rows]
    ranked = sorted([i for i in items if i["status"] == "scored"], key=lambda i: (-i["score"], i["enterprise_name"]))
    unranked = [i for i in items if i["status"] != "scored"]
    top = ranked[:3]
    for pos, i in enumerate(ranked, 1):
        i["rank"] = pos
    cfg = active_config(db)
    warn = coverage_warning([score_assessment(a, cfg) for a in rows if any(t["id"] == a.id for t in top)])
    return {"cluster": cluster_out(c), "top": top, "other_ranked": ranked[3:], "insufficient_evidence": unranked,
            "coverage_warning": warn,
            "disclaimer": "Suitability index for planning and readiness only. Not a probability of success.",
            "is_demo": any(i["is_demo"] for i in items)}


@app.get("/api/clusters/{cluster_id}/gaps")
def cluster_gaps(cluster_id: int, enterprise_type_id: int | None = None, db: Session = Depends(get_db)):
    get_or_404(db, m.Cluster, cluster_id, "Cluster")
    q = select(m.Gap, m.EnterpriseType).join(m.EnterpriseType).where(m.Gap.cluster_id == cluster_id)
    if enterprise_type_id:
        q = q.where(m.Gap.enterprise_type_id == enterprise_type_id)
    return {"items": [gap_out(g, e) for g, e in db.execute(q.order_by(m.Gap.enterprise_type_id, m.Gap.category))]}


@app.patch("/api/gaps/{gap_id}")
def patch_gap(gap_id: int, body: GapPatch, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    g = get_or_404(db, m.Gap, gap_id, "Gap")
    data = body.model_dump(exclude_unset=True)
    new_status = data.get("status", g.status)
    new_basis = data.get("basis", g.basis)
    new_evidence = data.get("evidence", g.evidence)
    # Missing/Available/Partial are factual claims: they need a basis and evidence. Unknown stays separate.
    if new_status in ("available", "partial", "missing") and (new_basis == "none" or not new_evidence.strip()):
        raise HTTPException(422, f"Status '{new_status}' needs a basis and supporting evidence. "
                                 "Use 'unknown' or 'needs_verification' if you have none.")
    changed = {k: v for k, v in data.items() if getattr(g, k) != v}
    for k, v in changed.items():
        setattr(g, k, v)
    if changed:
        g.last_checked = date.today()
        # analyst replaced the demo evidence text -> record is no longer illustrative
        if g.is_demo and "evidence" in changed:
            g.is_demo = False
    audit(db, role, "gap.update", f"gap={gap_id} {data}")
    db.commit()
    return gap_out(g)


@app.get("/api/evidence-sources")
def list_sources(db: Session = Depends(get_db)):
    return {"items": [source_out(s) for s in db.scalars(select(m.EvidenceSource).order_by(m.EvidenceSource.name))]}


@app.post("/api/evidence-sources", status_code=201)
def add_source(body: SourceIn, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    s = m.EvidenceSource(**body.model_dump(), is_demo=False)
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A source with this name already exists")
    audit(db, role, "source.create", s.name)
    db.commit()
    return source_out(s)


@app.get("/api/assessments")
def list_assessments(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(m.Assessment.id)))
    rows = db.scalars(select(m.Assessment).order_by(m.Assessment.updated_at.desc()).limit(limit).offset(offset))
    return {"total": total, "items": [assessment_out(db, a, with_detail=False) for a in rows]}


@app.get("/api/assessments/{assessment_id}")
def get_assessment(assessment_id: int, db: Session = Depends(get_db)):
    return assessment_out(db, get_or_404(db, m.Assessment, assessment_id, "Assessment"))


@app.post("/api/assessments", status_code=201)
def create_assessment(body: AssessmentIn, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    get_or_404(db, m.Cluster, body.cluster_id, "Cluster")
    get_or_404(db, m.EnterpriseType, body.enterprise_type_id, "Enterprise type")
    factors = [r.factor for r in body.ratings]
    if len(set(factors)) != len(factors):
        raise HTTPException(422, "Duplicate factor in ratings")
    for r in body.ratings:
        if r.rating is not None and r.evidence_class == "unknown":
            raise HTTPException(422, f"'{r.factor}' has a rating but evidence_class 'unknown'. "
                                     "Give the evidence class, or set rating to null.")
        if r.source_id is not None:
            get_or_404(db, m.EvidenceSource, r.source_id, "Evidence source")
    a = upsert_assessment(db, body.cluster_id, body.enterprise_type_id, [r.model_dump() for r in body.ratings])
    audit(db, role, "assessment.upsert", f"id={a.id}")
    db.commit()
    return assessment_out(db, a)


@app.post("/api/assessments/{assessment_id}/recalculate")
def recalculate(assessment_id: int, db: Session = Depends(get_db), role: str = Depends(require_analyst)):
    a = get_or_404(db, m.Assessment, assessment_id, "Assessment")
    apply_score(a, active_config(db))
    audit(db, role, "assessment.recalculate", f"id={a.id}")
    db.commit()
    return assessment_out(db, a)


@app.get("/api/metrics")
def metrics(db: Session = Depends(get_db)):
    n = lambda col: db.scalar(select(func.count(col))) or 0  # noqa: E731
    total = n(m.Assessment.id)
    scored = db.scalar(select(func.count(m.Assessment.id)).where(m.Assessment.status == "scored")) or 0
    demo = db.scalar(select(func.count(m.Assessment.id)).where(m.Assessment.is_demo)) or 0
    avg_cov = db.scalar(select(func.avg(m.Assessment.coverage)))
    gaps = {s: db.scalar(select(func.count(m.Gap.id)).where(m.Gap.status == s)) or 0
            for s in ("available", "partial", "missing", "unknown", "needs_verification")}
    newest = db.scalar(select(func.max(m.Assessment.updated_at)))
    return {"clusters": n(m.Cluster.id), "enterprise_types": n(m.EnterpriseType.id), "assessments": total,
            "scored": scored, "insufficient_evidence": total - scored, "demo_assessments": demo,
            "real_assessments": total - demo, "avg_evidence_coverage": round(avg_cov, 4) if avg_cov is not None else None,
            "last_assessment_update": newest, "gap_status_counts": gaps,
            "note": "Records flagged demo are illustrative, not verified field data." if demo else ""}


@app.get("/api/admin/scoring-config")
def get_config(db: Session = Depends(get_db)):
    c = active_config(db)
    db.commit()
    return {"id": c.id, "weights": c.weights, "min_coverage": c.min_coverage, "method_version": c.method_version,
            "factors": FACTORS, "evidence_classes": list(EVIDENCE_CLASSES)}


@app.put("/api/admin/scoring-config")
def put_config(body: ConfigIn, db: Session = Depends(get_db), role: str = Depends(require_admin)):
    if not body.confirm:
        raise HTTPException(422, "Weight change must be confirmed (confirm=true)")
    weights = validate_weights(body.weights)
    old = active_config(db)
    old.active = False
    new = m.ScoringConfig(weights=weights, min_coverage=body.min_coverage, method_version=METHOD_VERSION,
                          changed_by=role, change_note=body.note)
    db.add(new)
    db.flush()
    audit(db, role, "scoring_config.update", f"old={old.weights} new={weights} min_cov={body.min_coverage}")
    # re-score everything so stored scores match the active config
    for a in db.scalars(select(m.Assessment)):
        apply_score(a, new)
    db.commit()
    return {"id": new.id, "weights": new.weights, "min_coverage": new.min_coverage}


@app.get("/api/clusters/{cluster_id}/report.csv")
def report_csv(cluster_id: int, db: Session = Depends(get_db)):
    c = get_or_404(db, m.Cluster, cluster_id, "Cluster")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["cluster", "enterprise", "status", "score", "evidence_coverage", "method_version", "data_label"])
    for a in db.scalars(select(m.Assessment).where(m.Assessment.cluster_id == cluster_id)):
        w.writerow([c.name, a.enterprise_type.name, a.status, "" if a.score is None else a.score,
                    f"{a.coverage:.0%}", a.method_version,
                    "Illustrative demo data - not verified field data" if a.is_demo else "Entered by analyst"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="report_{c.code}.csv"'})
