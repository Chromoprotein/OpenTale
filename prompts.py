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

Based on the chapter outline:
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
Everything must be filtered through the senses, thoughts, and emotions of the specified POV character. 
The reader only knows what the POV character knows.
Include lots of realistic dialogue, deep point of view, and show more than tell. 

---
Based on the following:
- **Chapter outline:** 
{chapter_outline}

- **World:** 
{world_theme}

- **Characters:** 
{relevant_characters}

- **Scenes:**
{scene_details}

- **Previous chapter outline:**
{previous_chapter_outline}

- **Additional Prompt:**
{master_prompt}

---

Write a complete chapter that:
1. Follows the outlined plot points
2. Maintains consistent character voices and development
3. Incorporates world-building details naturally
4. Creates engaging prose with a mix of dialogue, action, and description
5. Has proper pacing with rising and falling tension
6. Connects logically to previous and upcoming chapters

Give the chapter a clear beginning, middle, and end. There is no minimum length: cover the story beats naturally and completely, and end the chapter when the material concludes rather than padding with filler.
"""

# Chapter editing prompt
CHAPTER_EDITING_PROMPT = """
Review and improve the following chapter:

{chapter_content}

---
Based on the following:
- **Chapter outline:** 
{chapter_outline}

- **World:** 
{world_theme}

- **Characters:** 
{relevant_characters}

- **Scenes:**
{scene_details}

- **Previous chapter outline:**
{previous_chapter_outline}

- **Additional Prompt:**
{master_prompt}

---

Provide a comprehensive edit that:
1. Improves prose quality and flow
2. Ensures character consistency
3. Ensures consistency with the outlined plot points
4. Enhances descriptive elements
5. Strengthens dialogue and character interactions
6. Maintains continuity with established world and plot
7. Fixes any grammatical or structural issues
8. Ensures the chapter covers its story beats naturally and completely without padding or filler

Return the complete edited chapter.
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
INLINE_REVISE_PROMPT = """Revise only the text found between [passage] and [/passage]. Improve clarity, tone, rhythm, and emotional or narrative impact. You may extend the original text, but the result must be no shorter than the original and no more than approximately three times its length.

Rules:
- Output only the revised text. Do NOT include any tags or explanations.
- Follow any optional user input, or the chapter outline if it is present below. Ignore them if not.
- Preserve the meaning and intention of the original text.
- Avoid unnecessary filler — all additions must serve tone, character, or clarity.

[passage]
{context}
[/passage]

User input: {user_input}  
Chapter outline: {chapter_outline}
"""
