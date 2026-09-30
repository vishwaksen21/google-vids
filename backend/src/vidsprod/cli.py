"""`vidsprod` command line.

    vidsprod serve [--port 8100]          API + embedded worker (one process owns the browser)
    vidsprod worker                       standalone worker (mock mode only; see below)
    vidsprod migrate                      apply database migrations
    vidsprod doctor                       check configuration and dependencies
    vidsprod browser login [--timeout S]  open Google Vids in the dedicated Chrome profile and
                                          wait while you sign in by hand
    vidsprod browser check                read-only sign-in check
    vidsprod browser diagnose             read-only selector discovery into recon/<date>/
    vidsprod recon template               print the locator keys recon must record

A Chrome profile can be open in one process at a time. In browser_test/live mode run
either `serve` (which owns the browser) or the `browser` commands, not both at once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date

from vidsprod.config import BACKEND_DIR, VidsMode, get_settings


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


async def _browser(args) -> int:
    from vidsprod.automation.google_vids import BrowserVidsAdapter, authentication, diagnostics
    from vidsprod.vids.capabilities import live_registry

    settings = get_settings()
    if not settings.browser_enabled:
        print("GOOGLE_VIDS_MODE is 'mock'. Set GOOGLE_VIDS_MODE=browser_test (read-only) to use "
              "the real browser.", file=sys.stderr)
        return 2
    if args.action == "login":
        settings = settings.model_copy(update={"browser_headless": False})
    adapter = BrowserVidsAdapter(settings, live_registry(settings.locator_catalogue_path.parent
                                                         / "capabilities.json"))
    try:
        if args.action == "login":
            print("Opening Google Vids in the dedicated Chrome window.")
            print("Sign in there by hand. This tool never asks for, sees or stores a password.")
            state = await authentication.open_login(adapter.manager)
            if state.state.value not in ("SIGNED_IN", "READY"):
                print(f"Now: {state.state.value}: {state.message}")
                print(f"Waiting up to {args.timeout}s for you to finish signing in...")
                state = await authentication.wait_for_login(adapter.manager,
                                                            timeout_seconds=args.timeout)
            _print(state.model_dump(mode="json", exclude={"evidence"}))
            return 0 if state.state.value in ("SIGNED_IN", "READY") else 1
        if args.action == "check":
            evidence = settings.artifact_dir / "browser" / "session"
            state = await adapter.check_session(evidence)
            _print({**state.model_dump(mode="json", exclude={"evidence"}),
                    "evidence": [str(e.path) for e in state.evidence]})
            return 0
        if args.action == "diagnose":
            out = BACKEND_DIR.parent / "recon" / date.today().isoformat()
            async with adapter.manager.lock:
                page = await adapter.manager.open_home()
                report = await diagnostics.diagnose(page, out, args.label)
            _print({k: v for k, v in report.items() if k != "candidate_locators"}
                   | {"candidates": len(report["candidate_locators"])})
            print(f"Full report in {out}")
            return 0
    finally:
        await adapter.close()
    return 2


async def _doctor() -> int:
    import shutil

    from vidsprod.brand.design_document import load_brand_guidelines
    from vidsprod.db.session import Database, make_engine
    from vidsprod.media import ffmpeg

    settings = get_settings()
    checks = {}
    db = Database(make_engine(settings))
    try:
        await db.ping()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"FAILED: {type(exc).__name__}"
    finally:
        await db.dispose()
    checks["ffmpeg"] = ffmpeg.FFMPEG
    checks["design_md"] = "ok" if load_brand_guidelines(settings.design_md_path).available else "MISSING"
    checks["ai_provider"] = settings.ai_provider.value
    if settings.ai_provider.value == "gateway":
        if not (settings.gateway_base_url and settings.gateway_api_key):
            checks["gateway"] = "NOT CONFIGURED (GATEWAY_BASE_URL, GATEWAY_API_KEY)"
        else:
            from vidsprod.llm.providers import GatewayProvider

            gw = GatewayProvider(settings)
            try:
                models = await gw.list_models()
                wanted = {settings.ai_model_creative, settings.ai_model_reasoning,
                          settings.ai_model_vision, settings.ai_model_transcribe}
                missing = sorted(m for m in wanted if not any(m in x for x in models))
                checks["gateway"] = "ok" if not missing else f"models not offered: {missing}"
            except Exception as exc:
                checks["gateway"] = f"FAILED: {type(exc).__name__}"
            finally:
                await gw.aclose()
    checks["vids_mode"] = settings.google_vids_mode.value
    checks["live_actions_allowed"] = settings.live_actions_allowed
    checks["allowed_account_configured"] = bool(settings.google_vids_allowed_account)
    checks["chrome"] = ("installed" if shutil.which("google-chrome") or
                        (settings.browser_channel == "chrome" and sys.platform == "darwin"
                         and __import__("pathlib").Path("/Applications/Google Chrome.app").exists())
                        else "not found (set BROWSER_CHANNEL= to use bundled Chromium)")
    checks["locator_catalogue"] = ("present" if settings.locator_catalogue_path.exists()
                                   else "absent: no Vids capability is verified yet")
    _print(checks)
    return 0 if checks["database"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vidsprod")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--host", default="127.0.0.1")
    sub.add_parser("worker")
    sub.add_parser("migrate")
    sub.add_parser("doctor")
    browser = sub.add_parser("browser")
    browser.add_argument("action", choices=["login", "check", "diagnose"])
    browser.add_argument("--timeout", type=float, default=300)
    browser.add_argument("--label", default="vids")
    recon = sub.add_parser("recon")
    recon.add_argument("action", choices=["template"])
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("vidsprod.main:app", host=args.host, port=args.port)
        return 0
    if args.command == "worker":
        settings = get_settings()
        if settings.google_vids_mode is not VidsMode.MOCK:
            print("In browser_test/live mode the API process owns the browser; run "
                  "`vidsprod serve` with WORKER_EMBEDDED=true instead.", file=sys.stderr)
            return 2

        async def run_worker() -> None:
            from vidsprod.container import Container

            c = await Container.build(settings)
            c.start_worker()
            try:
                await asyncio.Event().wait()
            finally:
                await c.close()

        asyncio.run(run_worker())
        return 0
    if args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
        return 0
    if args.command == "doctor":
        return asyncio.run(_doctor())
    if args.command == "browser":
        return asyncio.run(_browser(args))
    if args.command == "recon":
        from vidsprod.automation.google_vids import REQUIRED_LOCATORS

        _print({"ui_fingerprint": None, "fingerprint_landmarks": [],
                "locators": {key: {"strategy": "role", "role": "button", "value": "TODO",
                                   "verified_on": None}
                             for keys in REQUIRED_LOCATORS.values() for key in keys},
                "required_by_capability": REQUIRED_LOCATORS})
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
