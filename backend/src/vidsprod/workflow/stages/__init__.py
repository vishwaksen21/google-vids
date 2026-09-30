"""The stage handler registry, in pipeline order."""

from __future__ import annotations

from vidsprod.domain.enums import StageId
from vidsprod.workflow.context import StageHandler
from vidsprod.workflow.stages.planning import (
    ComplianceApprovalStage,
    CreativeStage,
    IntakeStage,
    PlanApprovalStage,
    PromptsStage,
    ScenePlanStage,
    ScriptStage,
)
from vidsprod.workflow.stages.production import (
    DeliveryStage,
    ExportStage,
    FinalQAStage,
    PreviewStage,
    SceneProductionStage,
    TimelineAssemblyStage,
    TimelineEditsStage,
    VidsProjectStage,
    VidsSessionStage,
)


def build_handlers() -> dict[StageId, StageHandler]:
    handlers: list[StageHandler] = [
        IntakeStage(), CreativeStage(), ScriptStage(), ScenePlanStage(), PromptsStage(),
        PlanApprovalStage(), VidsSessionStage(), VidsProjectStage(), SceneProductionStage(),
        TimelineAssemblyStage(), TimelineEditsStage(), PreviewStage(), ExportStage(),
        FinalQAStage(), ComplianceApprovalStage(), DeliveryStage(),
    ]
    return {h.stage: h for h in handlers}
