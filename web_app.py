"""
Flask web application for OpenTale
"""

import json
import math
import os
import re
import shutil
from datetime import datetime

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
)

import prompts
from agents import PROMPT_DEBUGGING_DIR, BookAgents, check_openai_connection
from config import get_config
from outline_schema import (
    OutlineValidationError,
    normalize_outline,
    outline_to_chapters,
    parse_legacy_outline_markdown,
    render_outline_markdown,
)

# Constants for file paths
BOOK_OUTPUT_DIR = "book_output"
TEXT_EXTENSION = ".txt"
WORLD_FILE = os.path.join(BOOK_OUTPUT_DIR, f"world{TEXT_EXTENSION}")
CHARACTERS_FILE = os.path.join(BOOK_OUTPUT_DIR, f"characters{TEXT_EXTENSION}")
WORLD_JSON_FILE = os.path.join(BOOK_OUTPUT_DIR, "world.json")
CHARACTERS_JSON_FILE = os.path.join(BOOK_OUTPUT_DIR, "characters.json")
SYNOPSIS_FILE = os.path.join(BOOK_OUTPUT_DIR, f"synopsis{TEXT_EXTENSION}")
OUTLINE_FILE = os.path.join(BOOK_OUTPUT_DIR, f"outline{TEXT_EXTENSION}")
CHAPTERS_JSON_FILE = os.path.join(BOOK_OUTPUT_DIR, "chapters.json")
MASTER_PROMPT_FILE = os.path.join(BOOK_OUTPUT_DIR, f"master_prompt{TEXT_EXTENSION}")
SETTINGS_FILE = os.path.join(BOOK_OUTPUT_DIR, "settings.json")
OUTLINE_JSON_FILE = os.path.join(BOOK_OUTPUT_DIR, "outline.json")
CHAPTERS_DIR = os.path.join(BOOK_OUTPUT_DIR, "chapters")

app = Flask(__name__)
app.secret_key = "ai-book-writer-secret-key"  # For session management

# Ensure book_output directory exists
os.makedirs(CHAPTERS_DIR, exist_ok=True)

# Initialize global variables
agent_config = get_config()


# Helper functions to read data from files
def get_world_theme(include_all=True):
    """Get the world setting as readable text.

    Structured data is stored in ``world.json``; legacy ``world.txt`` files are
    migrated on read. The text form is returned for templates and prompts that
    still expect a string.

    Pass ``include_all=False`` to omit sections the author excluded from the
    chapter context (``include_in_context`` is False).
    """
    return world_to_text(load_world(), include_all=include_all)


def get_characters(include_all=True):
    """Get character profiles as readable text.

    Structured data is stored in ``characters.json``; legacy ``characters.txt``
    files are migrated on read.

    Pass ``include_all=False`` to omit characters the author excluded from the
    chapter context (``include_in_context`` is False). If every character is
    excluded, an empty string is returned rather than the raw text.
    """
    return characters_to_text(load_characters(), include_all=include_all)


def get_outline():
    """Get the structured outline, or None if there isn't a usable one.

    Outlines are stored as JSON. Books created before that switch kept the
    outline as markdown in ``outline.txt``; those are migrated once, on read, so
    the old file is upgraded in place the first time it is touched.
    """
    if os.path.exists(OUTLINE_JSON_FILE):
        try:
            with open(OUTLINE_JSON_FILE, "r", encoding="utf-8") as f:
                return normalize_outline(json.load(f))
        except (ValueError, OutlineValidationError) as e:
            print(f"Could not read {OUTLINE_JSON_FILE}: {e}")

    # Migrate a legacy markdown outline, if there is one.
    if os.path.exists(OUTLINE_FILE):
        with open(OUTLINE_FILE, "r", encoding="utf-8") as f:
            legacy = f.read()

        outline = parse_legacy_outline_markdown(legacy)
        if outline is not None:
            save_outline_data(outline)
            print(f"Migrated legacy markdown outline to {OUTLINE_JSON_FILE}")
            return outline

        # Unparseable: keep the old file so the author can still recover it by
        # hand, and leave the structured outline absent rather than guessing.
        print(
            f"Could not recover any chapters from {OUTLINE_FILE}. "
            "The file has been left untouched; re-create the outline to continue."
        )

    return None


def save_outline_data(outline):
    """Validate and persist the structured outline. Returns the normalized form."""
    normalized = normalize_outline(outline)
    with open(OUTLINE_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    return normalized


def get_synopsis():
    """Get synopsis from file."""
    if os.path.exists(SYNOPSIS_FILE):
        with open(SYNOPSIS_FILE, "r") as f:
            return f.read().strip()
    return ""


def get_chapters():
    """Get chapters from file, including a flag if content exists."""
    chapters = []
    if os.path.exists(CHAPTERS_JSON_FILE):
        with open(CHAPTERS_JSON_FILE, "r") as f:
            try:
                chapters = json.load(f)
            except json.JSONDecodeError:
                chapters = []

    # Add 'has_content' and 'has_been_reviewed' flags to each chapter
    settings = get_settings()
    chapter_styles = settings.get("chapters", {})
    for chapter in chapters:
        chapter_file_path = os.path.join(
            CHAPTERS_DIR, f"chapter_{chapter['chapter_number']}{TEXT_EXTENSION}"
        )
        chapter["has_content"] = (
            os.path.exists(chapter_file_path) and os.path.getsize(chapter_file_path) > 0
        )
        editor_chapter_file_path = os.path.join(
            CHAPTERS_DIR, f"chapter_{chapter['chapter_number']}_editor{TEXT_EXTENSION}"
        )
        has_wip = (
            os.path.exists(editor_chapter_file_path)
            and os.path.getsize(editor_chapter_file_path) > 0
        )
        # A chapter counts as reviewed when it has been promoted through the
        # review workflow (persisted flag) OR a WIP review draft still exists.
        # This survives overwrite/delete-WIP, which remove the WIP file.
        was_promoted = bool(
            (chapter_styles.get(str(chapter["chapter_number"])) or {}).get("reviewed")
        )
        chapter["has_been_reviewed"] = has_wip or was_promoted
        scene_dir = os.path.join(
            CHAPTERS_DIR, f"chapter_{chapter['chapter_number']}_scenes"
        )
        chapter["has_scenes"] = os.path.exists(scene_dir) and any(
            f.endswith(TEXT_EXTENSION) for f in os.listdir(scene_dir)
        )
    return chapters


@app.context_processor
def inject_chapters_enabled():
    """Expose whether the Chapters page prerequisites are complete."""

    def has_content(path):
        return os.path.exists(path) and os.path.getsize(path) > 0

    def has_any(*paths):
        return any(has_content(path) for path in paths)

    return {
        "chapters_enabled": (
            has_content(SYNOPSIS_FILE)
            and has_any(WORLD_JSON_FILE, WORLD_FILE)
            and has_any(CHARACTERS_JSON_FILE, CHARACTERS_FILE)
            and (has_content(OUTLINE_JSON_FILE) or has_content(OUTLINE_FILE))
        ),
    }


def get_paginated_chapters(page, per_page):
    """Helper to get paginated chapters."""
    all_chapters = get_chapters()
    total_chapters = len(all_chapters)
    total_pages = math.ceil(total_chapters / per_page) if per_page > 0 else 1

    start_index = (page - 1) * per_page
    end_index = start_index + per_page
    paginated_chapters = all_chapters[start_index:end_index]

    return {
        "chapters": paginated_chapters,
        "total_pages": total_pages,
        "current_page": page,
        "total_chapters": total_chapters,
        "per_page": per_page,
    }


def get_paginated_chapters_from_request(request, chapters, chapter_number):
    chapters_per_page = request.args.get("per_page", 10, type=int)

    if "page" in request.args:
        page = request.args.get("page", 1, type=int)
    else:
        # Find the page for the active chapter if no page is specified
        try:
            active_chapter_index = [c["chapter_number"] for c in chapters].index(
                chapter_number
            )
            page = math.ceil((active_chapter_index + 1) / chapters_per_page)
        except (ValueError, ZeroDivisionError):
            page = 1  # Chapter not found or per_page is 0, fallback to page 1

    chapters_paginated = get_paginated_chapters(page, chapters_per_page)

    return chapters_paginated


def get_scenes(chapter_number):
    """Get all scenes for a specific chapter."""
    scenes = []
    scene_dir = os.path.join(CHAPTERS_DIR, f"chapter_{chapter_number}_scenes")
    if os.path.exists(scene_dir):
        scene_files = [f for f in os.listdir(scene_dir) if f.endswith(TEXT_EXTENSION)]
        scene_files.sort(key=lambda f: int(f.split("_")[1].split(".")[0]))
        for scene_file in scene_files:
            scene_path = os.path.join(scene_dir, scene_file)
            with open(scene_path, "r") as f:
                content = f.read()
            scenes.append(content)
    return scenes


def get_scenes_details(chapter_number):
    """Get formatted scene details for inclusion in chapter prompts."""
    scenes = get_scenes(chapter_number)
    if not scenes:
        return ""
    parts = []
    for i, scene_content in enumerate(scenes, 1):
        parts.append(f"Scene {i}:\n{scene_content}")
    return "\n\n".join(parts)


def get_master_prompt():
    """Get master prompt from file."""
    if os.path.exists(MASTER_PROMPT_FILE):
        with open(MASTER_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def chapter_edits_path(chapter_number):
    """Path of the required-changes list for a chapter."""
    return os.path.join(CHAPTERS_DIR, f"chapter_{chapter_number}_edits{TEXT_EXTENSION}")


def get_chapter_edits(chapter_number):
    """Get the saved required-changes list for a chapter."""
    path = chapter_edits_path(chapter_number)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def get_settings():
    """Get settings from file."""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def get_previous_chapter_outline(chapter_number, chapters):
    """Get the previous chapter's title and outline as continuity context."""
    if chapter_number <= 1:
        return ""

    previous_chapter = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number - 1), None
    )
    if not previous_chapter:
        return ""

    return (
        f"Chapter {previous_chapter['chapter_number']}: {previous_chapter['title']}\n"
        f"{previous_chapter['prompt']}"
    )


def save_settings(settings):
    """Save settings to file."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


DEFAULT_POV = "Third-person limited"
DEFAULT_TENSE = "Past tense"


def _get_chapter_style(settings, chapter_number):
    """Return (point_of_view, tense) for a chapter, in precedence order:
    the chapter's own saved value, then the book-wide last-used style,
    then the out-of-the-box defaults."""
    style = settings.get("chapters", {}).get(str(chapter_number)) or {}
    default_style = settings.get("default_style") or {}
    pov = (
        style.get("point_of_view") or default_style.get("point_of_view") or DEFAULT_POV
    )
    tense = style.get("tense") or default_style.get("tense") or DEFAULT_TENSE
    return pov, tense


@app.route("/")
def index():
    """Render the home page"""

    # Get chapter list
    chapters = get_chapters()

    return render_template("index.html", chapters=chapters)


@app.route("/synopsis", methods=["GET"])
def synopsis():
    """Display synopsis or chat interface"""
    synopsis_content = get_synopsis()
    settings = get_settings()
    chapters = get_chapters()

    return render_template(
        "synopsis.html",
        synopsis=synopsis_content,
        topic=settings.get("topic", ""),
        chapters=chapters,
        has_world=bool(get_world_theme()),
        has_characters=bool(get_characters()),
    )


def _close_stream(stream):
    """Close an upstream LLM stream so generation stops when the client disconnects."""
    try:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _sse_response(stream):
    """Wrap an LLM stream as a server-sent events response."""

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    yield f"data: {json.dumps({'content': chunk.choices[0].delta.content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"
        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/synopsis_chat_stream", methods=["POST"])
def synopsis_chat_stream():
    """Handle ongoing chat for synopsis building with streaming response"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    # Save topic to settings if available
    if topic:
        settings = get_settings()
        settings["topic"] = topic
        save_settings(settings)

    # Initialize agents for synopsis building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate streaming response
    stream = book_agents.generate_chat_response_synopsis_stream(
        chat_history, topic, user_message
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/finalize_synopsis_stream", methods=["POST"])
def finalize_synopsis_stream():
    """Finalize the synopsis based on chat history with streaming response"""
    data = request.json
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    if not chat_history:
        return jsonify(
            {
                "error": "Chat history is empty. Please chat with the AI first to build your synopsis."
            }
        ), 400

    # Initialize agents for synopsis building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate the final synopsis using streaming
    stream = book_agents.generate_final_synopsis_stream(chat_history, topic)

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Collect all chunks to save the complete response
            collected_content = []

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    collected_content.append(content)
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Combine all chunks for the complete content
            complete_content = "".join(collected_content)

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/save_synopsis", methods=["POST"])
def save_synopsis():
    """Save edited synopsis"""
    synopsis_content = request.form.get("synopsis")

    # Save to file
    with open(SYNOPSIS_FILE, "w") as f:
        f.write(synopsis_content)

    return jsonify({"success": True})


@app.route("/delete_synopsis", methods=["POST"])
def delete_synopsis():
    """Delete the saved synopsis so it can be recreated."""
    if os.path.exists(SYNOPSIS_FILE):
        os.remove(SYNOPSIS_FILE)

    return jsonify({"success": True})


@app.route("/world", methods=["GET"])
def world():
    # Check if synopsis exist
    if not os.path.exists(SYNOPSIS_FILE):
        flash("You need to create a synopsis first.", "warning")
        return redirect("/synopsis")

    # GET request - show world page with existing theme if available
    world_theme = get_world_theme()
    synopsis = get_synopsis()
    settings = get_settings()
    chapters = get_chapters()

    return render_template(
        "world.html",
        world_theme=world_theme,
        world=load_world(),
        characters=get_characters(),
        synopsis=synopsis,
        topic=settings.get("topic", ""),
        chapters=chapters,
    )


@app.route("/world_chat", methods=["POST"])
def world_chat():
    """Handle ongoing chat for world building"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    # Save topic to settings if available
    if topic:
        settings = get_settings()
        settings["topic"] = topic
        save_settings(settings)

    # Initialize agents for world building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate response using the direct chat method
    ai_response = book_agents.generate_chat_response_world(
        chat_history, topic, get_synopsis(), user_message, get_characters()
    )

    # Clean the response
    ai_response = ai_response.strip()

    return jsonify({"message": ai_response})


@app.route("/world_chat_stream", methods=["POST"])
def world_chat_stream():
    """Handle ongoing chat for world building with streaming response"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    # Save topic to settings if available
    if topic:
        settings = get_settings()
        settings["topic"] = topic
        save_settings(settings)

    # Initialize agents for world building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate streaming response
    stream = book_agents.generate_chat_response_world_stream(
        chat_history, topic, get_synopsis(), user_message, get_characters()
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/finalize_world", methods=["POST"])
def finalize_world():
    """Finalize the world setting based on chat history"""
    data = request.json
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    # Initialize agents for world building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate the final world setting using the direct method
    world_theme = book_agents.generate_final_world(
        chat_history, topic, get_synopsis(), get_characters()
    )

    # Clean and save world theme to file
    world_theme = world_theme.strip()
    world_theme = re.sub(r"\n+", "\n", world_theme.strip())

    save_world_text(world_theme)

    return jsonify({"world_theme": world_theme})


@app.route("/finalize_world_stream", methods=["POST"])
def finalize_world_stream():
    """Finalize the world setting based on chat history with streaming response"""
    data = request.json
    chat_history = data.get("chat_history", [])
    topic = data.get("topic", "")

    # Initialize agents for world building
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(topic, 0)

    # Generate the final world setting using streaming
    stream = book_agents.generate_final_world_stream(
        chat_history, topic, get_synopsis(), get_characters()
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Collect all chunks to save the complete response
            collected_content = []

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    collected_content.append(content)
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Combine all chunks for the complete content
            complete_content = "".join(collected_content)

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/save_world", methods=["POST"])
def save_world():
    """Save edited world theme"""
    world_theme = request.form.get("world_theme")

    save_world_text(world_theme)

    return jsonify({"success": True})


@app.route("/delete_world", methods=["POST"])
def delete_world():
    """Delete the saved world setting so it can be recreated."""
    if os.path.exists(WORLD_FILE):
        os.remove(WORLD_FILE)
    if os.path.exists(WORLD_JSON_FILE):
        os.remove(WORLD_JSON_FILE)

    return jsonify({"success": True})


@app.route("/characters", methods=["GET"])
def characters():
    # Check if synopsis exist
    if not os.path.exists(SYNOPSIS_FILE):
        flash("You need to create a synopsis first.", "warning")
        return redirect("/synopsis")

    """Display characters or character creation chat interface"""
    # GET request - show characters page with existing characters if available
    characters_content = get_characters()

    # Load world theme from file
    world_theme = get_world_theme()
    synopsis = get_synopsis()

    # Get chapter list
    chapters = get_chapters()

    settings = get_settings()
    num_characters = settings.get("num_characters", 3)

    return render_template(
        "characters.html",
        characters=characters_content,
        characters_data=load_characters(),
        world_theme=world_theme,
        synopsis=synopsis,
        num_characters=num_characters,
        chapters=chapters,
    )


@app.route("/save_characters", methods=["POST"])
def save_characters():
    """Save edited characters"""
    characters_content = request.form.get("characters")

    save_characters_text(characters_content)

    return jsonify({"success": True})


@app.route("/delete_characters", methods=["POST"])
def delete_characters():
    """Delete the saved characters so they can be recreated."""
    if os.path.exists(CHARACTERS_FILE):
        os.remove(CHARACTERS_FILE)
    if os.path.exists(CHARACTERS_JSON_FILE):
        os.remove(CHARACTERS_JSON_FILE)

    return jsonify({"success": True})


@app.route("/api/world", methods=["GET", "PUT"])
def api_world():
    """Read or replace the structured world setting."""
    if request.method == "GET":
        return jsonify({"success": True, "world": load_world()})

    data = request.get_json(silent=True) or {}
    world = data.get("world", data)
    try:
        saved = save_world_json(world)
    except (ValueError, TypeError) as e:
        return jsonify({"success": False, "error": str(e)}), 400

    # Keep the legacy text mirror in sync for anything still reading it.
    with open(WORLD_FILE, "w", encoding="utf-8") as f:
        f.write(world_to_text(saved))

    return jsonify({"success": True, "world": saved})


@app.route("/api/characters", methods=["GET", "PUT"])
def api_characters():
    """Read or replace the structured character data."""
    if request.method == "GET":
        return jsonify({"success": True, "characters": load_characters()})

    data = request.get_json(silent=True) or {}
    characters = data.get("characters", data)
    if isinstance(characters, dict):
        payload = characters
    else:
        payload = {"characters": characters}
    try:
        saved = save_characters_json(payload)
    except (ValueError, TypeError) as e:
        return jsonify({"success": False, "error": str(e)}), 400

    with open(CHARACTERS_FILE, "w", encoding="utf-8") as f:
        f.write(characters_to_text(saved))

    return jsonify({"success": True, "characters": saved})


@app.route("/outline", methods=["GET", "POST"])
def outline():
    # Check if synopsis, world theme and characters exist
    if not os.path.exists(SYNOPSIS_FILE):
        flash("You need to create a synopsis first.", "warning")
        return redirect("/synopsis")

    if not get_world_theme():
        flash("You need a world setting before the outline.", "warning")
        return redirect("/world")

    if not get_characters():
        flash("You need characters before the outline.", "warning")
        return redirect("/characters")

    # Get world theme and characters
    world_theme = get_world_theme()
    characters = get_characters()

    with open(SYNOPSIS_FILE, "r") as f:
        synopsis = f.read()

    # GET request - just show the page
    outline_data = get_outline()

    # Get chapter list
    chapters = get_chapters()

    settings = get_settings()
    num_chapters = settings.get("num_chapters", 20)

    return render_template(
        "outline.html",
        world_theme=world_theme,
        characters=characters,
        synopsis=synopsis,
        outline=outline_data,
        chapters=chapters,
        num_chapters=num_chapters,
    )


@app.route("/chapters", methods=["GET"])
def chapters_list():
    # Check if synopsis exist
    if not os.path.exists(SYNOPSIS_FILE):
        flash("You need to create a synopsis first.", "warning")
        return redirect("/synopsis")

    """Display the list of chapters"""
    chapters = get_chapters()
    return render_template("chapters.html", chapters=chapters)


def _apply_outline(outline, allow_shrink_cleanup=True):
    """Persist an outline, rebuild chapters.json, and prune files if it shrank.

    Returns (chapters, saved_outline, error). ``error`` is set when the outline was
    rejected, in which case nothing on disk has been touched.

    ``allow_shrink_cleanup`` guards the destructive case: a shorter outline means
    the author removed chapters on purpose, so their files should go. It is turned
    off when the outline arrives from a source we cannot fully trust, so a partial
    or malformed result can never delete written chapters.
    """
    # Read the existing chapter numbers before rebuilding, otherwise this
    # compares the new structure against itself and never detects a shrink.
    previous_numbers = {c["chapter_number"] for c in get_chapters()}

    try:
        chapters = build_chapters_from_outline(outline)
    except OutlineValidationError as e:
        print(f"Rejected outline: {e}")
        return None, None, str(e)

    # Written first, so the chapter numbers the cleanup compares against are the
    # ones that were actually persisted.
    saved = save_outline_data(outline)

    if allow_shrink_cleanup and not previous_numbers.issubset(
        {c["chapter_number"] for c in chapters}
    ):
        print("Outline shrank; removing files for chapters that no longer exist")
        _cleanup_orphaned_chapter_files(chapters)

    return chapters, saved, None


@app.route("/generate_chapters", methods=["POST"])
def generate_chapters():
    """Build the chapter structure from the saved outline."""
    outline = get_outline()
    if outline is None:
        return jsonify(
            {
                "error": "No usable outline found. Please create an outline first.",
                "success": False,
            }
        )

    chapters, _saved, error = _apply_outline(outline)
    if error:
        return jsonify({"error": error, "success": False})

    return jsonify({"success": True, "num_chapters": len(chapters)})


@app.route("/save_outline", methods=["POST"])
def save_outline():
    """Save an edited outline and rebuild the chapter structure.

    Accepts either the structured outline as JSON (from the chapter editors) or a
    legacy markdown outline, which is parsed leniently.
    """
    payload = request.form.get("outline")
    if payload is None and request.is_json:
        payload = request.get_json(silent=True)

    if payload is None:
        return jsonify({"error": "No outline was submitted.", "success": False})

    # The editor posts JSON; a markdown draft arrives as a plain string.
    outline = payload
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped.startswith("{"):
            outline = stripped
        else:
            outline = parse_legacy_outline_markdown(stripped)

    if outline is None:
        return jsonify(
            {
                "error": (
                    "Could not read any chapters from that outline. It has not "
                    "been saved and your existing chapters are untouched."
                ),
                "success": False,
            }
        )

    chapters, _saved, error = _apply_outline(outline)
    if error:
        return jsonify({"error": error, "success": False})

    return jsonify({"success": True, "num_chapters": len(chapters)})


@app.route("/delete_outline", methods=["POST"])
def delete_outline():
    """Delete the saved outline so it can be recreated."""
    for path in (OUTLINE_JSON_FILE, OUTLINE_FILE):
        if os.path.exists(path):
            os.remove(path)

    # Delete the chapter structure and all per-chapter files
    if os.path.exists(CHAPTERS_JSON_FILE):
        os.remove(CHAPTERS_JSON_FILE)

    if os.path.exists(CHAPTERS_DIR):
        for name in os.listdir(CHAPTERS_DIR):
            path = os.path.join(CHAPTERS_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    # Remove per-chapter style settings
    settings = get_settings()
    if "chapters" in settings:
        settings.pop("chapters")
        save_settings(settings)

    return jsonify({"success": True})


@app.route("/chapter/<int:chapter_number>", methods=["GET", "POST"])
def chapter(chapter_number):
    """Generate or display a specific chapter"""

    # Retrieve all chapters to find the relevant one
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )

    # If chapter not found, render an error page
    if not chapter_data:
        return render_template(
            "error.html", message=f"Chapter {chapter_number} not found"
        )

    # Handle POST request for chapter generation (non-streaming)
    if request.method == "POST":
        # Get data from the form

        # Get any additional context from the chat interface
        additional_context = request.form.get("additional_context", "")

        master_prompt = request.form.get("master_prompt", "")
        point_of_view = request.form.get("point_of_view", "Third-person limited")
        tense = request.form.get("tense", "Past tense")

        # Save chapter-specific settings (point_of_view and tense)
        settings_to_save = get_settings()
        if "chapters" not in settings_to_save:
            settings_to_save["chapters"] = {}
        if str(chapter_number) not in settings_to_save["chapters"]:
            settings_to_save["chapters"][str(chapter_number)] = {}
        settings_to_save["chapters"][str(chapter_number)]["point_of_view"] = (
            point_of_view
        )
        settings_to_save["chapters"][str(chapter_number)]["tense"] = tense
        save_settings(settings_to_save)

        # Load foundational book data (respecting per-item context selection)
        world_theme = get_world_theme(include_all=False)
        characters = get_characters(include_all=False)

        # Get the previous chapter's outline as continuity context
        previous_chapter_outline = get_previous_chapter_outline(
            chapter_number, chapters
        )

        # Initialize agents for chapter generation
        book_agents = BookAgents(agent_config, get_synopsis())
        book_agents.create_agents(world_theme, len(chapters))

        # Combine base prompt with chat context
        chapter_prompt = (
            f"{chapter_data['prompt']}\n\n{additional_context}"
            if additional_context
            else chapter_data["prompt"]
        )

        # Generate the chapter content
        chapter_content = book_agents.generate_content(
            "writer",
            prompts.CHAPTER_GENERATION_PROMPT.format(
                master_prompt=master_prompt,
                chapter_number=chapter_number,
                chapter_title=chapter_data["title"],
                chapter_outline=chapter_prompt,
                world_theme=world_theme,
                relevant_characters=characters,  # Already filtered by include_in_context
                scene_details=get_scenes_details(chapter_number),
                previous_chapter_outline=previous_chapter_outline,
                point_of_view=point_of_view,
                tense=tense,
            ),
        )

        # Clean and save the generated content
        chapter_content = chapter_content.strip()
        chapter_path = os.path.join(
            CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
        )
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write(chapter_content)

        return jsonify({"chapter_content": chapter_content})

    # Handle GET request to display the chapter page
    # Load existing chapter content if it exists
    chapter_content = ""
    chapter_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
    )
    if os.path.exists(chapter_path):
        with open(chapter_path, "r", encoding="utf-8") as f:
            chapter_content = f.read().strip()

    # Load other necessary data for the template
    master_prompt = get_master_prompt()
    settings = get_settings()

    # Get chapter-specific settings or the book-wide default
    point_of_view, tense = _get_chapter_style(settings, chapter_number)

    # Get pagination data for chapter navigation
    chapters_paginated = get_paginated_chapters_from_request(
        request, chapters, chapter_number
    )

    # Render the chapter template with all the data
    return render_template(
        "chapter.html",
        chapter=chapter_data,
        chapter_content=chapter_content,
        chapters=chapters,
        chapters_paginated=chapters_paginated,
        master_prompt=master_prompt,
        point_of_view=point_of_view,
        tense=tense,
    )


def _handle_chapter_stream(chapter_number, agent_name):
    """A helper function to handle chapter stream generation for both writer and editor."""

    # Retrieve all chapters to find the relevant one
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )

    # Return a 404 error if the chapter is not found
    if not chapter_data:
        return Response(
            json.dumps({"error": f"Chapter {chapter_number} not found"}),
            status=404,
            mimetype="application/json",
        )

    # Parse incoming JSON data from the request
    data = request.json

    master_prompt = data.get("master_prompt", "")
    default_pov, default_tense = _get_chapter_style(get_settings(), chapter_number)
    point_of_view = data.get("point_of_view", default_pov)
    tense = data.get("tense", default_tense)
    show_prompt = data.get("show_prompt", False)
    chapter_content = data.get("chapter_content", "")  # For editor

    # Save chapter-specific settings (point_of_view and tense)
    settings_to_save = get_settings()
    if "chapters" not in settings_to_save:
        settings_to_save["chapters"] = {}
    if str(chapter_number) not in settings_to_save["chapters"]:
        settings_to_save["chapters"][str(chapter_number)] = {}
    settings_to_save["chapters"][str(chapter_number)]["point_of_view"] = point_of_view
    settings_to_save["chapters"][str(chapter_number)]["tense"] = tense
    save_settings(settings_to_save)

    # Load foundational book data (respecting per-item context selection)
    world_theme = get_world_theme(include_all=False)
    characters = get_characters(include_all=False)

    # Get the previous chapter's outline as continuity context
    previous_chapter_outline = get_previous_chapter_outline(chapter_number, chapters)

    # Initialize the book agents
    book_agents = BookAgents(agent_config, get_synopsis())
    book_agents.create_agents(world_theme, len(chapters))

    # The editing pass works from the saved required-changes list. The chat is
    # only for brainstorming that list, so no chat messages are injected here.
    if agent_name == "editor":
        required_changes = get_chapter_edits(chapter_number) or (
            "(No required changes have been listed for this chapter. "
            "Review and improve it for overall quality.)"
        )
    else:
        required_changes = ""

    # Select the appropriate prompt template based on the agent
    prompt_template = (
        prompts.CHAPTER_EDITING_PROMPT
        if agent_name == "editor"
        else prompts.CHAPTER_GENERATION_PROMPT
    )

    # Format the final user prompt with all the necessary context
    user_prompt = prompt_template.format(
        master_prompt=master_prompt,
        chapter_number=chapter_number,
        chapter_title=chapter_data["title"],
        chapter_outline=chapter_data["prompt"],
        world_theme=world_theme,
        relevant_characters=characters,  # Already filtered by include_in_context
        scene_details=get_scenes_details(chapter_number),
        previous_chapter_outline=previous_chapter_outline,
        point_of_view=point_of_view,
        tense=tense,
        chapter_content=chapter_content,  # Included for editor
        required_changes=required_changes,  # Included for editor
    )

    # If requested, return the full prompt for debugging instead of generating
    if show_prompt:
        system_prompt = book_agents.system_prompts.get(agent_name, "")
        return jsonify({"system_prompt": system_prompt, "user_prompt": user_prompt})

    # Generate the content stream from the selected agent
    stream = book_agents.generate_content_stream(agent_name, user_prompt)

    # Define the generator function for the streaming response
    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'
            collected_content = []
            # Process each chunk from the stream
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    collected_content.append(content)
                    # Yield each piece of content as a server-sent event
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Once streaming is complete, save the full content to a file.
            # The editor agent does not auto-save; the reviewed WIP is only
            # written to disk when the user explicitly saves it.
            if agent_name != "editor":
                complete_content = "".join(collected_content)
                chapter_path = os.path.join(
                    CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
                )
                with open(chapter_path, "w", encoding="utf-8") as f:
                    f.write(complete_content)

                # A freshly generated chapter is not reviewed yet.
                settings_to_save = get_settings()
                chapter_style = settings_to_save.get("chapters", {}).get(
                    str(chapter_number)
                )
                if chapter_style and chapter_style.get("reviewed"):
                    chapter_style.pop("reviewed", None)
                    save_settings(settings_to_save)

            # Send a final marker to indicate the end of the stream
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    # Return the streaming response
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/chapter_stream/<int:chapter_number>", methods=["POST"])
def chapter_stream(chapter_number):
    """Generate or display a specific chapter using the writer agent."""
    return _handle_chapter_stream(chapter_number, "writer")


@app.route("/chapter_editor/<int:chapter_number>", methods=["GET"])
def chapter_editor(chapter_number):
    """Generate or display a specific chapter for editing"""

    # Retrieve all chapters to find the relevant one
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )

    # If chapter not found, render an error page
    if not chapter_data:
        return render_template(
            "error.html", message=f"Chapter {chapter_number} not found"
        )

    # If the chapter has not been written yet, send the user to the author page
    if not chapter_data["has_content"]:
        flash(
            f"Chapter {chapter_number} has not been written yet. "
            "Write it before using the review editor.",
            "info",
        )
        return redirect(f"/chapter/{chapter_number}")

    settings = get_settings()

    # Get existing chapter content for editing
    chapter_file_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
    )
    original_chapter_content = ""
    if os.path.exists(chapter_file_path):
        with open(chapter_file_path, "r") as f:
            original_chapter_content = f.read()

    # Get editor review content if it exists
    editor_review_file_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}_editor{TEXT_EXTENSION}"
    )
    chapter_content = ""
    has_review = False
    if os.path.exists(editor_review_file_path):
        with open(editor_review_file_path, "r") as f:
            chapter_content = f.read()
        has_review = True

    # Get the saved required-changes list if it exists
    chapter_edits = get_chapter_edits(chapter_number)
    has_edits = os.path.exists(chapter_edits_path(chapter_number))

    master_prompt = get_master_prompt()
    settings = get_settings()

    # Get point of view and tense from settings or the book-wide default
    point_of_view, tense = _get_chapter_style(settings, chapter_number)

    # Get chapter navigation pagination
    chapters_paginated = get_paginated_chapters_from_request(
        request, chapters, chapter_number
    )

    # Find the nearest previous/next chapters that have content
    content_chapters = sorted(
        ch["chapter_number"] for ch in chapters if ch["has_content"]
    )
    prev_content_chapter = next(
        (n for n in reversed(content_chapters) if n < chapter_number), None
    )
    next_content_chapter = next(
        (n for n in content_chapters if n > chapter_number), None
    )

    # Render the chapter template with all the data
    return render_template(
        "chapter_editor.html",
        chapter=chapter_data,
        chapters=chapters,  # Pass the full chapters list for total count
        chapters_paginated=chapters_paginated,
        original_chapter_content=original_chapter_content,
        chapter_content=chapter_content,
        chapter_edits=chapter_edits,
        has_edits=has_edits,
        has_review=has_review,
        master_prompt=master_prompt,
        point_of_view=point_of_view,
        tense=tense,
        prev_content_chapter=prev_content_chapter,
        next_content_chapter=next_content_chapter,
    )


@app.route("/chapter_editor_stream/<int:chapter_number>", methods=["POST"])
def chapter_editor_stream(chapter_number):
    """Generate or display a specific chapter using the editor agent."""
    return _handle_chapter_stream(chapter_number, "editor")


def _chapter_editing_context(chapter_number):
    """Load a chapter plus the book context used by the editing chat.

    Returns (chapters, chapter_data, world_theme, characters); chapter_data is
    None when the chapter does not exist.
    """
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )
    return (
        chapters,
        chapter_data,
        get_world_theme(include_all=False),
        get_characters(include_all=False),
    )


def _chapter_not_found_response(chapter_number):
    return Response(
        json.dumps({"error": f"Chapter {chapter_number} not found"}),
        status=404,
        mimetype="application/json",
    )


@app.route("/chapter_edits_chat_stream/<int:chapter_number>", methods=["POST"])
def chapter_edits_chat_stream(chapter_number):
    """Brainstorm improvements for a chapter in a streaming chat."""
    chapters, chapter_data, world_theme, characters = _chapter_editing_context(
        chapter_number
    )
    if chapter_data is None:
        return _chapter_not_found_response(chapter_number)

    data = request.json or {}
    chat_history = data.get("chat_history", [])
    user_message = data.get("message", "")
    chapter_content = data.get("chapter_content", "")

    book_agents = BookAgents(agent_config, get_synopsis())
    book_agents.create_agents(world_theme, len(chapters))

    stream = book_agents.generate_chat_response_editor_stream(
        chat_history,
        chapter_data["prompt"],
        chapter_content,
        world_theme,
        characters,
        user_message,
    )
    return _sse_response(stream)


@app.route("/finalize_chapter_edits_stream/<int:chapter_number>", methods=["POST"])
def finalize_chapter_edits_stream(chapter_number):
    """Turn the editing brainstorm chat into a final list of required changes."""
    chapters, chapter_data, world_theme, characters = _chapter_editing_context(
        chapter_number
    )
    if chapter_data is None:
        return _chapter_not_found_response(chapter_number)

    data = request.json or {}
    chat_history = data.get("chat_history", [])
    if not chat_history:
        return jsonify(
            {
                "error": "Chat history is empty. Please chat with the AI first to brainstorm changes."
            }
        ), 400

    chapter_content = data.get("chapter_content", "")

    book_agents = BookAgents(agent_config, get_synopsis())
    book_agents.create_agents(world_theme, len(chapters))

    stream = book_agents.generate_final_edits_stream(
        chat_history,
        chapter_data["prompt"],
        chapter_content,
        world_theme,
        characters,
    )
    return _sse_response(stream)


@app.route("/inline_llm_continue_stream", methods=["POST"])
def inline_llm_continue_stream():
    """Get a streaming response from the LLM based on the provided context."""
    data = request.json
    context = data.get("context", "")
    user_prompt = data.get("user_prompt", "")
    chapter_outline = data.get("chapter_outline", "")

    if not context:
        return Response(
            json.dumps({"error": "No context provided"}),
            status=400,
            mimetype="application/json",
        )

    # master_prompt = get_master_prompt()

    book_agents = BookAgents(agent_config)
    book_agents.create_agents("", 0)  # No initial prompt or chapters needed

    stream = book_agents.generate_content_stream(
        "inline_continuer",
        prompts.INLINE_CONTINUE_PROMPT.format(
            context=context,
            user_input=user_prompt,
            chapter_outline=chapter_outline,
        ),
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/inline_llm_revise_stream", methods=["POST"])
def inline_llm_revise_stream():
    """Get a streaming response from the LLM based on the provided context."""
    data = request.json
    context = data.get("context", "")
    chapter_outline = data.get("chapter_outline", "")
    user_prompt = data.get("user_prompt", "")

    if not context:
        return Response(
            json.dumps({"error": "No context provided"}),
            status=400,
            mimetype="application/json",
        )

    # master_prompt = get_master_prompt()

    book_agents = BookAgents(agent_config)
    book_agents.create_agents("", 0)  # No initial prompt or chapters needed

    stream = book_agents.generate_content_stream(
        "inline_reviser",
        prompts.INLINE_REVISE_PROMPT.format(
            context=context,
            user_input=user_prompt,
            chapter_outline=chapter_outline,
        ),
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/save_chapter/<int:chapter_number>", methods=["POST"])
def save_chapter(chapter_number):
    """Save edited chapter content"""
    chapter_content = request.form.get("chapter_content")

    # Strip extra newlines at the beginning and normalize newlines
    chapter_content = chapter_content.strip()

    chapter_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
    )
    with open(chapter_path, "w") as f:
        f.write(chapter_content)

    return jsonify({"success": True})


@app.route("/save_chapter_editor/<int:chapter_number>", methods=["POST"])
def save_chapter_editor(chapter_number):
    """Save edited chapter content (editor version)"""
    chapter_content = request.form.get("chapter_content")

    # Strip extra newlines at the beginning and normalize newlines
    chapter_content = chapter_content.strip()

    chapter_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}_editor{TEXT_EXTENSION}"
    )
    with open(chapter_path, "w") as f:
        f.write(chapter_content)

    return jsonify({"success": True})


@app.route("/save_chapter_edits/<int:chapter_number>", methods=["POST"])
def save_chapter_edits(chapter_number):
    """Save the edited required-changes list for a chapter."""
    chapter_edits = (request.form.get("chapter_edits") or "").strip()

    with open(chapter_edits_path(chapter_number), "w", encoding="utf-8") as f:
        f.write(chapter_edits)

    return jsonify({"success": True})


@app.route("/delete_chapter_edits/<int:chapter_number>", methods=["POST"])
def delete_chapter_edits(chapter_number):
    """Delete the saved required-changes list so it can be regenerated."""
    path = chapter_edits_path(chapter_number)
    if os.path.exists(path):
        os.remove(path)

    return jsonify({"success": True})


@app.route("/chapter_overwrite/<int:chapter_number>", methods=["POST"])
def chapter_overwrite(chapter_number):
    """Overwrite the canonical chapter with the given content and clear the WIP."""
    chapter_content = request.form.get("chapter_content")

    # Strip extra newlines at the beginning and normalize newlines
    chapter_content = chapter_content.strip()

    chapter_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
    )
    with open(chapter_path, "w") as f:
        f.write(chapter_content)

    # The promoted draft becomes the canonical version; remove the old WIP.
    wip_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}_editor{TEXT_EXTENSION}"
    )
    if os.path.exists(wip_path):
        os.remove(wip_path)

    # Record that this chapter has been reviewed (promoted) so the Reviewed
    # status survives the WIP file being removed.
    settings = get_settings()
    settings.setdefault("chapters", {}).setdefault(str(chapter_number), {})[
        "reviewed"
    ] = True
    save_settings(settings)

    return jsonify({"success": True})


@app.route("/delete_chapter_editor/<int:chapter_number>", methods=["POST"])
def delete_chapter_editor(chapter_number):
    """Delete the saved WIP review so it can be recreated."""
    wip_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}_editor{TEXT_EXTENSION}"
    )
    if os.path.exists(wip_path):
        os.remove(wip_path)

    return jsonify({"success": True})


@app.route("/delete_chapter/<int:chapter_number>", methods=["POST"])
def delete_chapter(chapter_number):
    """Delete the canonical chapter content (and its WIP) so it can be regenerated."""
    chapter_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
    )
    if os.path.exists(chapter_path):
        os.remove(chapter_path)

    wip_path = os.path.join(
        CHAPTERS_DIR, f"chapter_{chapter_number}_editor{TEXT_EXTENSION}"
    )
    if os.path.exists(wip_path):
        os.remove(wip_path)

    # The chapter content is gone, so it can no longer be considered reviewed.
    settings = get_settings()
    chapter_style = settings.get("chapters", {}).get(str(chapter_number))
    if chapter_style and chapter_style.get("reviewed"):
        chapter_style.pop("reviewed", None)
        save_settings(settings)

    return jsonify({"success": True})


@app.route("/save_master_prompt", methods=["POST"])
def save_master_prompt():
    """Save the master prompt to a file."""
    master_prompt = request.form.get("master_prompt", "")
    with open(MASTER_PROMPT_FILE, "w") as f:
        f.write(master_prompt)
    return jsonify({"success": True})


@app.route("/save_chapter_style/<int:chapter_number>", methods=["POST"])
def save_chapter_style(chapter_number):
    """Save chapter-specific style settings (point_of_view and tense)."""
    data = request.json
    point_of_view = data.get("point_of_view")
    tense = data.get("tense")

    settings = get_settings()
    if "chapters" not in settings:
        settings["chapters"] = {}
    if str(chapter_number) not in settings["chapters"]:
        settings["chapters"][str(chapter_number)] = {}

    if point_of_view is not None:
        settings["chapters"][str(chapter_number)]["point_of_view"] = point_of_view
        settings.setdefault("default_style", {})["point_of_view"] = point_of_view
    if tense is not None:
        settings["chapters"][str(chapter_number)]["tense"] = tense
        settings.setdefault("default_style", {})["tense"] = tense

    save_settings(settings)
    return jsonify({"success": True})


@app.route("/save_setting", methods=["POST"])
def save_setting():
    """Save a specific setting value."""
    data = request.json
    key = data.get("key")
    value = data.get("value")

    if not key or value is None:
        return jsonify({"error": "Key or value missing"}), 400

    settings = get_settings()
    settings[key] = value
    save_settings(settings)

    return jsonify({"success": True})


@app.route("/scene/<int:chapter_number>", methods=["GET", "POST"])
def scene(chapter_number):
    """Generate a scene for a specific chapter"""

    # Retrieve all chapters to find the relevant one
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )

    # If chapter not found, render an error page
    if not chapter_data:
        print(f"Chapter {chapter_number} not found in loaded data")
        # Try alternate approaches to find the chapter

        # Approach 1: Direct file check
        chapter_path = os.path.join(
            CHAPTERS_DIR, f"chapter_{chapter_number}{TEXT_EXTENSION}"
        )
        if os.path.exists(chapter_path):
            # Chapter exists but data isn't in memory
            chapter_data = {
                "chapter_number": chapter_number,
                "title": f"Chapter {chapter_number}",
                "prompt": "Chapter content from file",
            }
            print("Found chapter file, creating basic chapter data")
        else:
            # Approach 2: Create stub data if no chapters exist yet
            chapter_data = {
                "chapter_number": chapter_number,
                "title": f"Chapter {chapter_number}",
                "prompt": "No chapter outline available",
            }
            print("Creating stub chapter data")

    # Handle POST request for scene generation
    if request.method == "POST":
        # scene description is not used in this context, but can be added if needed
        # scene_description = request.form.get("scene_description", "")

        # Generate the scene
        world_theme = get_world_theme(include_all=False)
        characters = get_characters(include_all=False)

        # Get the previous chapter's outline as continuity context
        previous_chapter_outline = get_previous_chapter_outline(
            chapter_number, chapters
        )

        # Initialize agents
        book_agents = BookAgents(agent_config, get_synopsis())
        book_agents.create_agents(world_theme, len(chapters) if chapters else 1)

        # Generate the scene
        scene_content = book_agents.generate_content(
            "writer",
            prompts.SCENE_GENERATION_PROMPT.format(
                chapter_number=chapter_number,
                chapter_title=chapter_data.get("title", f"Chapter {chapter_number}"),
                chapter_outline=chapter_data.get("prompt", ""),
                world_theme=world_theme,
                relevant_characters=characters,  # Already filtered by include_in_context
                previous_chapter_outline=previous_chapter_outline,
            ),
        )

        # Save scene to a file
        scene_dir = os.path.join(CHAPTERS_DIR, f"chapter_{chapter_number}_scenes")
        os.makedirs(scene_dir, exist_ok=True)

        # Count existing scenes and create a new one
        scene_count = len(
            [f for f in os.listdir(scene_dir) if f.endswith(TEXT_EXTENSION)]
        )
        scene_path = os.path.join(scene_dir, f"scene_{scene_count + 1}{TEXT_EXTENSION}")

        with open(scene_path, "w") as f:
            f.write(scene_content)

        return jsonify({"scene_content": scene_content})

    # GET request - load existing scenes for this chapter
    scenes = []
    scene_dir = os.path.join(CHAPTERS_DIR, f"chapter_{chapter_number}_scenes")

    if os.path.exists(scene_dir):
        scene_files = [f for f in os.listdir(scene_dir) if f.endswith(TEXT_EXTENSION)]
        scene_files.sort(
            key=lambda f: int(f.split("_")[1].split(".")[0])
        )  # Sort by scene number

        for scene_file in scene_files:
            scene_path = os.path.join(scene_dir, scene_file)
            scene_number = int(scene_file.split("_")[1].split(".")[0])

            with open(scene_path, "r") as f:
                content = f.read()

                # Extract a title from the first line or first few words
                lines = content.split("\n")
                if lines:
                    title = lines[0][:30] + "..." if len(lines[0]) > 30 else lines[0]
                else:
                    title = f"Scene {scene_number}"

                scenes.append(
                    {"number": scene_number, "title": title, "content": content}
                )

    # Return the template with loaded scenes
    return render_template("scene.html", chapter=chapter_data, scenes=scenes)


@app.route("/characters_chat", methods=["POST"])
def characters_chat():
    """Handle ongoing chat for character creation"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    num_characters = data.get("num_characters", 3)
    world_theme = get_world_theme()

    # Initialize agents for character creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, 0)

    # Generate response using the direct chat method
    ai_response = book_agents.generate_chat_response_characters(
        chat_history, world_theme, get_synopsis(), user_message, num_characters
    )

    # Clean the response
    ai_response = ai_response.strip()

    return jsonify({"message": ai_response})


@app.route("/characters_chat_stream", methods=["POST"])
def characters_chat_stream():
    """Handle ongoing chat for character creation with streaming response"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    num_characters = data.get("num_characters", 3)
    world_theme = get_world_theme()

    # Initialize agents for character creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, 0)

    # Generate streaming response
    stream = book_agents.generate_chat_response_characters_stream(
        chat_history, world_theme, get_synopsis(), user_message, num_characters
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/finalize_characters_stream", methods=["POST"])
def finalize_characters_stream():
    """Finalize the characters based on chat history with streaming response"""
    data = request.json
    chat_history = data.get("chat_history", [])
    num_characters = data.get("num_characters", 3)
    world_theme = get_world_theme()

    # Initialize agents for character creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, 0)

    # Generate the final characters using streaming
    stream = book_agents.generate_final_characters_stream(
        chat_history, world_theme, get_synopsis(), num_characters
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Collect all chunks to save the complete response
            collected_content = []

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    collected_content.append(content)
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Combine all chunks for the complete content
            complete_content = "".join(collected_content)

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/outline_chat", methods=["POST"])
def outline_chat():
    """Handle ongoing chat for outline creation"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    num_chapters = data.get("num_chapters", 10)

    # Get world_theme, characters and synopsis for context
    world_theme = get_world_theme()
    characters = get_characters()
    synopsis = get_synopsis()

    # Ensure we have world and characters
    if not world_theme or not characters or not synopsis:
        return jsonify(
            {
                "error": "World theme, characters, or synopsis not found. Please complete previous steps first."
            }
        )

    # Initialize agents for outline creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, num_chapters)

    # Generate response using the direct chat method
    ai_response = book_agents.generate_chat_response_outline(
        chat_history, world_theme, characters, synopsis, user_message
    )

    # Clean the response
    ai_response = ai_response.strip()

    return jsonify({"message": ai_response})


@app.route("/outline_chat_stream", methods=["POST"])
def outline_chat_stream():
    """Handle ongoing chat for outline creation with streaming response"""
    data = request.json
    user_message = data.get("message", "")
    chat_history = data.get("chat_history", [])
    num_chapters = data.get("num_chapters", 10)

    # Get world_theme, characters and synopsis for context
    world_theme = get_world_theme()
    characters = get_characters()
    synopsis = get_synopsis()

    # Ensure we have world and characters
    if not world_theme or not characters or not synopsis:
        return jsonify(
            {
                "error": "World theme, characters, or synopsis not found. Please complete previous steps first."
            }
        )

    # Initialize agents for outline creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, num_chapters)

    # Generate streaming response
    stream = book_agents.generate_chat_response_outline_stream(
        chat_history, world_theme, characters, synopsis, user_message
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if (
                    chunk.choices
                    and len(chunk.choices) > 0
                    and chunk.choices[0].delta
                    and chunk.choices[0].delta.content is not None
                ):
                    content = chunk.choices[0].delta.content
                    # Send each token as it arrives
                    yield f"data: {json.dumps({'content': content})}\n\n"

            # Send completion marker
            yield f"data: {json.dumps({'content': '[DONE]'})}\n\n"

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/finalize_outline_stream", methods=["POST"])
def finalize_outline_stream():
    """Finalize the outline based on chat history with streaming response"""
    data = request.json
    chat_history = data.get("chat_history", [])
    num_chapters = data.get("num_chapters", 10)

    # Get world_theme, characters and synopsis for context
    world_theme = get_world_theme()
    characters = get_characters()
    synopsis = get_synopsis()

    # Ensure we have world and characters
    if not world_theme or not characters or not synopsis:
        return jsonify(
            {
                "error": "World theme, characters, or synopsis not found. Please complete previous steps first."
            }
        )

    # Initialize agents for outline creation
    book_agents = BookAgents(agent_config)
    book_agents.create_agents(world_theme, num_chapters)

    # Generate the final outline using streaming
    stream = book_agents.generate_final_outline_stream(
        chat_history, world_theme, characters, synopsis, num_chapters
    )

    def generate():
        try:
            # Send a heartbeat to establish the connection
            yield 'data: {"content": ""}\n\n'

            collected_content = []
            finish_reason = None

            # Iterate through the stream to get each chunk
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                if choice.delta and choice.delta.content is not None:
                    collected_content.append(choice.delta.content)

            complete_content = "".join(collected_content)

            # A response cut off mid-object cannot be parsed, and half an outline
            # is worse than none: report it instead of storing a truncated one.
            if finish_reason == "length":
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "error": (
                                "The outline was too long to complete and was "
                                "cut off. Try asking for fewer chapters, or "
                                "raise MAX_TOKENS in your .env."
                            )
                        }
                    )
                    + "\n\n"
                )
                yield 'data: {"content": "[DONE]"}\n\n'
                return

            # The model is constrained to the outline schema, but an endpoint
            # without structured output support returns the markdown fallback.
            # Validate before showing anything so the editor never opens on a
            # broken outline.
            try:
                outline = normalize_outline(complete_content)
            except OutlineValidationError:
                outline = parse_legacy_outline_markdown(complete_content)

            if outline is None:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "error": (
                                "The model did not return a usable outline. Try "
                                "regenerating, or set STRUCTURED_OUTPUT=true in "
                                "your .env if your endpoint supports it."
                            )
                        }
                    )
                    + "\n\n"
                )
                yield 'data: {"content": "[DONE]"}\n\n'
                return

            save_outline_data(outline)

            # Structured output can't be rendered as it arrives, so send the
            # finished outline in one piece. The client contract is unchanged.
            yield f"data: {json.dumps({'content': render_outline_markdown(outline)})}\n\n"

            # Send completion marker
            yield 'data: {"content": "[DONE]"}\n\n'

        finally:
            _close_stream(stream)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _cleanup_orphaned_chapter_files(chapters):
    """Remove per-chapter files, scene dirs, and style settings for chapters that no longer exist."""
    if not os.path.exists(CHAPTERS_DIR):
        return

    valid_numbers = {ch["chapter_number"] for ch in chapters}

    # Remove orphaned chapter files and scene directories
    for name in os.listdir(CHAPTERS_DIR):
        match = re.match(r"chapter_(\d+)(_editor)?\.txt$", name)
        if match and int(match.group(1)) not in valid_numbers:
            path = os.path.join(CHAPTERS_DIR, name)
            if os.path.isfile(path):
                os.remove(path)
            continue

        match = re.match(r"chapter_(\d+)_scenes$", name)
        if match and int(match.group(1)) not in valid_numbers:
            path = os.path.join(CHAPTERS_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path)

    # Remove per-chapter style settings for orphaned chapters
    settings = get_settings()
    chapter_settings = settings.get("chapters")
    if chapter_settings:
        for key in list(chapter_settings.keys()):
            if key.isdigit() and int(key) not in valid_numbers:
                del chapter_settings[key]
        if chapter_settings:
            settings["chapters"] = chapter_settings
        else:
            settings.pop("chapters", None)
        save_settings(settings)


def build_chapters_from_outline(outline):
    """Project the structured outline onto chapters.json and persist it.

    Raises OutlineValidationError when the outline has no usable chapters. The
    caller must treat that as a refusal to continue rather than substituting
    placeholder chapters: the previous behaviour wrote "Content for chapter N"
    entries and then deleted the author's real chapter files as orphans.
    """
    chapters = outline_to_chapters(outline)

    with open(CHAPTERS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(chapters, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(chapters)} chapters from the outline")
    return chapters


# API routes
@app.route("/api/chapters", methods=["GET"])
def api_chapters():
    """API endpoint to get all chapters."""
    all_chapters = get_chapters()

    return jsonify(all_chapters)


@app.route("/api/outline", methods=["GET"])
def api_outline():
    """Return the structured outline, or null when there isn't one."""
    outline = get_outline()
    return jsonify({"outline": outline, "success": True})


@app.route("/api/outline", methods=["PUT"])
def api_save_outline():
    """Replace the whole outline and rebuild the chapter structure."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON outline object.", "success": False})

    chapters, saved, error = _apply_outline(payload)
    if error:
        return jsonify({"error": error, "success": False})

    # Return the normalized outline so the client picks up server-side
    # corrections such as renumbering.
    return jsonify({"success": True, "num_chapters": len(chapters), "outline": saved})


@app.route("/api/outline/chapter/<int:chapter_number>", methods=["PUT"])
def api_save_outline_chapter(chapter_number):
    """Update one chapter's outline fields, leaving the rest untouched.

    The chapter's fields are edited as text in the UI and converted back into the
    structured shape here, so the client never has to build outline JSON itself.
    """
    outline = get_outline()
    if outline is None:
        return jsonify({"error": "No outline to update.", "success": False})

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected a JSON object of fields.", "success": False})

    # Accept both {"fields": {...}} and a bare {...}.
    fields = body.get("fields") if isinstance(body.get("fields"), dict) else body
    if not isinstance(fields, dict):
        return jsonify({"error": "Expected a JSON object of fields.", "success": False})

    index = chapter_number - 1
    if index < 0 or index >= len(outline["chapters"]):
        return jsonify(
            {
                "error": f"Chapter {chapter_number} is not in the outline.",
                "success": False,
            }
        )

    chapter = outline["chapters"][index]
    for field in (
        "title",
        "title_local",
        "character_developments",
        "setting",
        "tone",
    ):
        if field in fields:
            value = fields[field]
            chapter[field] = (
                value.strip() if isinstance(value, str) and value.strip() else None
            )

    if "key_events" in fields:
        events = fields["key_events"]
        if not isinstance(events, list):
            return jsonify(
                {"error": "'key_events' must be a list of strings.", "success": False}
            )
        chapter["key_events"] = [
            e.strip() for e in events if isinstance(e, str) and e.strip()
        ]

    if "act_number" in fields:
        chapter["act_number"] = fields["act_number"]

    # Editing one chapter cannot shrink the outline, so never prune files here.
    chapters, saved, error = _apply_outline(outline, allow_shrink_cleanup=False)
    if error:
        return jsonify({"error": error, "success": False})

    return jsonify(
        {
            "success": True,
            "num_chapters": len(chapters),
            # From the saved copy, so the client sees the normalized chapter.
            "chapter": saved["chapters"][index],
        }
    )


@app.route("/api/paginated_chapters", methods=["GET"])
def api_paginated_chapters():
    """API endpoint to get paginated chapters."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    data = get_paginated_chapters(page, per_page)

    return jsonify(
        {
            "chapters": data["chapters"],
            "total_pages": data["total_pages"],
            "current_page": data["current_page"],
            "total_chapters": data["total_chapters"],
        }
    )


@app.route("/api/chapter/<int:chapter_number>", methods=["GET"])
def api_chapter(chapter_number):
    """API endpoint to get a specific chapter by number."""

    # Retrieve all chapters to find the relevant one
    chapters = get_chapters()
    chapter_data = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )

    # Return a 404 error if the chapter is not found
    if not chapter_data:
        return Response(
            json.dumps({"error": f"Chapter {chapter_number} not found"}),
            status=404,
            mimetype="application/json",
        )

    return jsonify(chapter_data)


def _now_iso():
    from datetime import timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_characters_text_to_json(text):
    """Parse character profiles text into structured JSON.

    Expects format like:
    CHARACTER_PROFILES:
    [CHARACTER NAME]:
    - Field: value
    (or **NAME:** style)
    """
    if not text or not text.strip():
        return {
            "version": 1,
            "generated_at": _now_iso(),
            "characters": [],
            "raw_text": "",
        }

    raw = text.strip()
    characters = []
    # Remove leading "CHARACTER_PROFILES:" header if present
    content = raw
    content = re.sub(
        r"^\s*\*{0,2}CHARACTER[_\s]?PROFILES\*{0,2}:\s*\n?",
        "",
        content,
        flags=re.IGNORECASE,
    )

    # Split by character headers: **NAME:** or NAME: at start of line (possibly with asterisks)
    # Also handle [CHARACTER NAME] style in other parts? try to find blocks
    # Primary: lines like **LEO:** or **TIA:**
    char_pattern = re.compile(r"^\s*\*{0,2}([A-Z][A-Z0-9_]+)\*{0,2}:\s*", re.MULTILINE)
    matches = list(char_pattern.finditer(content))
    if matches:
        for i, m in enumerate(matches):
            name = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            block = content[start:end].strip()
            char = _extract_character_fields(name, block)
            if char:
                characters.append(char)
        if characters:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "characters": characters,
                "raw_text": raw,
            }

    # Fallback: split by blank lines followed by ALL CAPS name or title case
    # try to be more lenient
    alt_pattern = re.compile(r"^\s*\*{0,2}([^\n:]{2,40})\*{0,2}:\s*$", re.MULTILINE)
    matches = list(alt_pattern.finditer(content))
    # filter to likely names (not long descriptions)
    filtered = []
    for m in matches:
        name = m.group(1).strip()
        if len(name) < 50 and not re.search(
            r"Field|Role|Age|Appearance|Personality", name, re.I
        ):
            filtered.append(m)
    if filtered and len(filtered) >= 1:
        for i, m in enumerate(filtered):
            name = m.group(1).strip()
            start = m.end()
            end = filtered[i + 1].start() if i + 1 < len(filtered) else len(content)
            block = content[start:end].strip()
            char = _extract_character_fields(name, block)
            if char:
                characters.append(char)
        if characters:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "characters": characters,
                "raw_text": raw,
            }

    # If nothing parsed, store as single "unknown" character with full block as raw info?
    # but better to return empty list with raw_text preserved
    return {
        "version": 1,
        "generated_at": _now_iso(),
        "characters": characters,
        "raw_text": raw,
    }


def _extract_character_fields(name, block):
    char = {
        "id": f"char_{name.lower().replace(' ', '_').replace('-', '_')[:30]}",
        "name": name,
        "role": "",
        "age_species": "",
        "appearance": "",
        "personality": "",
        "goal_motivation": "",
        "background": "",
        "flaw": "",
        "skills": "",
        "relationships": "",
        "arc": "",
        "misc": "",
        "notes": "",
        "include_in_context": True,
    }
    # Map common field names
    field_map = [
        (r"role", "role"),
        (r"age[\s/]*species|age|species", "age_species"),
        (r"physical description|appearance", "appearance"),
        (r"personality", "personality"),
        (r"goals?/?motivations?|goal|motivation", "goal_motivation"),
        (r"background", "background"),
        (r"flaw", "flaw"),
        (r"skills?", "skills"),
        (r"relationships?", "relationships"),
        (r"arc", "arc"),
        (r"misc(?:ellaneous)?", "misc"),
        (r"notes?", "notes"),
    ]
    # Try bullet format: - **Field:** value or - Field: value
    # Split by lines starting with - or *
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # remove leading bullets
        line_clean = re.sub(r"^\s*[\-\*\+]\s*", "", line)
        if ":" not in line_clean and not line_clean.startswith("**"):
            continue
        # try to extract field name and value
        # handle **Field:** style
        m = re.match(r"\*{0,2}([^:]+?)\*{0,2}:\s*(.*)", line_clean)
        if m:
            fname = m.group(1).strip()
            fval = m.group(2).strip()
            # clean leading/trailing asterisks/spaces
            fval = re.sub(r"^\*{0,2}\s*", "", fval)
            fval = re.sub(r"\s*\*{0,2}$", "", fval)
            fl = fname.lower()
            for pat, key in field_map:
                if re.search(pat, fl):
                    # if key already has content and it's long, maybe append? but simple
                    if not char.get(key):
                        char[key] = fval
                    else:
                        char[key] = (
                            char[key] + " " + fval
                            if len(char[key]) < 2000
                            else char[key]
                        )  # keep reasonable
                    break
    # If nothing mapped, put whole block in misc
    if all(
        not v
        for k, v in char.items()
        if k not in ("id", "name", "include_in_context", "notes", "misc")
    ):
        char["misc"] = block
    return char


def _world_section_id_for(title, index=None):
    """Best-effort mapping of a section title to a canonical world section id."""
    tlow = (title or "").lower()
    keyword_map = [
        ("time_period_and_setting", ["time period", "setting"]),
        ("major_locations", ["location", "place", "geography", "region"]),
        ("cultural_historical_elements", ["cultur", "histor"]),
        ("technology_magical_elements", ["technolog", "magic", "weave"]),
        ("social_political_structures", ["social", "politic", "faction"]),
        ("environment_and_atmosphere", ["environment", "atmosphere"]),
    ]
    for sid, keywords in keyword_map:
        if any(keyword in tlow for keyword in keywords):
            return sid
    if index is not None and 0 <= index < len(WORLD_SECTION_IDS):
        return WORLD_SECTION_IDS[index]
    return "other"


def parse_world_text_to_json(text):
    """Parse worldbuilding text into structured JSON."""
    if not text or not text.strip():
        return {
            "version": 1,
            "generated_at": _now_iso(),
            "sections": [],
            "full_text": "",
        }

    text_stripped = text.strip()
    sections = []

    numbered_pattern = re.compile(
        r"^\s*(\d+)\.\s+(.+?)(?:\n|$)", re.MULTILINE | re.IGNORECASE
    )
    matches = list(numbered_pattern.finditer(text_stripped))

    if matches:
        for i, m in enumerate(matches):
            sec_num = int(m.group(1))
            title = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text_stripped)
            content = text_stripped[start:end].strip()
            content = re.sub(r"^[\s\-:]*\n?", "", content)
            sid = _world_section_id_for(title, sec_num - 1)
            sections.append(
                {
                    "id": sid,
                    "title": title,
                    "content": content,
                    "include_in_context": True,
                }
            )
        if sections:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "sections": sections,
                "full_text": text_stripped,
            }

    header_pattern = re.compile(r"^#{2,3}\s+(.+?)$", re.MULTILINE)
    hmatches = list(header_pattern.finditer(text_stripped))
    if hmatches:
        for i, m in enumerate(hmatches):
            title_raw = m.group(1).strip()
            title = re.sub(r"^\*+", "", title_raw).strip()
            title = re.sub(r"\*+$", "", title).strip()
            start = m.end()
            end = (
                hmatches[i + 1].start() if i + 1 < len(hmatches) else len(text_stripped)
            )
            content = text_stripped[start:end].strip()
            sid = _world_section_id_for(title)
            sections.append(
                {
                    "id": sid,
                    "title": title,
                    "content": content,
                    "include_in_context": True,
                }
            )
        if sections:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "sections": sections,
                "full_text": text_stripped,
            }

    bold_pattern = re.compile(r"^\s*\*\*(.+?)\*\*\s*$", re.MULTILINE)
    bmatches = list(bold_pattern.finditer(text_stripped))
    if bmatches and len(bmatches) >= 2:
        for i, m in enumerate(bmatches):
            title = m.group(1).strip()
            start = m.end()
            end = (
                bmatches[i + 1].start() if i + 1 < len(bmatches) else len(text_stripped)
            )
            content = text_stripped[start:end].strip()
            sid = _world_section_id_for(title)
            sections.append(
                {
                    "id": sid,
                    "title": title,
                    "content": content,
                    "include_in_context": True,
                }
            )
        if sections:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "sections": sections,
                "full_text": text_stripped,
            }

    # Try splitting by top-level world sections like ### **LEO'S WORLD** or ### **ELYSIA**
    h3_pattern = re.compile(r"^###\s+(.+?)$", re.MULTILINE)
    h3m = list(h3_pattern.finditer(text_stripped))
    if h3m:
        for i, m in enumerate(h3m):
            title_raw = m.group(1).strip()
            title = re.sub(r"^\*+", "", title_raw).strip()
            title = re.sub(r"\*+$", "", title).strip()
            start = m.end()
            end = h3m[i + 1].start() if i + 1 < len(h3m) else len(text_stripped)
            content = text_stripped[start:end].strip()
            sid = _world_section_id_for(title)
            sections.append(
                {
                    "id": sid,
                    "title": title,
                    "content": content,
                    "include_in_context": True,
                }
            )
        if sections:
            return {
                "version": 1,
                "generated_at": _now_iso(),
                "sections": sections,
                "full_text": text_stripped,
            }
    sections = [
        {
            "id": "other",
            "title": "World Setting",
            "content": text_stripped,
            "include_in_context": True,
        }
    ]
    return {
        "version": 1,
        "generated_at": _now_iso(),
        "sections": sections,
        "full_text": text_stripped,
    }


# --- Structured world/characters JSON I/O ---

WORLD_SECTION_IDS = [
    "time_period_and_setting",
    "major_locations",
    "cultural_historical_elements",
    "technology_magical_elements",
    "social_political_structures",
    "environment_and_atmosphere",
    "other",
]

CHARACTER_FIELD_KEYS = [
    "role",
    "age_species",
    "appearance",
    "personality",
    "goal_motivation",
    "background",
    "flaw",
    "skills",
    "relationships",
    "arc",
    "misc",
    "notes",
]


def _empty_world():
    return {
        "version": 1,
        "generated_at": _now_iso(),
        "sections": [],
        "full_text": "",
    }


def _normalize_world(world):
    """Coerce a world payload into the canonical structured shape."""
    if isinstance(world, str):
        return parse_world_text_to_json(world)
    if not isinstance(world, dict):
        return parse_world_text_to_json("")

    sections = world.get("sections")
    full_text = world.get("full_text", "") or ""
    if not isinstance(sections, list):
        sections = []
    if not sections and full_text.strip():
        parsed = parse_world_text_to_json(world.get("full_text", ""))
        sections = parsed.get("sections", [])

    normalized_sections = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        sid = (
            section.get("id")
            or WORLD_SECTION_IDS[min(index, len(WORLD_SECTION_IDS) - 1)]
        )
        title = section.get("title") or sid.replace("_", " ").title()
        content = section.get("content") or ""
        include = section.get("include_in_context", True)
        normalized_sections.append(
            {
                "id": str(sid),
                "title": str(title),
                "content": "" if content is None else str(content),
                "include_in_context": bool(include),
            }
        )

    full_text = world.get("full_text") or ""
    if not full_text and normalized_sections:
        full_text = "\n\n".join(
            f"## {s['title']}\n\n{s['content']}".strip() for s in normalized_sections
        )

    return {
        "version": world.get("version", 1),
        "generated_at": world.get("generated_at") or _now_iso(),
        "sections": normalized_sections,
        "full_text": full_text,
    }


def empty_world():
    return {
        "version": 1,
        "generated_at": _now_iso(),
        "sections": [],
        "full_text": "",
    }


def load_world():
    """Return the structured world setting, migrating legacy text once."""
    if os.path.exists(WORLD_JSON_FILE):
        try:
            with open(WORLD_JSON_FILE, "r", encoding="utf-8") as f:
                return _normalize_world(json.load(f))
        except (ValueError, OSError) as e:
            print(f"Could not read {WORLD_JSON_FILE}: {e}")

    if os.path.exists(WORLD_FILE):
        with open(WORLD_FILE, encoding="utf-8") as f:
            legacy = f.read()
        world = parse_world_text_to_json(legacy)
        if world.get("sections"):
            save_world_json(world)
            print(f"Migrated legacy world setting to {WORLD_JSON_FILE}")
        return world

    return empty_world()


def save_world_json(world):
    """Validate and persist the structured world setting."""
    normalized = _normalize_world(world)
    os.makedirs(BOOK_OUTPUT_DIR, exist_ok=True)
    with open(WORLD_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    return normalized


def world_to_text(world, include_all=True):
    """Serialize a structured world back into readable text for prompts/editors.

    When ``include_all`` is False, only sections flagged for inclusion in the
    chapter context are emitted. This is what narrows the worldbuilding passed
    to the chapter writer/editor.
    """
    if not world:
        return ""
    sections = world.get("sections") or []
    parts = []
    for section in sections:
        if not include_all and not section.get("include_in_context", True):
            continue
        title = section.get("title") or section.get("id", "")
        content = (section.get("content") or "").strip()
        if title and content:
            parts.append(f"## {title}\n\n{content}")
        elif content:
            parts.append(content)
        elif title:
            parts.append(f"## {title}")
    if parts:
        return "\n\n".join(parts).strip()
    if not include_all:
        return ""
    return (world.get("full_text") or "").strip()


def parse_and_save_world_text(text):
    """Parse raw world text, persist it as JSON, and return the structure."""
    return save_world_json(parse_world_text_to_json(text))


def save_world_text(text):
    """Persist world content from a text form, writing JSON and a txt mirror."""
    text = text or ""
    os.makedirs(BOOK_OUTPUT_DIR, exist_ok=True)
    with open(WORLD_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return save_world_json(parse_world_text_to_json(text))


def load_characters():
    """Return structured character data, migrating legacy characters.txt."""
    if os.path.exists(CHARACTERS_JSON_FILE):
        try:
            with open(CHARACTERS_JSON_FILE, encoding="utf-8") as f:
                return _normalize_characters(json.load(f))
        except (ValueError, OSError) as e:
            print(f"Could not read {CHARACTERS_JSON_FILE}: {e}")

    if os.path.exists(CHARACTERS_FILE):
        with open(CHARACTERS_FILE, encoding="utf-8") as f:
            legacy = f.read()
        parsed = parse_characters_text_to_json(legacy)
        if parsed.get("characters"):
            save_characters_json(parsed)
            print(f"Migrated legacy characters to {CHARACTERS_JSON_FILE}")
        return parsed
    return empty_characters()


def empty_characters():
    return {
        "version": 1,
        "generated_at": _now_iso(),
        "characters": [],
        "raw_text": "",
    }


def _normalize_characters(data):
    if not data:
        return empty_characters()
    if isinstance(data, list):
        data = {"characters": data}
    characters = []
    for index, char in enumerate(data.get("characters") or []):
        if not isinstance(char, dict):
            continue
        char_id = char.get("id") or f"char_{index + 1}"
        normalized = {"id": str(char_id)}
        normalized["name"] = str(char.get("name") or char.get("Name") or "")
        for key in CHARACTER_FIELD_KEYS:
            value = char.get(key)
            normalized[key] = "" if value is None else str(value)
        normalized["include_in_context"] = bool(char.get("include_in_context", True))
        characters.append(normalized)
    return {
        "version": data.get("version", 1),
        "generated_at": data.get("generated_at") or _now_iso(),
        "characters": characters,
        "raw_text": data.get("raw_text") or "",
    }


def save_characters_json(data):
    normalized = _normalize_characters(data)
    os.makedirs(BOOK_OUTPUT_DIR, exist_ok=True)
    with open(CHARACTERS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    return normalized


def save_characters_text(text):
    """Persist character profiles from a text form, writing JSON and a txt mirror."""
    text = text or ""
    os.makedirs(BOOK_OUTPUT_DIR, exist_ok=True)
    with open(CHARACTERS_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return save_characters_json(parse_characters_text_to_json(text))


def characters_to_text(data, include_all=True):
    """Serialize structured characters back into readable profile text.

    When ``include_all`` is False, only characters flagged for inclusion in the
    chapter context are emitted.
    """
    if not data:
        return ""
    characters = data.get("characters") or []
    if include_all:
        selected = characters
    else:
        selected = [char for char in characters if char.get("include_in_context", True)]
    if not selected:
        return "" if not include_all else (data.get("raw_text") or "").strip()
    labels = {
        "role": "Role",
        "age_species": "Age/Species",
        "appearance": "Appearance",
        "personality": "Personality",
        "goal_motivation": "Goal/motivation",
        "background": "Background",
        "flaw": "Flaw",
        "skills": "Skills",
        "relationships": "Relationships",
        "arc": "Arc",
        "misc": "Misc",
        "notes": "Notes",
    }
    blocks = ["CHARACTER_PROFILES:"]
    for char in selected:
        name = (char.get("name") or "").strip() or char.get("id", "")
        blocks.append(f"[{name}]:")
        for key, label in labels.items():
            value = (char.get(key) or "").strip()
            if value:
                blocks.append(f"- {label}: {value}")
        blocks.append("")
    return "\n".join(blocks).strip()


def parse_and_save_characters_text(text):
    return save_characters_json(parse_characters_text_to_json(text))


if __name__ == "__main__":
    # Check OpenAI connection on startup
    check_openai_connection(agent_config)

    # Notify if in debug mode
    if os.getenv("DEBUG", "False").lower() in ("true", "1", "t"):
        print("=" * 50)
        print("🚀 CAUTION: DEBUG mode is enabled.")
        print(
            f"📝 Prompts, requests, and responses will be saved to the '{PROMPT_DEBUGGING_DIR}' directory."
        )
        print("=" * 50)

    app.run(debug=True)
