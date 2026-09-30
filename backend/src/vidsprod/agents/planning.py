"""Planning agents: campaign intake, creative director, script writer, scene planner and
prompt engineer.

Each agent: builds a prompt from typed inputs → asks the AI service for a typed document →
overwrites any field that must not be model-authored (output requirements, disclosures,
approved claims, durations, allocation) with the deterministic value → runs deterministic
rules. The model proposes; the rules decide whether a stage may pass.
"""

from __future__ import annotations

from typing import Any

from vidsprod.agents.common import (
    AgentResult,
    check_claims,
    check_forbidden,
    compact_json,
    facts_by_id,
    feedback_block,
    is_claim_like,
)
from vidsprod.brand.alignment import BrandAlignmentChecker
from vidsprod.brand.guidelines import BrandGuidelines
from vidsprod.brand.projections import BrandStage, project
from vidsprod.config import Settings
from vidsprod.domain.documents import (
    BriefInput,
    CampaignBrief,
    CreativePlan,
    SceneDesigns,
    ScenePlan,
    Script,
    VidsPrompt,
)
from vidsprod.domain.enums import (
    AspectRatio,
    RuleCategory,
    ScriptLineKind,
    Severity,
    TransitionKind,
)
from vidsprod.domain.validation import RuleResult, failed, passed
from vidsprod.llm.service import AIService
from vidsprod.llm.types import LLMRequest, ModelRole
from vidsprod.planning.dialogue import (
    SpeechRate,
    check_allocation,
    check_dialogue_fits,
    check_vocalisations,
    is_disembodied,
    missing_from_scenes,
    normalise_line,
    speech_seconds,
    words,
)
from vidsprod.planning.durations import SkeletonPlan, build_skeletons
from vidsprod.vids.capabilities import CapabilityRegistry

C = RuleCategory
_checker = BrandAlignmentChecker()


def speech_rate(settings: Settings) -> SpeechRate:
    return SpeechRate(settings.speech_words_per_second, settings.speech_dialogue_share,
                      settings.speech_min_fill_share)


def duration_bounds(settings: Settings, brief: dict[str, Any]) -> tuple[float, float]:
    out = brief["output"]
    low = out.get("min_duration_seconds") or settings.video_min_seconds
    high = out.get("max_duration_seconds") or settings.video_max_seconds
    return float(max(low, settings.video_min_seconds)), float(min(high, settings.video_max_seconds))


def _brand_findings(texts: list[str], guidelines: BrandGuidelines, stage: BrandStage,
                    rule_id: str, subject: str) -> list[RuleResult]:
    projection = project(guidelines, stage)
    findings = []
    for text in texts:
        findings += _checker.check_text(text, projection)
    if not findings:
        return [passed(rule_id, C.BRAND_POLICY, f"{subject}: consistent with the brand system",
                       subject=subject)]
    return [failed(rule_id, C.BRAND_POLICY, Severity.MINOR if f.severity.value != "INFO" else Severity.INFO,
                   f.message, subject=subject, fix_hint=f.recommendation or None)
            for f in findings]


# ======================================================================= 1. intake
class IntakeAgent:
    def __init__(self, ai: AIService, settings: Settings) -> None:
        self.ai, self.settings = ai, settings

    async def run(self, *, brief_input: dict[str, Any], profile: dict[str, Any],
                  guidelines: BrandGuidelines, previous: list[dict], job_id: Any) -> AgentResult:
        raw = BriefInput.model_validate(brief_input)
        projection = project(guidelines, BrandStage.STORYLINE, brand_name=profile["name"],
                             brand_tone=profile.get("tone"))
        prompt = (
            "Turn this campaign brief into a structured brief for a short brand video. Use the "
            "structured fields where given; otherwise extract them from the free text. Never "
            "invent product claims: claims come only from the brief's required claims. Record "
            "anything ambiguous as an open question rather than guessing.\n\n"
            f"BRIEF:\n{compact_json(raw.model_dump(mode='json'))}\n\n"
            f"APPROVED FACTS (the only claims that may be made):\n"
            f"{compact_json(profile.get('approved_facts', []))}\n\n{projection.as_prompt_block()}"
            + feedback_block(previous)
        )
        reply = await self.ai.generate(LLMRequest(
            purpose="intake", role=ModelRole.REASONING, prompt=prompt,
            system="You are the campaign intake analyst for a regulated insurer.",
            response_model=CampaignBrief, job_id=job_id,
            simulation_hints={"brief": raw.model_dump(mode="json"),
                              "brand_tone": profile.get("tone")},
        ))
        brief = reply.parsed.model_dump(mode="json")
        # Deterministic fields: the model does not get to change what the requester fixed.
        brief["output"] = raw.output.model_dump(mode="json")
        brief["claims"] = [c.model_dump(mode="json") for c in raw.required_claims]
        disclosures = [d.model_dump(mode="json") for d in raw.disclosures]
        if profile.get("regulated"):
            present = {normalise_line(d["text"]) for d in disclosures}
            for standard in profile.get("standard_disclosures", []):
                if normalise_line(standard) not in present:
                    disclosures.append({"text": standard, "on_screen": True, "spoken": False})
        brief["disclosures"] = disclosures
        brief["reference_asset_ids"] = list(raw.reference_asset_ids)
        return AgentResult(brief, self.validate(brief, raw, profile))

    def validate(self, brief: dict, raw: BriefInput, profile: dict) -> list[RuleResult]:
        facts = facts_by_id(profile.get("approved_facts", []))
        results: list[RuleResult] = [passed("intake.schema", C.CONTENT,
                                            "brief has every required field")]
        for index, claim in enumerate(brief["claims"], 1):
            subject = f"claim {index}"
            if not claim.get("fact_id"):
                results.append(failed(
                    "intake.claims.fact_required", C.BRAND_POLICY, Severity.BLOCKER,
                    f'{subject} ("{claim["text"][:60]}") does not cite an approved fact',
                    subject=subject,
                    fix_hint="Every insurance claim must reference an approved fact id",
                ))
                continue
            results += check_claims(rule_prefix="intake", subject=subject, text=claim["text"],
                                    fact_ids=[claim["fact_id"]], facts=facts)
        if not any(r.failed for r in results if r.rule_id.startswith("intake.claims")):
            results.append(passed("intake.claims", C.BRAND_POLICY,
                                  f"{len(brief['claims'])} claim(s), all backed by approved facts"))
        results.append(check_forbidden(
            rule_id="intake.forbidden_terms", subject="brief",
            texts=[brief["key_message"], brief["cta"], brief["objective"]],
            forbidden=profile.get("forbidden_terms", []),
        ))
        low, high = raw.output.min_duration_seconds, raw.output.max_duration_seconds
        if high is not None and high < self.settings.video_min_seconds:
            results.append(failed("intake.duration", C.TECHNICAL, Severity.BLOCKER,
                                  f"the brief allows at most {high:.0f}s, below the platform "
                                  f"minimum of {self.settings.video_min_seconds:.0f}s"))
        else:
            results.append(passed("intake.duration", C.TECHNICAL,
                                  f"duration bounds: {low or 'any'}–{high or 'any'} seconds"))
        if brief.get("open_questions"):
            results.append(failed("intake.open_questions", C.CONTENT, Severity.MINOR,
                                  "open questions: " + "; ".join(brief["open_questions"])[:500],
                                  fix_hint="A reviewer should settle these at plan approval"))
        return results


# ======================================================================= 2. creative
class CreativeDirectorAgent:
    def __init__(self, ai: AIService, settings: Settings) -> None:
        self.ai, self.settings = ai, settings

    async def run(self, *, brief: dict, profile: dict, guidelines: BrandGuidelines,
                  limits: Any, previous: list[dict], job_id: Any) -> AgentResult:
        low, high = duration_bounds(self.settings, brief)
        max_beats = max(2, int(high // limits.min_seconds))
        projection = project(guidelines, BrandStage.STORYLINE, brand_name=profile["name"],
                             brand_tone=brief["tone"])
        prompt = (
            "Develop the creative for a short brand video from this brief. Produce a concept, "
            f"a narrative of 2 to {min(max_beats, 8)} beats in story order (the film runs "
            f"{low:.0f}–{high:.0f} seconds), the emotional and visual direction, and a small "
            "cast and set of locations described precisely enough to look identical in every "
            "scene (age, build, hair, face; exact wardrobe). Mark which beat delivers the "
            "objective and which carries the call to action. Nothing in the picture may be "
            "text, signage or a logo: branding is added afterwards from the real asset.\n\n"
            f"BRIEF:\n{compact_json(brief)}\n\n{projection.as_prompt_block()}"
            + feedback_block(previous)
        )
        reply = await self.ai.generate(LLMRequest(
            purpose="creative", role=ModelRole.CREATIVE, prompt=prompt,
            system="You are the creative director of an insurance brand's in-house studio.",
            response_model=CreativePlan, temperature=0.7, job_id=job_id,
            simulation_hints={"brief": brief},
        ))
        plan = reply.parsed.model_dump(mode="json")
        return AgentResult(plan, self.validate(plan, brief, profile, guidelines, max_beats))

    def validate(self, plan: dict, brief: dict, profile: dict, guidelines: BrandGuidelines,
                 max_beats: int) -> list[RuleResult]:
        results: list[RuleResult] = []
        beat_ids = [b["id"] for b in plan["beats"]]
        for label, ids in (("beat", beat_ids), ("character", [c["id"] for c in plan["characters"]]),
                           ("location", [x["id"] for x in plan["locations"]])):
            if len(ids) != len(set(ids)):
                results.append(failed(f"creative.unique_{label}_ids", C.CONTENT, Severity.MAJOR,
                                      f"{label} ids repeat", fix_hint=f"Give every {label} a unique id"))
        for key in ("objective_beat_id", "cta_beat_id"):
            if plan[key] not in beat_ids:
                results.append(failed(f"creative.{key}", C.CONTENT, Severity.MAJOR,
                                      f"{key} names a beat that does not exist",
                                      fix_hint=f"{key} must be one of the beat ids"))
        if plan["cta_beat_id"] in beat_ids and beat_ids.index(plan["cta_beat_id"]) != len(beat_ids) - 1:
            results.append(failed("creative.cta_last", C.CONTENT, Severity.MINOR,
                                  "the call to action is not the final beat",
                                  fix_hint="End the film on the call-to-action beat"))
        if len(beat_ids) > max_beats:
            results.append(failed("creative.beats_fit", C.CONTENT, Severity.MAJOR,
                                  f"{len(beat_ids)} beats cannot fit the maximum duration",
                                  fix_hint=f"Use at most {max_beats} beats"))
        if not any(r.failed for r in results):
            results.append(passed("creative.structure", C.CONTENT,
                                  f"{len(beat_ids)} beats; objective and CTA beats present"))
        results.append(check_forbidden(rule_id="creative.forbidden_terms", subject="concept",
                                       texts=[plan["concept"], plan["logline"], plan["title"]],
                                       forbidden=profile.get("forbidden_terms", [])))
        results += _brand_findings([plan["visual_direction"], plan["concept"]], guidelines,
                                   BrandStage.STORYLINE, "creative.brand_alignment", "creative plan")
        return results


# ======================================================================= 3. script
class ScriptWriterAgent:
    def __init__(self, ai: AIService, settings: Settings) -> None:
        self.ai, self.settings = ai, settings

    async def run(self, *, brief: dict, plan: dict, profile: dict, guidelines: BrandGuidelines,
                  registry: CapabilityRegistry, previous: list[dict], job_id: Any) -> AgentResult:
        rate = speech_rate(self.settings)
        low, high = duration_bounds(self.settings, brief)
        target = min(high, max(low, len(plan["beats"]) * 5.0))
        budget = int(target * rate.dialogue_share * rate.words_per_second)
        dialogue_ok = registry.is_verified("clip.generates_speech")
        projection = project(guidelines, BrandStage.STORYLINE, brand_name=profile["name"],
                             brand_tone=brief["tone"])
        prompt = (
            "Write the script for this film: voiceover lines (speaker 'Narrator') for the "
            "beats, on-screen supers where useful, and one call-to-action line in the CTA beat. "
            f"The whole script is about {int(budget * 0.8)}–{budget} spoken words in total "
            f"(speech runs at {rate.words_per_second} words per second). "
            + ("Characters may speak short DIALOGUE lines. " if dialogue_ok else
               "Use VOICEOVER only; characters do not speak on camera. ")
            + "Any figure or product fact must be one of the approved facts, word for word, "
            "and the line must list that fact's id in fact_ids. No promises, guarantees or "
            "superlatives. Disclosures are added separately; do not write them.\n\n"
            f"BRIEF:\n{compact_json(brief)}\n\nCREATIVE PLAN:\n{compact_json(plan)}\n\n"
            f"APPROVED FACTS:\n{compact_json(profile.get('approved_facts', []))}\n\n"
            f"{projection.as_prompt_block()}" + feedback_block(previous)
        )
        reply = await self.ai.generate(LLMRequest(
            purpose="script", role=ModelRole.CREATIVE, prompt=prompt,
            system="You are a copywriter for a regulated insurer. Every word is reviewed by compliance.",
            response_model=Script, temperature=0.6, job_id=job_id, max_output_tokens=4096,
            simulation_hints={"brief": brief, "creative": plan},
        ))
        script = reply.parsed.model_dump(mode="json")
        script["disclosure_texts"] = [d["text"] for d in brief["disclosures"]]  # verbatim, always
        skeletons = build_skeletons(
            beats=plan["beats"], lines=script["lines"], supers=script["supers"],
            limits=registry.clip_limits(), min_total=low, max_total=high, rate=rate,
            reading_wps=self.settings.reading_words_per_second,
            min_super_seconds=self.settings.min_super_seconds,
        )
        script["estimated_speech_seconds"] = round(speech_seconds(script["lines"], rate), 1)
        return AgentResult(script, self.validate(script, brief, plan, profile, dialogue_ok, skeletons))

    def validate(self, script: dict, brief: dict, plan: dict, profile: dict, dialogue_ok: bool,
                 skeletons: SkeletonPlan) -> list[RuleResult]:
        results: list[RuleResult] = []
        facts = facts_by_id(profile.get("approved_facts", []))
        beat_ids = {b["id"] for b in plan["beats"]}
        characters = {c["name"].lower() for c in plan["characters"]}
        ids = [x["id"] for x in script["lines"]] + [s["id"] for s in script["supers"]]
        if len(ids) != len(set(ids)):
            results.append(failed("script.unique_ids", C.CONTENT, Severity.MAJOR,
                                  "line and super ids repeat", fix_hint="Use unique ids"))
        for line in script["lines"]:
            subject = f"line {line['id']}"
            if line["beat_id"] not in beat_ids:
                results.append(failed("script.beat_ref", C.CONTENT, Severity.MAJOR,
                                      f"{subject} belongs to unknown beat {line['beat_id']}",
                                      subject=subject, fix_hint="Use the creative plan's beat ids"))
            if line["kind"] == ScriptLineKind.VOICEOVER.value and not is_disembodied(line["speaker"]):
                results.append(failed("script.voiceover_speaker", C.CONTENT, Severity.MAJOR,
                                      f"{subject} is voiceover but spoken by {line['speaker']}",
                                      subject=subject, fix_hint="Voiceover lines are spoken by 'Narrator'"))
            if line["kind"] == ScriptLineKind.DIALOGUE.value:
                if not dialogue_ok:
                    results.append(failed(
                        "script.dialogue_unverified", C.OPERATIONAL, Severity.MAJOR,
                        f"{subject} is on-camera dialogue, but speech in generated clips is not "
                        "a verified Vids capability", subject=subject,
                        fix_hint="Write every line as Narrator voiceover"))
                elif line["speaker"].lower() not in characters:
                    results.append(failed("script.dialogue_speaker", C.CONTENT, Severity.MAJOR,
                                          f"{subject} is spoken by {line['speaker']}, who is not in the cast",
                                          subject=subject))
            results += check_claims(rule_prefix="script", subject=subject, text=line["text"],
                                    fact_ids=line["fact_ids"], facts=facts)
        allowed_super_texts = {normalise_line(f["text"]).lower() for f in facts.values()}
        allowed_super_texts.add(normalise_line(brief["cta"]).lower())
        for sup in script["supers"]:
            if (is_claim_like(sup["text"])
                    and normalise_line(sup["text"]).lower() not in allowed_super_texts):
                results.append(failed(
                    "script.claims.unsupported_super", C.BRAND_POLICY, Severity.MAJOR,
                    f'super {sup["id"]} reads as a claim but is not an approved fact: '
                    f'"{sup["text"][:80]}"', subject=f"super {sup['id']}",
                    fix_hint="On-screen claims must be an approved fact word for word"))
        cta = next((x for x in script["lines"] if x["id"] == script["cta_line_id"]), None)
        cta_words = {w for w in words(brief["cta"]) if len(w) > 2}
        if cta is None:
            results.append(failed("script.cta", C.CONTENT, Severity.MAJOR,
                                  "cta_line_id does not name a line",
                                  fix_hint="Point cta_line_id at the call-to-action line"))
        else:
            said = set(words(cta["text"]))  # word tokens: "today." matches "today"
            overlap = len(cta_words & said) / len(cta_words) if cta_words else 1.0
            if cta["beat_id"] != plan["cta_beat_id"] or overlap < 0.6:
                results.append(failed("script.cta", C.CONTENT, Severity.MAJOR,
                                      "the call-to-action line does not deliver the brief's CTA "
                                      "in the CTA beat",
                                      fix_hint=f'The CTA beat must say: "{brief["cta"]}"'))
            else:
                results.append(passed("script.cta", C.CONTENT, "the CTA is delivered in its beat"))
        results.append(check_forbidden(
            rule_id="script.forbidden_terms", subject="script",
            texts=[x["text"] for x in script["lines"]] + [s["text"] for s in script["supers"]],
            forbidden=profile.get("forbidden_terms", [])))
        required = [normalise_line(d["text"]) for d in brief["disclosures"]]
        present = [normalise_line(t) for t in script["disclosure_texts"]]
        if required != present:
            results.append(failed("script.disclosures", C.BRAND_POLICY, Severity.BLOCKER,
                                  "the script's disclosures differ from the approved disclosures"))
        else:
            results.append(passed("script.disclosures", C.BRAND_POLICY,
                                  f"{len(required)} disclosure(s) carried verbatim"))
        if skeletons.problems:
            for problem in skeletons.problems:
                results.append(failed("script.fits_duration", C.TECHNICAL, Severity.MAJOR,
                                      problem, fix_hint=problem))
        else:
            results.append(passed("script.fits_duration", C.TECHNICAL,
                                  f"fits {len(skeletons.scenes)} scenes, {skeletons.total_seconds:.0f}s"))
        if not any(r.failed and r.rule_id.startswith("script.claims") for r in results):
            results.append(passed("script.claims", C.BRAND_POLICY,
                                  "every claim cites an approved fact"))
        return results


# ======================================================================= 4. scene planner
class ScenePlannerAgent:
    def __init__(self, ai: AIService, settings: Settings) -> None:
        self.ai, self.settings = ai, settings

    async def run(self, *, brief: dict, plan: dict, script: dict, guidelines: BrandGuidelines,
                  registry: CapabilityRegistry, previous: list[dict], job_id: Any) -> AgentResult:
        rate = speech_rate(self.settings)
        low, high = duration_bounds(self.settings, brief)
        limits = registry.clip_limits()
        exact = (brief["output"].get("min_duration_seconds")
                 if brief["output"].get("min_duration_seconds") == brief["output"].get("max_duration_seconds")
                 else None)
        skeleton = build_skeletons(
            beats=plan["beats"], lines=script["lines"], supers=script["supers"], limits=limits,
            min_total=low, max_total=high, rate=rate,
            reading_wps=self.settings.reading_words_per_second,
            min_super_seconds=self.settings.min_super_seconds, exact_total=exact,
        )
        skeletons = [
            {"scene_key": s.scene_key, "beat_id": s.beat_id, "duration_seconds": s.duration_seconds,
             "line_ids": s.line_ids, "super_ids": s.super_ids, "reason": s.reason}
            for s in skeleton.scenes
        ]
        projection = project(guidelines, BrandStage.SCENE_BREAKDOWN, brand_name=brief["product"])
        lines = {x["id"]: x for x in script["lines"]}
        prompt = (
            "Design the picture for each scene below. The scene list, order and durations are "
            "fixed; for each scene key describe what the camera sees (one or two actions, no "
            "more), the camera direction, the lighting, which characters appear (by id) and the "
            "location (by id). Describe only what is seen: no dialogue, no narration, no text, "
            "captions, signage or logos in the frame. List 2–5 concrete validation criteria "
            "per scene that a reviewer could check in the generated clip.\n\n"
            f"SCENES:\n{compact_json([{**s, 'voiceover': [lines[i]['text'] for i in s['line_ids']]} for s in skeletons])}\n\n"
            f"CREATIVE PLAN:\n{compact_json(plan)}\n\n{projection.as_prompt_block()}"
            + feedback_block(previous)
        )
        reply = await self.ai.generate(LLMRequest(
            purpose="scene_design", role=ModelRole.CREATIVE, prompt=prompt,
            system="You are a director of photography planning a commercial shoot.",
            response_model=SceneDesigns, temperature=0.5, job_id=job_id, max_output_tokens=6000,
            simulation_hints={"creative": plan, "skeletons": skeletons},
        ))
        designs = {d.scene_key: d.model_dump(mode="json") for d in reply.parsed.scenes}
        scene_plan = self._merge(skeletons, designs, plan)
        return AgentResult(scene_plan, self.validate(scene_plan, skeleton, skeletons, designs,
                                                      plan, script, brief, guidelines, registry))

    def _merge(self, skeletons: list[dict], designs: dict, plan: dict) -> dict:
        beats = {b["id"]: b for b in plan["beats"]}
        scenes = []
        for index, s in enumerate(skeletons, 1):
            d = designs.get(s["scene_key"]) or {}
            scenes.append({
                "scene_key": s["scene_key"], "order": index, "beat_id": s["beat_id"],
                "narrative_purpose": beats[s["beat_id"]]["purpose"],
                "duration_seconds": s["duration_seconds"],
                "visual_description": d.get("visual_description", ""),
                "camera_direction": d.get("camera_direction", ""),
                "lighting": d.get("lighting", ""),
                "character_ids": d.get("character_ids", []),
                "location_id": d.get("location_id", ""),
                "line_ids": s["line_ids"], "super_ids": s["super_ids"],
                "transition_out": TransitionKind.CUT.value, "depends_on": [],
                "validation_criteria": d.get("validation_criteria", []) or ["matches the prompt"],
            })
        # Edit grammar (ported from media-generation timeline_service): a change of place
        # dissolves, the same place cuts, and only the film's tail fades.
        for index, scene in enumerate(scenes):
            if index == len(scenes) - 1:
                scene["transition_out"] = TransitionKind.FADE.value
            elif scenes[index + 1]["location_id"] != scene["location_id"]:
                scene["transition_out"] = TransitionKind.DISSOLVE.value
            # Continuity: the latest earlier scene sharing a character or the location.
            for earlier in reversed(scenes[:index]):
                if (set(earlier["character_ids"]) & set(scene["character_ids"])
                        or earlier["location_id"] == scene["location_id"]):
                    scene["depends_on"] = [earlier["scene_key"]]
                    break
        total = sum(s["duration_seconds"] for s in scenes)
        return {"total_duration_seconds": total, "scenes": scenes,
                "rationale": f"{len(scenes)} scenes from {len(plan['beats'])} beats; durations "
                             "sized to each scene's speech and supers within verified clip limits"}

    def validate(self, scene_plan: dict, skeleton: SkeletonPlan, skeletons: list[dict],
                 designs: dict, plan: dict, script: dict, brief: dict,
                 guidelines: BrandGuidelines, registry: CapabilityRegistry) -> list[RuleResult]:
        results: list[RuleResult] = []
        ScenePlan.model_validate(scene_plan)  # structural contract
        rate = speech_rate(self.settings)
        limits = registry.clip_limits()
        low, high = duration_bounds(self.settings, brief)
        for problem in skeleton.problems:
            results.append(failed("plan.skeleton", C.TECHNICAL, Severity.MAJOR, problem,
                                  fix_hint=problem))
        expected = [s["scene_key"] for s in skeletons]
        if sorted(designs) != sorted(expected):
            results.append(failed("plan.scene_keys", C.CONTENT, Severity.MAJOR,
                                  f"designs returned for {sorted(designs)}, expected {expected}",
                                  fix_hint="Return exactly one design per given scene key"))
        chars = {c["id"] for c in plan["characters"]}
        locs = {x["id"] for x in plan["locations"]}
        lines = {x["id"]: x for x in script["lines"]}
        for scene in scene_plan["scenes"]:
            key = scene["scene_key"]
            unknown = [c for c in scene["character_ids"] if c not in chars]
            if unknown or scene["location_id"] not in locs:
                results.append(failed("plan.references", C.CONTENT, Severity.MAJOR,
                                      f"{key} references unknown characters or location",
                                      subject=f"scene:{key}",
                                      fix_hint="Use only character and location ids from the plan"))
            problems = check_vocalisations(f"{key} visual description", scene["visual_description"])
            for p in problems:
                results.append(failed("plan.no_speech_in_visuals", C.CONTENT, Severity.MAJOR, p,
                                      subject=f"scene:{key}",
                                      fix_hint="Describe only what is seen; speech lives in the script"))
            if any(w in scene["visual_description"].lower()
                   for w in ("logo", "wordmark", "signage", "caption", "text reading", "sign that says")):
                results.append(failed("plan.no_text_in_frame", C.BRAND_POLICY, Severity.MAJOR,
                                      f"{key} asks for text or a logo in the picture",
                                      subject=f"scene:{key}",
                                      fix_hint="No text, signage or logos in the frame; branding is added later"))
            scene_lines = [lines[i] for i in scene["line_ids"]]
            for p in check_dialogue_fits(scene_lines, scene["duration_seconds"], limits.max_seconds, rate):
                results.append(failed("plan.speech_fits", C.TECHNICAL, Severity.MAJOR, p,
                                      subject=f"scene:{key}", fix_hint=p))
            if not limits.min_seconds <= scene["duration_seconds"] <= limits.max_seconds:
                results.append(failed("plan.clip_limits", C.TECHNICAL, Severity.BLOCKER,
                                      f"{key} is {scene['duration_seconds']}s, outside "
                                      f"{limits.min_seconds}-{limits.max_seconds}s",
                                      subject=f"scene:{key}"))
        # Every approved line exactly once, and in one scene only.
        all_lines = [lines[i] for s in scene_plan["scenes"] for i in s["line_ids"]]
        missing = missing_from_scenes(script["lines"], all_lines)
        dupes = []
        for scene in scene_plan["scenes"]:
            others = [lines[i] for s in scene_plan["scenes"] if s is not scene for i in s["line_ids"]]
            dupes += check_allocation(scene_lines=[lines[i] for i in scene["line_ids"]],
                                      other_scene_lines=others)
        if missing or dupes:
            results.append(failed("plan.allocation", C.CONTENT, Severity.BLOCKER,
                                  f"{len(missing)} line(s) unallocated, {len(dupes)} allocated twice"))
        else:
            results.append(passed("plan.allocation", C.CONTENT,
                                  f"all {len(script['lines'])} lines allocated exactly once"))
        total = scene_plan["total_duration_seconds"]
        if not low - 1e-6 <= total <= high + 1e-6:
            results.append(failed("plan.total_duration", C.TECHNICAL, Severity.MAJOR,
                                  f"total {total:.0f}s is outside {low:.0f}-{high:.0f}s"))
        else:
            results.append(passed("plan.total_duration", C.TECHNICAL,
                                  f"{len(scene_plan['scenes'])} scenes, {total:.0f}s total "
                                  f"(bounds {low:.0f}-{high:.0f}s)"))
        source = registry.get("clip.generate").source
        if source in ("documented", "unknown"):
            results.append(failed("plan.clip_limits_source", C.OPERATIONAL, Severity.MINOR,
                                  f"clip limits {limits.min_seconds:.0f}-{limits.max_seconds:.0f}s "
                                  "come from Google's documentation and are not yet observed",
                                  fix_hint="Verify the duration control in recon"))
        else:
            results.append(passed("plan.clip_limits_source", C.OPERATIONAL,
                                  f"clip limits are {source}"))
        results += _brand_findings([s["visual_description"] for s in scene_plan["scenes"]],
                                   guidelines, BrandStage.SCENE_BREAKDOWN, "plan.brand_alignment",
                                   "scene plan")
        return results


# ======================================================================= 5. prompt engineer
_NO_TEXT = ("Do not show any text, captions, subtitles, signage, logos, wordmarks or brand "
            "marks anywhere in the picture.")
_NO_SPEECH = ("Nobody speaks on camera: no dialogue, no narration, no singing, no lip movement "
              "as if talking. Natural ambient sound only.")


class PromptEngineerAgent:
    """Deterministic: the prompt is composed from the plan, so continuity text is carried
    verbatim into every scene rather than paraphrased by a model."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, *, brief: dict, plan: dict, script: dict, scene_plan: dict,
            guidelines: BrandGuidelines, registry: CapabilityRegistry) -> AgentResult:
        speech_ok = registry.is_verified("clip.generates_speech")
        chars = {c["id"]: c for c in plan["characters"]}
        locs = {x["id"]: x for x in plan["locations"]}
        beats = {b["id"]: b for b in plan["beats"]}
        lines = {x["id"]: x for x in script["lines"]}
        brand_block = project(guidelines, BrandStage.SCENE_GENERATION).as_prompt_block()
        aspect = AspectRatio(brief["output"]["aspect_ratio"])
        prompts = []
        for scene in scene_plan["scenes"]:
            loc = locs.get(scene["location_id"], {"name": "", "description": ""})
            tokens = [loc["description"]]
            cast = []
            for cid in scene["character_ids"]:
                c = chars[cid]
                cast.append(f"- {c['name']} ({c['role']}): {c['appearance']}. Wearing {c['wardrobe']}.")
                tokens += [c["appearance"], c["wardrobe"]]
            spoken = [lines[i] for i in scene["line_ids"]
                      if lines[i]["kind"] == ScriptLineKind.DIALOGUE.value]
            if speech_ok and spoken:
                audio = "Only these exact words are spoken on camera, in this order: " + " ".join(
                    f'{x["speaker"]} says "{x["text"]}".' for x in spoken)
            else:
                audio = _NO_SPEECH
            text = "\n".join(filter(None, [
                scene["visual_description"],
                f"Camera: {scene['camera_direction']}. Lighting: {scene['lighting']}.",
                f"Setting: {loc['name']}: {loc['description']}.",
                "Characters, exactly as described and identical to the other scenes:\n" + "\n".join(cast)
                if cast else "",
                f"Mood: {beats[scene['beat_id']]['emotion']}. Look: {plan['visual_direction']}",
                brand_block, audio, _NO_TEXT,
                f"{scene['duration_seconds']:g} seconds, {aspect.value}.",
            ]))
            prompts.append(VidsPrompt(
                scene_key=scene["scene_key"], prompt=text,
                duration_seconds=scene["duration_seconds"], aspect_ratio=aspect,
                continuity_tokens=[t for t in tokens if t],
                spoken_lines=[x["text"] for x in spoken] if speech_ok else [],
                reference_asset_ids=list(brief.get("reference_asset_ids", [])),
            ).model_dump(mode="json"))
        document = {"prompts": prompts}
        return AgentResult(document, self.validate(document, scene_plan, registry, aspect, brand_block))

    def validate(self, document: dict, scene_plan: dict, registry: CapabilityRegistry,
                 aspect: AspectRatio, brand_block: str = "") -> list[RuleResult]:
        results: list[RuleResult] = []
        cap = registry.get("clip.generate")
        max_chars = cap.params.get("max_prompt_chars")
        allowed = cap.params.get("aspect_ratios", ["16:9", "9:16"])
        if aspect.value not in allowed:
            results.append(failed("prompts.aspect_ratio", C.TECHNICAL, Severity.BLOCKER,
                                  f"{aspect.value} is not a supported aspect ratio ({allowed})"))
        for p in document["prompts"]:
            key = p["scene_key"]
            missing = [t for t in p["continuity_tokens"] if t not in p["prompt"]]
            if missing:
                results.append(failed("prompts.continuity", C.CONTENT, Severity.MAJOR,
                                      f"{key} lost continuity text", subject=f"scene:{key}"))
            if max_chars and len(p["prompt"]) > max_chars:
                results.append(failed("prompts.length", C.TECHNICAL, Severity.MAJOR,
                                      f"{key} prompt is {len(p['prompt'])} characters; the "
                                      f"verified limit is {max_chars}", subject=f"scene:{key}"))
            # The no-text instruction and the brand colour-scope note name the logo on
            # purpose; anything else that mentions one would ask the model to draw it.
            body = p["prompt"].replace(_NO_TEXT, "").replace(brand_block, "")
            if "logo" in body.lower():
                results.append(failed("prompts.no_logo", C.BRAND_POLICY, Severity.MAJOR,
                                      f"{key} mentions a logo outside the no-text instruction",
                                      subject=f"scene:{key}"))
        if not max_chars:
            results.append(failed("prompts.length_limit_known", C.OPERATIONAL, Severity.INFO,
                                  "the prompt box's character limit is not verified yet"))
        if not any(r.failed and r.severity in (Severity.MAJOR, Severity.BLOCKER) for r in results):
            longest = max(len(p["prompt"]) for p in document["prompts"])
            results.append(passed("prompts.composed", C.CONTENT,
                                  f"{len(document['prompts'])} prompts, continuity text verbatim, "
                                  f"longest {longest} characters"))
        return results
