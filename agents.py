"""Define the API client for book generation system"""

import os
from typing import Dict, List, Optional

from openai import OpenAI

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
    def __init__(self, agent_config: Dict, outline: Optional[List[Dict]] = None):
        """Initialize with book outline context"""
        self.agent_config = agent_config
        self.outline = outline
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

    def _format_outline_context(self) -> str:
        """Format the book outline into a readable context"""
        if not self.outline:
            return ""

        context_parts = ["Complete Book Outline:"]
        for chapter in self.outline:
            context_parts.extend(
                [
                    f"\nChapter {chapter['chapter_number']}: {chapter['title']}",
                    chapter["prompt"],
                ]
            )
        return "\n".join(context_parts)

    def _add_synopsis_context(self, messages: List[Dict], synopsis: str) -> None:
        """Add the finalized synopsis as context to the messages, if present."""
        if synopsis and synopsis.strip():
            messages.append(
                {
                    "role": "system",
                    "content": f"The book's story synopsis is:\n\n{synopsis.strip()}",
                }
            )

    def _add_world_and_synopsis_context(
        self, messages: List[Dict], world_theme: str, synopsis: str
    ) -> None:
        """Add the world setting and finalized synopsis as context to the messages."""
        context = f"The book takes place in the following world:\n\n{world_theme}"
        if synopsis and synopsis.strip():
            context += f"\n\nThe Story Synopsis is:\n\n{synopsis.strip()}"
        messages.append({"role": "system", "content": context})

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
        outline_context = self._format_outline_context()

        # Define system prompts for each agent type
        self.system_prompts = {
            "memory_keeper": f"""You are the keeper of the story's continuity and context.
Your responsibilities:
1. Track and summarize each chapter's key events
2. Monitor character development and relationships
3. Maintain world-building consistency
4. Flag any continuity issues

### Outline Context
{outline_context}

Format your responses as follows:
- Start updates with 'MEMORY UPDATE:'
- List key events with 'EVENT:'
- List character developments with 'CHARACTER:'
- List world details with 'WORLD:'
- Flag issues with 'CONTINUITY ALERT:'
""",
            "character_generator": """You are an expert character creator who designs rich, memorable characters.

Your responsibility is creating detailed character profiles for a story.
When given a world setting and number of characters:
1. Create unique, interesting characters that fit within the world
2. Give each character distinct traits, motivations, and backgrounds
3. Ensure characters have depth and potential for development
4. Include both protagonists and antagonists as appropriate
5. Treat the target number of characters as a guideline, not a hard limit — create as many as the story needs

Format your output EXACTLY as:
CHARACTER_PROFILES:

[CHARACTER NAME 1]:
- Role: [Main character, supporting character, antagonist, etc.]
- Age/Species: [Character's age and species]
- Physical Description: [Detailed appearance]
- Personality: [Core personality traits]
- Background: [Character history and origins]
- Motivations: [What drives the character]
- Skills/Abilities: [Special talents or powers]
- Relationships: [Connections to other characters or groups]
- Arc: [How this character might develop over the story]

[CHARACTER NAME 2]:
[Follow same format as above]

[And so on for all requested characters]

Always provide specific, detailed content - never use placeholders.
Ensure characters fit logically within the established world setting.
""",
            "story_planner": """You are an expert story planner. Your task is to create a concise, complete story synopsis based on a conversation with an author.

From the provided conversation, you must extract the following information:
- **Genre**: The genre of the story.
- **Premise**: The core idea or setup of the story.
- **Ending**: The intended conclusion of the story.
- **Other Information**: Any other relevant details provided by the author.

Then, using this information, generate a synopsis of the story in a traditional three-act structure. Each act must be clearly labeled.

The synopsis should be concise and complete:
- Aim for 500 to 1000 words.
- Present the story at the level of its essential narrative arc: the driving conflicts, the key turning points that move the story forward, and how the premise builds toward the ending.
- Prioritize the events that matter most to the whole story, and give each of them enough focus to be clear.
- Write economically so every sentence adds meaningful new information about the plot, the conflicts, or the main character's journey.

The final output should be only the complete synopsis.
""",
            "action_beats_generator": """You are an expert in creating detailed action beats for a script.

Your responsibility is to take a chapter summary and generate a list of highly detailed action beats.
When given a chapter summary:
1. Generate a list of action beats that flesh out the chapter
2. Always use proper nouns instead of pronouns
3. Ensure the action beats are highly detailed and suitable for a script

Format your output EXACTLY as:
ACTION_BEATS:
- Beat 1: [Detailed description of the action]
- Beat 2: [Detailed description of the action]
- Beat 3: [Detailed description of the action]

Always provide specific, detailed content - never use placeholders.
""",
            "outline_creator": f"""Generate a detailed outline targeting approximately {num_chapters} chapters.

Start with "OUTLINE:" and end with "END OF OUTLINE"

YOU MUST USE EXACTLY THIS FORMAT FOR EACH CHAPTER - NO DEVIATIONS:

Optional: ### [Act 1]: [Act Title] ([Act Title in local language if applicable])

Chapter 1: [Title] ([Title in local language if applicable])
- Key Events:
    * [Event 1]
    * [Event 2]
    * [Event 3]
- Character Developments: [Specific character moments and changes]
- Setting: [Specific location and atmosphere]
- Tone: [Specific emotional and narrative tone]

Chapter 2: [Title] ([Title in local language if applicable])
- Key Events:
    * [Event 1]
    * [Event 2]
    * [Event 3]
- Character Developments: [Specific character moments and changes]
- Setting: [Specific location and atmosphere]
- Tone: [Specific emotional and narrative tone]

[CONTINUE THE SEQUENCE, TARGETING AROUND {num_chapters} CHAPTERS TOTAL]

CRITICAL REQUIREMENTS:
1. Aim for roughly {num_chapters} chapters (a guideline, not a strict limit), numbered sequentially from 1 without gaps or duplicates
2. NEVER repeat chapter numbers or restart the numbering
3. EVERY chapter must have AT LEAST 3 specific Key Events
4. Maintain a coherent story flow from the first chapter to the last
5. Use proper indentation with bullet points for Key Events
6. NO EXCEPTIONS to this format - follow it precisely for all chapters

Initial Premise:
{initial_prompt}
""",
            "world_builder": f"""You are an expert in world-building who creates rich, consistent settings.
            
Your role is to establish ALL settings and locations needed for the entire story based on a provided story arc.

### Outline Context
{outline_context}

Your responsibilities:
1. Review the story arc to identify every location and setting needed
2. Create detailed descriptions for each setting, including:
- Physical layout and appearance
- Atmosphere and environmental details
- Important objects or features
- Sensory details (sights, sounds, smells)
3. Identify recurring locations that appear multiple times
4. Note how settings might change over time
5. Create a cohesive world that supports the story's themes

Format your response as:
WORLD_ELEMENTS:

[LOCATION NAME]:
- Physical Description: [detailed description]
- Atmosphere: [mood, time of day, lighting, etc.]
- Key Features: [important objects, layout elements]
- Sensory Details: [what characters would experience]

[RECURRING ELEMENTS]:
- List any settings that appear multiple times
- Note any changes to settings over time

[TRANSITIONS]:
- How settings connect to each other
- How characters move between locations
""",
            "writer": f"""You are an expert creative writer, a master storyteller who brings scenes to life with breathtaking detail and deep emotional resonance.

Your mission is to write scenes based on the provided outline context and the user's request, 
adhering to the following directives and craft rules at all times.

### Outline Context
{outline_context}

---
### Core Directives (Non-Negotiable Rules)
1.  **Strict Plot Adherence:** You must follow the provided **Chapter Outline / Action Beats** with absolute precision and in the correct order. Do not add new plot points, deviate from the sequence, or skip any beats. Your task is to bring the provided outline to life.
2.  **Mandatory 5000-Word Minimum:** Each chapter **MUST be at least 5000 words**. This is a hard requirement. If you feel the story beats have been covered but the chapter is too short, you must expand upon the existing scenes with deeper internal monologue, richer sensory detail, and more nuanced character interactions. Do not rush to the end.
3.  **Scene Integrity:** Write a single, complete chapter with a clear beginning, middle, and end as defined by the story beats. Conclude the chapter exactly where the final story beat specifies. Ensure all transitions are smooth and logical.

---
### Craft & Style Rules (Your Authorial Voice)
*   **Show, Don't Tell:** This is your primary storytelling technique. Reveal character, plot, and world-building through character actions, subtext, body language, and sensory information, not exposition.
*   **Prose and Cadence:** Create engaging, dynamic prose. Employ a varied sentence structure, mixing short, punchy sentences for tension with longer, descriptive sentences for atmosphere.
*   **Details Matter:** Use rich, vivid details to immerse the reader. Add a lot of details, and describe the environment and characters where it makes sense.
*   **Authentic, Purposeful Dialogue:** Dialogue must sound like real people talking. Every line must either reveal character, advance the plot, or build tension. Each character's voice must be distinct and consistent with their profile.
*   **Grounded Tone:** Avoid clichés, melodrama, and overly sentimental prose. Keep the emotional expression authentic and grounded.
*   **Forbidden Words:** You are forbidden from using the following words: **peril, fraught, thwart, dire, that, feel/feeling/felt, back, just, then, ail, look, maybe, knew/know**. Use stronger verbs and more descriptive phrasing instead.

---

Always reference the outline and previous content.
Mark drafts with 'SCENE:' and final versions with 'SCENE FINAL:'
""",
            "editor": f"""You are an expert editor ensuring quality and consistency.

Your mission is to review and improve the provided chapter content based on the provided outline context and the user's request, 
adhering to the following directives at all times.

### Outline Context
{outline_context}

---
### Core Directives (Non-Negotiable Rules)
1. Check alignment with outline
2. Verify character consistency
3. Maintain world-building rules
4. Improve prose quality
5. Return complete edited chapter
6. Never ask to start the next chapter, as the next step is finalizing this chapter
7. Each chapter MUST be at least 5000 words.

Format your responses:
1. Start critiques with 'FEEDBACK:'
2. Provide suggestions with 'SUGGEST:'
3. Return full edited chapter with 'EDITED_SCENE:'

---

Always reference specific outline elements in your feedback.
""",
            # Add a special system prompt for conversational world building
            "world_builder_chat": """You are a collaborative, creative world-building assistant helping an author develop a rich, detailed world for their book.

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
6. Gently guide them toward creating a coherent, interesting world

When they're ready to finalize, you'll help organize their ideas into a comprehensive world setting document.
""",
            # Add a special system prompt for conversational action beats building
            "action_beats_chat": """You are a collaborative, creative assistant helping an author brainstorm and refine action beats for a chapter.

Your approach during this brainstorming phase:
1. Focus on DISCUSSING action beat ideas, not generating the complete list yet.
2. Help explore different action sequences, character movements, and plot advancements.
3. Ask thoughtful questions about their vision for the action beats.
4. Offer suggestions that build on their ideas, including:
    - Potential dynamic actions or conflicts.
    - Ways to integrate character development into action.
    - Pacing and tension within action sequences.
    - Visual and sensory details for the action.
5. Maintain a friendly, conversational tone.
6. Help them think through different action beat options.
7. NEVER generate a full list of action beats during this chat phase.

IMPORTANT: This is a brainstorming conversation. DO NOT generate the formal action beats until the author is ready to finalize.
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
6. Help them think through different story options
7. NEVER generate a full chapter-by-chapter outline during this chat phase
8. DO NOT use chapter numbers or list out chapters - this is for brainstorming only

IMPORTANT: This is a brainstorming conversation. DO NOT generate the formal outline until the author is ready to finalize.

The book will likely have around {num_chapters} chapters (a target, not a strict requirement), but during this chat focus on story elements, not chapter structure.
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
            # Add a special system prompt for inline writing
            "inline_writer": """You are an expert creative writer — a master storyteller who brings scenes to life with immersive detail, emotional subtlety, and narrative precision.

Your task is to write or revise narrative text in a way that follows these core storytelling principles:

---
### Craft & Style Rules (Your Authorial Voice)

* **Show, Don’t Tell:** Prioritize subtext, action, and sensory cues over exposition. Reveal characters and world through what they do, say, and notice — not what is explained.
* **Prose and Cadence:** Use varied sentence structure. Short, sharp sentences build tension; longer, descriptive ones evoke atmosphere and introspection.
* **Details Matter:** Describe environments, physical gestures, and internal states with vivid, purposeful detail that serves character or tone.
* **Authentic, Purposeful Dialogue:** Dialogue must sound natural and distinct to each character. Every line should reveal character, escalate tension, or move the plot forward.
* **Grounded Emotion:** Avoid melodrama or sentimentality. Emotional moments should be honest, restrained, and earned through context.
* **Banned Words:** Avoid the following: **peril, fraught, thwart, dire, that, feel/feeling/felt, back, just, then, ail, look, maybe, knew/know**. Replace them with stronger, more specific language.
""",
            # Add specific inline writer prompts
            "inline_reviser": "You are a creative writer who revises narrative text to improve clarity, tone, and flow while preserving intent.",
            "inline_continuer": "You are a creative writer who continues narrative text in the same tone and voice without repeating content.",
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

        # Disable the clean-up logic for now
        # # Clean up the response based on agent type
        # if agent_name == "outline_creator":
        #     # Extract just the outline part
        #     start = response.find("OUTLINE:")
        #     end = response.find("END OF OUTLINE")
        #     if start != -1 and end != -1:
        #         cleaned_response = response[start : end + len("END OF OUTLINE")]
        #         return cleaned_response
        # elif agent_name == "writer":
        #     # Handle writer's scene format
        #     if "SCENE FINAL:" in response:
        #         parts = response.split("SCENE FINAL:")
        #         if len(parts) > 1:
        #             return parts[1].strip()
        # elif agent_name == "world_builder":
        #     # Extract the world elements part
        #     start = response.find("WORLD_ELEMENTS:")
        #     if start != -1:
        #         return response[start:].strip()
        #     else:
        #         # Try to find any content that looks like world-building
        #         for marker in [
        #             "Time Period",
        #             "Setting:",
        #             "Locations:",
        #             "Major Locations",
        #         ]:
        #             if marker in response:
        #                 return response
        # elif agent_name == "story_planner":
        #     # Extract the story arc part
        #     start = response.find("STORY_ARC:")
        #     if start != -1:
        #         return response[start:].strip()
        # elif agent_name == "character_generator":
        #     # Extract the character profiles part
        #     start = response.find("CHARACTER_PROFILES:")
        #     if start != -1:
        #         return response[start:].strip()
        #     else:
        #         # Try to find any content that looks like character profiles
        #         for marker in [
        #             "Character 1:",
        #             "Main Character:",
        #             "Protagonist:",
        #             "CHARACTER_PROFILES",
        #         ]:
        #             if marker in response:
        #                 return response

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
        self, chat_history, topic, synopsis, user_message
    ) -> str:
        """Generate a chat response based on conversation history"""
        # Format the messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["world_builder_chat"]}
        ]

        # Add the story synopsis context
        self._add_synopsis_context(messages, synopsis)

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
        self, chat_history, topic, synopsis, user_message
    ):
        """Generate a streaming chat response based on conversation history"""
        # Format the messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["world_builder_chat"]}
        ]

        # Add the story synopsis context
        self._add_synopsis_context(messages, synopsis)

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

    def generate_final_world(self, chat_history, topic, synopsis) -> str:
        """Generate final world setting based on chat history"""
        # Format the messages for the API call
        messages = [
            {
                "role": "system",
                "content": """You are an expert world-building specialist.
    Based on the entire conversation with the user, create a comprehensive, well-structured world setting document.
    
    Format your response as:
    WORLD_ELEMENTS:
    
    1. Time period and setting: [detailed description]
    2. Major locations: [detailed description of each key location]
    3. Cultural/historical elements: [key cultural and historical aspects]
    4. Technology/magical elements: [if applicable]
    5. Social/political structures: [governments, factions, etc.]
    6. Environment and atmosphere: [natural world aspects]
    
    Make this a complete, cohesive reference document that covers all important aspects of the world
    mentioned in the conversation. Add necessary details to fill any gaps, while staying true to
    everything established in the chat history.
""",
            }
        ]

        # Add the story synopsis context
        self._add_synopsis_context(messages, synopsis)

        # Add conversation history
        for entry in chat_history:
            role = "user" if entry["role"] == "user" else "assistant"
            messages.append({"role": role, "content": entry["content"]})

        # Add a final instruction to generate the world setting
        messages.append(
            {
                "role": "user",
                "content": f"Please create the final, comprehensive world setting document for my book about '{topic}' based on our conversation.",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "world_builder_specialist", "final_world_request"
        )

        # Call the API
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **self._sampling_kwargs(),
        )

        # Extract the response
        response = completion.choices[0].message.content

        # Ensure it has the WORLD_ELEMENTS header for consistency
        if "WORLD_ELEMENTS:" not in response:
            response = "WORLD_ELEMENTS:\n\n" + response

        return response

    def generate_final_world_stream(self, chat_history, topic, synopsis):
        """Generate the final world setting based on the chat history using streaming."""
        # Format messages for the API call
        messages = [{"role": "system", "content": self.system_prompts["world_builder"]}]

        # Add the story synopsis context
        self._add_synopsis_context(messages, synopsis)

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete world setting
        messages.append(
            {
                "role": "user",
                "content": f"Based on our conversation about '{topic}', please create a comprehensive and detailed world setting. Format it with clear sections for different aspects of the world (geography, magic/technology, culture, etc.). This will be the final world setting for the book.",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "world_builder", "final_world_stream_request"
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
            stream, "world_builder", "final_world_stream_response"
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

    def get_world_context(self) -> str:
        """Get a formatted string of all world elements"""
        if not self.world_elements:
            return ""

        elements = ["WORLD ELEMENTS:"]
        for name, desc in self.world_elements.items():
            elements.append(f"\n{name}:\n{desc}")

        return "\n".join(elements)

    def get_character_context(self) -> str:
        """Get a formatted string of all character developments"""
        if not self.character_developments:
            return ""

        developments = ["CHARACTER DEVELOPMENTS:"]
        for name, devs in self.character_developments.items():
            developments.append(f"\n{name}:")
            for i, dev in enumerate(devs, 1):
                developments.append(f"{i}. {dev}")

        return "\n".join(developments)

    def generate_chat_response_characters(
        self, chat_history, world_theme, synopsis, user_message, num_characters=3
    ):
        """Generate a chat response about character creation."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["character_generator"]}
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
            messages, "character_generator", "chat_characters_request"
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
            {"role": "system", "content": self.system_prompts["character_generator"]}
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
            messages, "character_generator", "chat_characters_stream_request"
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
            stream, "character_generator", "chat_characters_stream_response"
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
                "content": f"Based on our conversation, please create around {num_characters} detailed character profiles for the book (this is a target, not a strict limit — use your judgment based on the story). Format each character with Name, Role, Physical Description, Background, Personality, and Goals/Motivations. This will be the final character list for the book.",
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

        # Add world theme and character context
        messages.append(
            {
                "role": "system",
                "content": f"The book takes place in the following world:\n\n{world_theme}\n\nThe characters include:\n\n{characters}\n\nThe Story Synopsis is:\n\n{synopsis}",
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

        # Add world theme and character context
        messages.append(
            {
                "role": "system",
                "content": f"The book takes place in the following world:\n\n{world_theme}\n\nThe characters include:\n\n{characters}\n\nThe Story Synopsis is:\n\n{synopsis}",
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

    def generate_final_outline_stream(
        self, chat_history, world_theme, characters, synopsis, num_chapters=10
    ):
        """Generate the final outline based on chat history using streaming."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["outline_creator"]}
        ]

        # Add world theme and character context
        messages.append(
            {
                "role": "system",
                "content": f"The book takes place in the following world:\n\n{world_theme}\n\nThe characters include:\n\n{characters}\n\nThe Story Synopsis is:\n\n{synopsis}",
            }
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete outline with specific formatting guidance
        messages.append(
            {
                "role": "user",
                "content": f"""Based on our conversation, please create a detailed outline of approximately {num_chapters} chapters for the book.

CRITICAL REQUIREMENTS:
1. Aim for roughly {num_chapters} chapters (a guideline, not a strict limit); number chapters sequentially from 1 without gaps or duplicates
2. NEVER repeat chapter numbers or restart the numbering
3. Follow the exact format specified in your instructions
4. Each chapter must have a unique title and at least 3 specific key events
5. Maintain a coherent story from beginning to end

Format it as a properly structured outline with clear chapter sections and events. This will be the final outline for the book.
""",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "outline_creator", "final_outline_stream_request"
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
            stream, "outline_creator", "final_outline_stream_response"
        )

    def generate_chat_response_action_beats_stream(
        self, chat_history, chapter_summary, world_theme, characters, user_message
    ):
        """Generate a streaming chat response about action beats creation."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["action_beats_chat"]}
        ]

        # Add context
        messages.append(
            {
                "role": "system",
                "content": f"Chapter Summary:\n\n{chapter_summary}\n\nWorld:\n\n{world_theme}\n\nCharacters:\n\n{characters}",
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

        self._save_debug_messages(
            messages, "action_beats_chat", "chat_action_beats_stream_request"
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
            stream, "action_beats_chat", "chat_action_beats_stream_response"
        )

    def generate_final_action_beats_stream(
        self, chat_history, chapter_summary, world_theme, characters, num_beats
    ):
        """Generate the final action beats based on chat history using streaming."""
        # Format messages for the API call
        messages = [
            {"role": "system", "content": self.system_prompts["action_beats_generator"]}
        ]

        # Add context
        messages.append(
            {
                "role": "system",
                "content": f"Chapter Summary:\n\n{chapter_summary}\n\nWorld:\n\n{world_theme}\n\nCharacters:\n\n{characters}",
            }
        )

        # Add conversation context from chat history
        for message in chat_history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})

        # Add the final instruction to create the complete action beat profiles
        messages.append(
            {
                "role": "user",
                "content": f"Based on our conversation, please generate the chapter's action beats, targeting approximately {num_beats} - you may produce a few more or fewer if the chapter's pacing or structure calls for it. Ensure proper nouns are used instead of pronouns.",
            }
        )

        # Save the messages for debugging
        self._save_debug_messages(
            messages, "action_beats_generator", "final_action_beats_stream_request"
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
            stream, "action_beats_generator", "final_action_beats_stream_response"
        )
