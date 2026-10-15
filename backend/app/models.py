from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


class Cluster(Base):
    """Village cluster. No boundary geometry in MVP: only an optional illustrative centroid."""
    __tablename__ = "clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(80))
    block: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)


class EnterpriseType(Base):
    __tablename__ = "enterprise_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(120))
    requirements: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    validation_steps: Mapped[str] = mapped_column(Text, default="")


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    organisation: Mapped[str] = mapped_column(String(200), default="")
    reference: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    geographic_scope: Mapped[str] = mapped_column(String(200), default="")
    observed_on: Mapped[datetime | None] = mapped_column(Date)
    accessed_on: Mapped[datetime | None] = mapped_column(Date)
    licence: Mapped[str] = mapped_column(String(200), default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[str] = mapped_column(String(20), default="unknown")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint(
        "verification_status IN ('verified','official','estimated','proxy','unknown')", name="ck_source_vstatus"),)


class ScoringConfig(Base):
    __tablename__ = "scoring_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    weights: Mapped[dict] = mapped_column(JSON)
    min_coverage: Mapped[float] = mapped_column(Float, default=0.6)
    method_version: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    changed_by: Mapped[str] = mapped_column(String(80), default="system")
    change_note: Mapped[str] = mapped_column(String(300), default="")


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), index=True)
    enterprise_type_id: Mapped[int] = mapped_column(ForeignKey("enterprise_types.id"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("scoring_configs.id"))
    score: Mapped[float | None] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))
    method_version: Mapped[str] = mapped_column(String(40))
    explanation: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    cluster: Mapped[Cluster] = relationship()
    enterprise_type: Mapped[EnterpriseType] = relationship()
    factors: Mapped[list["FactorRating"]] = relationship(cascade="all, delete-orphan", back_populates="assessment")
    __table_args__ = (UniqueConstraint("cluster_id", "enterprise_type_id", name="uq_assessment_pair"),)


class FactorRating(Base):
    """Input rating for one factor. rating NULL means unknown / not assessed (never 0)."""
    __tablename__ = "factor_ratings"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    factor: Mapped[str] = mapped_column(String(40))
    rating: Mapped[float | None] = mapped_column(Float)
    evidence_class: Mapped[str] = mapped_column(String(20), default="unknown")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_sources.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    assessment: Mapped[Assessment] = relationship(back_populates="factors")
    source: Mapped[EvidenceSource | None] = relationship()
    __table_args__ = (
        UniqueConstraint("assessment_id", "factor", name="uq_factor_per_assessment"),
        CheckConstraint("rating IS NULL OR (rating >= 0 AND rating <= 5)", name="ck_rating_range"),
        CheckConstraint("evidence_class IN ('verified','official','estimated','proxy','unknown')", name="ck_evclass"),
    )


GAP_CATEGORIES = ("raw_material", "transport", "storage", "packaging", "equipment", "skills", "finance",
                  "buyers", "legal", "digital")
GAP_STATUSES = ("available", "partial", "missing", "unknown", "needs_verification")
GAP_BASIS = ("observed", "officially_reported", "estimated", "inferred", "none")


class Gap(Base):
    __tablename__ = "gaps"
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), index=True)
    enterprise_type_id: Mapped[int] = mapped_column(ForeignKey("enterprise_types.id"), index=True)
    category: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="unknown")
    basis: Mapped[str] = mapped_column(String(30), default="none")
    evidence: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_sources.id"))
    last_checked: Mapped[datetime | None] = mapped_column(Date)
    implication: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    stakeholder: Mapped[str] = mapped_column(String(120), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("cluster_id", "enterprise_type_id", "category", name="uq_gap"),
        CheckConstraint("status IN ('available','partial','missing','unknown','needs_verification')", name="ck_gap_status"),
        CheckConstraint("basis IN ('observed','officially_reported','estimated','inferred','none')", name="ck_gap_basis"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")


class Indicator(Base):
    """A sourced statistic about a cluster/block (e.g. Census figure). Value kept as text with unit and year."""
    __tablename__ = "indicators"
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    value: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(40), default="")
    year: Mapped[str] = mapped_column(String(20), default="")
    group: Mapped[str] = mapped_column(String(40), default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_sources.id"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[EvidenceSource | None] = relationship()
    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_indicator"),)
