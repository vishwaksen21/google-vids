# Ported from phi-service-layer/media-generation/backend/app/brand (2026-09-29).
"""Parse `DESIGN.md` into `BrandGuidelines`.

The parse is **anchor-driven, not position-driven**: it splits the document on markdown
headings and then looks for stable local cues (a palette table row, a "Do NOT introduce"
line, an "Avoid:" line, a `font-family` block). Nothing depends on a section *number*,
so renumbering or reordering `DESIGN.md` does not break it.

Everything is best-effort and independently optional. A heading that has been renamed
yields an empty tuple for that facet rather than an exception, and `BrandGuidelines.
available` still reports true as long as the palette or identity survived. A missing or
unreadable document yields `BrandGuidelines.unavailable()` — the platform then degrades
to "no brand context" and says so, rather than inventing a brand.

`parse_design_document` is pure. `load_brand_guidelines` is the only function here that
touches the filesystem.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from vidsprod.brand.guidelines import BrandGuidelines, ColourToken

# `| Prudential Red | `#E71D2D` | Brand / CTA / Active |`
_TABLE_ROW = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*`?(?P<hex>#[0-9A-Fa-f]{3,6})`?\s*\|(?P<rest>.*)\|?\s*$"
)
# `- HEX: `#E71D2D``  /  `HEX: #E71D2D`
_HEX_LABEL = re.compile(r"HEX:\s*`?(?P<hex>#[0-9A-Fa-f]{3,6})`?", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-*+]\s+(?P<text>.+?)\s*$")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
_FONT_FAMILY = re.compile(r"font-family:\s*(?P<body>[^;}]+)", re.IGNORECASE)

# Leading decoration used in "what not to do" lists, e.g. "❌ Blue corporate themes".
_DECORATION = re.compile(r"^[❌✅⚠️\s]+")


def _strip_markup(text: str) -> str:
    """Remove bold/italic/code markers and list decoration from a bullet."""
    text = _DECORATION.sub("", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    return text.strip().rstrip(".").strip()


def _sections(text: str) -> dict[str, str]:
    """Map lower-cased heading title -> that section's body (excluding sub-headings' own
    titles but including their bodies, so a lookup on a parent heading still sees them).
    """
    lines = text.splitlines()
    starts: list[tuple[int, int, str]] = []  # (line index, level, title)
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            title = _strip_markup(m.group("title")).lower()
            # Drop a leading section number: "4. Color System" -> "color system"
            title = re.sub(r"^\d+[.)]?\s*", "", title)
            starts.append((i, len(m.group("hashes")), title))

    out: dict[str, str] = {}
    for idx, (line_no, level, title) in enumerate(starts):
        end = len(lines)
        for later_line, later_level, _ in starts[idx + 1 :]:
            if later_level <= level:
                end = later_line
                break
        body = "\n".join(lines[line_no + 1 : end])
        # First heading of a given name wins; later duplicates do not clobber it.
        out.setdefault(title, body)
    return out


def _find_section(sections: dict[str, str], *needles: str) -> str:
    """Return the first section whose title contains all words of any needle."""
    for needle in needles:
        wanted = needle.lower().split()
        for title, body in sections.items():
            if all(w in title for w in wanted):
                return body
    return ""


def _bullets(block: str, *, limit: int = 40) -> tuple[str, ...]:
    found: list[str] = []
    for line in block.splitlines():
        m = _BULLET.match(line)
        if not m:
            continue
        value = _strip_markup(m.group("text"))
        if value and value not in found:
            found.append(value)
        if len(found) >= limit:
            break
    return tuple(found)


# How far past a marker we will look for the start of its list. Without this bound a
# marker whose list is a fenced code block rather than bullets would keep scanning and
# absorb the next unrelated bullet list further down the document.
_LOOKAHEAD_LINES = 4


def _bullets_after(block: str, *markers: str, limit: int = 40) -> tuple[str, ...]:
    """Bullets in the run immediately following a line containing one of `markers`.

    The list must begin within `_LOOKAHEAD_LINES` of the marker, and collection stops at
    the first non-blank, non-bullet line after it has begun. Together those two bounds
    stop an adjacent — or distant — unrelated list from being absorbed.
    """
    lowered = [m.lower() for m in markers]
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if not any(m in line.lower() for m in lowered):
            continue
        found: list[str] = []
        started = False
        for offset, candidate in enumerate(lines[i + 1 :]):
            bullet = _BULLET.match(candidate)
            if bullet:
                started = True
                value = _strip_markup(bullet.group("text"))
                if value and value not in found:
                    found.append(value)
                if len(found) >= limit:
                    break
            elif not candidate.strip():
                continue  # blank lines are allowed inside a list
            elif started:
                break
            elif offset >= _LOOKAHEAD_LINES:
                break  # the marker's list is not bullets; do not drift onward
        if found:
            return tuple(found)
    return ()


def _palette(sections: dict[str, str], whole: str) -> tuple[ColourToken, ...]:
    """Palette from the allowed-palette table, falling back to `HEX:` labels."""
    tokens: list[ColourToken] = []
    seen: set = set()

    def add(name: str, hex_value: str, usage: str | None) -> None:
        try:
            token = ColourToken(name=name, hex_value=hex_value, usage=usage)
        except ValueError:
            return
        if token.normalised in seen:
            return
        seen.add(token.normalised)
        tokens.append(token)

    table_block = _find_section(sections, "allowed color palette", "allowed colour palette") or whole
    for line in table_block.splitlines():
        m = _TABLE_ROW.match(line.strip())
        if not m:
            continue
        name = _strip_markup(m.group("name"))
        if not name or name.lower() in ("name", "colour", "color"):
            continue  # header row
        usage = _strip_markup(m.group("rest").strip("|")) or None
        add(name, m.group("hex"), usage)

    if not tokens:
        # Fall back to the "HEX: `#RRGGBB`" lines in the colour-system section, naming
        # each from the nearest preceding heading.
        colour_block = _find_section(sections, "color system", "colour system") or whole
        current = "Brand colour"
        for line in colour_block.splitlines():
            heading = _HEADING.match(line)
            if heading:
                current = _strip_markup(heading.group("title"))
                continue
            m = _HEX_LABEL.search(line)
            if m:
                add(current, m.group("hex"), None)
    return tuple(tokens)


def _typography(sections: dict[str, str]) -> tuple[str, ...]:
    block = _find_section(sections, "typography")
    m = _FONT_FAMILY.search(block)
    if not m:
        return ()
    fonts = []
    for raw in m.group("body").split(","):
        name = raw.strip().strip("\"'").strip()
        if name and name not in fonts:
            fonts.append(name)
    return tuple(fonts)


def _dedupe(*groups: Iterable[str], limit: int = 40) -> tuple[str, ...]:
    out: list[str] = []
    for group in groups:
        for value in group:
            key = value.lower()
            if value and key not in {v.lower() for v in out}:
                out.append(value)
            if len(out) >= limit:
                return tuple(out)
    return tuple(out)


def parse_design_document(
    text: str,
    *,
    source_path: str | None = None,
    source_digest: str | None = None,
) -> BrandGuidelines:
    """Parse design-document markdown. Pure — no filesystem access."""
    sections = _sections(text)
    digest = source_digest or hashlib.sha256(text.encode("utf-8")).hexdigest()

    colour_block = _find_section(sections, "color system", "colour system")
    identity_block = _find_section(sections, "brand identity")
    not_to_do = _find_section(sections, "what not to do", "not to do")

    forbidden = _dedupe(
        _bullets_after(colour_block, "do not introduce unrelated", "must not use", "avoid"),
        _bullets_after(text, "do not automatically use standard chart colors"),
    )

    avoid = _dedupe(
        _bullets(not_to_do),
        _bullets_after(identity_block, "avoid"),
        _bullets_after(_find_section(sections, "illustrations"), "avoid"),
        _bullets_after(_find_section(sections, "backgrounds"), "avoid"),
        _bullets_after(_find_section(sections, "animation"), "avoid"),
    )

    return BrandGuidelines(
        source_path=source_path,
        source_digest=digest,
        palette=_palette(sections, text),
        forbidden_colours=forbidden,
        identity_values=_bullets_after(identity_block, "communicate", limit=20),
        appearance=_bullets_after(identity_block, "should feel", "appearance", limit=20),
        avoid_styles=avoid,
        typography=_typography(sections),
        logo_rules=_dedupe(
            _bullets(_find_section(sections, "logo rules")),
            limit=24,
        ),
        photography_rules=_bullets(_find_section(sections, "photography"), limit=16),
        illustration_rules=_bullets(_find_section(sections, "illustrations"), limit=16),
        design_principles=_headings_of(_find_section(sections, "design principles")),
    )


def _headings_of(block: str, *, limit: int = 16) -> tuple[str, ...]:
    """Sub-heading titles inside a block — used for the named design principles."""
    out: list[str] = []
    for line in block.splitlines():
        m = _HEADING.match(line)
        if not m:
            continue
        title = re.sub(r"^\d+[.)]?\s*", "", _strip_markup(m.group("title")))
        if title and title not in out:
            out.append(title)
        if len(out) >= limit:
            break
    return tuple(out)


def load_brand_guidelines(path: Path | None) -> BrandGuidelines:
    """Read and parse a design document. Never raises: a missing or unreadable file
    yields `BrandGuidelines.unavailable()` so the platform degrades honestly.
    """
    if path is None:
        return BrandGuidelines.unavailable()
    try:
        raw = path.read_bytes()
    except OSError:
        return BrandGuidelines.unavailable(source_path=str(path))
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return parse_design_document(text, source_path=str(path), source_digest=digest)


def describe_sequence(values: Sequence[str], *, limit: int = 8) -> str:
    """Join a capped list for prompt injection, e.g. "a, b, c"."""
    return ", ".join(values[:limit])
