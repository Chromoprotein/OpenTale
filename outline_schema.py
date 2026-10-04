"""
Structured representation of a book outline.

The outline used to be a markdown document whose headings were load-bearing:
``web_app`` regex-matched ``Chapter N: Title`` to build ``chapters.json`` and
anything that failed to match silently produced placeholder chapters. The
outline is now a JSON document with an explicit schema, so the shape of the data
no longer depends on how the model happened to format its prose.

Responsibilities of this module:

* :data:`OUTLINE_JSON_SCHEMA` -- the strict schema sent to the API as
  ``response_format``, so the model is constrained at generation time.
* :func:`normalize_outline` -- validate and coerce untrusted input (model output
  or hand-edited JSON from the editor) into a canonical outline.
* :func:`outline_to_chapters` -- project an outline onto the ``chapters.json``
  record shape the rest of the app already consumes.
* :func:`parse_legacy_outline_markdown` -- one-way migration for books created
  before this module existed, which stored the outline as markdown.

The chapter record shape (``chapter_number``, ``title``, ``prompt``) is
deliberately unchanged so chapter generation, the previous-chapter-continuity
helper, the templates, and existing books keep working untouched.
"""

import re
from typing import Any, Dict, List, Optional

__all__ = [
    "OUTLINE_JSON_SCHEMA",
    "OutlineValidationError",
    "normalize_outline",
    "outline_to_chapters",
    "outline_chapter_prompt",
    "render_outline_markdown",
    "parse_legacy_outline_markdown",
]


class OutlineValidationError(ValueError):
    """Raised when outline data cannot be normalized into a usable outline."""


# Chapter fields that hold free prose, keyed by the canonical field name.
_TEXT_FIELDS = (
    "character_developments",
    "setting",
    "tone",
)

# Canonical field name -> the labels the model is told to use. Used when reading
# back a legacy markdown outline, where the label is whatever the model wrote.
_FIELD_ALIASES = {
    "key events": "key_events",
    "key event": "key_events",
    "key events and developments": "key_events",
    "key plot points": "key_events",
    "events": "key_events",
    "character developments": "character_developments",
    "character development": "character_developments",
    "character arcs": "character_developments",
    "characters": "character_developments",
    "setting": "setting",
    "settings": "setting",
    "location": "setting",
    "locations": "setting",
    "tone": "tone",
    "mood": "tone",
}

# --------------------------------------------------------------------------
# API schema
# --------------------------------------------------------------------------

# Strict-mode schema: every object sets additionalProperties false and lists all
# of its properties in required. Optional values are modelled as nullable rather
# than omitted, because strict mode forbids optional properties.
OUTLINE_JSON_SCHEMA: Dict[str, Any] = {
    "name": "book_outline",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["acts", "chapters"],
        "properties": {
            "acts": {
                "type": "array",
                "description": (
                    "The narrative acts the chapters are grouped into. Use an "
                    "empty array if the book is a single continuous act."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["number", "title"],
                    "properties": {
                        "number": {"type": "integer"},
                        "title": {"type": "string"},
                    },
                },
            },
            "chapters": {
                "type": "array",
                "description": "Every chapter of the book, in reading order.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "number",
                        "title",
                        "title_local",
                        "act_number",
                        "key_events",
                        "character_developments",
                        "setting",
                        "tone",
                    ],
                    "properties": {
                        "number": {
                            "type": "integer",
                            "description": "Sequential position, starting at 1.",
                        },
                        "title": {"type": "string"},
                        "title_local": {
                            "type": ["string", "null"],
                            "description": (
                                "The title in the language the book is written "
                                "in, when it differs from the English title."
                            ),
                        },
                        "act_number": {
                            "type": ["integer", "null"],
                            "description": "The act this chapter belongs to.",
                        },
                        "key_events": {
                            "type": "array",
                            "description": "What happens in this chapter.",
                            "items": {"type": "string"},
                        },
                        "character_developments": {
                            "type": ["string", "null"],
                            "description": (
                                "The character moments and changes this chapter "
                                "carries."
                            ),
                        },
                        "setting": {
                            "type": ["string", "null"],
                            "description": "Where the chapter takes place.",
                        },
                        "tone": {
                            "type": ["string", "null"],
                            "description": (
                                "The emotional and narrative tone of the chapter."
                            ),
                        },
                    },
                },
            },
        },
    },
}


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def _as_text(value: Any) -> Optional[str]:
    """Return a stripped string, or None for anything empty or non-textual."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _as_int(value: Any) -> Optional[int]:
    """Best-effort integer coercion, returning None when there is no number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group())
    return None


def _normalize_act(raw: Any, position: int) -> Optional[Dict[str, Any]]:
    """Coerce one act entry, or return None if it carries no usable title."""
    if not isinstance(raw, dict):
        return None
    title = _as_text(raw.get("title"))
    if title is None:
        return None
    number = _as_int(raw.get("number"))
    return {"number": number if number is not None else position + 1, "title": title}


def _normalize_chapter(raw: Any, position: int) -> Optional[Dict[str, Any]]:
    """Coerce one chapter entry, or return None if it has neither title nor events.

    A chapter is kept when it has a title, or when it has key events to infer a
    title from. Anything less is not a chapter.
    """
    if not isinstance(raw, dict):
        return None

    title = _as_text(raw.get("title"))
    title_local = _as_text(raw.get("title_local"))

    events_raw = raw.get("key_events")
    key_events: List[str] = []
    if isinstance(events_raw, str):
        events_raw = [events_raw]
    if isinstance(events_raw, list):
        for item in events_raw:
            text = _as_text(item)
            if text and text not in key_events:
                key_events.append(text)

    if title is None and title_local is None:
        if not key_events:
            return None
        title = f"Chapter {position + 1}"

    chapter: Dict[str, Any] = {
        "number": position + 1,
        "title": title or title_local or f"Chapter {position + 1}",
        "title_local": title_local,
        "act_number": _as_int(raw.get("act_number")),
        "key_events": key_events,
    }

    for field in _TEXT_FIELDS:
        chapter[field] = _as_text(raw.get(field))

    # A title duplicated in both slots carries no extra information.
    if chapter["title_local"] and chapter["title_local"] == chapter["title"]:
        chapter["title_local"] = None

    return chapter


def normalize_outline(data: Any) -> Dict[str, Any]:
    """Validate and canonicalize outline data.

    Accepts an outline object, a bare list of chapters, or a JSON string of
    either. Chapters are renumbered sequentially from 1 and de-duplicated, so a
    model that skips or repeats a number cannot corrupt the book's structure.
    Raises :class:`OutlineValidationError` when there is nothing usable, rather
    than silently substituting placeholder chapters.
    """
    if isinstance(data, str):
        data = _loads(data)

    # A bare list is treated as the chapter list.
    if isinstance(data, list):
        data = {"acts": [], "chapters": data}

    if not isinstance(data, dict):
        raise OutlineValidationError(
            "Outline must be a JSON object or a list of chapters."
        )

    raw_chapters = data.get("chapters")
    if raw_chapters is None:
        raw_chapters = []
    elif not isinstance(raw_chapters, list):
        raise OutlineValidationError("Outline 'chapters' must be a list.")

    chapters: List[Dict[str, Any]] = []
    seen_titles = set()
    for position, raw in enumerate(raw_chapters):
        chapter = _normalize_chapter(raw, position)
        if chapter is None:
            continue
        # Two chapters may not share a title; disambiguate rather than drop one.
        key = chapter["title"].casefold()
        if key in seen_titles:
            chapter["title"] = f"{chapter['title']} ({chapter['number']})"
            key = chapter["title"].casefold()
        seen_titles.add(key)
        chapters.append(chapter)

    if not chapters:
        raise OutlineValidationError(
            "Outline contains no usable chapters. Regenerate the outline."
        )

    raw_acts = data.get("acts") if isinstance(data, dict) else None
    acts: List[Dict[str, Any]] = []
    if isinstance(raw_acts, list):
        for position, raw in enumerate(raw_acts):
            act = _normalize_act(raw, position)
            if act is not None:
                acts.append(act)

    act_numbers = {act["number"] for act in acts}
    for chapter in chapters:
        # Drop act references that point at an act which does not exist.
        if (
            chapter["act_number"] is not None
            and chapter["act_number"] not in act_numbers
        ):
            chapter["act_number"] = None

    return {"acts": acts, "chapters": chapters}


def _loads(text: str) -> Any:
    """json.loads with a clearer error, tolerating a ```json code fence."""
    import json

    candidate = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n?```$", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except ValueError as exc:
        raise OutlineValidationError(f"Outline is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# Projection onto the chapter record shape
# --------------------------------------------------------------------------


def chapter_display_title(chapter: Dict[str, Any]) -> str:
    """The chapter's title, with the local-language title appended if present."""
    title = chapter.get("title") or "Untitled"
    title_local = chapter.get("title_local")
    if title_local and title_local != title:
        return f"{title} ({title_local})"
    return title


def outline_chapter_prompt(chapter: Dict[str, Any]) -> str:
    """Compose the outline text handed to the writer agent for one chapter.

    This is the value that becomes ``chapter['prompt']`` in ``chapters.json`` and
    is interpolated as ``{chapter_outline}`` in the generation prompts. Built
    from structured fields, so it carries no markdown noise.
    """
    sections: List[str] = []

    events = [e for e in (chapter.get("key_events") or []) if e]
    if events:
        sections.append("Key Events:\n" + "\n".join(f"- {event}" for event in events))

    for field, heading in (
        ("character_developments", "Character Developments"),
        ("setting", "Setting"),
        ("tone", "Tone"),
    ):
        value = _as_text(chapter.get(field))
        if value:
            sections.append(f"{heading}: {value}")

    if not sections:
        return f"Continue the story from the previous chapter into {chapter_display_title(chapter)}."

    return "\n\n".join(sections)


def outline_to_chapters(outline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Project a normalized outline onto the ``chapters.json`` record shape."""
    normalized = normalize_outline(outline)
    return [
        {
            "chapter_number": chapter["number"],
            "title": chapter_display_title(chapter),
            "prompt": outline_chapter_prompt(chapter),
        }
        for chapter in normalized["chapters"]
    ]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_outline_markdown(outline: Dict[str, Any]) -> str:
    """Render a normalized outline as markdown, for reference and export.

    Round-trips through :func:`parse_legacy_outline_markdown`, so an exported
    outline can be imported again without loss.
    """
    normalized = normalize_outline(outline)

    by_number = {a["number"]: a for a in normalized["acts"]}

    lines: List[str] = ["# OUTLINE", ""]
    # Sentinel, so a leading act-less chapter does not emit a stray heading.
    current_act: Any = object()
    for chapter in normalized["chapters"]:
        act_number = chapter["act_number"]
        if act_number != current_act:
            previous_act = current_act
            current_act = act_number
            act = by_number.get(act_number) if act_number is not None else None
            if act is not None:
                lines.append(f"## Act {act['number']}: {act['title']}")
            elif act_number is None and previous_act is not None:
                # A chapter with no act following one that has an act. Without an
                # explicit boundary the parser would read it as still belonging to
                # the previous act.
                lines.append(_NO_ACT_HEADING)
            lines.append("")
        lines.append(f"Chapter {chapter['number']}: {chapter['title']}")
        for event in chapter["key_events"]:
            lines.append(f"- Key Event: {event}")
        for field, heading in (
            ("character_developments", "Character Developments"),
            ("setting", "Setting"),
            ("tone", "Tone"),
        ):
            value = _as_text(chapter.get(field))
            if value:
                lines.append(f"- {heading}: {value}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Legacy markdown migration
# --------------------------------------------------------------------------

# Emitted by render_outline_markdown to mark a run of chapters that belong to no
# act, so an exported outline survives a re-import unchanged.
_NO_ACT_HEADING = "## Chapters"
_NO_ACT_RE = re.compile(r"^\s{0,3}#{1,6}\s*[*_`\s]*chapters\b[*_`\s]*$", re.IGNORECASE)

# A chapter heading, with or without markdown decoration, in any of the shapes a
# model has produced: "Chapter 3: Title", "**Chapter 3: Title**",
# "## Chapter 3: Title", "Chapter Three: Title". Number words are accepted so a
# cosmetic formatting change cannot drop a chapter.
_CHAPTER_HEADING = re.compile(
    r"^\s{0,3}#{0,6}\s*[*_`\s]*"
    r"chapter\s+(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen"
    r"|nineteen|twenty)\s*[:.\-–]\s*"
    r"(.+?)\s*[*_`]*\s*$",
    re.IGNORECASE,
)

# An act heading, e.g. "### **Act 1: Flight to the North**". The label is
# alphanumeric because legacy outlines used subdivisions like "Act 2A".
_ACT_HEADING = re.compile(
    r"^\s{0,3}#{0,6}\s*[*_`\s]*act\s+([0-9]{1,2}\s*[a-z]?)\s*[:.\-–]\s*(.+?)\s*[*_`]*\s*$",
    re.IGNORECASE,
)

# Envelope and prose markers that used to delimit or decorate the outline.
_ENVELOPE = re.compile(r"^\s*[*_`#\s]*(outline\s*:|end of outline)\b", re.IGNORECASE)

_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _clean_emphasis(text: str) -> str:
    """Strip markdown emphasis and stray backticks from a fragment."""
    return re.sub(r"[*_`]+", "", text).strip()


def _split_label(line: str) -> Optional[tuple]:
    """If the line is a ``- **Label:** value`` style field, return (field, value)."""
    match = _BULLET.match(line)
    body = match.group(1) if match else line.strip()
    if ":" not in body:
        return None
    label, _, value = body.partition(":")
    field = _FIELD_ALIASES.get(_clean_emphasis(label).casefold())
    if field is None:
        return None
    return field, _clean_emphasis(value)


def parse_legacy_outline_markdown(text: str) -> Optional[Dict[str, Any]]:
    """Parse a pre-JSON markdown outline into an outline dict.

    Only needed to migrate books created before structured output; new outlines
    never pass through here. Returns None when no chapters can be recovered, so
    the caller can refuse the migration instead of overwriting real data with
    placeholders.
    """
    if not text or not text.strip():
        return None

    acts: List[Dict[str, Any]] = []
    chapters: List[Dict[str, Any]] = []
    # The act the chapters currently being read belong to: None before any act
    # heading, and after an explicit no-act boundary.
    act_context: Optional[int] = None
    current: Optional[Dict[str, Any]] = None
    current_field: Optional[str] = None

    for line in text.replace("\r\n", "\n").split("\n"):
        if _ENVELOPE.match(line):
            continue

        if _NO_ACT_RE.match(line):
            # Boundary written by render_outline_markdown: the chapters that
            # follow belong to no act, whatever came before.
            act_context = None
            current = None
            current_field = None
            continue

        act_match = _ACT_HEADING.match(line)
        if act_match:
            # Legacy outlines subdivided acts ("Act 2A", "Act 2B"), so acts are
            # renumbered sequentially in document order. Chapter membership is
            # what matters downstream; the descriptive titles are preserved.
            acts.append(
                {
                    "number": len(acts) + 1,
                    "title": _clean_emphasis(act_match.group(2)),
                }
            )
            act_context = acts[-1]["number"]
            current = None
            current_field = None
            continue

        heading_match = _CHAPTER_HEADING.match(line)
        if heading_match:
            raw_number, raw_title = heading_match.groups()
            number = _NUMBER_WORDS.get(raw_number.casefold())
            if number is None:
                number = int(raw_number)

            raw_title = _clean_emphasis(raw_title)
            title, title_local = _split_local_title(raw_title)

            current = {
                "number": number,
                "title": title,
                "title_local": title_local,
                "act_number": act_context,
                "key_events": [],
                "character_developments": None,
                "setting": None,
                "tone": None,
            }
            chapters.append(current)
            current_field = None
            continue

        if current is None:
            continue

        stripped = line.strip()
        if not stripped:
            continue

        labelled = _split_label(line)
        if labelled is not None:
            field, value = labelled
            current_field = field
            if field == "key_events":
                if value:
                    current["key_events"].append(value)
            else:
                current[field] = value or None
            continue

        bullet = _BULLET.match(line)
        if bullet is not None:
            text_value = _clean_emphasis(bullet.group(1))
            if not text_value:
                continue
            # In the legacy format key events were emitted as plain bullets at
            # the same indent level as their own "- Key Events:" label.
            if current_field == "key_events":
                current["key_events"].append(text_value)
            elif current_field in _TEXT_FIELDS:
                existing = current[current_field] or ""
                current[current_field] = f"{existing}\n{text_value}".strip()
            continue

        # A continuation line for whichever field was last seen.
        text_value = _clean_emphasis(stripped)
        if not text_value:
            continue
        if current_field == "key_events":
            current["key_events"].append(text_value)
        elif current_field in _TEXT_FIELDS:
            existing = current[current_field] or ""
            current[current_field] = f"{existing}\n{text_value}".strip()

    if not chapters:
        return None

    try:
        return normalize_outline({"acts": acts, "chapters": chapters})
    except OutlineValidationError:
        return None


def _split_local_title(raw_title: str):
    """Split "Title (Local Title)" into its two parts."""
    match = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", raw_title)
    if match:
        title = match.group(1).strip()
        title_local = match.group(2).strip()
        if title and title_local:
            return title, title_local
    return raw_title, None
