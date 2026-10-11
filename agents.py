"""Define the API client for book generation system"""

import os
from typing import Dict, List, Tuple

from openai import OpenAI

from outline_schema import OUTLINE_JSON_SCHEMA

# Constants
PROMPT_DEBUGGING_DIR = "prompt_debugging"


def check_openai_connection(agent_config: Dict):
    """Checks if the OpenAI API connection is valid."""
    try:
        client = OpenAI(
            base_url=agent_config["config_list"][0]["base_url"],
            api_key=agent_config["config_list"][0]["api_key"],
        )
        # Make a cheap call to list models
        client.models.list()
        print("✅ OpenAI API connection successful.")
    except Exception as e:
        print(
            f"❌ OpenAI API connection failed. Please check your API key and configuration. Error: {e}"
        )


class BookAgents:
    def __init__(self, agent_config: Dict, synopsis: str = ""):
        """Initialize with the book's story synopsis as context"""
        self.agent_config = agent_config
        self.synopsis = (synopsis or "").strip()
        self.world_elements = {}  # Track described locations/elements
        self.character_developments = {}  # Track character arcs
        self.debug = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")

        # Initialize OpenAI client
        self.client = OpenAI(
            base_url=self.agent_config["config_list"][0]["base_url"],
            api_key=self.agent_config["config_list"][0]["api_key"],
        )
        self.model = self.agent_config["config_list"][0]["model"]

    def _sampling_kwargs(self) -> Dict:
        """Return the sampling/token-limit keyword arguments for the API call.

        Uses max_completion_tokens for OpenAI reasoning models and max_tokens
        for everything else, per the resolved token_param in the agent config.
        """
        token_param = self.agent_config.get("token_param", "max_completion_tokens")
        return {
            "temperature": self.agent_config.get("temperature", 1),
            token_param: self.agent_config.get("max_tokens", 10000),
        }

    def _add_context_blocks(
        self, messages: List[Dict], blocks: List[Tuple[str, str]]
    ) -> None:
        """Append the non-empty context blocks as a single system message.

        Each block is a (heading, content) pair. A block whose content is empty
        or whitespace-only is dropped entirely, heading included, so callers can
        pass optional context without emitting a heading followed by nothing.
        Order is preserved: artifact first, story synopsis last.
        """
        sections = [
            f"{heading}\n\n{content.strip()}"
            for heading, content in blocks
            if content and content.strip()
        ]
        if sections:
            messages.append({"role": "system", "content": "\n\n".join(sections)})

    def _add_synopsis_context(self, messages: List[Dict], synopsis: str) -> None:
        """Add the finalized synopsis as context to the messages, if present."""
        self._add_context_blocks(messages, [("The Story Synopsis is:", synopsis)])

    def _add_world_and_synopsis_context(
        self, messages: List[Dict], world_theme: str, synopsis: str
    ) -> None:
        """Add the world setting and finalized synopsis as context, if present.

        Either may be missing: the author can design characters before the world.
        """
        self._add_context_blocks(
            messages,
            [
                ("The book takes place in the following world:", world_theme),
                ("The Story Synopsis is:", synopsis),
            ],
        )

    def _add_characters_and_synopsis_context(
        self, messages: List[Dict], characters: str, synopsis: str
    ) -> None:
        """Add the characters and finalized synopsis as context, if present.

        Used by the worldbuilding agents so a world designed after the cast can
        fit the characters that already exist.
        """
        self._add_context_blocks(
            messages,
            [
                ("The characters include:", characters),
                ("The Story Synopsis is:", synopsis),
            ],
        )

    def _add_world_characters_and_synopsis_context(
        self,
        messages: List[Dict],
        world_theme: str,
        characters: str,
        synopsis: str,
    ) -> None:
        """Add the world setting, characters and finalized synopsis as context."""
        self._add_context_blocks(
            messages,
            [
                ("The book takes place in the following world:", world_theme),
                ("The characters include:", characters),
                ("The Story Synopsis is:", synopsis),
            ],
        )

    def _save_debug_messages(
        self, messages: List[Dict], agent_name: str, request_type: str
    ):
        """Saves the request messages for debugging, grouping by role."""
        if not self.debug:
            return

        if not os.path.exists(PROMPT_DEBUGGING_DIR):
            os.makedirs(PROMPT_DEBUGGING_DIR)

        # Group messages by role to combine content for the same role
        grouped_messages = {}
        for message in messages:
            role = message["role"]
            content = message["content"]
            if role not in grouped_messages:
                grouped_messages[role] = []
            grouped_messages[role].append(content)

        # Write combined messages to files
        for role, contents in grouped_messages.items():
            file_path = os.path.join(
                PROMPT_DEBUGGING_DIR,
                f"{agent_name}_{request_type}_{role}.txt",
            )
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(contents))

    def _create_debug_stream_wrapper(self, stream, agent_name: str, response_name: str):
        """Creates a wrapper to save the full response from a stream while streaming."""

        def stream_wrapper():
            """A wrapper to save the full response while streaming"""
            full_response_content = []
            for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    full_response_content.append(content)
                yield chunk

            # After the stream is exhausted, save the complete response
            response_filepath = os.path.join(
                PROMPT_DEBUGGING_DIR, f"{agent_name}_{response_name}.txt"
            )
            with open(response_filepath, "w", encoding="utf-8") as f:
                f.write("".join(full_response_content))

        return stream_wrapper()

    def create_agents(self, initial_prompt, num_chapters) -> Dict:
        """Set up system prompts for each agent type"""
        synopsis_context = self.synopsis or "(No story synopsis is available.)"

        # Define system prompts for each agent type
        self.system_prompts = {
            "character_generator": """You are a creative assistant helping an author develop the cast for their book. If a world setting has already been established, create detailed profiles for characters that fit in it; otherwise create characters whose backgrounds naturally imply the world they inhabit.
            Treat the target number of characters as a guideline, not a hard limit.

Format your output EXACTLY as:
CHARACTER_PROFILES:

[CHARACTER NAME 1]:
- Role: [Main character, supporting character, antagonist, etc.]
- Age/Species: [Character's age and species]
- Appearance: [A few interesting identifying traits plus the character's overall vibe]
- Personality: [A few core personality traits. Which personality traits does the character show to others and which ones do they hide?]
- Goal/motivation: [What does this character want the most?]
- Background: [A couple of events that shaped who the character is today and influenced their goals]
- Flaw: [What core flaw hinders the character getting what they want?]
- Skills: [A couple of things this character is good at or will learn during the story to help achieve their goals]
- Relationships: [Who does the character care about the most and what is their role in the character's pursuit of the goals? Who gets in the way of the character's goals?]
- Arc: [How this character might develop over the story. What happens if the character fails to overcome their flaw and doesn't reach their goal?]
- Misc: [Anything else that should be known about this character]

[CHARACTER NAME 2]:
[Follow same format as above]

[And so on for all requested characters]

Ensure characters fit logically within the established world setting when one is available.
""",
            # Add a system prompt for conversational character brainstorming
            "character_generator_chat": """You are a collaborative, creative assistant helping an author develop the cast for their book.

Your primary goal is to help the author shape distinct, memorable characters that fit the established world. If no world setting has been established yet, let the world emerge from the characters instead:
1.  **Role**: What part does each character play in the story (protagonist, antagonist, ally, foil)?
2.  **Core traits and flaws**: What makes them interesting, and what holds them back?
3.  **Motivations and goals**: What are they driving at, and why does it matter to them?
4.  **Backstory**: What shaped them into who they are?
5.  **Relationships**: How do they connect to, clash with, or mirror the other characters?

Your approach:
*   Build the cast incrementally, focusing on one character at a time rather than listing everyone at once.
*   Start by asking the author who the central character is and what they want.
*   Proactively probe for the weaknesses and contradictions that make a character feel real.
*   Offer concrete suggestions grounded in the established world when there is one, and ask clarifying questions to sharpen them.
*   Pay attention to which characters the author keeps returning to, and which ones they seem to drop.
*   Maintain a friendly, conversational tone.
*   NEVER generate the final formatted profiles during this chat phase. This is for brainstorming only.

After developing a character, **ALWAYS continue the conversation by asking further questions** to deepen that character or to move on to the next one. Do not stop at just describing a character. Don't ask more than three questions per turn, and stop when the character is sufficiently developed for the author's intended use. Do not invent answers to unresolved questions.

When they're ready to finalize, you'll help organize their ideas into a complete set of character profiles.
""",
            "story_planner": """You are a collaborative, creative writing assistant. Your task is to create a concise, complete story synopsis based on a conversation with an author.

The synopsis should be concise and complete:
- Aim for 500 to 1000 words.
- Present the story at the level of its essential narrative arc: the premise, the inciting incident, the driving conflicts, the key turning points that move the story forward, and the ending.
- Prioritize the events that matter most to the whole story, and give each of them enough focus to be clear.

The final output should be only the complete synopsis.
""",
            "outline_creator": f"""Generate a detailed outline for a novel, targeting approximately {num_chapters} chapters.

You are not asked to write prose. Fill in the structured outline you are given,
aiming for roughly {num_chapters} chapters.

What makes a useful chapter entry:
1. Number chapters sequentially from 1 with no gaps and no repeated numbers.
2. Give every chapter a distinct, evocative title. If the book is written in a
   language other than English, also give the title in that language; leave
   title_local null when it would just repeat the English title.
3. List at least 3 specific key events per chapter. Prefer concrete things that
   happen -- a decision made, a betrayal revealed, a wound reopened -- over
   vague statements about pacing or themes. Each event should read as a
   instruction to the writer, not as a summary of the finished chapter.
4. Describe the character developments the chapter carries: who changes, and how.
5. Name the setting and its atmosphere.
6. State the tone, and note where the tone shifts within the chapter.
7. Keep the story coherent from beginning to end. Events in one chapter should
   follow from the previous chapters, and the final chapters should resolve the
   central conflict.

Group the chapters into acts if the story has a clear act structure, and set each
chapter's act_number accordingly. Otherwise return an empty acts array.

Initial Premise:
{initial_prompt}
""",
            "writer": f"""You are a creative writing assistant. Your task is to write a chapter for a novel based on the provided chapter outline and story synopsis. Ensure all story beats are completed, and don't introduce new plot points beyond the outline.

### Story Synopsis
{synopsis_context}

---

Always reference the outline and previous content.
Output only the chapter prose, with no preamble, commentary, or section markers.
""",
            "editor": f"""You are an expert editor ensuring quality and consistency.

Your task is to review and improve the provided chapter content based on the provided chapter outline, story synopsis, the required changes and the user's request, 
adhering to the following directives at all times.

### Story Synopsis
{synopsis_context}

---
### Core Directives
1. Check alignment with outline
2. Verify character consistency
3. Maintain world-building rules
4. Improve prose quality
5. Return complete edited chapter
6. Never ask to start the next chapter, as the next step is finalizing this chapter

Apply every required change you are given, always referencing the relevant outline elements, then return the complete edited chapter.

---

Output only the complete edited chapter prose. Do not include critique, suggestions, commentary, headings, or section markers before or after it.
""",
            # Brainstorming chat about a single chapter's improvements
            "editor_chat": """You are a collaborative, expert fiction editor helping an author improve a single chapter of their novel.

Your approach during this brainstorming phase:
1. Discuss the chapter with the author: point out what works, what is weak, and what conflicts with the outline, the characters, or the established world
2. Answer specific questions about the chapter's prose, pacing, dialogue, continuity, and structure
3. Offer concrete, actionable suggestions, and ask clarifying questions when the author's intent is unclear
4. Reference specific parts of the chapter and of the chapter outline in your feedback
5. Maintain a friendly, conversational tone

This is for brainstorming and feedback only. NEVER rewrite or reproduce the chapter during this chat. When the author is ready, you will help condense the discussion into a final list of required changes for the editing pass.
""",
            # Final pass that turns the chat into the change list artifact
            "chapter_edits_finalizer": """You are an expert fiction editor. Your task is to turn a brainstorming conversation about a single chapter into the final list of required changes that an editing pass must make to that chapter.

Produce a numbered list of concrete, actionable items. Each item must say what to change and where -- not merely that something is weak. Cover continuity, outline alignment, character voice, pacing, prose quality, and any specific instructions the author gave during the conversation.

Do not rewrite the chapter, do not discuss the list, and do not add commentary before or after it. Output only the numbered change list.
""",
            # Add a special system prompt for conversational world building
            "world_builder_chat": """You are a collaborative, creative world-building assistant helping an author develop a detailed world for their book.

Your approach:
1. Ask thoughtful questions about their world ideas
2. Offer creative suggestions that build on their ideas
3. Help them explore different aspects of world-building:
    - Geography and physical environment
    - Culture and social structures
    - History and mythology
    - Technology or magic systems
    - Political systems or factions
    - Economy and resources
4. Maintain a friendly, conversational tone
5. Keep track of their preferences and established world elements
6. If the book already has characters, build a world that fits them: their motives should be answerable by the world's politics, economy and culture, and the places they travel should matter to their arcs

When they're ready to finalize, you'll help organize their ideas into a comprehensive world setting document.
""",
            # Add a system prompt for the final world-setting document pass
            "world_builder_specialist": """You are a creative writing assistant helping an author write a worldbuilding document for their novel. Your task is to extract relevant worldbuilding information from the conversation with the author and organize it into a well-structured document.

Organize your response as a document covering:
1. Time period and setting: [detailed description]
2. Major locations: [detailed description of each key location]
3. Cultural/historical elements: [key cultural and historical aspects]
4. Technology/magical elements: [if applicable]
5. Social/political structures: [governments, factions, etc. if applicable]
6. Environment and atmosphere: [natural world aspects]

Add necessary details to fill any gaps, while staying true to everything established in the chat history.

If characters have already been established, the world must fit them: their motives should be answerable by the world's politics, economy and culture, and the locations they inhabit should serve their arcs.
""",
            # Add a special system prompt for conversational outline brainstorming
            "outline_creator_chat": f"""You are a collaborative, creative story development assistant helping an author brainstorm and develop their book outline.

Your approach during this brainstorming phase:
1. Focus on DISCUSSING story ideas, not generating the complete outline yet
2. Help explore plot structure, character arcs, themes, and story beats
3. Ask thought-provoking questions about their story ideas
4. Offer suggestions that build on their ideas, including:
    - Potential plot twists or conflicts
    - Character development opportunities
    - Thematic elements to explore
    - Pacing considerations
    - Structure recommendations
5. Maintain a friendly, conversational tone
6. DO NOT use chapter numbers or list out chapters - this is for brainstorming only

The book will likely have around {num_chapters} chapters, but during this chat focus on story elements, not chapter structure.
""",
            # Add a special system prompt for conversational synopsis brainstorming
            "story_synopsis_chat": """You are a collaborative, creative story development assistant helping an author brainstorm and develop their book synopsis.

Your primary goal is to guide the author to define three key elements for their story:
1.  **Genre**: What kind of story is it (e.g., fantasy, sci-fi, thriller, romance)?
2.  **Premise**: What is the core idea or setup of the story?
3.  **Ending**: How does the story conclude?

Your approach:
*   Start by asking the author for the **genre** of their story.
*   Once the genre is provided, ask for the **premise**.
*   After the premise, ask for the **ending**.
*   You can also ask for "other information" to enrich the synopsis.
*   Offer creative suggestions and ask clarifying questions to help them flesh out these elements.
*   Maintain a friendly, conversational tone.
*   NEVER generate a full synopsis during this chat phase. This is for brainstorming only.

After identifying an element, **ALWAYS continue the conversation by asking further questions** to help the user refine their ideas or move on to the next key element (premise after genre, ending after premise, etc.). Do not stop at just identifying the element.

When they're ready to finalize, you'll help organize their ideas into a overview with genre, premise and ending.
""",
            # Add specific inline writer prompts
            "inline_reviser": "You are a creative writing assistant. Your task is to revise narrative text to improve clarity, tone, and flow while preserving intent.",
            "inline_continuer": "You are a creative writing assistant. Your task is to extend the provided narrative text in the same tone and voice without repeating content.",
        }

        # Save the raw system prompts to a file for debugging
        if self.debug:
            if not os.path.exists(PROMPT_DEBUGGING_DIR):
                os.makedirs(PROMPT_DEBUGGING_DIR)
            for agent_name, prompt_content in self.system_prompts.items():
                file_path = os.path.join(
                    PROMPT_DEBUGGING_DIR, f"{agent_name}_prompt.txt"
                )
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(prompt_content)

        # Return empty dict since we're not using actual agent objects anymore
        return {}

    def generate_content(self, agent_name: str, prompt: str) -> str:
        """Generate content using the OpenAI API with the specified agent system prompt"""
        if agent_name not in self.system_prompts:
            raise ValueError(
                f"Agent '{agent_name}' not found. Available agents: {list(self.system_prompts.keys())}"
            )

        # Create the messages array with system prompt and user message
        messages = [
            {"role": "system", "content": self.system_prompts[agent_name]},
            {"role": "user", "content": prompt},
        ]

        # Save the messages for debugging
        self._save_debug_messages(messages, agent_name, "request")

        # Call the API
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
        )

        # Extract the response
        response = completion.choices[0].message.content

        # Save the raw response for debugging
        if self.debug:
            response_filepath = os.path.join(
                PROMPT_DEBUGGING_DIR, f"{agent_name}_response.txt"
            )
            with open(response_filepath, "w", encoding="utf-8") as f:
                f.write(response)

        return response

    def generate_content_stream(self, agent_name: str, prompt: str):
        """Generate content using the OpenAI API with the specified agent system prompt (streaming)"""
        if agent_name not in self.system_prompts:
            raise ValueError(
                f"Agent '{agent_name}' not found. Available agents: {list(self.system_prompts.keys())}"
            )

        messages = [
            {"role": "system", "content": self.system_prompts[agent_name]},
            {"role": "user", "content": prompt},
        ]

        # Save the messages for debugging
        self._save_debug_messages(messages, agent_name, "stream_request")

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(stream, agent_name, "stream_response")

    def generate_chat_response_world(
        self, chat_history, topic, synopsis, user_message, characters=""
    ) -> str:
        """Generate a chat response based on conversation history"""
        # Format the messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["world_builder_chat"]}
        ]

        # Add the existing characters and the story synopsis context
        self._add_characters_and_synopsis_context(messages, characters, synopsis)

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Save the messages for debugging
        self._save_debug_messages(messages, "world_builder_chat", "chat_request")

        # Call the API
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
        )

        # Extract the response
        return completion.choices[0].message.content

    def generate_chat_response_world_stream(
        self, chat_history, topic, synopsis, user_message, characters=""
    ):
        """Generate a streaming chat response based on conversation history"""
        # Format the messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["world_builder_chat"]}
        ]

        # Add the existing characters and the story synopsis context
        self._add_characters_and_synopsis_context(messages, characters, synopsis)

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(messages, "world_builder_chat", "chat_stream_request")

        # Call the API with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,  # Enable streaming
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "world_builder_chat", "chat_stream_response"
        )

    def generate_chat_response_synopsis_stream(self, chat_history, topic, user_message):
        """Generate a streaming chat response about synopsis building."""
        # Format the messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["story_synopsis_chat"]}
        ]

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "story_synopsis_chat", "chat_synopsis_stream_request"
        )

        # Call the API with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,  # Enable streaming
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "story_synopsis_chat", "chat_synopsis_stream_response"
        )

    def generate_final_synopsis_stream(self, chat_history, topic):
        """Generate the final synopsis based on the chat history using streaming."""
        # Format messages for the API call
        messages = [{"role": "system", "content": self.system_prompts["story_planner"]}]

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete synopsis
        messages.append(
            {
                "role": "user",
                "content": f"Based on our conversation about '{topic}', create the final synopsis for the book. First extract the genre, premise, and ending, and then generate the full synopsis in a traditional three-act structure. Keep the synopsis concise and complete: focus on the essential narrative arc, the driving conflicts, and the key turning points, aiming for 500 to 1000 words.",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "story_planner", "final_synopsis_stream_request"
        )

        # Make the API call with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "story_planner", "final_synopsis_stream_response"
        )

    def _build_final_world_messages(
        self, chat_history, topic, synopsis, characters=""
    ) -> List[Dict]:
        """Build the message array for the final world-setting pass.

        Shared by the streaming and non-streaming finalize paths so both emit an
        identical document from identical input.
        """
        messages = [
            {
                "role": "system",
                "content": self.system_prompts["world_builder_specialist"],
            }
        ]

        # Add the existing characters and the story synopsis context
        self._add_characters_and_synopsis_context(messages, characters, synopsis)

        # Add conversation context from chat history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add the final instruction to create the complete world setting
        messages.append(
            {
                "role": "user",
                "content": f"Based on our conversation about '{topic}', please create a comprehensive and detailed world setting. Format it with clear sections for different aspects of the world (geography, magic/technology, culture, etc.). This will be the final world setting for the book.",
            }
        )

        return messages

    def generate_final_world(self, chat_history, topic, synopsis, characters="") -> str:
        """Generate final world setting based on chat history.

        Blocking counterpart to generate_final_world_stream: consumes the same
        stream and returns the full document as a string.
        """
        stream = self.generate_final_world_stream(
            chat_history, topic, synopsis, characters
        )

        content = []
        for chunk in stream:
            if (
                chunk.choices
                and len(chunk.choices) > 0
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content is not None
            ):
                content.append(chunk.choices[0].delta.content)

        return "".join(content)

    def generate_final_world_stream(self, chat_history, topic, synopsis, characters=""):
        """Generate the final world setting based on the chat history using streaming."""
        messages = self._build_final_world_messages(
            chat_history, topic, synopsis, characters
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "world_builder_specialist", "final_world_stream_request"
        )

        # Make the API call with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "world_builder_specialist", "final_world_stream_response"
        )

    def update_world_element(self, element_name: str, description: str) -> None:
        """Update a world element description"""
        self.world_elements[element_name] = description

    def update_character_development(
        self, character_name: str, development: str
    ) -> None:
        """Update a character's development"""
        if character_name not in self.character_developments:
            self.character_developments[character_name] = []
        self.character_developments[character_name].append(development)

    def generate_chat_response_characters(
        self, chat_history, world_theme, synopsis, user_message, num_characters=3
    ):
        """Generate a chat response about character creation."""
        # Format messages for the API call
        messages = [
            {
                "role": "system",
                "content": self.system_prompts["character_generator_chat"],
            }
        ]

        # Add world theme and synopsis context
        self._add_world_and_synopsis_context(messages, world_theme, synopsis)

        # Inform the assistant of the target character count (as guidance)
        messages.append(
            {
                "role": "system",
                "content": f"The author is currently targeting about {num_characters} characters for the book. Treat this as a guideline, not a hard requirement.",
            }
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "character_generator_chat", "chat_characters_request"
        )

        # Make the API call
        response = (
            self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **self._sampling_kwargs(),
            )
            .choices[0]
            .message.content
        )

        return response

    def generate_chat_response_characters_stream(
        self, chat_history, world_theme, synopsis, user_message, num_characters=3
    ):
        """Generate a streaming chat response about character creation."""
        # Format messages for the API call
        messages = [
            {
                "role": "system",
                "content": self.system_prompts["character_generator_chat"],
            }
        ]

        # Add world theme and synopsis context
        self._add_world_and_synopsis_context(messages, world_theme, synopsis)

        # Inform the assistant of the target character count (as guidance)
        messages.append(
            {
                "role": "system",
                "content": f"The author is currently targeting about {num_characters} characters for the book. Treat this as a guideline, not a hard requirement.",
            }
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "character_generator_chat", "chat_characters_stream_request"
        )

        # Make the API call with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "character_generator_chat", "chat_characters_stream_response"
        )

    def generate_final_characters_stream(
        self, chat_history, world_theme, synopsis, num_characters=3
    ):
        """Generate the final character profiles based on chat history using streaming."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["character_generator"]}
        ]

        # Add world theme and synopsis context
        self._add_world_and_synopsis_context(messages, world_theme, synopsis)

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete character profiles
        messages.append(
            {
                "role": "user",
                "content": f"Based on our conversation, please create around {num_characters} detailed character profiles for the book (this is a target, not a strict limit — use your judgment based on the story). Format each character with Name, Role, Age/Species, Physical Description, Personality, Background, Motivations, Skills/Abilities, Relationships, and Arc. This will be the final character list for the book.",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "character_generator", "final_characters_stream_request"
        )

        # Make the API call with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "character_generator", "final_characters_stream_response"
        )

    def generate_chat_response_outline(
        self, chat_history, world_theme, characters, synopsis, user_message
    ):
        """Generate a chat response about outline creation."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["outline_creator_chat"]}
        ]

        # Add world theme, characters and synopsis context
        self._add_world_characters_and_synopsis_context(
            messages, world_theme, characters, synopsis
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "outline_creator_chat", "chat_outline_request"
        )

        # Make the API call
        response = (
            self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **self._sampling_kwargs(),
            )
            .choices[0]
            .message.content
        )

        return response

    def generate_chat_response_outline_stream(
        self, chat_history, world_theme, characters, synopsis, user_message
    ):
        """Generate a streaming chat response about outline creation."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["outline_creator_chat"]}
        ]

        # Add world theme, characters and synopsis context
        self._add_world_characters_and_synopsis_context(
            messages, world_theme, characters, synopsis
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "outline_creator_chat", "chat_outline_stream_request"
        )

        # Make the API call with streaming enabled
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "outline_creator_chat", "chat_outline_stream_response"
        )

    def _supports_structured_output(self) -> bool:
        """Whether this endpoint accepts an OpenAI ``json_schema`` response format.

        OpenAI and most compatible servers do. OpenRouter, Ollama and other
        gateways may not, so the outline degrades to asking for markdown rather
        than failing the request outright.
        """
        return self.agent_config.get("structured_output", True)

    def generate_final_outline_stream(
        self, chat_history, world_theme, characters, synopsis, num_chapters=10
    ):
        """Generate the final outline based on chat history using streaming.

        Returns an iterable of chunks. The content is JSON matching
        ``OUTLINE_JSON_SCHEMA`` when the endpoint supports structured output, and
        a legacy markdown outline otherwise. Callers should not assume either --
        try :func:`outline_schema.normalize_outline` and fall back to
        :func:`outline_schema.parse_legacy_outline_markdown`, as
        ``finalize_outline_stream`` does.
        """
        structured = self._supports_structured_output()

        # Only the fallback needs to be told how to lay the outline out in prose;
        # with structured output the schema already decides that.
        format_requirements = ""
        if not structured:
            format_requirements = """
OUTPUT FORMAT (this endpoint does not support structured output, so plain text):
- Begin with a line reading exactly: # OUTLINE
- Start each act with a heading: ## Act <number>: <title>
- Start each chapter with a heading: Chapter <number>: <title>
- Under each chapter, list at least 3 key events as bullets:
    - Key Event: <what happens>
- Optionally add these bullets under a chapter:
    - Character Developments: <how the cast changes>
    - Setting: <where it happens>
    - Tone: <the mood>
- Do not add commentary before or after the outline.
"""

        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["outline_creator"]}
        ]

        # Add world theme, characters and synopsis context
        self._add_world_characters_and_synopsis_context(
            messages, world_theme, characters, synopsis
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete outline
        messages.append(
            {
                "role": "user",
                "content": f"""Based on our conversation, please create a detailed outline of approximately {num_chapters} chapters for the book.

CRITICAL REQUIREMENTS:
1. Aim for roughly {num_chapters} chapters (a guideline, not a strict limit); number chapters sequentially from 1 without gaps or duplicates
2. NEVER repeat chapter numbers or restart the numbering
3. Each chapter must have a unique title and at least 3 specific key events
4. Maintain a coherent story from beginning to end

This will be the final outline for the book.
{format_requirements}""",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "outline_creator", "final_outline_stream_request"
        )

        # Constrain the response to the outline schema so the caller never has
        # to parse prose to recover the book's structure.
        request_kwargs = {}
        if structured:
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": OUTLINE_JSON_SCHEMA,
            }

        # Make the API call with streaming enabled
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **request_kwargs,
                **self._sampling_kwargs(),
                stream=True,
            )
        except Exception as exc:
            if not structured:
                raise
            # The endpoint rejected the schema. Retry as a markdown outline rather
            # than failing the request, and tell the model the layout we will parse.
            print(f"⚠️ Structured output unavailable, falling back to text: {exc}")
            if self.debug:
                print("Set STRUCTURED_OUTPUT=false in .env to skip this attempt.")
            messages[-1]["content"] += format_requirements
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **self._sampling_kwargs(),
                stream=True,
            )

        if not self.debug:
            return stream

        # If debugging is enabled, wrap the stream to save the full response at the end
        return self._create_debug_stream_wrapper(
            stream, "outline_creator", "final_outline_stream_response"
        )

    def _add_chapter_editing_context(
        self,
        messages: List[Dict],
        chapter_outline: str,
        chapter_content: str,
        world_theme: str,
        characters: str,
    ) -> None:
        """Add the chapter under review plus book-level context as system messages.

        The chapter artifacts come first so the conversation that follows reads
        as commentary on them; the synopsis stays last, as elsewhere.
        """
        self._add_context_blocks(
            messages,
            [
                ("The chapter outline for the chapter under review:", chapter_outline),
                ("The current chapter content:", chapter_content),
                ("The book takes place in the following world:", world_theme),
                ("The characters include:", characters),
                ("The Story Synopsis is:", self.synopsis),
            ],
        )

    def generate_chat_response_editor_stream(
        self,
        chat_history,
        chapter_outline,
        chapter_content,
        world_theme,
        characters,
        user_message,
    ):
        """Generate a streaming brainstorming response about improving a chapter."""
        messages = [{"role": "system", "content": self.system_prompts["editor_chat"]}]

        self._add_chapter_editing_context(
            messages, chapter_outline, chapter_content, world_theme, characters
        )

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add the latest user message
        messages.append({"role": "user", "content": user_message})

        self._save_debug_messages(messages, "editor_chat", "chat_stream_request")

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        return self._create_debug_stream_wrapper(
            stream, "editor_chat", "chat_stream_response"
        )

    def generate_final_edits_stream(
        self,
        chat_history,
        chapter_outline,
        chapter_content,
        world_theme,
        characters,
    ):
        """Generate the final numbered change list from the brainstorming chat."""
        messages = [
            {
                "role": "system",
                "content": self.system_prompts["chapter_edits_finalizer"],
            }
        ]

        self._add_chapter_editing_context(
            messages, chapter_outline, chapter_content, world_theme, characters
        )

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add the final instruction to produce the change list
        messages.append(
            {
                "role": "user",
                "content": "Based on our conversation, produce the final numbered list of required changes for this chapter's editing pass. This list will be handed to the editor as its complete set of instructions, so include everything that must change and nothing that is merely nice to have. Output only the numbered list.",
            }
        )

        self._save_debug_messages(
            messages, "chapter_edits_finalizer", "final_edits_stream_request"
        )

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
            stream=True,
        )

        if not self.debug:
            return stream

        return self._create_debug_stream_wrapper(
            stream, "chapter_edits_finalizer", "final_edits_stream_response"
        )
