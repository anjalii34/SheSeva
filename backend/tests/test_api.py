from sqlalchemy import func, select

from app import models as m
from app import seed
from app.db import SessionLocal

from .conftest import ADMIN, ANALYST


def dc(client, i):
    """Id of the i-th DEMO cluster (the real Balurghat cluster is excluded)."""
    demo = [c for c in client.get("/api/clusters").json()["items"] if c["is_demo"]]
    return demo[i]["id"]


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_seed_is_repeatable(client):
    seed.run()
    seed.run()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(m.Cluster.id))) == 4
        assert db.scalar(select(func.count(m.Assessment.id))) == 13
        assert db.scalar(select(func.count(m.Gap.id))) == 80
        assert db.scalar(select(func.count(m.Indicator.id))) == 14


def test_recommendations_ranked_and_labelled(client):
    cid = dc(client, 0)
    r = client.get(f"/api/clusters/{cid}/recommendations").json()
    assert r["is_demo"] is True
    assert len(r["top"]) == 3
    scores = [t["score"] for t in r["top"]]
    assert scores == sorted(scores, reverse=True)
    assert "Not a probability" in r["disclaimer"]
    # personal-care has 3/6 factors unknown or proxy -> insufficient, not ranked
    names = [i["enterprise_name"] for i in r["insufficient_evidence"]]
    assert any("personal-care" in n for n in names)
    assert all(i["score"] is None for i in r["insufficient_evidence"])


def test_factor_detail_shows_unknown_not_zero(client):
    cid = dc(client, 0)
    r = client.get(f"/api/clusters/{cid}/recommendations").json()
    handi = next(t for t in r["top"] + r["other_ranked"] if "Handicrafts" in t["enterprise_name"])
    fin = next(f for f in handi["detail"]["factors"] if f["factor"] == "financial")
    assert fin["status"] == "not_assessed" and fin["contribution"] is None
    assert handi["coverage"] == 0.9


def test_missing_records_404(client):
    assert client.get("/api/clusters/9999").status_code == 404
    assert client.get("/api/assessments/9999").status_code == 404
    assert client.get("/api/clusters/9999/recommendations").status_code == 404
    assert client.get("/api/clusters/abc").status_code == 422


def test_write_requires_auth(client):
    assert client.post("/api/assessments", json={}).status_code == 401
    assert client.patch("/api/gaps/1", json={"status": "unknown"}).status_code == 401
    assert client.put("/api/admin/scoring-config", json={}).status_code == 401
    assert client.patch("/api/gaps/1", json={"status": "unknown"}, headers={"X-API-Key": "bad"}).status_code == 401


def test_analyst_cannot_change_weights(client):
    body = {"weights": {"market_demand": 30, "raw_material": 20, "transport": 15, "skills": 15,
                        "infrastructure": 10, "financial": 10}, "confirm": True}
    assert client.put("/api/admin/scoring-config", json=body, headers=ANALYST).status_code == 403


def _ids(client):
    cid = dc(client, 2)  # Cluster C
    et = {e["name"]: e["id"] for e in client.get("/api/enterprise-types").json()["items"]}
    return cid, et


def test_create_assessment_and_roundtrip(client):
    cid, et = _ids(client)
    eid = next(v for k, v in et.items() if "Tailoring" in k)
    ratings = [{"factor": "market_demand", "rating": 4, "evidence_class": "verified"},
               {"factor": "raw_material", "rating": 3, "evidence_class": "official"},
               {"factor": "transport", "rating": 5, "evidence_class": "verified"},
               {"factor": "skills", "rating": 2, "evidence_class": "verified"}]
    r = client.post("/api/assessments", json={"cluster_id": cid, "enterprise_type_id": eid, "ratings": ratings},
                    headers=ANALYST)
    assert r.status_code == 201
    body = r.json()
    assert body["coverage"] == 0.8 and body["status"] == "scored" and body["is_demo"] is False
    expected = (30 * 4 + 20 * 3 + 15 * 5 + 15 * 2) / 5 / 0.8
    assert abs(body["score"] - round(expected, 2)) < 0.01
    got = client.get(f"/api/assessments/{body['id']}").json()
    assert got["score"] == body["score"]
    assert client.post(f"/api/assessments/{body['id']}/recalculate", headers=ANALYST).json()["score"] == body["score"]


def test_invalid_rating_rejected(client):
    cid, et = _ids(client)
    eid = next(iter(et.values()))
    for bad in (7, -1):
        r = client.post("/api/assessments", headers=ANALYST, json={
            "cluster_id": cid, "enterprise_type_id": eid,
            "ratings": [{"factor": "skills", "rating": bad, "evidence_class": "verified"}]})
        assert r.status_code == 422
    r = client.post("/api/assessments", headers=ANALYST, json={
        "cluster_id": cid, "enterprise_type_id": eid,
        "ratings": [{"factor": "skills", "rating": 3, "evidence_class": "unknown"}]})
    assert r.status_code == 422  # a rating with no evidence class is not allowed
    r = client.post("/api/assessments", headers=ANALYST, json={
        "cluster_id": 9999, "enterprise_type_id": eid, "ratings": []})
    assert r.status_code == 404


def test_insufficient_evidence_assessment(client):
    cid, et = _ids(client)
    eid = next(v for k, v in et.items() if "Local service" in k)
    r = client.post("/api/assessments", headers=ANALYST, json={
        "cluster_id": cid, "enterprise_type_id": eid,
        "ratings": [{"factor": "market_demand", "rating": 5, "evidence_class": "verified"}]}).json()
    assert r["status"] == "insufficient_evidence" and r["score"] is None


def test_gap_unknown_vs_missing(client):
    cid = dc(client, 0)
    gaps = client.get(f"/api/clusters/{cid}/gaps").json()["items"]
    statuses = {g["status"] for g in gaps}
    assert {"unknown", "missing"} <= statuses
    unknown = next(g for g in gaps if g["status"] == "unknown")
    # cannot flip unknown -> missing without basis and evidence
    r = client.patch(f"/api/gaps/{unknown['id']}", headers=ANALYST, json={"status": "missing"})
    assert r.status_code == 422
    ok = client.patch(f"/api/gaps/{unknown['id']}", headers=ANALYST, json={
        "status": "missing", "basis": "observed", "evidence": "Site visit: no packaging supplier within block."})
    assert ok.status_code == 200 and ok.json()["status"] == "missing"
    # invalid status value
    assert client.patch(f"/api/gaps/{unknown['id']}", headers=ANALYST, json={"status": "bogus"}).status_code == 422
    assert client.patch("/api/gaps/99999", headers=ANALYST, json={"status": "unknown"}).status_code == 404


def test_unchanged_gap_save_stays_demo(client):
    cid = dc(client, 0)
    g = next(x for x in client.get(f"/api/clusters/{cid}/gaps").json()["items"] if x["status"] == "available")
    r = client.patch(f"/api/gaps/{g['id']}", headers=ANALYST, json={"status": g["status"], "basis": g["basis"], "evidence": g["evidence"]})
    assert r.status_code == 200 and r.json()["is_demo"] is True


def test_evidence_source_provenance(client):
    body = {"name": "District SHG list 2025", "organisation": "Block office", "reference": "doc ref 12",
            "verification_status": "official", "limitations": "No coordinates"}
    r = client.post("/api/evidence-sources", json=body, headers=ANALYST)
    assert r.status_code == 201 and r.json()["is_demo"] is False
    assert client.post("/api/evidence-sources", json=body, headers=ANALYST).status_code == 409
    assert client.post("/api/evidence-sources", json={**body, "name": "x2", "verification_status": "great"},
                       headers=ANALYST).status_code == 422
    items = client.get("/api/evidence-sources").json()["items"]
    assert any(s["is_demo"] for s in items) and any(not s["is_demo"] for s in items)


def test_weight_update_validated_confirmed_audited(client):
    ok = {"market_demand": 40, "raw_material": 20, "transport": 10, "skills": 10, "infrastructure": 10, "financial": 10}
    assert client.put("/api/admin/scoring-config", headers=ADMIN, json={"weights": ok, "confirm": False}).status_code == 422
    bad = dict(ok, financial=20)
    r = client.put("/api/admin/scoring-config", headers=ADMIN, json={"weights": bad, "confirm": True})
    assert r.status_code == 422 and "100" in r.json()["error"]["message"]
    assert client.put("/api/admin/scoring-config", headers=ADMIN, json={"weights": ok, "confirm": True}).status_code == 200
    assert client.get("/api/admin/scoring-config").json()["weights"]["market_demand"] == 40
    with SessionLocal() as db:
        assert db.scalar(select(func.count(m.AuditLog.id)).where(m.AuditLog.action == "scoring_config.update")) == 1


def test_metrics_label_demo(client):
    r = client.get("/api/metrics").json()
    assert r["clusters"] == 4 and r["assessments"] == 13
    assert r["demo_assessments"] == 8 and r["real_assessments"] == 5
    assert "demo" in r["note"].lower()


def test_csv_report_labels_demo(client):
    cid = dc(client, 0)
    r = client.get(f"/api/clusters/{cid}/report.csv")
    assert r.status_code == 200 and "Illustrative demo data" in r.text


def test_real_block_has_sourced_facts_and_no_invented_scores(client):
    real = next(c for c in client.get("/api/clusters").json()["items"] if not c["is_demo"])
    assert real["block"] == "Balurghat"
    ind = client.get(f"/api/clusters/{real['id']}/indicators").json()["items"]
    assert len(ind) == 14 and all(i["source"] and i["source"]["reference"] for i in ind)
    assert all(not i["is_demo"] for i in ind)
    rec = client.get(f"/api/clusters/{real['id']}/recommendations").json()
    assert rec["top"] == [] and rec["is_demo"] is False
    assert len(rec["insufficient_evidence"]) == 5
    assert all(a["score"] is None and a["coverage"] == 0 for a in rec["insufficient_evidence"])
    gaps = client.get(f"/api/clusters/{real['id']}/gaps").json()["items"]
    assert {g["status"] for g in gaps} == {"unknown", "partial", "needs_verification"}
    assert all(g["evidence"] for g in gaps if g["status"] != "unknown")


def test_create_cluster_and_indicator(client):
    body = {"code": "NEW-1", "name": "New cluster", "state": "West Bengal", "district": "Dakshin Dinajpur", "block": "Balurghat"}
    assert client.post("/api/clusters", json=body).status_code == 401
    r = client.post("/api/clusters", json=body, headers=ANALYST)
    assert r.status_code == 201 and r.json()["is_demo"] is False
    assert client.post("/api/clusters", json=body, headers=ANALYST).status_code == 409
    assert client.post("/api/clusters", json={**body, "code": "bad code!"}, headers=ANALYST).status_code == 422
    assert client.post("/api/clusters", json={**body, "code": "X2", "lat": 123}, headers=ANALYST).status_code == 422
    cid = r.json()["id"]
    rec = client.get(f"/api/clusters/{cid}/recommendations").json()
    assert len(rec["insufficient_evidence"]) == 5 and rec["top"] == []
    src = client.get("/api/evidence-sources").json()["items"][0]["id"]
    ok = client.post(f"/api/clusters/{cid}/indicators", headers=ANALYST,
                     json={"name": "Households", "value": "100", "year": "2024", "source_id": src})
    assert ok.status_code == 201
    assert client.post(f"/api/clusters/{cid}/indicators", headers=ANALYST,
                       json={"name": "X1", "value": "1", "source_id": 9999}).status_code == 404


def test_auth_check(client):
    assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers=ANALYST).json()["role"] == "analyst"


def test_empty_db_is_handled():
    from fastapi.testclient import TestClient

    from app.db import Base, engine
    from app.main import app
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        assert c.get("/api/clusters").json()["items"] == []
        m_ = c.get("/api/metrics").json()
        assert m_["assessments"] == 0 and m_["avg_evidence_coverage"] is None
