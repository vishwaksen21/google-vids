"""Selector discovery and diagnostics (read-only).

`diagnose` records what the current page offers: a screenshot, the accessibility snapshot,
and every visible interactive control by role and accessible name. From that, recon maps
catalogue keys (e.g. `generate.submit`) to concrete role+name locators. Candidates are
exactly that: nothing becomes a catalogue entry, and no capability becomes VERIFIED,
until a person confirms the mapping and a verification run passes against the live UI.

It never clicks, types or submits anything.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from vidsprod.logging import get_logger

from .browser_manager import safe_url

log = get_logger(__name__)

ROLES = ("button", "link", "textbox", "combobox", "menuitem", "tab", "checkbox", "radio",
         "slider", "dialog", "heading")
MAX_PER_ROLE = 80


async def diagnose(page: Any, out_dir: Path, label: str = "diagnostics") -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    shot = out_dir / f"{stamp}_{label}.png"
    await page.screenshot(path=str(shot), full_page=False)
    try:
        snapshot = await page.locator("body").aria_snapshot(timeout=15_000)
    except Exception as exc:  # recorded, not hidden
        snapshot = f"(accessibility snapshot unavailable: {type(exc).__name__})"
    snap_file = out_dir / f"{stamp}_{label}.aria.yaml"
    snap_file.write_text(snapshot, encoding="utf-8")

    controls: list[dict[str, Any]] = []
    for role in ROLES:
        loc = page.get_by_role(role)
        try:
            n = min(await loc.count(), MAX_PER_ROLE)
        except Exception as exc:
            log.info("role query failed", role=role, error=type(exc).__name__)
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                name = (await el.get_attribute("aria-label")) or (await el.inner_text(timeout=2000))
            except Exception as exc:  # detached while reading: skip this control
                log.info("control read failed", role=role, error=type(exc).__name__)
                continue
            name = " ".join((name or "").split())[:120]
            if name:
                controls.append({"role": role, "name": name})
    unique: dict[tuple[str, str], int] = {}
    for c in controls:
        unique[(c["role"], c["name"])] = unique.get((c["role"], c["name"]), 0) + 1
    candidates = [
        {"strategy": "role", "role": role, "value": name, "exact": True,
         "matches": count, "usable": count == 1}
        for (role, name), count in sorted(unique.items())
    ]
    report = {
        "page": safe_url(page.url),
        "title": await page.title(),
        "screenshot": str(shot),
        "aria_snapshot": str(snap_file),
        "controls": len(controls),
        "candidate_locators": candidates,
        "note": "Candidates only. A locator is used by the agent only after it is recorded "
                "in recon/locators.json and verified against the live UI.",
    }
    (out_dir / f"{stamp}_{label}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
