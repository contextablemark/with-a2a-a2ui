# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Restaurant agent for the pure AG-UI stack.

Assembles the LLM instruction by combining google/A2UI's basic-catalog
schema and few-shot examples (pulled via `a2ui-agent-sdk`) with our
own workflow rules that target the `render_a2ui` tool. The standard
`generate_system_prompt()` hardcodes `<a2ui-json>`-tag workflow rules
which conflict with the pure AG-UI path, so we bypass that wrapper and
pull the schema + examples pieces directly.

CopilotRuntime's a2ui middleware still injects the `render_a2ui` tool
spec and a generic usage guide into RunAgentInput; our instruction
provides the *domain* prompt (role, workflow, component choices).
"""

import logging
import os

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_9
from a2ui.schema.manager import A2uiSchemaManager
from ag_ui_adk import AGUIToolset
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from prompt_builder import ROLE_DESCRIPTION, UI_DESCRIPTION, get_text_prompt
from tools import get_restaurants

logger = logging.getLogger(__name__)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# Workflow rules tailored to the pure AG-UI path. Replaces
# a2ui-agent-sdk's DEFAULT_WORKFLOW_RULES (which teaches <a2ui-json>
# tag wrapping — the wire format of the old A2A path).
_RENDER_A2UI_WORKFLOW_RULES = """
The generated response MUST follow these rules:
- To show the user any interactive UI, call the `render_a2ui` tool. Do
  NOT emit A2UI JSON inline in your text response, and do NOT wrap
  output in `<a2ui-json>` / `</a2ui-json>` tags — those tags are legacy
  and the runtime will ignore them.
- When you call `render_a2ui`, pass:
    * `surfaceId`: a stable identifier for this UI surface.
    * `catalogId`: "https://a2ui.org/specification/v0_9/basic_catalog.json".
    * `components`: the flat v0.9 component array (see examples below).
      The root component MUST be the FIRST element, with `id: "root"`.
      Parent components MUST appear before their children (top-down order).
    * `data`: the data model for path-bound components. For list layouts,
      pass `{"items": [ … ]}` where `items` mirrors the structure in the
      examples' `updateDataModel` payloads.
- Your FINAL action in every turn where you have something to show the
  user MUST be a call to `render_a2ui`. Never end a turn with plain
  text, a summary, or a tool result alone.
- The components must validate against the A2UI component schema below.
"""


def _build_instruction() -> str:
    """Compose instruction = role + custom workflow + UI + schema + examples."""
    try:
        examples_path = os.path.join(_SCRIPT_DIR, "examples", VERSION_0_9)
        schema_manager = A2uiSchemaManager(
            version=VERSION_0_9,
            catalogs=[
                BasicCatalog.get_config(
                    version=VERSION_0_9, examples_path=examples_path
                )
            ],
            schema_modifiers=[remove_strict_validation],
        )
        catalog = schema_manager.get_selected_catalog()
        parts = [
            ROLE_DESCRIPTION,
            f"## Workflow Description:\n{_RENDER_A2UI_WORKFLOW_RULES}",
            f"## UI Description:\n{UI_DESCRIPTION}",
            catalog.render_as_llm_instructions(),
        ]
        examples_str = schema_manager.load_examples(catalog, validate=True)
        if examples_str:
            parts.append(
                "### Examples (component trees and data models; adapt "
                "to render_a2ui tool arguments):\n" + examples_str
            )
        return "\n\n".join(parts)
    except Exception as e:
        logger.warning(
            "Falling back to text-only prompt; schema-manager prompt"
            f" generation failed: {e}"
        )
        return get_text_prompt()


# The rendered instruction contains literal `{…}` patterns (JSON-schema
# property names, example data model paths) that ADK's string-instruction
# path would try to interpolate as session-state variables and crash on.
# Passing instruction as a callable sets `bypass_state_injection=True` in
# ADK's canonical_instruction resolver, so the string is handed to the
# LLM verbatim.
_CACHED_INSTRUCTION = _build_instruction()


def build_agent() -> LlmAgent:
    return LlmAgent(
        # `reasoning_effort="disable"` turns off Gemini thinking mode
        # (`includeThoughts: false` in GenerationConfig).
        #
        # Why: with thinking on, Gemini embeds an ephemeral thought
        # signature into tool_call_ids via a `__thought__<base64>`
        # suffix. The suffix differs between the streamed (client-
        # facing) and persisted (server-facing) events for the same
        # call, so ag_ui_adk's pending-tool-call list stores one id
        # while the client sends back a tool result with a different
        # id. The comparison fails ("Skipping tool result batch - no
        # matching pending tool calls"), leaving the second turn with
        # nothing to do. Disabling thoughts drops the suffix and makes
        # ids stable across the round trip.
        model=LiteLlm(
            model=os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash"),
            reasoning_effort="disable",
        ),
        name="restaurant_agent",
        description="Finds restaurants and helps book tables.",
        instruction=lambda _ctx: _CACHED_INSTRUCTION,
        # AGUIToolset surfaces client-injected tools (e.g. `render_a2ui`
        # added by CopilotRuntime's a2ui middleware) as long-running /
        # fire-and-forget tools so ADK doesn't try to execute them
        # server-side.
        tools=[get_restaurants, AGUIToolset()],
    )
