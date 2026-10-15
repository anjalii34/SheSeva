"""Repeatable demo seed. Every record is flagged is_demo=True.

ALL DATA HERE IS ILLUSTRATIVE DEMO DATA — NOT VERIFIED FIELD DATA.
Clusters have no coordinates or boundaries on purpose: none were invented.
Run: python -m app.seed   (safe to run many times; no duplicates)
"""
from datetime import date

from sqlalchemy import select

from . import models as m
from .db import Base, SessionLocal, engine
from .service import active_config, upsert_assessment

DEMO_NOTE = "Illustrative demo data - not verified field data."

CLUSTERS = [
    ("DEMO-A", "Demo Cluster A"), ("DEMO-B", "Demo Cluster B"), ("DEMO-C", "Demo Cluster C"),
]

ENTERPRISES = [
    ("food", "Packaged local food products", "Food processing",
     "Clean processing space; packaging material; FSSAI registration; cold/dry storage",
     "Shelf-life and hygiene compliance; seasonal raw material; buyer concentration",
     "Confirm raw-material supply with 3 local producers; check FSSAI route; sample-test with 2 retailers"),
    ("tailoring", "Tailoring and garment products", "Tailoring and textiles",
     "Sewing machines; reliable power; fabric supplier; trained members",
     "Competition from ready-made garments; working-capital for fabric",
     "Survey existing tailors; verify institutional uniform demand directly with buyers"),
    ("handicraft", "Handicrafts and traditional products", "Handicrafts",
     "Artisan skills; raw material (cane, jute, clay); market linkage; packaging for transit",
     "Irregular demand; reliance on intermediaries; transport damage",
     "Verify artisan skill base; test sales through one fair or retailer"),
    ("personalcare", "Household and personal-care products", "Household products",
     "Production space; safe ingredients sourcing; labelling and licences",
     "Licensing and quality compliance; brand competition",
     "Check licensing needs; verify local retail demand"),
    ("services", "Local service enterprise (e.g. catering)", "Local services",
     "Equipment; hygiene certification; repeat institutional customers",
     "Irregular orders; hygiene compliance",
     "Identify and verify institutional customers directly"),
]

# rating, evidence_class  (None = unknown). Order: demand, raw, transport, skills, infra, financial
F = ["market_demand", "raw_material", "transport", "skills", "infrastructure", "financial"]
ASSESS = {
    ("DEMO-A", "food"): [(4, "official"), (4, "verified"), (3, "verified"), (3, "official"), (2, "verified"), (3, "estimated")],
    ("DEMO-A", "tailoring"): [(3, "estimated"), (3, "official"), (4, "verified"), (4, "verified"), (3, "official"), (3, "estimated")],
    ("DEMO-A", "handicraft"): [(2, "estimated"), (4, "official"), (2, "verified"), (4, "official"), (2, "verified"), (None, "unknown")],
    ("DEMO-A", "personalcare"): [(None, "unknown"), (3, "proxy"), (3, "verified"), (None, "unknown"), (2, "verified"), (None, "unknown")],
    ("DEMO-B", "food"): [(3, "estimated"), (5, "verified"), (2, "verified"), (3, "official"), (2, "official"), (3, "estimated")],
    ("DEMO-B", "tailoring"): [(None, "unknown"), (3, "official"), (3, "verified"), (4, "verified"), (3, "official"), (None, "unknown")],
    ("DEMO-B", "services"): [(3, "estimated"), (3, "official"), (4, "verified"), (3, "official"), (3, "official"), (3, "estimated")],
    ("DEMO-C", "handicraft"): [(3, "official"), (4, "verified"), (3, "verified"), (5, "official"), (3, "verified"), (3, "estimated")],
}

GAP_SEED = {  # (category): (status, basis, evidence, implication, action, stakeholder)
    "raw_material": ("partial", "officially_reported", "Block agriculture office lists local producers (demo).", "Supply may be seasonal.", "Confirm supply months with producers.", "Block agriculture office"),
    "transport": ("available", "observed", "Metalled road to market town noted in demo field note.", "Low transport risk.", "Record actual travel time with a documented method.", "Block officials"),
    "storage": ("missing", "observed", "No dry storage seen at demo site visit.", "Spoilage risk for food products.", "Identify shared storage option.", "SHG federation"),
    "packaging": ("unknown", "none", "", "Cannot judge packaging readiness.", "Survey packaging suppliers.", ""),
    "equipment": ("partial", "estimated", "Some equipment reported; condition unverified.", "Capacity uncertain.", "Inventory and verify equipment.", "NGO partner"),
    "skills": ("available", "officially_reported", "Training records reported (demo).", "Skill base likely adequate.", "Spot-check skills.", "Livelihood mission staff"),
    "finance": ("needs_verification", "inferred", "Inferred from typical working-capital needs.", "Funding gap may exist.", "Check credit-linkage options.", "Bank / federation"),
    "buyers": ("needs_verification", "none", "No buyer has been verified.", "Demand is unconfirmed.", "Contact and verify at least 3 buyers directly.", "NGO partner"),
    "legal": ("unknown", "none", "", "Compliance status unknown.", "Check licence requirements.", ""),
    "digital": ("missing", "observed", "No digital record-keeping observed (demo).", "Order tracking is manual.", "Pilot a simple records sheet.", "NGO partner"),
}


REAL_SOURCE = "Balurghat CD block profile (Census 2011 / District Statistical Handbook 2014, via Wikipedia)"
REAL_URL = "https://en.wikipedia.org/wiki/Balurghat_(community_development_block)"
# (group, name, value, unit, year)
REAL_INDICATORS = [
    ("Population", "Total population", "250,764", "people", "2011"),
    ("Population", "Inhabited villages", "294", "villages", "2014"),
    ("Population", "Female literacy (age 6+)", "67.24", "%", "2011"),
    ("Population", "Male literacy (age 6+)", "80.25", "%", "2011"),
    ("Work", "Total workers", "113,610", "workers", "2011"),
    ("Work", "Cultivators (share of workers)", "26.29", "%", "2011"),
    ("Work", "Agricultural labourers (share of workers)", "38.20", "%", "2011"),
    ("Work", "Household industry workers", "6,010 (5.29% of workers)", "", "2011"),
    ("Access", "Villages with pucca approach road", "121 of 294 (41.16%)", "", "2011"),
    ("Access", "Villages with transport communication", "89 of 294 (30.27%)", "", "2011"),
    ("Access", "Villages with telephones", "269 of 294 (91.5%)", "", "2011"),
    ("Finance", "Villages with a bank", "9 of 294 (3.06%)", "", "2011"),
    ("Finance", "Villages with agricultural credit society", "11 of 294 (3.74%)", "", "2011"),
    ("Finance", "Bank branches in block", "21 commercial + 7 gramin", "", "2013-14"),
]
REAL_GAPS = {  # category: (status, basis, evidence)
    "transport": ("partial", "officially_reported", "Only 121 of 294 villages (41.16%) have a pucca approach road; 89 (30.27%) have transport communication. Census 2011, block level."),
    "finance": ("needs_verification", "officially_reported", "Only 9 of 294 villages have a bank; 21 commercial and 7 gramin bank branches in the block (2013-14). Working-capital access for SHGs not verified."),
    "digital": ("needs_verification", "officially_reported", "269 of 294 villages have telephones (Census 2011). Digital and record-keeping capability not verified."),
}


def seed_real(db):
    """Real, sourced block-level facts. No ratings are invented: every factor stays Unknown until field evidence exists."""
    src = db.scalars(select(m.EvidenceSource).where(m.EvidenceSource.name == REAL_SOURCE)).first()
    if not src:
        src = m.EvidenceSource(
            name=REAL_SOURCE, organisation="Census of India 2011; Govt of West Bengal District Statistical Handbook 2014",
            reference=REAL_URL, category="demographics and amenities", geographic_scope="Balurghat CD block, Dakshin Dinajpur",
            accessed_on=date.today(), licence="Wikipedia text CC BY-SA; underlying census figures are public statistics",
            limitations="Read from a Wikipedia summary, not the primary census tables. Block-level only: no village, SHG or market data. "
                        "Figures are from 2011-2014 and may be out of date. Not independently verified.",
            verification_status="official", is_demo=False)
        db.add(src)
    if not db.scalars(select(m.Cluster).where(m.Cluster.code == "BALURGHAT-BLOCK")).first():
        db.add(m.Cluster(code="BALURGHAT-BLOCK", name="Balurghat block (whole block)", state="West Bengal",
                         district="Dakshin Dinajpur", block="Balurghat", is_demo=False))
    for code, name, cat, req, risks, val in ENTERPRISES:
        if not db.scalars(select(m.EnterpriseType).where(m.EnterpriseType.code == code)).first():
            db.add(m.EnterpriseType(code=code, name=name, category=cat, requirements=req, risks=risks, validation_steps=val))
    db.flush()
    c = db.scalars(select(m.Cluster).where(m.Cluster.code == "BALURGHAT-BLOCK")).one()
    for grp, name, value, unit, year in REAL_INDICATORS:
        if not db.scalars(select(m.Indicator).where(m.Indicator.cluster_id == c.id, m.Indicator.name == name)).first():
            db.add(m.Indicator(cluster_id=c.id, name=name, value=value, unit=unit, year=year, group=grp, source_id=src.id))
    for e in db.scalars(select(m.EnterpriseType)):
        upsert_assessment(db, c.id, e.id, [{"factor": f, "rating": None, "evidence_class": "unknown", "source_id": None,
                                            "note": "No field evidence yet."} for f in F], is_demo=False)
        for cat, (st, basis, ev, imp, act, who) in GAP_SEED.items():
            if db.scalars(select(m.Gap).where(m.Gap.cluster_id == c.id, m.Gap.enterprise_type_id == e.id,
                                              m.Gap.category == cat)).first():
                continue
            if cat in REAL_GAPS:
                st, basis, ev = REAL_GAPS[cat]
                last = date.today()
            else:
                st, basis, ev, last = "unknown", "none", "", None
            db.add(m.Gap(cluster_id=c.id, enterprise_type_id=e.id, category=cat, status=st, basis=basis, evidence=ev,
                         source_id=src.id if ev else None, last_checked=last, implication=imp, next_action=act,
                         stakeholder=who, is_demo=False))


def run(demo=False):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        active_config(db)
        seed_real(db)
        db.commit()
        if demo:
            seed_demo(db)


def seed_demo(db):
    if True:
        src = db.scalars(select(m.EvidenceSource).where(m.EvidenceSource.name == "Demo evidence set")).first()
        if not src:
            src = m.EvidenceSource(
                name="Demo evidence set", organisation="Livelihood Gap Engine (demo)", reference="seed script",
                category="demo", geographic_scope="Demo Block (illustrative)", accessed_on=date.today(),
                licence="n/a", limitations=DEMO_NOTE, verification_status="unknown", is_demo=True)
            db.add(src)
        for code, name in CLUSTERS:
            if not db.scalars(select(m.Cluster).where(m.Cluster.code == code)).first():
                db.add(m.Cluster(code=code, name=name, state="West Bengal", district="Demo District (illustrative)",
                                 block="Demo Block (illustrative)", is_demo=True))
        for code, name, cat, req, risks, val in ENTERPRISES:
            if not db.scalars(select(m.EnterpriseType).where(m.EnterpriseType.code == code)).first():
                db.add(m.EnterpriseType(code=code, name=name, category=cat, requirements=req, risks=risks,
                                        validation_steps=val))
        db.flush()
        cl = {c.code: c for c in db.scalars(select(m.Cluster))}
        et = {e.code: e for e in db.scalars(select(m.EnterpriseType))}
        for (ccode, ecode), vals in ASSESS.items():
            ratings = [{"factor": f, "rating": r, "evidence_class": ev, "source_id": src.id,
                        "note": DEMO_NOTE} for f, (r, ev) in zip(F, vals)]
            upsert_assessment(db, cl[ccode].id, et[ecode].id, ratings, is_demo=True)
        for ccode, ecode in [("DEMO-A", "food"), ("DEMO-A", "tailoring"), ("DEMO-B", "food")]:
            for cat, (st, basis, ev, imp, act, who) in GAP_SEED.items():
                exists = db.scalars(select(m.Gap).where(m.Gap.cluster_id == cl[ccode].id,
                                                        m.Gap.enterprise_type_id == et[ecode].id,
                                                        m.Gap.category == cat)).first()
                if not exists:
                    db.add(m.Gap(cluster_id=cl[ccode].id, enterprise_type_id=et[ecode].id, category=cat, status=st,
                                 basis=basis, evidence=(ev + " " + DEMO_NOTE).strip(), source_id=src.id,
                                 last_checked=date.today(), implication=imp, next_action=act, stakeholder=who,
                                 is_demo=True))
        db.commit()


if __name__ == "__main__":
    import sys
    run(demo="--demo" in sys.argv)
    print("Seed complete (idempotent)." + (" Demo data included." if "--demo" in sys.argv else " Real block data only."))
