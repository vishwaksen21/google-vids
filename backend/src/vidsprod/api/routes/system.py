"""Health, system status, approvals, artifacts, and the browser session."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select

from vidsprod.api.deps import ActorDep, ContainerDep, require_token
from vidsprod.api.schemas import ApprovalOut, ArtifactOut, Decision
from vidsprod.automation.google_vids import BrowserVidsAdapter
from vidsprod.db.models import ApprovalRequest, Artifact, BrowserSession
from vidsprod.domain.enums import ApprovalKind, ApprovalStatus, ArtifactKind
from vidsprod.errors import ConflictError, NotFoundError
from vidsprod.media import ffmpeg
from vidsprod.vids.port import SessionState, SessionStateName
from vidsprod.workflow import audit, budget

public = APIRouter(tags=["health"])
router = APIRouter(prefix="/api", tags=["system"], dependencies=[Depends(require_token)])

_LOGIN_CODES = {"SESSION_EXPIRED", "WRONG_ACCOUNT", "GOOGLE_CHALLENGE"}


@public.get("/health")
async def health() -> dict[str, str]:
    """Liveness only: the process answers. Details are behind authentication."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(c: ContainerDep) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        await c.db.ping()
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    probe = c.store.root / ".probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        checks["artifact_store"] = {"ok": True, "free_gb": round(shutil.disk_usage(c.store.root).free / 1e9, 1)}
    except OSError as exc:
        checks["artifact_store"] = {"ok": False, "error": type(exc).__name__}
    checks["ffmpeg"] = {"ok": bool(ffmpeg.FFMPEG)}
    checks["ai_provider"] = {"ok": c.provider_error is None, "provider": c.ai.provider_name,
                             "error": c.provider_error}
    checks["design_md"] = {"ok": c.brand.available, "digest": c.brand.source_digest}
    checks["vids"] = {"ok": True, "mode": c.settings.google_vids_mode.value,
                      "live_actions_allowed": c.settings.live_actions_allowed}
    return {"ok": all(v.get("ok") for v in checks.values()), "checks": checks}


@router.get("/system")
async def system(c: ContainerDep) -> dict[str, Any]:
    async with c.db.transaction() as session:
        usage = await budget.usage(session, c.settings, c.adapter.account_key)
    return {
        "settings": c.settings.public_view(),
        "simulation": c.settings.simulation,
        "provider_error": c.provider_error,
        "capabilities": c.adapter.capabilities.to_list(),
        "capabilities_simulated": c.adapter.capabilities.simulated,
        "budget": usage,
        "brand": {"available": c.brand.available, "digest": c.brand.source_digest,
                  "palette": list(c.brand.palette_hexes)},
    }


# ------------------------------------------------------------------ approvals
@router.get("/approvals")
async def list_approvals(c: ContainerDep, status: str = "PENDING") -> list[ApprovalOut]:
    async with c.db.session() as session:
        rows = (await session.execute(select(ApprovalRequest).where(
            ApprovalRequest.status == status).order_by(ApprovalRequest.created_at))).scalars().all()
        return [ApprovalOut.model_validate(r) for r in rows]


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: uuid.UUID, body: Decision, c: ContainerDep, actor: ActorDep) -> ApprovalOut:
    payload = body.model_dump(exclude={"approve", "reason"}, exclude_none=True)
    return ApprovalOut.model_validate(
        await c.control.decide(approval_id, approve=body.approve, actor=actor, reason=body.reason,
                               payload=payload))


# ------------------------------------------------------------------ artifacts
@router.get("/artifacts/{artifact_id}")
async def artifact_meta(artifact_id: uuid.UUID, c: ContainerDep) -> ArtifactOut:
    async with c.db.session() as session:
        row = await session.get(Artifact, artifact_id)
        if row is None:
            raise NotFoundError("artifact not found")
        return ArtifactOut.model_validate(row)


@router.get("/artifacts/{artifact_id}/content")
async def artifact_content(artifact_id: uuid.UUID, c: ContainerDep) -> FileResponse:
    async with c.db.session() as session:
        row = await session.get(Artifact, artifact_id)
    if row is None:
        raise NotFoundError("artifact not found")
    path = c.store.resolve(row.uri)
    safe_mime = row.mime if row.mime.split("/")[0] in ("image", "video", "audio") or row.mime in (
        "application/json", "text/plain") else "application/octet-stream"
    return FileResponse(path, media_type=safe_mime, headers={
        "Content-Disposition": f'inline; filename="{row.id}{path.suffix}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------------ browser session
def _browser(c) -> BrowserVidsAdapter:
    if not isinstance(c.adapter, BrowserVidsAdapter):
        raise ConflictError("GOOGLE_VIDS_MODE is mock: there is no real browser session. "
                            "Set GOOGLE_VIDS_MODE=browser_test to launch Chrome read-only.")
    return c.adapter


async def _record_session(c, state: SessionState, actor: str) -> dict[str, Any]:
    stored = await _store_evidence(c, state)
    async with c.db.transaction() as session:
        session.add(BrowserSession(
            adapter=c.adapter.name, account_key=c.adapter.account_key, state=state.state.value,
            authenticated=state.authenticated, account_label=state.account_label,
            ui_fingerprint=state.ui_fingerprint, fingerprint_matches=state.fingerprint_matches,
            details={"message": state.message, "page": state.page,
                     "account_confirmed": state.account_confirmed},
            evidence_artifact_id=stored,
        ))
        await audit.record(session, type="browser.session_checked", actor=actor,
                           message=f"{state.state.value}: {state.message}")
    return _session_view(state, stored)


async def _store_evidence(c, state: SessionState) -> uuid.UUID | None:
    for file in state.evidence:
        if file.path.exists():
            async with c.db.transaction() as session:
                artifact = await c.store.put_file(session, file.path, job_id=None, kind=file.kind,
                                                  producer="browser", mime=file.mime)
                return artifact.id
    return None


def _session_view(state: SessionState, evidence_id: uuid.UUID | None = None) -> dict[str, Any]:
    if state.simulated:  # never let a simulated session read as real authentication
        return {"state": "SIMULATED", "authenticated": False, "account_confirmed": False,
                "account": None, "page": None, "simulated": True,
                "message": "Mock mode: no real Google session exists. " + state.message,
                "ui_fingerprint_matches": None, "evidence_artifact_id": None}
    return {"state": state.state.value, "authenticated": state.authenticated,
            "account_confirmed": state.account_confirmed, "account": state.account_label,
            "page": state.page, "message": state.message,
            "ui_fingerprint_matches": state.fingerprint_matches,
            "evidence_artifact_id": str(evidence_id) if evidence_id else None}


@router.get("/browser/status")
async def browser_status(c: ContainerDep) -> dict[str, Any]:
    async with c.db.session() as session:
        # Only a real browser session is evidence of Google sign-in; the mock adapter's
        # simulated checks are kept in the audit history but never reported as sign-in.
        last = await session.scalar(select(BrowserSession).where(BrowserSession.adapter == "browser")
                                    .order_by(BrowserSession.checked_at.desc()).limit(1))
    base = {
        "mode": c.settings.google_vids_mode.value,
        "live_actions_allowed": c.settings.live_actions_allowed,
        "live_automation_flag": c.settings.google_vids_live_automation_enabled,
        "allowed_account_configured": bool(c.settings.google_vids_allowed_account),
        "simulated": c.adapter.simulated,
        "last_check": None if last is None else {
            "state": last.state, "authenticated": last.authenticated, "account": last.account_label,
            "message": last.details.get("message"), "page": last.details.get("page"),
            "checked_at": last.checked_at, "evidence_artifact_id": str(last.evidence_artifact_id)
            if last.evidence_artifact_id else None},
    }
    if isinstance(c.adapter, BrowserVidsAdapter):
        base["browser"] = c.adapter.manager.status()
        base["unsaved_clip"] = c.adapter.unsaved_clip
    else:
        base["browser"] = {"enabled": False, "running": False, "mode": "mock"}
    base["readiness"] = _readiness(c, base)
    return base


# What real production needs from Vids, in order. Anything not VERIFIED blocks live mode.
_PRODUCTION_CAPABILITIES = ("session.check", "project.create", "project.open", "clip.configure",
                            "clip.generate", "clip.capture_frames", "clip.insert", "timeline.add_scene",
                            "timeline.read", "timeline.image",
                            "timeline.text_overlay", "timeline.captions", "export.download_mp4")


def _readiness(c, base: dict[str, Any]) -> dict[str, Any]:
    """One honest state for the dashboard, from real evidence only. A simulated session is
    never 'authenticated'."""
    if c.adapter.simulated:
        return {"state": "MOCK", "label": "Mock mode: no real Google Vids", "steps": [],
                "blockers": ["GOOGLE_VIDS_MODE=mock"]}
    last = base.get("last_check") or {}
    registry = c.adapter.capabilities
    verified = [k for k in _PRODUCTION_CAPABILITIES if registry.is_verified(k)]
    missing = [k for k in _PRODUCTION_CAPABILITIES if k not in verified]
    signed_in = last.get("state") in ("SIGNED_IN", "READY")
    steps = [
        {"key": "browser", "label": "Chrome running", "ok": bool(base["browser"].get("running"))},
        {"key": "auth", "label": "Signed in to Google Vids", "ok": signed_in},
        {"key": "editor", "label": "Editor accessible (project create/open verified)",
         "ok": registry.is_verified("project.create") and registry.is_verified("project.open")},
        {"key": "recon", "label": f"Recon: {len(verified)} of {len(_PRODUCTION_CAPABILITIES)} "
                                  "production capabilities verified", "ok": not missing},
        {"key": "live", "label": "Live automation authorised", "ok": c.settings.live_actions_allowed},
    ]
    blockers = []
    if base.get("unsaved_clip"):
        blockers.append("a generated clip is open in Chrome but not inserted: insert it in the open "
                        "Vid (do not close Chrome or reload)")
    if not signed_in:
        blockers.append("sign in to Google Vids in the dedicated Chrome profile")
    if missing:
        blockers.append("unverified: " + ", ".join(missing))
    if not c.settings.live_actions_allowed:
        blockers.append("live automation disabled (GOOGLE_VIDS_MODE / GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED)")
    if not last:
        state, label = "NOT_CHECKED", "Session not checked yet"
    elif last.get("state") in ("SIGNED_OUT", "CHALLENGE", "WRONG_ACCOUNT", "ACCOUNT_UNCONFIRMED"):
        state, label = "SIGNED_OUT", "Signed out" if last.get("state") == "SIGNED_OUT" else last.get("state")
    elif not blockers:
        state, label = "LIVE_READY", "Live ready"
    elif missing and signed_in:
        state, label = "RECON_INCOMPLETE", f"Authenticated · recon incomplete ({len(missing)} left)"
    else:
        state, label = "LIVE_BLOCKED", "Authenticated · live blocked"
    if not base["browser"].get("running") and state not in ("SIGNED_OUT", "NOT_CHECKED"):
        label += " (Chrome not running now)"
    return {"state": state, "label": label, "steps": steps, "blockers": blockers,
            "verified": verified, "missing": missing}


@router.post("/browser/launch")
async def browser_launch(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    status = await _browser(c).launch()
    async with c.db.transaction() as session:
        await audit.record(session, type="browser.launched", actor=actor, message="Chrome launched")
    return status


@router.post("/browser/check")
async def browser_check(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    """Read-only: open Google Vids and report the sign-in state (simulated in mock mode)."""
    state = await c.adapter.check_session(c.store.work_dir(None, "session"))
    return await _record_session(c, state, actor)


@router.post("/browser/login")
async def browser_login(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    """Show Google Vids / Google sign-in in the dedicated Chrome window. The person signs in
    there by hand; the application never sees or types a password."""
    state = await _browser(c).open_home()
    return await _record_session(c, state, actor)


@router.post("/browser/login/complete")
async def browser_login_complete(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    """The person says they have signed in: verify it, and if it holds, resume every job
    that was waiting for sign-in."""
    state = await _browser(c).check_session(c.store.work_dir(None, "session"))
    view = await _record_session(c, state, actor)
    resumed = []
    if state.state in (SessionStateName.SIGNED_IN, SessionStateName.READY):
        async with c.db.session() as session:
            waiting = (await session.execute(select(ApprovalRequest).where(
                ApprovalRequest.kind == ApprovalKind.INTERVENTION.value,
                ApprovalRequest.status == ApprovalStatus.PENDING.value))).scalars().all()
        for approval in waiting:
            code = ((approval.payload or {}).get("error") or {}).get("code")
            if code in _LOGIN_CODES:
                await c.control.decide(approval.id, approve=True, actor=actor,
                                       reason=f"sign-in confirmed: {state.message}")
                resumed.append(str(approval.job_id))
    view["resumed_jobs"] = resumed
    return view


@router.post("/browser/diagnose")
async def browser_diagnose(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    """Read-only selector discovery: screenshot, accessibility snapshot, visible controls."""
    report = await _browser(c).diagnose()
    async with c.db.transaction() as session:
        shot = await c.store.put_file(session, Path(report["screenshot"]), job_id=None,
                                      kind=ArtifactKind.SCREENSHOT, producer="diagnostics",
                                      mime="image/png")
        snap = await c.store.put_file(session, Path(report["aria_snapshot"]), job_id=None,
                                      kind=ArtifactKind.DOM_SNAPSHOT, producer="diagnostics",
                                      mime="text/plain")
        await audit.record(session, type="browser.diagnosed", actor=actor,
                           message=f"{report['controls']} controls on {report['page']}")
    report["screenshot_artifact_id"] = str(shot.id)
    report["aria_snapshot_artifact_id"] = str(snap.id)
    report.pop("screenshot"), report.pop("aria_snapshot")
    return report


@router.post("/browser/release-clip")
async def browser_release_clip(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    """A person confirmed the pending generated clip is in the Vid (or accepts it is lost), so
    productions may use the browser again."""
    browser = _browser(c)
    clip = browser.unsaved_clip
    browser.release_unsaved_clip()
    async with c.db.transaction() as session:
        await audit.record(session, type="browser.clip_released", actor=actor,
                           message="pending generated clip released by a person", data={"clip": clip})
    return {"released": clip}


@router.post("/browser/close")
async def browser_close(c: ContainerDep, actor: ActorDep) -> dict[str, Any]:
    browser = _browser(c)
    if browser.unsaved_clip is not None:  # closing would lose a generated, uninserted clip
        raise ConflictError("Chrome stays open: a generated clip is in the page but not inserted. "
                            "Insert it in the open Vid first.", details={"clip": browser.unsaved_clip})
    await browser.close()
    async with c.db.transaction() as session:
        await audit.record(session, type="browser.closed", actor=actor, message="Chrome closed")
    return browser.manager.status()
