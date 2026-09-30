"""The fake provider: deterministic, offline, and always labelled as simulation.

Two uses:

* **Tests** script exact replies per purpose (`FakeProvider(script={...})`), including
  malformed JSON and transient errors, to exercise the AI service and the agents.
* **Local simulation** (AI_PROVIDER=fake, refused in production) builds schema-valid
  outputs from the structured context each agent passes in `simulation_hints`, so the
  whole pipeline can run end to end without credentials. Every job run this way is
  flagged `simulation=true` and the dashboard says so; nothing produced here is presented
  as real creative work.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from vidsprod.errors import ProviderTransientError
from vidsprod.llm.types import LLMRequest, ProviderReply, Usage

Reply = str | dict | Exception | Callable[[LLMRequest], str | dict]


class FakeProvider:
    name = "fake"

    def __init__(self, script: dict[str, list[Reply]] | None = None, simulate: bool = True):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.simulate = simulate
        self.calls: list[LLMRequest] = []

    async def generate(self, request: LLMRequest, model: str) -> ProviderReply:
        self.calls.append(request)
        queue = self.script.get(request.purpose)
        if queue:
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(reply, Exception):
                raise reply
            if callable(reply):
                reply = reply(request)
        elif self.simulate:
            handler = _SIMULATORS.get(request.purpose)
            if handler is None:
                raise ProviderTransientError(f"fake provider has no simulator for {request.purpose}")
            reply = handler(request.simulation_hints)
        else:
            raise AssertionError(f"unscripted fake call for purpose {request.purpose!r}")
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ProviderReply(text=text, model=f"fake:{model}",
                             usage=Usage(input_tokens=len(request.prompt) // 4,
                                         output_tokens=len(text) // 4))

    async def aclose(self) -> None:
        return None


# ======================================================================= simulators
def _first_sentence(text: str, limit: int = 120) -> str:
    sentence = text.strip().split(".")[0].strip()
    return (sentence or text.strip())[:limit]


def _sim_intake(h: dict[str, Any]) -> dict:
    b = h["brief"]
    raw = b["raw_brief"]
    missing = [k for k in ("objective", "audience", "product", "key_message", "cta", "tone")
               if not b.get(k)]
    return {
        "objective": b.get("objective") or _first_sentence(raw),
        "audience": b.get("audience") or "Adults planning for their family's health",
        "product": b.get("product") or "Health cover",
        "key_message": b.get("key_message") or _first_sentence(raw),
        "cta": b.get("cta") or "Talk to us today",
        "tone": b.get("tone") or h.get("brand_tone") or "Warm, reassuring, credible",
        "claims": b.get("required_claims", []),
        "disclosures": b.get("disclosures", []),
        "brand_rules": ["Restraint and credibility; no guarantees or hype"],
        "mandatory_elements": b.get("mandatory_elements", []),
        "output": b["output"],
        "open_questions": [f"The brief does not state the {m.replace('_', ' ')}" for m in missing],
    }


def _sim_creative(h: dict[str, Any]) -> dict:
    brief = h["brief"]
    beats = [
        {"id": "b1", "name": "Everyday moment", "purpose": "Meet the family", "emotion": "warmth"},
        {"id": "b2", "name": "The question", "purpose": f"Surface the need: {brief['objective']}",
         "emotion": "concern"},
        {"id": "b3", "name": "The answer", "purpose": f"Introduce {brief['product']}",
         "emotion": "relief"},
    ]
    if brief.get("claims"):
        beats.append({"id": "b4", "name": "Why it helps", "purpose": "State the approved facts",
                      "emotion": "confidence"})
    beats.append({"id": f"b{len(beats) + 1}", "name": "Invitation", "purpose": "Call to action",
                  "emotion": "reassurance"})
    return {
        "title": f"{brief['product']}: {brief['key_message']}"[:120],
        "logline": f"A family finds calm in {brief['product']}.",
        "concept": f"A grounded, human story for {brief['audience']}: {brief['key_message']}",
        "emotional_direction": brief["tone"],
        "visual_direction": "Natural daylight, clean interiors, steady camera, white space; "
                            "brand red only in graphics.",
        "beats": beats,
        "characters": [
            {"id": "c1", "name": "Asha", "role": "mother",
             "appearance": "Woman in her late thirties, shoulder-length black hair, warm smile",
             "wardrobe": "Soft grey cardigan over a white top"},
            {"id": "c2", "name": "Ravi", "role": "father",
             "appearance": "Man in his early forties, short black hair, trimmed beard",
             "wardrobe": "Light blue shirt, sleeves rolled"},
        ],
        "locations": [{"id": "loc1", "name": "Family kitchen",
                       "description": "Bright kitchen, white tiles, morning light"}],
        "objective_beat_id": "b3",
        "cta_beat_id": beats[-1]["id"],
    }


def _sim_script(h: dict[str, Any]) -> dict:
    brief, plan = h["brief"], h["creative"]
    beats = plan["beats"]
    lines, supers = [], []
    texts = {
        0: "Every morning starts with the people we love most.",
        1: "But are we ready if their health needs us tomorrow?",
        2: f"{brief['product']} is designed around your whole family.",
    }
    claim_index = 0
    for index, beat in enumerate(beats):
        if beat["id"] == plan["cta_beat_id"]:
            lines.append({"id": f"l{index + 1}", "beat_id": beat["id"], "kind": "VOICEOVER",
                          "speaker": "Narrator", "text": f"{brief['cta']}.".replace("..", "."),
                          "fact_ids": []})
            supers.append({"id": "sup_cta", "beat_id": beat["id"], "text": brief["cta"],
                           "purpose": "call to action"})
            continue
        if beat["name"] == "Why it helps" and brief.get("claims"):
            claim = brief["claims"][claim_index]
            lines.append({"id": f"l{index + 1}", "beat_id": beat["id"], "kind": "VOICEOVER",
                          "speaker": "Narrator", "text": claim["text"],
                          "fact_ids": [claim["fact_id"]] if claim.get("fact_id") else []})
            continue
        lines.append({"id": f"l{index + 1}", "beat_id": beat["id"], "kind": "VOICEOVER",
                      "speaker": "Narrator", "text": texts.get(index, brief["key_message"]),
                      "fact_ids": []})
    cta_line = next(x for x in lines if x["beat_id"] == plan["cta_beat_id"])
    return {"lines": lines, "supers": supers, "cta_line_id": cta_line["id"],
            "disclosure_texts": [d["text"] for d in brief.get("disclosures", [])]}


def _sim_scene_design(h: dict[str, Any]) -> dict:
    plan = h["creative"]
    loc = plan["locations"][0]["id"]
    chars = [c["id"] for c in plan["characters"]]
    beats = {b["id"]: b for b in plan["beats"]}
    return {"scenes": [
        {
            "scene_key": s["scene_key"],
            "visual_description": f"{beats[s['beat_id']]['name']}: the family in the kitchen, "
                                  f"{beats[s['beat_id']]['emotion']} in their expressions.",
            "camera_direction": "Medium shot, slow push in, eye level",
            "lighting": "Soft natural morning light from a window",
            "character_ids": chars,
            "location_id": loc,
            "validation_criteria": [
                "Both characters are recognisable and consistent with earlier scenes",
                "No text, logos or signage appear in the frame",
            ],
        }
        for s in h["skeletons"]
    ]}


_DEFECT_FINDINGS = {
    "VISUAL_MINOR": {"aspect": "visual_quality", "severity": "MINOR", "correctable_by_edit": True,
                     "message": "Colour grade is slightly cool against the warm brief",
                     "frame_timestamps": [1.0],
                     "edit_instruction": "Warm the colour grade slightly; keep everything else"},
    "VISUAL_MAJOR": {"aspect": "continuity", "severity": "MAJOR", "correctable_by_edit": False,
                     "message": "The mother's appearance differs from the accepted scenes",
                     "frame_timestamps": [0.5, 2.0], "edit_instruction": None},
    "PERSISTENT": {"aspect": "prompt_adherence", "severity": "MAJOR",
                   "correctable_by_edit": False,
                   "message": "The action described in the prompt does not happen",
                   "frame_timestamps": [1.5], "edit_instruction": None},
    "BRAND_TEXT": {"aspect": "text", "severity": "MAJOR", "correctable_by_edit": True,
                   "message": "Garbled signage text appears in the background",
                   "frame_timestamps": [2.0],
                   "edit_instruction": "Remove the signage text in the background"},
}


def _sim_scene_inspection(h: dict[str, Any]) -> dict:
    defect = h.get("defect")
    if defect in _DEFECT_FINDINGS:
        return {"summary": f"Simulated review: {defect}", "findings": [_DEFECT_FINDINGS[defect]]}
    return {"summary": "Simulated review: no visual issues", "findings": []}


def _sim_transcribe(h: dict[str, Any]) -> str:
    return h.get("transcript") or "NO_SPEECH"


def _sim_final_review(h: dict[str, Any]) -> dict:
    findings = []
    for defect in h.get("defects", []):
        if defect == "LOGO_MISSING":
            findings.append({"aspect": "logo", "severity": "MAJOR", "correctable_by_edit": True,
                             "message": "The logo is not visible on the end card",
                             "frame_timestamps": [], "edit_instruction": None})
    return {"summary": "Simulated final review", "findings": findings}


_SIMULATORS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "intake": _sim_intake,
    "creative": _sim_creative,
    "script": _sim_script,
    "scene_design": _sim_scene_design,
    "scene_inspection": _sim_scene_inspection,
    "transcribe": _sim_transcribe,
    "final_review": _sim_final_review,
}
