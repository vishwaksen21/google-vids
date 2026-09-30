"""Validation of what a person writes in a brief."""

import pydantic
import pytest

from vidsprod.domain.documents import BriefInput


def test_a_written_scene_is_one_clip_long_and_its_photo_must_be_uploaded():
    ok = BriefInput.model_validate({"raw_brief": "b", "reference_asset_ids": ["p1"],
                                    "scenes": [{"prompt": "x", "duration_seconds": 10, "photo_asset_id": "p1"}]})
    assert ok.scenes[0].duration_seconds == 10
    for bad in ({"prompt": "x", "duration_seconds": 11}, {"prompt": "x", "duration_seconds": 2},
                {"prompt": "x", "photo_asset_id": "not-uploaded"}):
        with pytest.raises(pydantic.ValidationError):
            BriefInput.model_validate({"raw_brief": "b", "scenes": [bad]})
