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
