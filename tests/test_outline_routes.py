"""Tests for outline persistence, migration, and the outline API routes."""

import importlib
import json
import os

import pytest


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    """Import web_app with book_output redirected into a temporary directory."""
    monkeypatch.chdir(tmp_path)
    for name in [n for n in list(__import__("sys").modules) if n == "web_app"]:
        del __import__("sys").modules[name]
    module = importlib.import_module("web_app")
    yield module
    __import__("sys").modules.pop("web_app", None)


@pytest.fixture()
def client(app_module):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def write_outline_json(module, outline):
    os.makedirs(module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(module.OUTLINE_JSON_FILE, "w", encoding="utf-8") as handle:
        json.dump(outline, handle)


def make_outline(*titles):
    return {
        "acts": [{"number": 1, "title": "Act One"}],
        "chapters": [
            {
                "number": index + 1,
                "title": title,
                "title_local": None,
                "act_number": 1,
                "key_events": [f"Event for {title}"],
                "character_developments": None,
                "setting": None,
                "tone": None,
            }
            for index, title in enumerate(titles)
        ],
    }


# --------------------------------------------------------------------------
# Loading and migration
# --------------------------------------------------------------------------


def test_missing_outline_returns_none(app_module):
    assert app_module.get_outline() is None


def test_json_outline_is_loaded_as_is(app_module):
    write_outline_json(app_module, make_outline("Alpha"))

    outline = app_module.get_outline()

    assert outline["chapters"][0]["title"] == "Alpha"


def test_legacy_markdown_is_migrated_and_written_as_json(app_module):
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.OUTLINE_FILE, "w", encoding="utf-8") as handle:
        handle.write(
            "### **Act 1: One**\n\n"
            "**Chapter 1: Alpha (Alfa)**\n\n"
            "- **Key Events:**\n- Something happens.\n\n"
            "**Chapter 2: Beta**\n\n"
            "- **Key Events:**\n- Something else.\n"
        )

    outline = app_module.get_outline()

    assert [c["title"] for c in outline["chapters"]] == ["Alpha", "Beta"]
    assert outline["chapters"][0]["title_local"] == "Alfa"
    assert os.path.exists(app_module.OUTLINE_JSON_FILE)


def test_migration_does_not_overwrite_an_existing_json_outline(app_module):
    write_outline_json(app_module, make_outline("From JSON"))
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.OUTLINE_FILE, "w", encoding="utf-8") as handle:
        handle.write("Chapter 1: From Markdown\n- Key Events:\n- x\n")

    assert app_module.get_outline()["chapters"][0]["title"] == "From JSON"


def test_unparseable_legacy_file_is_ignored(app_module):
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.OUTLINE_FILE, "w", encoding="utf-8") as handle:
        handle.write("no chapters here at all")

    assert app_module.get_outline() is None


def test_corrupt_json_falls_back_to_the_legacy_file(app_module):
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.OUTLINE_JSON_FILE, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    with open(app_module.OUTLINE_FILE, "w", encoding="utf-8") as handle:
        handle.write("Chapter 1: Recovered\n- Key Events:\n- x\n")

    assert app_module.get_outline()["chapters"][0]["title"] == "Recovered"


# --------------------------------------------------------------------------
# Rebuilding chapters.json
# --------------------------------------------------------------------------


def test_build_writes_chapters_json(app_module):
    app_module.build_chapters_from_outline(make_outline("Alpha", "Beta"))

    chapters = app_module.get_chapters()

    assert [c["chapter_number"] for c in chapters] == [1, 2]
    assert all(c["prompt"].strip() for c in chapters)


def test_build_rejects_an_unusable_outline(app_module, client):
    response = client.put("/api/outline", json={"chapters": []})

    assert response.get_json()["success"] is False
    assert not os.path.exists(app_module.CHAPTERS_JSON_FILE)


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------


def test_api_outline_get_returns_the_outline(client, app_module):
    write_outline_json(app_module, make_outline("Alpha"))

    response = client.get("/api/outline")

    assert response.get_json()["outline"]["chapters"][0]["title"] == "Alpha"


def test_api_outline_put_saves_and_returns_the_normalized_outline(client, app_module):
    payload = make_outline("Alpha", "Beta")
    # Out-of-order numbers, to prove the server canonicalizes them.
    payload["chapters"][0]["number"] = 7
    payload["chapters"][1]["number"] = 3

    response = client.put("/api/outline", json=payload)

    body = response.get_json()
    assert body["success"] is True
    assert body["num_chapters"] == 2
    assert [c["number"] for c in body["outline"]["chapters"]] == [1, 2]
    assert app_module.get_chapters()[0]["chapter_number"] == 1


def test_api_outline_put_rejects_an_invalid_payload_without_writing(client, app_module):
    write_outline_json(app_module, make_outline("Keep Me"))

    response = client.put("/api/outline", json={"chapters": "nonsense"})

    assert response.get_json()["success"] is False
    assert app_module.get_outline()["chapters"][0]["title"] == "Keep Me"


def test_save_chapter_updates_only_that_chapter(client, app_module):
    write_outline_json(app_module, make_outline("Alpha", "Beta"))

    response = client.put(
        "/api/outline/chapter/2",
        json={
            "fields": {
                "title": "Beta Revised",
                "key_events": ["One", "Two"],
                "tone": "Hopeful",
            }
        },
    )

    body = response.get_json()
    assert body["success"] is True
    assert body["chapter"]["title"] == "Beta Revised"
    assert body["chapter"]["key_events"] == ["One", "Two"]

    saved = app_module.get_outline()["chapters"]
    assert saved[0]["title"] == "Alpha"
    assert saved[0]["key_events"] == ["Event for Alpha"]
    assert saved[1]["title"] == "Beta Revised"


def test_save_chapter_blank_string_clears_an_optional_field(client, app_module):
    write_outline_json(app_module, make_outline("Alpha"))

    client.put("/api/outline/chapter/1", json={"fields": {"tone": "Grim"}})
    client.put("/api/outline/chapter/1", json={"fields": {"tone": "   "}})

    assert app_module.get_outline()["chapters"][0]["tone"] is None


def test_save_chapter_accepts_flat_fields_like_the_editor_sends(client, app_module):
    # The outline editor posts the chapter fields at the top level.
    write_outline_json(app_module, make_outline("Alpha"))

    response = client.put(
        "/api/outline/chapter/1",
        json={
            "title": "Alpha Flat",
            "title_local": None,
            "act_number": None,
            "key_events": ["One", "Two"],
            "character_developments": None,
            "setting": None,
            "tone": None,
        },
    )

    assert response.get_json()["chapter"]["title"] == "Alpha Flat"
    assert response.get_json()["chapter"]["key_events"] == ["One", "Two"]


def test_save_chapter_rejects_a_non_list_key_events(client, app_module):
    write_outline_json(app_module, make_outline("Alpha"))

    response = client.put(
        "/api/outline/chapter/1", json={"fields": {"key_events": "nope"}}
    )

    assert response.get_json()["success"] is False
    assert app_module.get_outline()["chapters"][0]["key_events"] == ["Event for Alpha"]


def test_save_unknown_chapter_fails_without_writing(client, app_module):
    write_outline_json(app_module, make_outline("Alpha"))

    response = client.put("/api/outline/chapter/9", json={"fields": {"title": "x"}})

    assert response.get_json()["success"] is False
    assert app_module.get_outline()["chapters"][0]["title"] == "Alpha"


def test_save_chapter_never_prunes_other_chapter_files(client, app_module):
    write_outline_json(app_module, make_outline("Alpha", "Beta"))
    app_module.build_chapters_from_outline(make_outline("Alpha", "Beta"))
    chapter_dir = app_module.CHAPTERS_DIR
    os.makedirs(chapter_dir, exist_ok=True)
    stray = os.path.join(chapter_dir, "chapter_003.txt")
    with open(stray, "w", encoding="utf-8") as handle:
        handle.write("written text")

    client.put("/api/outline/chapter/1", json={"fields": {"title": "Alpha Revised"}})

    assert os.path.exists(stray)


# --------------------------------------------------------------------------
# Deliberate shrinking removes orphaned files
# --------------------------------------------------------------------------


def test_shrinking_the_outline_removes_orphaned_chapter_files(client, app_module):
    write_outline_json(app_module, make_outline("Alpha", "Beta", "Gamma"))
    app_module.build_chapters_from_outline(make_outline("Alpha", "Beta", "Gamma"))
    chapter_dir = app_module.CHAPTERS_DIR
    os.makedirs(chapter_dir, exist_ok=True)
    files = {}
    for number in (1, 2, 3):
        path = os.path.join(
            chapter_dir, f"chapter_{number:03d}{app_module.TEXT_EXTENSION}"
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"chapter {number}")
        files[number] = path

    response = client.put("/api/outline", json=make_outline("Alpha"))

    assert response.get_json()["success"] is True
    assert os.path.exists(files[1])
    assert not os.path.exists(files[2])
    assert not os.path.exists(files[3])
    assert os.path.exists(app_module.CHAPTERS_JSON_FILE)


def test_generating_chapters_with_no_outline_fails_cleanly(client, app_module):
    response = client.post("/generate_chapters")

    assert response.get_json()["success"] is False
    assert not os.path.exists(app_module.CHAPTERS_JSON_FILE)


def test_save_outline_accepts_a_legacy_markdown_post(client, app_module):
    response = client.post(
        "/save_outline",
        data={
            "outline": "Chapter 1: Alpha\n- Key Events:\n- x\n\nChapter 2: Beta\n- Key Events:\n- y\n"
        },
    )

    assert response.get_json()["success"] is True
    assert [c["title"] for c in app_module.get_chapters()] == ["Alpha", "Beta"]


def test_save_outline_refuses_unusable_markdown(client, app_module):
    write_outline_json(app_module, make_outline("Keep Me"))

    response = client.post("/save_outline", data={"outline": "no chapters in here"})

    body = response.get_json()
    assert body["success"] is False
    assert "untouched" in body["error"]
    assert app_module.get_outline()["chapters"][0]["title"] == "Keep Me"


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------


def test_delete_outline_removes_both_files(client, app_module):
    write_outline_json(app_module, make_outline("Alpha"))
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    with open(app_module.OUTLINE_FILE, "w", encoding="utf-8") as handle:
        handle.write("legacy")

    response = client.post("/delete_outline")

    assert response.get_json()["success"] is True
    assert not os.path.exists(app_module.OUTLINE_JSON_FILE)
    assert not os.path.exists(app_module.OUTLINE_FILE)


# --------------------------------------------------------------------------
# Finalize stream: JSON and markdown fallback
# --------------------------------------------------------------------------


def sse_payloads(response):
    """Collect the JSON bodies of an SSE response."""
    import json as _json

    bodies = []
    for chunk in response.get_data(as_text=True).split("\n\n"):
        if not chunk.strip().startswith("data: "):
            continue
        bodies.append(_json.loads(chunk.strip()[len("data: ") :]))
    return bodies


class FakeDelta:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content=None, finish_reason=None):
        self.delta = FakeDelta(content) if content is not None else FakeDelta(None)
        self.finish_reason = finish_reason


class FakeChunk:
    def __init__(self, choice):
        self.choices = [choice]


def fake_stream(pieces, finish_reason="stop"):
    return [FakeChunk(FakeChoice(content=piece)) for piece in pieces] + [
        FakeChunk(FakeChoice(finish_reason=finish_reason))
    ]


def stream_outline(client, app_module, pieces, monkeypatch, finish_reason="stop"):
    # The route needs the upstream context files before it will generate anything.
    os.makedirs(app_module.BOOK_OUTPUT_DIR, exist_ok=True)
    for path in (
        app_module.WORLD_FILE,
        app_module.CHARACTERS_FILE,
        app_module.SYNOPSIS_FILE,
    ):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("context")

    # BookAgents is constructed per request, so patch the class.
    monkeypatch.setattr(
        app_module.BookAgents,
        "generate_final_outline_stream",
        lambda self, *a, **k: fake_stream(pieces, finish_reason),
    )
    return sse_payloads(
        client.post("/finalize_outline_stream", json={"chat_history": []})
    )


def test_finalize_saves_a_structured_outline(client, app_module, monkeypatch):
    payload = json.dumps(
        {
            "acts": [{"number": 1, "title": "Act One"}],
            "chapters": [
                {
                    "number": 1,
                    "title": "Alpha",
                    "key_events": ["One", "Two", "Three"],
                    "act_number": 1,
                }
            ],
        }
    )

    bodies = stream_outline(
        client, app_module, [payload[:20], payload[20:]], monkeypatch
    )

    errors = [b for b in bodies if "error" in b]
    assert errors == []
    assert any(b.get("content") == "[DONE]" for b in bodies)

    saved = app_module.get_outline()
    assert saved["chapters"][0]["title"] == "Alpha"
    assert saved["chapters"][0]["key_events"] == ["One", "Two", "Three"]


def test_finalize_falls_back_to_parsing_markdown(client, app_module, monkeypatch):
    markdown = (
        "# OUTLINE\n\n## Act 1: One\n\nChapter 1: Alpha\n"
        "- Key Event: One\n- Key Event: Two\n- Key Event: Three\n"
    )

    bodies = stream_outline(client, app_module, [markdown], monkeypatch)

    assert [b for b in bodies if "error" in b] == []
    saved = app_module.get_outline()
    assert saved["chapters"][0]["title"] == "Alpha"
    assert saved["chapters"][0]["act_number"] == 1


def test_finalize_reports_a_truncated_response_without_saving(
    client, app_module, monkeypatch
):
    bodies = stream_outline(
        client,
        app_module,
        ['{"chapters": [{"title": "Al'],
        monkeypatch,
        finish_reason="length",
    )

    assert any("error" in b for b in bodies)
    assert app_module.get_outline() is None


def test_finalize_reports_unusable_output_without_saving(
    client, app_module, monkeypatch
):
    bodies = stream_outline(
        client, app_module, ["I'm sorry, I can't help with that."], monkeypatch
    )

    assert any("error" in b for b in bodies)
    assert app_module.get_outline() is None
