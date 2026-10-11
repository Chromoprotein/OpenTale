"""
User-message prompt templates for the book writing app.

Each prompt below is a per-request template that gets interpolated with runtime
data and sent as the "user" turn, alongside an agent's system prompt. They are
plain ``str.format`` templates using named placeholders.

The system prompts that frame these requests live in ``agents.py``, in the
``self.system_prompts`` dict built by ``BookAgents.create_agents()``.
"""

# Scene generation prompt
SCENE_GENERATION_PROMPT = """
For Chapter {chapter_number}: {chapter_title}

Author's scene request:
{scene_description}

Write only the requested scene. For reference, the entire chapter's outline is:
{chapter_outline}

And considering:
- World: {world_theme}
- Characters: {relevant_characters}
- Previous chapter outline: {previous_chapter_outline}

Generate a detailed scene that includes:
1. Setting description with sensory details
2. Character interactions and dialogue
3. Action and plot advancement
4. Emotional beats and character development
5. Connections to the overall narrative

Write engaging, immersive prose that advances the story while staying true to the established world and characters.
"""

# Chapter generation prompt
CHAPTER_GENERATION_PROMPT = """
Generate Chapter {chapter_number}: {chapter_title}
The entire chapter must be in {tense} and from a {point_of_view} point of view. 

---
Based on the following:
- **Chapter outline:** 
{chapter_outline}

- **Worldbuilding - include only what fits naturally:** 
{world_theme}

- **Characters - Do not invent new characters unless the outline mentions additional characters who aren't listed here:** 
{relevant_characters}

- **Scenes that should be included in the chapter:**
{scene_details}

- **Previous chapter outline (for consistency):**
{previous_chapter_outline}

- **Additional Prompt:**
{master_prompt}

"""

# Chapter editing prompt
CHAPTER_EDITING_PROMPT = """
Review and improve the following chapter:

{chapter_content}

---
Based on the following:
- **Chapter outline:** 
{chapter_outline}

- **Worldbuilding - only include what fits naturally:** 
{world_theme}

- **Characters:** 
{relevant_characters}

- **Previous chapter outline:**
{previous_chapter_outline}

- **Additional Prompt:**
{master_prompt}

---

**Required changes:**
{required_changes}

---

Provide a comprehensive edit that:
1. Applies every required change listed above
2. Improves prose quality and flow
3. Ensures character consistency
4. Ensures consistency with the outlined plot points
5. Enhances descriptive elements
6. Strengthens dialogue and character interactions
7. Maintains continuity with established world and plot
8. Fixes any grammatical or structural issues

Return the complete edited chapter, and nothing else.
"""

# Inline continue prompt
INLINE_CONTINUE_PROMPT = """Instructions:
You are continuing a story. Do not repeat what has already been written unless doing so briefly for literary effect. Your continuation should match the tone, voice, and style of the preceding text.

Output only the continuation — no headings, explanations, or tags.

Story so far:
{context}

Optional guidance (use only if helpful):
User input: {user_input}
Chapter outline: {chapter_outline}
"""

# Inline revise prompt
INLINE_REVISE_PROMPT = """Revise only the text found between [passage] and [/passage]. Improve clarity, tone, rhythm, and emotional or narrative impact. You may extend the original text, but not excessively.

Rules:
- Output only the revised text. Do NOT include any tags or explanations.
- Follow any optional user input, or the chapter outline if it is present below. Ignore them if not.
- Preserve the meaning and intention of the original text.

[passage]
{context}
[/passage]

User input: {user_input}  
Chapter outline: {chapter_outline}
"""
