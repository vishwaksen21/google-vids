"""A production from scenes the person wrote: no AI planning, their prompts and on-screen
text used word for word, each text shown for its whole scene."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.conftest import approve_pending, create_campaign, dump, job_row, run_to_rest
from vidsprod.db.models import JobDocument
from vidsprod.domain.enums import JobStatus

pytestmark = pytest.mark.db

SCENES = [
    {"prompt": "A calm sunlit family kitchen, slow push in, natural morning light.",
     "on_screen_text": "health insurance is about more than managing medical expenses.", "duration_seconds": 8},
    {"prompt": "A mother and daughter laughing in a garden, soft afternoon light.",
     "on_screen_text": "PRUHealth Edge: unlimited restoration from the second claim onwards.", "duration_seconds": 10},
]
BRIEF = {
    "raw_brief": "Two scenes for Prudential Health India, written by the producer.",
    "scenes": SCENES,
    "output": {"aspect_ratio": "16:9", "captions_required": False, "voiceover_required": False,
               "end_card_required": False},
}


async def _docs(c, job_id) -> dict[str, dict]:
    async with c.db.session() as session:
        rows = (await session.execute(select(JobDocument).where(JobDocument.job_id == job_id)
                                      .order_by(JobDocument.version))).scalars().all()
    return {r.kind: r.content for r in rows}


async def test_written_scenes_are_used_word_for_word_and_the_video_completes(container):
    c = container
    campaign = await create_campaign(c, brief=BRIEF, name="Written scenes")
    job = await c.control.start(campaign.id, actor="producer")
    await run_to_rest(c)
    docs = await _docs(c, job.id)
    assert [p["prompt"] for p in docs["PROMPTS"]["prompts"]] == [s["prompt"] for s in SCENES]
    assert [p["duration_seconds"] for p in docs["PROMPTS"]["prompts"]] == [8.0, 10.0]
    assert [s["text"] for s in docs["SCRIPT"]["supers"]] == [
        "Health insurance is about more than managing medical expenses.",  # Vids capitalises it
        "PRUHealth Edge: unlimited restoration from the second claim onwards."]
    assert docs["SCRIPT"]["lines"] == [], "no voiceover is invented"
    assert docs["CREATIVE_PLAN"]["characters"] == [], "no AI characters are invented"

    assert [a.kind for a in await approve_pending(c)] == ["PLAN"], await dump(c, job.id)
    await run_to_rest(c)
    ops = (await _docs(c, job.id))["TIMELINE_PLAN"]["operations"]
    overlays = [o for o in ops if o["kind"] == "TEXT_OVERLAY" and o["params"]["role"] == "super"]
    assert [(o["params"]["start"], o["params"]["end"]) for o in overlays] == [(0.0, 8.0), (8.0, 18.0)], \
        "each scene's text is on screen for the whole scene"
    assert not [o for o in ops if o["kind"] == "TRANSITION"], "scenes join with the default cut"

    await approve_pending(c)
    await run_to_rest(c)
    row = await job_row(c, job.id)
    assert row.status == JobStatus.COMPLETED.value, await dump(c, job.id)


async def test_a_scene_photo_is_that_scene_s_ingredient_only(container):
    c = container
    brief = {**BRIEF, "reference_asset_ids": ["photo-a"],
             "scenes": [{**SCENES[0], "photo_asset_id": "photo-a"}, SCENES[1]]}
    campaign = await create_campaign(c, brief=brief, name="Talking photo")
    job = await c.control.start(campaign.id, actor="producer")
    await run_to_rest(c)
    prompts = (await _docs(c, job.id))["PROMPTS"]["prompts"]
    assert [p["reference_asset_ids"] for p in prompts] == [["photo-a"], []]


async def test_a_last_scene_too_short_for_the_disclosure_stops_before_anything_is_generated(container):
    """The profile's standard disclosure needs 10s of reading time; it is shown on the last scene."""
    c = container
    brief = {**BRIEF, "scenes": [SCENES[0], {**SCENES[1], "duration_seconds": 5}]}
    campaign = await create_campaign(c, brief=brief, name="Too short")
    job = await c.control.start(campaign.id, actor="producer")
    await run_to_rest(c)
    row = await job_row(c, job.id)
    assert row.current_stage == "SCENE_PLAN" and row.status != JobStatus.COMPLETED.value, await dump(c, job.id)
    assert "plan.disclosure_time" in await dump(c, job.id)
    async with c.db.session() as session:
        from vidsprod.db.models import SceneTake
        assert not (await session.execute(select(SceneTake))).scalars().all(), "nothing was generated"
