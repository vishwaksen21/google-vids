"""The capability registry.

Each Google Vids operation the workflow might use has a status:

* VERIFIED: observed working in the live UI by a recon script, with evidence, against the
  UI fingerprint currently seen. Only VERIFIED operations may be executed.
* UNVERIFIED: documented by Google, or plausible, but not yet observed by us.
* UNSUPPORTED: verified absent (or ruled out by policy).
* REQUIRES_HUMAN: exists, but a person must do it (e.g. signing in).

`source` says where the claim comes from: "documented" (Google help / Workspace Updates),
"observed" (recon evidence), "simulated" (the mock adapter's own behaviour — never a claim
about Vids) or "unknown".

The real registry is built from `recon/capabilities.json` when recon has produced it.
Until then every live operation is UNVERIFIED and the browser adapter refuses to run it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vidsprod.domain.enums import CapabilityStatus
from vidsprod.errors import CapabilityUnavailableError
from vidsprod.planning.durations import ClipLimits

HELP_GENERATE = "https://support.google.com/docs/answer/16143507"
HELP_EDITOR = "https://support.google.com/docs/answer/15082958"
UPDATES_OMNI = (
    "https://workspaceupdates.googleblog.com/2026/07/"
    "generate-higher-quality-ai-video-clips-and-edit-any-video-with-Gemini-Omni-in-Vids.html"
)
UPDATES_EXTEND = (
    "https://workspaceupdates.googleblog.com/2026/06/"
    "create-longer-veo-videos-and-generate-multiple-at-once-in-Google-Vids.html"
)


class Capability(BaseModel):
    key: str
    description: str
    status: CapabilityStatus
    source: str
    reference: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    verified_on: date | None = None
    ui_fingerprint: str | None = None
    evidence: list[str] = Field(default_factory=list)
    notes: str = ""


# What Google documents (checked 2026-09-29). None of it is observed yet.
_DOCUMENTED: list[dict[str, Any]] = [
    {"key": "session.sign_in", "description": "Sign in to the Google account",
     "status": "REQUIRES_HUMAN", "source": "policy",
     "notes": "A person signs in by hand; the application never handles credentials."},
    {"key": "session.check", "description": "Read whether the profile is signed in, and as whom",
     "status": "UNVERIFIED", "source": "unknown"},
    {"key": "project.create", "description": "Create a new blank Vid", "status": "UNVERIFIED",
     "source": "documented", "reference": HELP_EDITOR},
    {"key": "project.open", "description": "Open an existing Vid by URL", "status": "UNVERIFIED",
     "source": "documented", "reference": HELP_EDITOR},
    {"key": "clip.generate",
     "description": "'Create AI videos' → 'Create' → prompt → 'Generate'",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_GENERATE,
     "params": {"min_seconds": 3, "max_seconds": 10, "step_seconds": 1,
                "aspect_ratios": ["16:9", "9:16"], "max_ingredients": 7,
                "monthly_quota_typical": 50}},
    {"key": "clip.configure",
     "description": "Open the AI video panel, enter the prompt, set duration and aspect ratio "
                    "(without submitting)",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_GENERATE},
    {"key": "clip.ingredients", "description": "Attach up to 7 reference images",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_GENERATE},
    {"key": "clip.capture_frames",
     "description": "Obtain frames of a generated clip for inspection",
     "status": "UNVERIFIED", "source": "unknown",
     "notes": "Recon question Q5: preview <video> element, scrubbing, or screenshots only."},
    {"key": "clip.capture_audio", "description": "Obtain a clip's audio for transcription",
     "status": "UNVERIFIED", "source": "unknown"},
    {"key": "clip.generates_speech",
     "description": "Generated clips contain speech matching the prompt's dialogue",
     "status": "UNVERIFIED", "source": "unknown",
     "notes": "Recon question Q6. Until verified, scripts are voiceover-first."},
    {"key": "clip.edit_text", "description": "Edit a clip with a text instruction (Omni)",
     "status": "UNVERIFIED", "source": "documented", "reference": UPDATES_OMNI},
    {"key": "clip.extend", "description": "Extend an existing clip", "status": "UNVERIFIED",
     "source": "documented", "reference": UPDATES_EXTEND},
    {"key": "clip.insert", "description": "'Insert' a generated clip into the Vid",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_GENERATE},
    {"key": "timeline.add_scene",
     "description": "Add a blank scene at the end and make it active (the scene Insert fills)",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.read", "description": "Read scene order and durations back",
     "status": "UNVERIFIED", "source": "unknown"},
    {"key": "timeline.arrange", "description": "Reorder scenes", "status": "UNVERIFIED",
     "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.transition", "description": "Set the transition between scenes",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.text_overlay", "description": "Add and format on-screen text",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.image", "description": "Upload a still image into a scene and place it",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.layout_video", "description": "Resize and move a scene's clip on the canvas",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.logo", "description": "Upload and place the logo image",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.voiceover", "description": "Add AI voice-over from script text",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.music", "description": "Add stock or uploaded music",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "timeline.captions", "description": "Enable captions", "status": "UNVERIFIED",
     "source": "unknown"},
    {"key": "preview.capture", "description": "Play the preview and capture screenshots",
     "status": "UNVERIFIED", "source": "unknown"},
    {"key": "export.download_mp4", "description": "File → 'Download as MP4'",
     "status": "UNVERIFIED", "source": "documented", "reference": HELP_EDITOR},
    {"key": "export.drive", "description": "'Export to Drive'", "status": "UNVERIFIED",
     "source": "documented", "reference": HELP_EDITOR},
    {"key": "quota.read", "description": "Read the remaining monthly generations",
     "status": "UNVERIFIED", "source": "unknown"},
]


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability], *, simulated: bool) -> None:
        self._caps = {cap.key: cap for cap in capabilities}
        self.simulated = simulated

    def get(self, key: str) -> Capability:
        cap = self._caps.get(key)
        if cap is None:
            return Capability(key=key, description="(not in registry)",
                              status=CapabilityStatus.UNVERIFIED, source="unknown")
        return cap

    def is_verified(self, key: str) -> bool:
        return self.get(key).status is CapabilityStatus.VERIFIED

    def require(self, key: str) -> Capability:
        cap = self.get(key)
        if cap.status is not CapabilityStatus.VERIFIED:
            raise CapabilityUnavailableError(
                f"Vids capability '{key}' is {cap.status.value} ({cap.source}); it must be "
                "verified by recon against the live UI before it can be used",
                details={"capability": key, "status": cap.status.value, "source": cap.source},
            )
        return cap

    def clip_limits(self) -> ClipLimits:
        params = self.get("clip.generate").params
        return ClipLimits(
            min_seconds=float(params.get("min_seconds", 3)),
            max_seconds=float(params.get("max_seconds", 10)),
            step_seconds=float(params.get("step_seconds", 1)),
        )

    def invalidate_for_fingerprint(self, fingerprint: str) -> list[str]:
        """Demote VERIFIED capabilities verified against a different UI."""
        demoted = []
        for cap in self._caps.values():
            if (cap.status is CapabilityStatus.VERIFIED and cap.ui_fingerprint
                    and cap.ui_fingerprint != fingerprint):
                cap.status = CapabilityStatus.UNVERIFIED
                cap.notes = f"demoted: UI fingerprint changed from {cap.ui_fingerprint}"
                demoted.append(cap.key)
        return demoted

    def to_list(self) -> list[dict[str, Any]]:
        return [cap.model_dump(mode="json") for cap in self._caps.values()]


def live_registry(recon_path: Path) -> CapabilityRegistry:
    """Documented defaults, overlaid with recon evidence where it exists."""
    caps = {entry["key"]: Capability(**entry) for entry in _DOCUMENTED}
    if recon_path.exists():
        for entry in json.loads(recon_path.read_text(encoding="utf-8")).get("capabilities", []):
            # Recon may only claim VERIFIED with evidence and a fingerprint.
            if entry.get("status") == "VERIFIED" and not (
                entry.get("evidence") and entry.get("ui_fingerprint") and entry.get("verified_on")
            ):
                entry = {**entry, "status": "UNVERIFIED",
                         "notes": "claimed VERIFIED without evidence; ignored"}
            base = caps.get(entry["key"])
            merged = {**(base.model_dump() if base else {}), **entry, "source":
                      "observed" if entry.get("status") == "VERIFIED" else entry.get(
                          "source", base.source if base else "unknown")}
            caps[entry["key"]] = Capability(**merged)
    return CapabilityRegistry(list(caps.values()), simulated=False)


def mock_registry() -> CapabilityRegistry:
    """The mock adapter's own abilities. 'simulated' is not a statement about Vids."""
    caps = []
    for entry in _DOCUMENTED:
        status = "REQUIRES_HUMAN" if entry["key"] == "session.sign_in" else "VERIFIED"
        caps.append(Capability(**{
            **entry, "status": status, "source": "simulated",
            "notes": "Implemented by the mock adapter (simulation only).",
        }))
    return CapabilityRegistry(caps, simulated=True)
