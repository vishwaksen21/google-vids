# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Composition style derived from the brand palette.

The final render burns brand-controlled colour into the video: the caption/overlay text,
the box behind it, the pad colour on letterboxed clips and the fade colour. Those were
previously hard-coded in the FFmpeg composer, which meant the brand document could say
one thing and the rendered pixels another.

`BrandStyle` derives them from `BrandGuidelines` instead, so `DESIGN.md` is the single
source of truth all the way to the encoder, and the same values can be handed to the
brand validator for a pre-render check.

Derivation follows DESIGN.md's own guidance — white text on a dark neutral derived from
the brand grey, with the brand primary available as an accent — and falls back to safe
neutrals when no palette could be parsed.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from vidsprod.brand.guidelines import BrandGuidelines

# Lightness the caption box is pushed to: dark enough for white text to clear contrast
# requirements, while keeping the brand neutral's hue.
_BOX_LIGHTNESS = 0.10
_BOX_OPACITY = 0.55

_FALLBACK_TEXT = "#FFFFFF"
_FALLBACK_BOX = "#1A1A1A"
_FALLBACK_PAD = "#FFFFFF"


def _shade(hex_value: str, lightness: float) -> str:
    """Return `hex_value` re-lit to `lightness`, keeping its hue and saturation."""
    value = hex_value.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
    nr, ng, nb = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(nr * 255):02X}{round(ng * 255):02X}{round(nb * 255):02X}"


@dataclass(frozen=True)
class BrandStyle:
    """Brand-controlled colours used by the composer, as `#RRGGBB`."""

    text_colour: str = _FALLBACK_TEXT
    box_colour: str = _FALLBACK_BOX
    box_opacity: float = _BOX_OPACITY
    pad_colour: str = _FALLBACK_PAD
    fade_colour: str = _FALLBACK_PAD
    accent_colour: str | None = None

    @classmethod
    def derive(cls, guidelines: BrandGuidelines) -> BrandStyle:
        if not guidelines.palette:
            return cls()
        neutral = guidelines.neutral
        primary = guidelines.primary
        box = _shade(neutral.normalised, _BOX_LIGHTNESS) if neutral else _FALLBACK_BOX
        # White is the brand's background colour where it exists in the palette.
        white = next(
            (t.normalised for t in guidelines.palette if t.normalised == "#FFFFFF"),
            _FALLBACK_PAD,
        )
        return cls(
            text_colour=white,
            box_colour=box,
            box_opacity=_BOX_OPACITY,
            pad_colour=white,
            fade_colour=white,
            accent_colour=primary.normalised if primary else None,
        )

    def ffmpeg_text_colour(self) -> str:
        """FFmpeg accepts `0xRRGGBB` for drawtext/pad/fade colours."""
        return "0x" + self.text_colour.lstrip("#")

    def ffmpeg_box_colour(self) -> str:
        return f"0x{self.box_colour.lstrip('#')}@{self.box_opacity:.2f}"

    def ffmpeg_pad_colour(self) -> str:
        return "0x" + self.pad_colour.lstrip("#")

    def ffmpeg_fade_colour(self) -> str:
        return "0x" + self.fade_colour.lstrip("#")

    def brand_controlled_colours(self) -> list[tuple[str, str]]:
        """`(component, hex)` pairs for the pre-render brand check."""
        pairs = [
            ("Caption text", self.text_colour),
            ("Caption box", self.box_colour),
            ("Letterbox padding", self.pad_colour),
            ("Fade colour", self.fade_colour),
        ]
        if self.accent_colour:
            pairs.append(("Accent", self.accent_colour))
        return pairs
