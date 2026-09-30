"""Configuration safety, log redaction, media inspection on real files."""

from __future__ import annotations

import io
import json
import logging

import pytest

from tests.conftest import make_settings
from vidsprod.logging import JsonFormatter, redact
from vidsprod.media import ffmpeg


# ------------------------------------------------------------------ configuration
def test_defaults_are_the_safe_ones(tmp_path):
    s = make_settings(tmp_path, app_env="development", ai_provider="gateway")
    assert s.google_vids_live_automation_enabled is False
    assert s.gemini_direct_enabled is False
    assert s.live_actions_allowed is False


@pytest.mark.parametrize("overrides,message", [
    ({"app_env": "production", "ai_provider": "fake", "google_vids_mode": "live"}, "AI_PROVIDER=fake"),
    ({"app_env": "production", "ai_provider": "gateway", "google_vids_mode": "mock"}, "mock"),
    ({"google_vids_mode": "browser_test"}, "GOOGLE_VIDS_ALLOWED_ACCOUNT"),
    ({"google_vids_live_automation_enabled": True}, "GOOGLE_VIDS_ALLOWED_ACCOUNT"),
    ({"video_min_seconds": 90, "video_max_seconds": 60}, "VIDEO_MIN_SECONDS"),
])
def test_unsafe_combinations_are_refused_at_startup(tmp_path, overrides, message):
    with pytest.raises(ValueError, match=message):
        make_settings(tmp_path, **overrides)


def test_live_needs_both_the_mode_and_the_flag(tmp_path):
    account = {"google_vids_allowed_account": "someone@example.com"}
    assert not make_settings(tmp_path, google_vids_mode="live", **account).live_actions_allowed
    assert not make_settings(tmp_path, google_vids_mode="browser_test",
                             google_vids_live_automation_enabled=True, **account).live_actions_allowed
    assert make_settings(tmp_path, google_vids_mode="live", google_vids_live_automation_enabled=True,
                         **account).live_actions_allowed


def test_the_public_settings_view_has_no_secrets(tmp_path):
    s = make_settings(tmp_path, gateway_api_key="super-secret-key", gateway_base_url="https://g.example",
                      api_token="tok-123")
    text = json.dumps(s.public_view())
    assert "super-secret-key" not in text and "tok-123" not in text and "postgres" not in text


# ------------------------------------------------------------------ logging
def test_secrets_are_redacted_by_key_and_by_value():
    out = redact({
        "api_key": "abc", "headers": {"Authorization": "Bearer xyz", "cookie": "SID=1"},
        "msg": "calling with Bearer eyJhbGciOi.token and ?key=AIzaSy123&x=1",
        "dsn": "postgresql+asyncpg://vidsprod:hunter2@localhost/vids",
        "note": 'x-api-key: "k-999"',
    })
    text = json.dumps(out)
    for secret in ("abc", "xyz", "SID=1", "eyJhbGciOi", "AIzaSy123", "hunter2", "k-999"):
        assert secret not in text, secret


def test_log_lines_are_json_with_context():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("t.redact")
    logger.addHandler(handler)
    logger.warning("token=%s", "Bearer abc.def")
    line = json.loads(stream.getvalue())
    assert line["level"] == "WARNING" and "abc.def" not in line["msg"]


# ------------------------------------------------------------------ media
async def _clip(tmp_path, name="c.mp4", seconds=2, video="testsrc2", audio=True):
    path = tmp_path / name
    sep = ":" if "=" in video else "="
    args = ["-y", "-f", "lavfi", "-i", f"{video}{sep}s=320x180:d={seconds}:r=24"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-shortest"]
    args += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(path)]
    code, err = await ffmpeg.run_ffmpeg(*args)
    assert code == 0, err[-300:]
    return path


async def test_probe_reads_real_stream_facts(tmp_path):
    p = await ffmpeg.probe(await _clip(tmp_path))
    assert p.container_ok and p.video_codec == "h264" and (p.width, p.height) == (320, 180)
    assert abs(p.duration_seconds - 2) < 0.1 and p.has_audio


async def test_a_corrupted_file_does_not_pass_as_playable(tmp_path):
    good = await _clip(tmp_path, seconds=3)
    data = bytearray(good.read_bytes())
    for i in range(len(data) // 3, len(data) // 3 + 4000):
        data[i] = 0
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(bytes(data))
    assert (await ffmpeg.analyse(good, has_audio=True)).decode_ok
    assert not (await ffmpeg.analyse(bad, has_audio=True)).decode_ok


async def test_a_file_named_truncated_is_not_judged_by_its_name(tmp_path):
    clip = await _clip(tmp_path, name="truncated_invalid_missing_error.mp4")
    assert (await ffmpeg.analyse(clip, has_audio=True)).decode_ok


async def test_black_and_frozen_pictures_and_silence_are_detected(tmp_path):
    black = await _clip(tmp_path, "black.mp4", seconds=3, video="color=c=black")
    frozen = await _clip(tmp_path, "frozen.mp4", seconds=4, video="color=c=gray")
    silent = await _clip(tmp_path, "silent.mp4", seconds=3, audio=False)
    assert (await ffmpeg.analyse(black, has_audio=True)).black_intervals
    frozen_analysis = await ffmpeg.analyse(frozen, has_audio=True)
    assert frozen_analysis.freeze_intervals, "a picture frozen to the end is still a freeze"
    json.dumps(frozen_analysis.to_dict(), allow_nan=False)  # storable as JSONB
    moving = await ffmpeg.analyse(await _clip(tmp_path, "moving.mp4", seconds=4), has_audio=True)
    assert not moving.freeze_intervals and not moving.black_intervals
    assert not (await ffmpeg.probe(silent)).has_audio


def test_sample_timestamps_stay_inside_the_clip():
    stamps = ffmpeg.sample_timestamps(8.0, 4)
    assert stamps == sorted(stamps) and 0 < stamps[0] and stamps[-1] < 8.0
