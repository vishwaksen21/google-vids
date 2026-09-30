"""Media inspection with the bundled ffmpeg binary (imageio-ffmpeg).

No system ffprobe is assumed. Probing parses `ffmpeg -i` output; detection uses ffmpeg's
own analysis filters (blackdetect, freezedetect, silencedetect, volumedetect, ebur128),
and playability is a full decode to the null muxer: a file is only called playable when
every frame decodes without error.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(
    r"Stream #\S+.*?Video:\s*(?P<codec>\w+).*?,\s*(?P<w>\d{2,5})x(?P<h>\d{2,5})"
    r"(?:.*?,\s*(?P<fps>[\d.]+)\s*fps)?"
)
_AUDIO = re.compile(r"Stream #\S+.*?Audio:\s*(?P<codec>\w+).*?(?P<rate>\d+)\s*Hz")
_BLACK = re.compile(r"black_start:(?P<s>[\d.]+)\s+black_end:(?P<e>[\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*(?P<s>[\d.]+)")
_FREEZE_DUR = re.compile(r"freeze_duration:\s*(?P<d>[\d.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(?P<s>-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(?P<e>[\d.]+)\s*\|\s*silence_duration:\s*(?P<d>[\d.]+)")
_MEAN_VOL = re.compile(r"mean_volume:\s*(?P<v>-?[\d.]+|-inf) dB")
_MAX_VOL = re.compile(r"max_volume:\s*(?P<v>-?[\d.]+|-inf) dB")
_LUFS = re.compile(r"I:\s*(?P<v>-?[\d.]+|-inf)\s*LUFS")


class MediaError(Exception):
    pass


async def run_ffmpeg(*args: str, timeout: float = 300.0) -> tuple[int, str]:  # noqa: ASYNC109
    """Run ffmpeg with an argument list (never a shell string). Returns (code, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-nostdin", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError(f"ffmpeg timed out after {timeout:.0f}s") from None
    return proc.returncode or 0, err.decode("utf-8", "replace")


@dataclass
class Probe:
    path: str
    size_bytes: int
    duration_seconds: float | None
    container_ok: bool
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_codec: str | None = None
    audio_rate: int | None = None
    raw: str = ""

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def aspect_ratio(self) -> float | None:
        if self.width and self.height:
            return self.width / self.height
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("raw")
        return data


async def probe(path: Path) -> Probe:
    if not path.exists():
        raise MediaError(f"{path.name} does not exist")
    code, err = await run_ffmpeg("-i", str(path), timeout=60)
    # `ffmpeg -i` with no output always exits non-zero; the stream report is what counts.
    duration = None
    if m := _DURATION.search(err):
        duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    result = Probe(path=path.name, size_bytes=path.stat().st_size, duration_seconds=duration,
                   container_ok="Invalid data found" not in err and duration is not None, raw=err)
    if v := _VIDEO.search(err):
        result.video_codec, result.width, result.height = v["codec"], int(v["w"]), int(v["h"])
        result.fps = float(v["fps"]) if v["fps"] else None
    if a := _AUDIO.search(err):
        result.audio_codec, result.audio_rate = a["codec"], int(a["rate"])
    return result


@dataclass
class Analysis:
    decode_ok: bool
    decode_errors: list[str] = field(default_factory=list)
    black_intervals: list[tuple[float, float]] = field(default_factory=list)
    freeze_intervals: list[tuple[float, float]] = field(default_factory=list)
    silence_intervals: list[tuple[float, float]] = field(default_factory=list)
    mean_volume_db: float | None = None
    max_volume_db: float | None = None
    integrated_lufs: float | None = None

    def to_dict(self) -> dict:
        """JSON-safe: an interval running to the end of the file ends at None, not infinity
        (which JSON, and so JSONB, cannot represent)."""
        data = asdict(self)
        for key in ("black_intervals", "freeze_intervals", "silence_intervals"):
            data[key] = [[a, None if b == float("inf") else b] for a, b in data[key]]
        return data


def _db(value: str) -> float | None:
    return None if value == "-inf" else float(value)


async def analyse(
    path: Path, *, has_audio: bool, black_min_seconds: float = 0.3,
    freeze_min_seconds: float = 2.0, silence_min_seconds: float = 1.0,
) -> Analysis:
    """Decode the whole file once, running every detector on the way."""
    vf = (f"blackdetect=d={black_min_seconds}:pic_th=0.98:pix_th=0.10,"
          f"freezedetect=n=-60dB:d={freeze_min_seconds}")
    # `level+info` prefixes every line with its log level, so decode problems are read from
    # [error]/[fatal] lines only — never guessed from words in ordinary output (a file path
    # containing "truncated" once failed a perfectly good clip).
    args = ["-loglevel", "level+info", "-i", str(path), "-vf", vf]
    if has_audio:
        args += ["-af", f"silencedetect=n=-50dB:d={silence_min_seconds},volumedetect,ebur128"]
    else:
        args += ["-an"]
    args += ["-f", "null", "-"]
    code, err = await run_ffmpeg(*args, timeout=600)
    errors = [line for line in err.splitlines()
              if ("[error]" in line or "[fatal]" in line) and "Output file is empty" not in line][:20]
    result = Analysis(decode_ok=code == 0 and not errors, decode_errors=errors)
    result.black_intervals = [(float(m["s"]), float(m["e"])) for m in _BLACK.finditer(err)]
    starts = [float(m["s"]) for m in _FREEZE_START.finditer(err)]
    durs = [float(m["d"]) for m in _FREEZE_DUR.finditer(err)]
    result.freeze_intervals = [(s, s + d) for s, d in zip(starts, durs, strict=False)]
    if len(starts) > len(durs):
        # Frozen until the end of the file: ffmpeg reports no duration for the last freeze.
        result.freeze_intervals.append((starts[-1], float("inf")))
    s_starts = [max(0.0, float(m["s"])) for m in _SILENCE_START.finditer(err)]
    s_ends = [(float(m["e"])) for m in _SILENCE_END.finditer(err)]
    result.silence_intervals = [(s, e) for s, e in zip(s_starts, s_ends, strict=False)]
    if len(s_starts) > len(s_ends):  # silence running to the end of the file
        result.silence_intervals.append((s_starts[-1], float("inf")))
    if m := _MEAN_VOL.search(err):
        result.mean_volume_db = _db(m["v"])
    if m := _MAX_VOL.search(err):
        result.max_volume_db = _db(m["v"])
    lufs = list(_LUFS.finditer(err))
    if lufs:
        result.integrated_lufs = _db(lufs[-1]["v"])
    return result


async def extract_frames(path: Path, timestamps: list[float], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, ts in enumerate(timestamps):
        target = out_dir / f"frame_{index:02d}_{ts:06.2f}.jpg"
        code, err = await run_ffmpeg("-y", "-ss", f"{ts:.3f}", "-i", str(path), "-frames:v", "1",
                                     "-q:v", "3", str(target), timeout=60)
        if code == 0 and target.exists() and target.stat().st_size > 0:
            frames.append(target)
    return frames


async def extract_audio(path: Path, target: Path) -> Path | None:
    code, _ = await run_ffmpeg("-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                               str(target), timeout=120)
    return target if code == 0 and target.exists() and target.stat().st_size > 44 else None


async def average_hash(path: Path, at: float = 0.5) -> str | None:
    """64-bit average hash of one frame, as a cheap "is this the same clip" fingerprint."""
    target = path.with_suffix(f".ahash{at:.2f}.gray")
    code, _ = await run_ffmpeg("-y", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                               "-vf", "scale=8:8,format=gray", "-f", "rawvideo", str(target),
                               timeout=60)
    if code != 0 or not target.exists():
        return None
    pixels = target.read_bytes()[:64]
    target.unlink(missing_ok=True)
    if len(pixels) < 64:
        return None
    mean = sum(pixels) / 64
    bits = "".join("1" if p >= mean else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def sample_timestamps(duration: float, count: int = 4) -> list[float]:
    if duration <= 0:
        return [0.0]
    step = duration / (count + 1)
    return [round(step * (i + 1), 2) for i in range(count)]


_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def font_file() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def drawtext_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
            .replace("%", "\\%").replace(",", "\\,"))
