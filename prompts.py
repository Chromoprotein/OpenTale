"""
This module contains all the prompts used by the AI agents in the book writing process.
Each prompt is a template that can be formatted with specific data.
"""

# World building prompt
WORLD_THEME_PROMPT = """
Based on the general topic: {topic}

Create a rich and detailed world setting for a book. Include:
1. Time period and setting
2. Major locations and their descriptions
3. Prominent cultural/historical elements
4. Technology level or magical elements (if applicable)
5. Social/political structures
6. Environment and atmosphere

Be specific and detailed, creating a cohesive world that would support an engaging narrative.
"""

# World suggestions prompt
WORLD_SUGGESTIONS_PROMPT = """
Based on the general topic: {topic}

Create a brief overview of potential world elements for a book. Include:
1. 2-3 potential time periods or settings that would work well
2. 3-5 key elements that would make this world interesting and unique
3. Brief suggestions for the atmosphere and tone
4. Any potential conflicts or tensions that could exist in this world

This is a preliminary summary to help guide the creation of a more detailed world setting.
Keep it concise but inspiring, focusing on elements that would spark the imagination.
"""

# Character creation prompt
CHARACTER_CREATION_PROMPT = """
Based on the world setting:
{world_theme}

Create {num_characters} distinct characters for a book set in this world. For each character include:
1. Name and role in the story
2. Age and physical description
3. Personality traits and quirks
4. Background/history
5. Motivations and goals
6. Conflicts or challenges they face
7. Relationships with other characters (if applicable)

Make each character complex and three-dimensional, with strengths, flaws, and distinguishing characteristics.
"""

# Outline generation prompt
OUTLINE_GENERATION_PROMPT = """
Based on the synopsis:
{synopsis}

The world:
{world_theme}

And the characters:
{characters}

Create a detailed {num_chapters}-chapter outline for a book.

For each chapter include:
1. Chapter title
2. Key events and plot developments
3. Character appearances and development
4. Setting/location
5. Major themes or emotional beats
6. Any important revelations or plot twists

Ensure the outline follows a satisfying story structure with a clear beginning, middle, and end.
The plot should build logically with rising action, climax, and resolution.
"""

# Synopsis generation prompt
SYNOPSIS_GENERATION_PROMPT = """
Given the following genre, premise and story information, give me a highly detailed synopsis for a story in the traditional three act structure. Each act should be clearly labeled and should build toward the ending I've described. Make sure to include plenty of conflict, and include a main character.

GENRE: {genre}
PREMISE: {premise}
ENDING: {ending}
OTHER INFORMATION: {other_information}
"""

# Synopsis suggestions prompt
SYNOPSIS_SUGGESTIONS_PROMPT = """
Based on the general topic: {topic}

Create a brief overview of potential story elements for a book. Include:
1. 2-3 potential premises or story hooks
2. 3-5 key plot points that would make the story interesting
3. Brief suggestions for the tone and genre
4. Any potential conflicts or tensions that could exist in the story

This is a preliminary summary to help guide the creation of a more detailed synopsis.
Keep it concise but inspiring, focusing on elements that would spark the imagination.
"""

# Scene generation prompt
SCENE_GENERATION_PROMPT = """
For Chapter {chapter_number}: {chapter_title}

Based on the chapter outline:
{chapter_outline}

And considering:
- World: {world_theme}
- Characters: {relevant_characters}
- Previous chapters: {previous_context}

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

- **Action Beats:**
{action_beats}

- **Previous chapters:**
{previous_context}

- **Additional Prompt:**
{master_prompt}

---

Write a complete chapter that:
1. Follows the outlined plot points and action beats if provided
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

- **Action Beats:**
{action_beats}

- **Previous chapters:**
{previous_context}

- **Additional Prompt:**
{master_prompt}

---

Provide a comprehensive edit that:
1. Improves prose quality and flow
2. Ensures character consistency
3. Ensures consistency with the outlined plot points and action beats if provided
4. Enhances descriptive elements
5. Strengthens dialogue and character interactions
6. Maintains continuity with established world and plot
7. Fixes any grammatical or structural issues
8. Ensures the chapter covers its story beats naturally and completely without padding or filler

Return the complete edited chapter.
"""


# Action beats generation prompt
ACTIONBEATS_GENERATION_PROMPT = """
For Chapter {chapter_number}: {chapter_title}

Take the following chapter summary, and generate a list of {num_beats} highly detailed action beats for a script, with additional story information to fully flesh out the chapter. Make sure to always use proper nouns instead of pronouns.

Based on the chapter summary:
{chapter_summary}

And considering:
- World: {world_theme}
- Characters: {relevant_characters}
- Previous chapters: {previous_context}
"""

# Inline continue prompt
INLINE_CONTINUE_PROMPT = """Instructions:
You are continuing a story. Do not repeat what has already been written unless doing so briefly for literary effect. Your continuation should match the tone, voice, and style of the preceding text.

Output only the continuation — no headings, explanations, or tags.

Story so far:
{context}

Optional guidance (use only if helpful):
User input: {user_input}
Action beats: {action_beats}
"""

# Inline revise prompt
INLINE_REVISE_PROMPT = """Revise only the text found between [passage] and [/passage]. Improve clarity, tone, rhythm, and emotional or narrative impact. You may extend the original text, but the result must be no shorter than the original and no more than approximately three times its length.

Rules:
- Output only the revised text. Do NOT include any tags or explanations.
- Follow any optional user input, or action beats if they are present below. Ignore them if not.
- Preserve the meaning and intention of the original text.
- Avoid unnecessary filler — all additions must serve tone, character, or clarity.

[passage]
{context}
[/passage]

User input: {user_input}  
Action beats: {action_beats}
"""
