"""Tests for the scene generation route, including the scene description."""

import importlib
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


def make_outline(*titles):
    return {
        "chapters": [
            {"title": title, "key_events": ["Something happens."]} for title in titles
        ]
    }


def prepare_book(app_module):
    app_module.build_chapters_from_outline(make_outline("Alpha"))
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.SYNOPSIS_FILE, "w", encoding="utf-8") as handle:
        handle.write("A synopsis.")


def capture_prompts(app_module, monkeypatch):
    """Replace the model call so tests can inspect the rendered prompt."""
    captured = []

    def fake_generate_content(self, agent_name, prompt):
        captured.append(prompt)
        return "SCENE TEXT"

    monkeypatch.setattr(
        app_module.BookAgents, "generate_content", fake_generate_content
    )
    return captured


def test_scene_description_is_included_in_the_prompt(client, app_module, monkeypatch):
    prepare_book(app_module)
    captured = capture_prompts(app_module, monkeypatch)

    response = client.post(
        "/scene/1",
        data={"scene_description": "A tense duel in the rain"},
    )

    assert response.get_json()["scene_content"] == "SCENE TEXT"
    assert "A tense duel in the rain" in captured[0]


def test_empty_scene_description_uses_a_neutral_placeholder(
    client, app_module, monkeypatch
):
    prepare_book(app_module)
    captured = capture_prompts(app_module, monkeypatch)

    client.post("/scene/1", data={})

    assert "No specific scene was requested" in captured[0]


def test_generated_scene_is_saved_to_disk(client, app_module, monkeypatch):
    prepare_book(app_module)
    capture_prompts(app_module, monkeypatch)

    client.post("/scene/1", data={"scene_description": "Anything"})

    scene_dir = os.path.join(app_module.CHAPTERS_DIR, "chapter_1_scenes")
    files = [f for f in os.listdir(scene_dir) if f.endswith(app_module.TEXT_EXTENSION)]
    assert files == ["scene_1" + app_module.TEXT_EXTENSION]
