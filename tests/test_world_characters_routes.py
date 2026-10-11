"""Tests for structured world and character storage, migration, and API routes."""

import importlib
import json
import os
import sys

import pytest


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    """Import web_app with book_output redirected into a temporary directory."""
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("web_app", None)
    module = importlib.import_module("web_app")
    yield module
    sys.modules.pop("web_app", None)


@pytest.fixture()
def client(app_module):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# --------------------------------------------------------------------------
# Parsing legacy text
# --------------------------------------------------------------------------


def test_world_text_parses_numbered_sections(app_module):
    world = app_module.parse_world_text_to_json(
        "1. Time period and setting\n\nA modern city.\n\n"
        "2. Major locations\n\nThe warehouse.\n"
    )

    assert [s["id"] for s in world["sections"]] == [
        "time_period_and_setting",
        "major_locations",
    ]
    assert world["sections"][1]["content"] == "The warehouse."


def test_world_text_parses_markdown_headings(app_module):
    world = app_module.parse_world_text_to_json(
        "### **LEO'S WORLD**\n\nA mundane city.\n\n### **ELYSIA**\n\nA magic land.\n"
    )

    assert [s["title"] for s in world["sections"]] == ["LEO'S WORLD", "ELYSIA"]


def test_world_text_headings_win_over_numbered_lists_inside_sections(app_module):
    world = app_module.parse_world_text_to_json(
        "### Time period and setting\n"
        "A modern world with old magic.\n\n"
        "### Major locations\n"
        "The city of X.\n"
        "1. The market district\n"
        "2. The palace\n"
        "3. The docks\n\n"
        "### Culture\n"
        "Festivals and such.\n"
    )

    sections = world["sections"]
    assert [s["title"] for s in sections] == [
        "Time period and setting",
        "Major locations",
        "Culture",
    ]
    assert "1. The market district" in sections[1]["content"]
    assert "### Culture" not in sections[1]["content"]
    assert sections[2]["content"] == "Festivals and such."


def test_world_text_numbered_titles_split_on_colon(app_module):
    world = app_module.parse_world_text_to_json(
        "1. Time period and setting: A modern world with old magic.\n\n"
        "2. Major locations: The warehouse.\n"
    )

    sections = world["sections"]
    assert [s["title"] for s in sections] == [
        "Time period and setting",
        "Major locations",
    ]
    assert sections[0]["content"] == "A modern world with old magic."
    assert sections[1]["content"] == "The warehouse."


def test_character_text_parses_profiles_and_fields(app_module):
    data = app_module.parse_characters_text_to_json(
        "CHARACTER_PROFILES:\n\n**LEO:**\n\n"
        "- **Role:** Main Character\n"
        "- **Personality:** Cynical but decent\n\n"
        "**TIA:**\n\n"
        "- **Role:** Bearer of the blood\n"
    )

    assert [c["name"] for c in data["characters"]] == ["LEO", "TIA"]
    assert data["characters"][0]["role"] == "Main Character"
    assert data["characters"][1]["role"] == "Bearer of the blood"


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def test_legacy_world_is_migrated_to_json(app_module):
    write(app_module.WORLD_FILE, "1. Major locations\n\nA city.\n")

    text = app_module.get_world_theme()

    assert "A city." in text
    assert os.path.exists(app_module.WORLD_JSON_FILE)
    with open(app_module.WORLD_JSON_FILE, encoding="utf-8") as handle:
        assert json.load(handle)["sections"][0]["id"] == "major_locations"


def test_legacy_characters_are_migrated_to_json(app_module):
    write(
        app_module.CHARACTERS_FILE,
        "CHARACTER_PROFILES:\n\n**LEO:**\n\n- **Role:** Hero\n",
    )

    text = app_module.get_characters()

    assert "LEO" in text
    assert os.path.exists(app_module.CHARACTERS_JSON_FILE)


def test_json_takes_precedence_over_legacy_text(app_module):
    app_module.save_world_json(
        {"sections": [{"id": "other", "title": "From JSON", "content": "json"}]}
    )
    write(app_module.WORLD_FILE, "1. Major locations\n\nfrom text\n")

    assert "From JSON" in app_module.get_world_theme()


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------


def test_api_world_round_trip_preserves_include_flag(client, app_module):
    response = client.put(
        "/api/world",
        json={
            "sections": [
                {
                    "id": "major_locations",
                    "title": "Places",
                    "content": "A city.",
                    "include_in_context": False,
                }
            ]
        },
    )

    assert response.get_json()["success"] is True
    body = client.get("/api/world").get_json()
    section = body["world"]["sections"][0]
    assert section["title"] == "Places"
    assert section["include_in_context"] is False


def test_api_characters_round_trip_preserves_include_flag(client, app_module):
    response = client.put(
        "/api/characters",
        json={
            "characters": [
                {"name": "Leo", "role": "Hero", "include_in_context": False},
                {"name": "Tia", "role": "Mage"},
            ]
        },
    )

    assert response.get_json()["success"] is True
    body = client.get("/api/characters").get_json()
    characters = body["characters"]["characters"]
    assert [c["name"] for c in characters] == ["Leo", "Tia"]
    assert characters[0]["include_in_context"] is False
    assert characters[1]["include_in_context"] is True


def test_api_save_writes_text_mirror(client, app_module):
    client.put(
        "/api/characters",
        json={"characters": [{"name": "Leo", "role": "Hero"}]},
    )

    assert os.path.exists(app_module.CHARACTERS_FILE)
    with open(app_module.CHARACTERS_FILE, encoding="utf-8") as handle:
        assert "Role: Hero" in handle.read()


def test_delete_removes_json_and_text(client, app_module):
    app_module.save_world_json(
        {"sections": [{"id": "other", "title": "X", "content": "y"}]}
    )
    write(app_module.WORLD_FILE, "1. Major locations\n\nz\n")

    client.post("/delete_world")

    assert not os.path.exists(app_module.WORLD_JSON_FILE)
    assert not os.path.exists(app_module.WORLD_FILE)


# --------------------------------------------------------------------------
# Chapter context filtering (include_in_context)
# --------------------------------------------------------------------------


def world_with_flags(app_module):
    return app_module.save_world_json(
        {
            "sections": [
                {
                    "id": "major_locations",
                    "title": "Included Place",
                    "content": "KEEP_WORLD",
                    "include_in_context": True,
                },
                {
                    "id": "other",
                    "title": "Hidden Place",
                    "content": "DROP_WORLD",
                    "include_in_context": False,
                },
            ]
        }
    )


def characters_with_flags(app_module):
    return app_module.save_characters_json(
        {
            "characters": [
                {"name": "Keeper", "role": "Hero", "include_in_context": True},
                {"name": "DropMe", "role": "Villain", "include_in_context": False},
            ]
        }
    )


def make_outline(*titles):
    return {
        "chapters": [
            {
                "title": title,
                "key_events": ["Something happens."],
            }
            for title in titles
        ]
    }


def test_world_to_text_filters_excluded_sections(app_module):
    world = app_module._normalize_world(
        {
            "sections": [
                {
                    "id": "other",
                    "title": "Keep",
                    "content": "KEEP",
                    "include_in_context": True,
                },
                {
                    "id": "other",
                    "title": "Drop",
                    "content": "DROP",
                    "include_in_context": False,
                },
            ]
        }
    )

    full = app_module.world_to_text(world)
    filtered = app_module.world_to_text(world, include_all=False)

    assert "DROP" in full and "KEEP" in full
    assert "KEEP" in filtered
    assert "DROP" not in filtered


def test_characters_to_text_filters_excluded_characters(app_module):
    data = {
        "characters": [
            {"name": "Keeper", "role": "Hero", "include_in_context": True},
            {"name": "DropMe", "role": "Villain", "include_in_context": False},
        ]
    }

    assert "DropMe" in app_module.characters_to_text(data)
    filtered = app_module.characters_to_text(data, include_all=False)
    assert "Keeper" in filtered
    assert "DropMe" not in filtered


def test_excluding_everything_returns_empty_string(app_module):
    app_module.save_world_json(
        {
            "sections": [
                {
                    "id": "other",
                    "title": "X",
                    "content": "y",
                    "include_in_context": False,
                }
            ]
        }
    )
    app_module.save_characters_json(
        {"characters": [{"name": "X", "include_in_context": False}]}
    )

    assert app_module.get_world_theme(include_all=False) == ""
    assert app_module.get_characters(include_all=False) == ""
    # The full view still shows everything.
    assert app_module.get_world_theme()
    assert app_module.get_characters()


def test_chapter_prompt_respects_include_in_context(client, app_module):
    app_module.build_chapters_from_outline(_outline("Alpha"))
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.SYNOPSIS_FILE, "w", encoding="utf-8") as handle:
        handle.write("A synopsis.")
    app_module.save_world_json(
        {
            "sections": [
                {
                    "id": "major_locations",
                    "title": "IncludedPlace",
                    "content": "KEEP_WORLD",
                    "include_in_context": True,
                },
                {
                    "id": "other",
                    "title": "HiddenPlace",
                    "content": "DROP_WORLD",
                    "include_in_context": False,
                },
            ]
        }
    )
    app_module.save_characters_json(
        {
            "characters": [
                {"name": "Keeper", "role": "Hero", "include_in_context": True},
                {"name": "DropMe", "role": "Villain", "include_in_context": False},
            ]
        }
    )

    response = client.post(
        "/chapter_stream/1", json={"master_prompt": "", "show_prompt": True}
    )

    prompt = response.get_json()["user_prompt"]
    assert "KEEP_WORLD" in prompt
    assert "Keeper" in prompt
    assert "DROP_WORLD" not in prompt
    assert "DropMe" not in prompt


def _outline(*titles):
    return {
        "chapters": [
            {
                "number": index + 1,
                "title": title,
                "key_events": ["Event"],
            }
            for index, title in enumerate(titles)
        ]
    }
