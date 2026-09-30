"""The HTTP API: authentication, attribution, campaign CRUD, controls, uploads, artifacts,
browser endpoints, and a production driven entirely over HTTP."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import run_to_rest
from vidsprod.main import create_app

pytestmark = pytest.mark.db

BRIEF = {
    "raw_brief": "A warm film for young families about one policy that covers everyone at home.",
    "cta": "Talk to a Prudential advisor today",
    "required_claims": [{"text": "One policy covers every member of the family named on it.",
                         "fact_id": "PRU-HEALTH-FF-001"}],
    "output": {"aspect_ratio": "16:9", "min_duration_seconds": 15, "max_duration_seconds": 45},
}


@pytest.fixture
async def api(container):
    app = create_app(container)
    app.state.container = container  # the transport does not run the lifespan
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers={
            "Authorization": "Bearer test-token", "X-Actor": "API Tester"}) as client:
        yield client


async def _campaign(api, name="API campaign"):
    r = await api.post("/api/campaigns", json={"name": name, "brief": BRIEF})
    assert r.status_code == 201, r.text
    return r.json()


async def test_liveness_is_public_and_everything_else_needs_the_token(api):
    assert (await api.get("/health")).json() == {"status": "ok"}
    no_token = await api.get("/api/system", headers={"Authorization": ""})
    assert no_token.status_code == 401
    wrong = await api.get("/api/system", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert (await api.get("/api/system")).status_code == 200


async def test_changes_must_name_the_person_acting(api):
    r = await api.post("/api/campaigns", json={"name": "x" * 5, "brief": BRIEF},
                       headers={"X-Actor": ""})
    assert r.status_code == 400 and "X-Actor" in r.text


async def test_readiness_reports_each_dependency(api):
    body = (await api.get("/api/health/ready")).json()
    assert body["checks"]["database"]["ok"] and body["checks"]["design_md"]["ok"]
    assert body["checks"]["vids"]["mode"] == "mock"


async def test_campaign_crud_versions_and_optimistic_concurrency(api):
    created = await _campaign(api)
    assert created["status"] == "READY" and created["brief"]["cta"] == BRIEF["cta"]
    listed = (await api.get("/api/campaigns")).json()
    assert [c["id"] for c in listed] == [created["id"]]
    renamed = await api.patch(f"/api/campaigns/{created['id']}",
                              json={"name": "Renamed", "expected_row_version": created["row_version"]})
    assert renamed.json()["name"] == "Renamed"
    stale = await api.patch(f"/api/campaigns/{created['id']}",
                            json={"name": "Again", "expected_row_version": created["row_version"]})
    assert stale.status_code == 409
    v2 = await api.put(f"/api/campaigns/{created['id']}/brief",
                       json={"brief": {**BRIEF, "tone": "Calm"}})
    assert v2.json()["current_brief_version"] == 2
    versions = (await api.get(f"/api/campaigns/{created['id']}/briefs")).json()
    assert [v["version"] for v in versions] == [2, 1]
    archived = await api.post(f"/api/campaigns/{created['id']}/archive")
    assert archived.json()["status"] == "ARCHIVED"
    assert (await api.get("/api/campaigns")).json() == []
    assert len((await api.get("/api/campaigns?include_archived=true")).json()) == 1


async def test_briefs_are_validated(api):
    bad = await api.post("/api/campaigns", json={"name": "Bad", "brief": {"raw_brief": ""}})
    assert bad.status_code == 422
    backwards = await api.post("/api/campaigns", json={"name": "Bad", "brief": {
        **BRIEF, "output": {"min_duration_seconds": 40, "max_duration_seconds": 20}}})
    assert backwards.status_code == 422
    unknown_ref = await api.post("/api/campaigns", json={"name": "Bad", "brief": {
        **BRIEF, "reference_asset_ids": ["0190a0a0-0000-7000-8000-000000000000"]}})
    assert unknown_ref.status_code == 422


async def test_reference_uploads_are_checked_by_content(api, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
    ok = await api.post("/api/references", files={"file": ("ref.png", png, "image/png")})
    assert ok.status_code == 201
    liar = await api.post("/api/references", files={"file": ("x.png", b"<script>", "image/png")})
    assert liar.status_code == 422
    wrong_type = await api.post("/api/references", files={"file": ("x.svg", b"<svg/>", "image/svg+xml")})
    assert wrong_type.status_code == 422
    created = await api.post("/api/campaigns", json={"name": "With ref", "brief": {
        **BRIEF, "reference_asset_ids": [ok.json()["id"]]}})
    assert created.status_code == 201


async def test_archiving_an_active_campaign_is_refused(api):
    campaign = await _campaign(api)
    await api.post(f"/api/campaigns/{campaign['id']}/jobs")
    assert (await api.post(f"/api/campaigns/{campaign['id']}/archive")).status_code == 409


async def test_start_is_idempotent_and_one_production_at_a_time(api):
    campaign = await _campaign(api)
    first = await api.post(f"/api/campaigns/{campaign['id']}/jobs", headers={"Idempotency-Key": "k1"})
    again = await api.post(f"/api/campaigns/{campaign['id']}/jobs", headers={"Idempotency-Key": "k1"})
    other = await api.post(f"/api/campaigns/{campaign['id']}/jobs")
    assert first.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert other.status_code == 409 and other.json()["error"]["code"] == "CONFLICT"


async def test_a_production_driven_entirely_over_http(api, container):
    campaign = await _campaign(api)
    job = (await api.post(f"/api/campaigns/{campaign['id']}/jobs")).json()
    for expected in ("PLAN", "COMPLIANCE"):
        await run_to_rest(container)
        detail = (await api.get(f"/api/jobs/{job['id']}")).json()
        assert detail["status"] == "WAITING_FOR_HUMAN"
        pending = [a for a in detail["approvals"] if a["status"] == "PENDING"]
        assert [a["kind"] for a in pending] == [expected]
        r = await api.post(f"/api/approvals/{pending[0]['id']}/decision",
                           json={"approve": True, "reason": "checked"})
        assert r.status_code == 200 and r.json()["decided_by"] == "API Tester"
    await run_to_rest(container)
    detail = (await api.get(f"/api/jobs/{job['id']}")).json()
    assert detail["status"] == "COMPLETED" and detail["progress"] == 1.0 and detail["simulation"]
    assert all(s["status"] in ("PASSED", "PASSED_WITH_WARNINGS") for s in detail["stages"])
    plan = (await api.get(f"/api/jobs/{job['id']}/plan")).json()
    assert plan["SCENE_PLAN"]["content"]["scenes"]
    scenes = (await api.get(f"/api/jobs/{job['id']}/scenes")).json()
    assert scenes and all(s["status"] == "ACCEPTED" and s["takes"][-1]["locked"] for s in scenes)
    assert all(s["takes"][-1]["validations"] for s in scenes), "every accepted take has evidence"
    frame_id = scenes[0]["takes"][0]["evidence"][0]
    meta = (await api.get(f"/api/artifacts/{frame_id}")).json()
    content = await api.get(f"/api/artifacts/{frame_id}/content")
    assert content.status_code == 200 and content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-type"].startswith(meta["mime"].split(";")[0])
    qa = (await api.get(f"/api/jobs/{job['id']}/qa")).json()
    assert qa["content"]["verdict"] in ("PASS", "PASS_WITH_WARNINGS")
    exports = (await api.get(f"/api/jobs/{job['id']}/exports")).json()
    assert exports["exports"][-1]["status"] == "VERIFIED"
    assert exports["deliveries"][0]["simulated"] is True
    stage = (await api.get(f"/api/jobs/{job['id']}/stages/EXPORT")).json()
    assert stage[0]["validations"] and "traceback" not in str(stage)
    audit = (await api.get(f"/api/jobs/{job['id']}/audit?limit=1000")).json()
    assert audit[0]["id"] < audit[-1]["id"]
    ai = (await api.get(f"/api/jobs/{job['id']}/ai-requests")).json()
    assert {r["purpose"] for r in ai} >= {"intake", "creative", "script", "scene_inspection"}


async def test_pause_resume_cancel_and_retry_over_http(api, container):
    campaign = await _campaign(api)
    job = (await api.post(f"/api/campaigns/{campaign['id']}/jobs")).json()
    paused = await api.post(f"/api/jobs/{job['id']}/pause")
    assert paused.json()["status"] == "PAUSED"
    assert (await api.post(f"/api/jobs/{job['id']}/pause")).status_code == 409
    assert (await api.post(f"/api/jobs/{job['id']}/resume")).json()["status"] == "QUEUED"
    bad_stage = await api.post(f"/api/jobs/{job['id']}/retry", json={"stage": "NOPE"})
    assert bad_stage.status_code == 422
    assert (await api.post(f"/api/jobs/{job['id']}/cancel")).json()["status"] == "CANCELLED"


async def test_scene_review_endpoint_only_acts_on_scenes_waiting_for_review(api, container):
    campaign = await _campaign(api)
    job = (await api.post(f"/api/campaigns/{campaign['id']}/jobs")).json()
    r = await api.post(f"/api/jobs/{job['id']}/scenes/S01/review",
                       json={"action": "accept", "reason": "looks fine"})
    assert r.status_code == 409


async def test_browser_endpoints_explain_mock_mode(api):
    status = (await api.get("/api/browser/status")).json()
    assert status["mode"] == "mock" and status["live_actions_allowed"] is False
    launch = await api.post("/api/browser/launch")
    assert launch.status_code == 409 and "browser_test" in launch.json()["error"]["message"]
    check = (await api.post("/api/browser/check")).json()
    # A simulated session is never reported as real sign-in.
    assert check["state"] == "SIMULATED" and check["authenticated"] is False
    assert (await api.get("/api/browser/status")).json()["last_check"] is None


async def test_live_events_stream_from_the_audit_trail(api, container):
    """The in-process transport buffers whole responses, so the stream generator is driven
    directly with a request that disconnects after two polls."""
    from vidsprod.api.routes.jobs import job_events

    campaign = await _campaign(api)
    job = (await api.post(f"/api/campaigns/{campaign['id']}/jobs")).json()
    await run_to_rest(container)

    class Req:
        polls = 0

        async def is_disconnected(self):
            Req.polls += 1
            return Req.polls > 2

    events = [e async for e in job_events(job["id"], Req(), container, after=0)]
    kinds = [e.event for e in events]
    assert kinds.count("status") == 1, "status is sent once, then only when it changes"
    audit_ids = [int(e.id) for e in events if e.event == "audit"]
    assert len(audit_ids) >= 5 and audit_ids == sorted(audit_ids)
    resumed = [e async for e in job_events(job["id"], type("R", (), {
        "polls": 0, "is_disconnected": lambda self: _once(self)})(), container, after=audit_ids[-2])]
    assert [int(e.id) for e in resumed if e.event == "audit"] == audit_ids[-1:], "resume after a cursor"


async def _once(req):
    req.polls += 1
    return req.polls > 1
