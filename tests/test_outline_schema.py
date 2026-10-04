"""Tests for the structured outline schema."""

import pytest

from outline_schema import (
    OutlineValidationError,
    chapter_display_title,
    normalize_outline,
    outline_chapter_prompt,
    outline_to_chapters,
    parse_legacy_outline_markdown,
    render_outline_markdown,
)


def make_chapter(number=1, **overrides):
    chapter = {
        "number": number,
        "title": f"Chapter {number}",
        "title_local": None,
        "act_number": None,
        "key_events": ["Something happens", "Then something else"],
        "character_developments": "The hero relents.",
        "setting": "A rain-soaked city.",
        "tone": "Grim.",
    }
    chapter.update(overrides)
    return chapter


# --------------------------------------------------------------------------
# normalize_outline
# --------------------------------------------------------------------------


def test_normalize_keeps_chapter_order_and_renumbers_sequentially():
    outline = normalize_outline(
        {"chapters": [make_chapter(7), make_chapter(3), make_chapter(99)]}
    )

    assert [c["number"] for c in outline["chapters"]] == [1, 2, 3]
    assert [c["title"] for c in outline["chapters"]] == [
        "Chapter 7",
        "Chapter 3",
        "Chapter 99",
    ]


def test_normalize_rejects_an_outline_with_no_usable_chapters():
    with pytest.raises(OutlineValidationError):
        normalize_outline({"acts": [], "chapters": []})


def test_normalize_drops_chapters_that_carry_nothing():
    outline = normalize_outline(
        {"chapters": [make_chapter(1), {"number": 2}, "not a chapter", None]}
    )

    assert len(outline["chapters"]) == 1


def test_normalize_accepts_a_bare_list_of_chapters():
    outline = normalize_outline([make_chapter(1), make_chapter(2)])

    assert len(outline["chapters"]) == 2


def test_normalize_parses_a_json_string_and_strips_a_code_fence():
    outline = normalize_outline(
        '```json\n{"chapters": [{"title": "Alpha", "key_events": ["x"]}]}\n```'
    )

    assert outline["chapters"][0]["title"] == "Alpha"


def test_normalize_rejects_a_json_string_that_is_not_json():
    with pytest.raises(OutlineValidationError):
        normalize_outline("this is not json")


def test_normalize_deduplicates_identical_titles():
    outline = normalize_outline(
        {"chapters": [make_chapter(1), make_chapter(1), make_chapter(1)]}
    )

    titles = [c["title"] for c in outline["chapters"]]
    assert len(set(titles)) == 3


def test_normalize_drops_act_references_to_nonexistent_acts():
    outline = normalize_outline(
        {
            "acts": [{"number": 1, "title": "One"}],
            "chapters": [make_chapter(1, act_number=4)],
        }
    )

    assert outline["chapters"][0]["act_number"] is None


def test_normalize_keeps_act_references_that_resolve():
    outline = normalize_outline(
        {
            "acts": [{"number": 2, "title": "Two"}],
            "chapters": [make_chapter(1, act_number=2)],
        }
    )

    assert outline["chapters"][0]["act_number"] == 2


def test_normalize_blanks_whitespace_only_fields():
    outline = normalize_outline(
        {"chapters": [make_chapter(1, tone="   ", setting="", title=" Real ")]}
    )

    chapter = outline["chapters"][0]
    assert chapter["tone"] is None
    assert chapter["setting"] is None
    assert chapter["title"] == "Real"


def test_normalize_drops_a_local_title_identical_to_the_title():
    outline = normalize_outline([make_chapter(1, title="Same", title_local="Same")])

    assert outline["chapters"][0]["title_local"] is None


def test_normalize_accepts_key_events_given_as_a_string():
    outline = normalize_outline([make_chapter(1, key_events="just one")])

    assert outline["chapters"][0]["key_events"] == ["just one"]


def test_normalize_coerces_numeric_fields_from_strings():
    outline = normalize_outline(
        {
            "acts": [{"number": 2, "title": "Two"}],
            "chapters": [make_chapter(1, act_number="2")],
        }
    )

    assert outline["chapters"][0]["act_number"] == 2


# --------------------------------------------------------------------------
# Projection onto chapters.json
# --------------------------------------------------------------------------


def test_outline_to_chapters_matches_the_existing_record_shape():
    chapters = outline_to_chapters([make_chapter(1)])

    assert set(chapters[0]) == {"chapter_number", "title", "prompt"}
    assert chapters[0]["chapter_number"] == 1


def test_chapter_prompt_lists_events_without_markdown_noise():
    prompt = outline_chapter_prompt(
        make_chapter(1, key_events=["First", "Second"], tone="Tense")
    )

    assert "**" not in prompt
    assert "- First" in prompt
    assert "- Second" in prompt
    assert "Tone: Tense" in prompt


def test_chapter_prompt_omits_absent_fields():
    prompt = outline_chapter_prompt(
        make_chapter(
            1, key_events=[], setting=None, tone=None, character_developments=None
        )
    )

    assert "Key Events" not in prompt
    assert "Tone" not in prompt


def test_chapter_prompt_is_never_empty():
    prompt = outline_chapter_prompt(
        make_chapter(
            4, key_events=[], setting=None, tone=None, character_developments=None
        )
    )

    assert prompt.strip()


def test_display_title_appends_the_local_title():
    assert chapter_display_title(
        make_chapter(1, title="Drowning", title_local="Drukning")
    ) == ("Drowning (Drukning)")


def test_display_title_does_not_repeat_an_identical_local_title():
    assert (
        chapter_display_title(make_chapter(1, title="Same", title_local="Same"))
        == "Same"
    )


# --------------------------------------------------------------------------
# Legacy markdown migration
# --------------------------------------------------------------------------


LEGACY = """### **OUTLINE: THE TITLE**

### **Act 1: Flight to the North**

**Chapter 1: The Color of Drowning (Drukningens Farge)**

-   **Key Events:**
-   Leo finds a coin.
-   The coin dissolves his reality.
-   He meets Tia.
-   **Tone:** Melancholic, then wondrous.

**Chapter 2: The Whisper (Hviskningen)**

-   **Key Events:**
-   Asakael tortures a knight.
-   He summons the Traitor.
-   **Tone:** Chilling.

END OF OUTLINE
"""


def test_legacy_parse_recovers_chapters_acts_and_fields():
    outline = parse_legacy_outline_markdown(LEGACY)

    assert len(outline["chapters"]) == 2
    assert outline["acts"] == [{"number": 1, "title": "Flight to the North"}]

    first = outline["chapters"][0]
    assert first["title"] == "The Color of Drowning"
    assert first["title_local"] == "Drukningens Farge"
    assert first["key_events"] == [
        "Leo finds a coin.",
        "The coin dissolves his reality.",
        "He meets Tia.",
    ]
    assert first["tone"] == "Melancholic, then wondrous."


def test_legacy_parse_assigns_chapters_to_the_current_act():
    outline = parse_legacy_outline_markdown(LEGACY)

    assert all(c["act_number"] == 1 for c in outline["chapters"])


@pytest.mark.parametrize(
    "heading,expected_title",
    [
        ("Chapter 3: Plain", "Plain"),
        ("**Chapter 3: Bold**", "Bold"),
        ("## Chapter 3: Heading", "Heading"),
        ("### Chapter 3 - Dash", "Dash"),
        ("Chapter Three: Spelled Out", "Spelled Out"),
        ("#OUTLINE: x\nChapter 4: Envelope", "Envelope"),
    ],
)
def test_legacy_parse_tolerates_heading_variations(heading, expected_title):
    outline = parse_legacy_outline_markdown(heading + "\n- Key Events:\n- An event.\n")

    assert outline is not None
    assert len(outline["chapters"]) == 1
    assert outline["chapters"][0]["title"] == expected_title
    assert outline["chapters"][0]["key_events"] == ["An event."]


def test_legacy_parse_handles_subdivided_act_labels():
    outline = parse_legacy_outline_markdown(
        "### **Act 1: One**\n\nChapter 1: A\n- Key Events:\n- x\n\n"
        "### **Act 2A: Two A**\n\nChapter 2: B\n- Key Events:\n- y\n\n"
        "### **Act 2B: Two B**\n\nChapter 3: C\n- Key Events:\n- z\n"
    )

    assert len(outline["acts"]) == 3
    assert [a["number"] for a in outline["acts"]] == [1, 2, 3]
    assert outline["chapters"][1]["act_number"] == 2
    assert outline["chapters"][2]["act_number"] == 3


def test_legacy_parse_ignores_content_before_the_first_chapter():
    outline = parse_legacy_outline_markdown(
        "Some preamble prose.\n\nChapter 1: Only\n- Key Events:\n- x\n"
    )

    assert len(outline["chapters"]) == 1
    assert outline["chapters"][0]["title"] == "Only"


def test_legacy_parse_returns_none_when_there_are_no_chapters():
    assert parse_legacy_outline_markdown("Just some prose with no chapters.") is None
    assert parse_legacy_outline_markdown("") is None
    assert parse_legacy_outline_markdown("   \n  ") is None


def test_legacy_parse_renumbers_out_of_sequence_chapters():
    outline = parse_legacy_outline_markdown(
        "Chapter 7: Seven\n- Key Events:\n- x\n\nChapter 4: Four\n- Key Events:\n- y\n"
    )

    assert [c["number"] for c in outline["chapters"]] == [1, 2]
    assert [c["title"] for c in outline["chapters"]] == ["Seven", "Four"]


def test_legacy_parse_strips_markdown_emphasis_from_values():
    outline = parse_legacy_outline_markdown(
        "Chapter 1: A\n- **Key Events:**\n- A **bold** event.\n- **Tone:** **Very** tense.\n"
    )

    assert outline["chapters"][0]["key_events"] == ["A bold event."]
    assert outline["chapters"][0]["tone"] == "Very tense."


def test_legacy_parse_reads_all_documented_field_labels():
    outline = parse_legacy_outline_markdown(
        "Chapter 1: A\n"
        "- **Key Events:**\n- one\n"
        "- **Character Developments:** grows\n"
        "- **Setting:** a city\n"
        "- **Tone:** bleak\n"
    )

    chapter = outline["chapters"][0]
    assert chapter["character_developments"] == "grows"
    assert chapter["setting"] == "a city"
    assert chapter["tone"] == "bleak"


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_render_then_parse_preserves_chapters_and_events():
    original = normalize_outline(
        {
            "acts": [{"number": 1, "title": "Act One"}],
            "chapters": [
                make_chapter(1, title="Alpha", title_local="Alfa", act_number=1),
                make_chapter(2, title="Beta"),
            ],
        }
    )

    reparsed = parse_legacy_outline_markdown(render_outline_markdown(original))

    assert [c["title"] for c in reparsed["chapters"]] == ["Alpha", "Beta"]
    assert [c["key_events"] for c in reparsed["chapters"]] == [
        c["key_events"] for c in original["chapters"]
    ]
    assert [c["act_number"] for c in reparsed["chapters"]] == [1, None]


def test_rendered_markdown_keeps_local_titles_out_of_the_heading():
    rendered = render_outline_markdown(
        [make_chapter(1, title="Alpha", title_local="Alfa")]
    )

    assert "Chapter 1: Alpha" in rendered
    assert "Alfa" not in rendered.split("\n")[2]
