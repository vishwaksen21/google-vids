"""Stages INTAKE → CREATIVE → SCRIPT → SCENE_PLAN → PROMPTS, and the approval gates."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from vidsprod.agents.planning import (
    CreativeDirectorAgent,
    IntakeAgent,
    PromptEngineerAgent,
    ScenePlannerAgent,
    ScriptWriterAgent,
)
from vidsprod.db.models import Scene, SceneVersion
from vidsprod.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    RuleCategory,
    SceneStatus,
    Severity,
    StageId,
)
from vidsprod.domain.validation import failed, passed
from vidsprod.planning import authored
from vidsprod.workflow.context import ApprovalSpec, StageContext, StageOutcome, content_hash


def _profile_dict(profile) -> dict[str, Any]:
    return {"name": profile.name, "tone": profile.tone, "regulated": profile.regulated,
            "approved_facts": profile.approved_facts,
            "standard_disclosures": profile.standard_disclosures,
            "forbidden_terms": profile.forbidden_terms, "logo_position": profile.logo_position}


class IntakeStage:
    stage = StageId.INTAKE
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        profile = _profile_dict(await ctx.brand_profile())
        result = await IntakeAgent(ctx.s.ai, ctx.settings).run(
            brief_input=await ctx.brief_input(), profile=profile, guidelines=ctx.s.brand,
            previous=await ctx.previous_failures(), job_id=ctx.job_id)
        row = await ctx.save_doc("CAMPAIGN_BRIEF", result.document)
        return StageOutcome.done(result.results, {"document": "CAMPAIGN_BRIEF", "version": row.version})


class CreativeStage:
    stage = StageId.CREATIVE
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        brief_input = await ctx.brief_input()
        if authored.is_authored(brief_input):  # the person wrote the scenes: no AI planning
            row = await ctx.save_doc("CREATIVE_PLAN", authored.creative_plan(brief_input))
            return StageOutcome.done([passed("creative.authored", RuleCategory.CONTENT,
                                             "scenes written by you; no AI creative plan")],
                                     {"document": "CREATIVE_PLAN", "version": row.version})
        result = await CreativeDirectorAgent(ctx.s.ai, ctx.settings).run(
            brief=await ctx.doc("CAMPAIGN_BRIEF"), profile=_profile_dict(await ctx.brand_profile()),
            guidelines=ctx.s.brand, limits=ctx.s.adapter.capabilities.clip_limits(),
            previous=await ctx.previous_failures(), job_id=ctx.job_id)
        row = await ctx.save_doc("CREATIVE_PLAN", result.document)
        return StageOutcome.done(result.results, {"document": "CREATIVE_PLAN", "version": row.version})


class ScriptStage:
    stage = StageId.SCRIPT
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        brief_input = await ctx.brief_input()
        if authored.is_authored(brief_input):
            doc = authored.script(brief_input, await ctx.doc("CAMPAIGN_BRIEF"))
            row = await ctx.save_doc("SCRIPT", doc)
            return StageOutcome.done([passed("script.authored", RuleCategory.CONTENT,
                                             f"{len(doc['supers'])} on-screen texts written by you, no voiceover")],
                                     {"document": "SCRIPT", "version": row.version})
        result = await ScriptWriterAgent(ctx.s.ai, ctx.settings).run(
            brief=await ctx.doc("CAMPAIGN_BRIEF"), plan=await ctx.doc("CREATIVE_PLAN"),
            profile=_profile_dict(await ctx.brand_profile()), guidelines=ctx.s.brand,
            registry=ctx.s.adapter.capabilities, previous=await ctx.previous_failures(),
            job_id=ctx.job_id)
        row = await ctx.save_doc("SCRIPT", result.document)
        return StageOutcome.done(result.results, {"document": "SCRIPT", "version": row.version})


class ScenePlanStage:
    stage = StageId.SCENE_PLAN
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        brief_input = await ctx.brief_input()
        if authored.is_authored(brief_input):
            doc = authored.scene_plan(brief_input)
            registry = ctx.s.adapter.capabilities
            results = authored.validate(
                brief_input, limits=registry.clip_limits(), settings=ctx.settings,
                forbidden=(await ctx.brand_profile()).forbidden_terms or [],
                aspect_ratios=registry.get("clip.generate").params.get("aspect_ratios", ["16:9", "9:16"]),
                disclosures=[d["text"] for d in (await ctx.doc("CAMPAIGN_BRIEF")).get("disclosures", [])])
            row = await ctx.save_doc("SCENE_PLAN", doc)
            return StageOutcome.done(results, {"document": "SCENE_PLAN", "version": row.version,
                                               "scene_count": len(doc["scenes"]),
                                               "total_duration_seconds": doc["total_duration_seconds"]})
        result = await ScenePlannerAgent(ctx.s.ai, ctx.settings).run(
            brief=await ctx.doc("CAMPAIGN_BRIEF"), plan=await ctx.doc("CREATIVE_PLAN"),
            script=await ctx.doc("SCRIPT"), guidelines=ctx.s.brand,
            registry=ctx.s.adapter.capabilities, previous=await ctx.previous_failures(),
            job_id=ctx.job_id)
        row = await ctx.save_doc("SCENE_PLAN", result.document)
        return StageOutcome.done(result.results, {
            "document": "SCENE_PLAN", "version": row.version,
            "scene_count": len(result.document["scenes"]),
            "total_duration_seconds": result.document["total_duration_seconds"],
        })


class PromptsStage:
    stage = StageId.PROMPTS
    auto_retry = True

    async def run(self, ctx: StageContext) -> StageOutcome:
        scene_plan = await ctx.doc("SCENE_PLAN")
        brief_input = await ctx.brief_input()
        if authored.is_authored(brief_input):  # each prompt exactly as the person wrote it
            document = authored.prompts(brief_input)
            results = [passed("prompts.authored", RuleCategory.CONTENT,
                              f"{len(document['prompts'])} prompts, word for word as written")]
        else:
            result = PromptEngineerAgent(ctx.settings).run(
                brief=await ctx.doc("CAMPAIGN_BRIEF"), plan=await ctx.doc("CREATIVE_PLAN"),
                script=await ctx.doc("SCRIPT"), scene_plan=scene_plan, guidelines=ctx.s.brand,
                registry=ctx.s.adapter.capabilities)
            document, results = result.document, result.results
        row = await ctx.save_doc("PROMPTS", document)
        prompts = {p["scene_key"]: p for p in document["prompts"]}
        async with ctx.s.db.transaction() as session:
            for spec in scene_plan["scenes"]:
                scene = await session.scalar(select(Scene).where(
                    Scene.job_id == ctx.job_id, Scene.scene_key == spec["scene_key"]))
                if scene is None:
                    scene = Scene(job_id=ctx.job_id, scene_key=spec["scene_key"],
                                  order_index=spec["order"], status=SceneStatus.READY.value)
                    session.add(scene)
                    await session.flush()
                prompt = prompts[spec["scene_key"]]
                digest = content_hash(prompt)
                latest = await session.scalar(select(SceneVersion).where(
                    SceneVersion.scene_id == scene.id).order_by(SceneVersion.version.desc()).limit(1))
                if latest is not None and latest.prompt_hash == digest:
                    continue
                version = (await session.scalar(select(func.max(SceneVersion.version)).where(
                    SceneVersion.scene_id == scene.id)) or 0) + 1
                sv = SceneVersion(scene_id=scene.id, version=version, spec=spec, prompt=prompt,
                                  prompt_hash=digest, reason="planned" if version == 1 else "re-planned")
                session.add(sv)
                await session.flush()
                scene.current_version_id = sv.id
        return StageOutcome.done(results, {"document": "PROMPTS", "version": row.version})


# ======================================================================= approval gates
async def _planning_hashes(ctx: StageContext) -> dict[str, str]:
    hashes = {}
    for kind in ("CAMPAIGN_BRIEF", "CREATIVE_PLAN", "SCRIPT", "SCENE_PLAN", "PROMPTS"):
        row = await ctx.doc_row(kind)
        hashes[kind] = row.content_hash if row else ""
    return hashes


class PlanApprovalStage:
    stage = StageId.PLAN_APPROVAL
    auto_retry = False

    async def run(self, ctx: StageContext) -> StageOutcome:
        if not ctx.settings.approval_plan_required:
            return StageOutcome.done([passed("approval.plan", RuleCategory.OPERATIONAL,
                                             "plan approval is not required by configuration")])
        hashes = await _planning_hashes(ctx)
        decided = [a for a in await ctx.approvals() if a.kind == ApprovalKind.PLAN.value]
        if not decided:
            scene_plan = await ctx.doc("SCENE_PLAN")
            brief = await ctx.doc("CAMPAIGN_BRIEF")
            return StageOutcome.wait([ApprovalSpec(
                kind=ApprovalKind.PLAN, subject="plan",
                title="Approve the creative plan, script and storyboard",
                summary=(f"{len(scene_plan['scenes'])} scenes, "
                         f"{scene_plan['total_duration_seconds']:.0f}s. "
                         f"Open questions: {len(brief.get('open_questions', []))}. "
                         "Approving starts generation in Google Vids and spends generations."),
                payload={"hashes": hashes},
            )], "waiting for plan approval")
        last = decided[-1]
        if last.status == ApprovalStatus.REJECTED.value:
            return StageOutcome.done([failed(
                "approval.plan", RuleCategory.OPERATIONAL, Severity.BLOCKER,
                f"plan rejected by {last.decided_by}: {(last.decision or {}).get('reason', '')}",
                judge="human", fix_hint="Edit the brief and start a new production")])
        if last.payload.get("hashes") != hashes:
            return StageOutcome.done([failed(
                "approval.plan", RuleCategory.OPERATIONAL, Severity.BLOCKER,
                "the plan changed after it was approved; the approval no longer applies",
                judge="human")])
        return StageOutcome.done([passed("approval.plan", RuleCategory.OPERATIONAL,
                                         f"plan approved by {last.decided_by}", judge="human",
                                         data={"approval_id": str(last.id)})])


class ComplianceApprovalStage:
    stage = StageId.COMPLIANCE_APPROVAL
    auto_retry = False

    async def run(self, ctx: StageContext) -> StageOutcome:
        profile = await ctx.brand_profile()
        if not (ctx.settings.approval_compliance_required or profile.regulated):
            return StageOutcome.done([passed("approval.compliance", RuleCategory.BRAND_POLICY,
                                             "compliance approval not required for this brand")])
        from vidsprod.workflow.stages.production import latest_verified_export

        qa = await ctx.doc_row("QA_REPORT")
        found = await latest_verified_export(ctx)
        payload = {"qa_report_hash": qa.content_hash if qa else "",
                   "export_sha256": found[1].sha256 if found else None,
                   "export_artifact_id": str(found[1].id) if found else None}
        decided = [a for a in await ctx.approvals() if a.kind == ApprovalKind.COMPLIANCE.value]
        if not decided:
            issues = (qa.content.get("actionable_issues", []) if qa else [])
            return StageOutcome.wait([ApprovalSpec(
                kind=ApprovalKind.COMPLIANCE, subject="export",
                title="Compliance review of the final video",
                summary=(f"QA: {qa.content.get('summary') if qa else 'missing'}; "
                         f"{len(issues)} open issue(s). Review claims, disclosures and the export "
                         "before it can be delivered."
                         + (" SIMULATION: this video was produced by the mock adapter."
                            if ctx.job.simulation else "")),
                payload=payload,
            )], "waiting for compliance approval")
        last = decided[-1]
        if last.status == ApprovalStatus.REJECTED.value:
            return StageOutcome.done([failed(
                "approval.compliance", RuleCategory.BRAND_POLICY, Severity.BLOCKER,
                f"rejected by compliance ({last.decided_by}): "
                f"{(last.decision or {}).get('reason', '')}", judge="human")])
        if last.payload != payload:
            return StageOutcome.done([failed(
                "approval.compliance", RuleCategory.BRAND_POLICY, Severity.BLOCKER,
                "the export or QA report changed after approval", judge="human")])
        return StageOutcome.done([passed("approval.compliance", RuleCategory.BRAND_POLICY,
                                         f"approved by {last.decided_by}", judge="human",
                                         data={"approval_id": str(last.id)})])
