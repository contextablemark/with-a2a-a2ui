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

Under this architecture the agent is a plain ADK LlmAgent. The Node
CopilotRuntime in front of it injects a `render_a2ui` tool (via
@copilotkit/runtime's a2ui middleware) and the usage guide that
teaches Gemini how to call it. We just expose `get_restaurants` as
the data-fetch tool; the LLM chains the two.
"""

import os

from ag_ui_adk import AGUIToolset
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from tools import get_restaurants

INSTRUCTION = """You help users find restaurants and book tables.

## Absolute rule

Every turn in which you have something to show the user, your FINAL
tool call of the turn MUST be `render_a2ui`. No exceptions. Never end a
turn with a plain-text reply, a summary, a clarifying question, or a
tool result alone — always finish by calling `render_a2ui` to present
the result as an interactive surface. Follow the usage guide for
`render_a2ui` provided in context exactly.

If `get_restaurants` returned data, your very next action is
`render_a2ui` — do not think, do not summarise, do not apologise, just
call it.

## Workflow

1. User asks for restaurants → call `get_restaurants` with their cuisine
   and location, then IMMEDIATELY call `render_a2ui` (same turn) to show
   the list. Each card must have an `action` whose
   `userAction.actionId` is `"select_restaurant"` with a `requestBody`
   containing `restaurantName`.

2. A `userAction` for `select_restaurant` comes back → call `render_a2ui`
   with a booking form (restaurant name, date, time, party size, diner
   name). The submit button's `action.userAction.actionId` must be
   `"submit_booking"`.

3. A `userAction` for `submit_booking` comes back → call `render_a2ui`
   with a confirmation surface summarising the reservation.

## Catalog rules

- Use the v0.9 basic catalog.
- Root component id MUST be `"root"`.
- Do not emit prose text alongside surface updates — the UI is the
  response."""


def build_agent() -> LlmAgent:
    return LlmAgent(
        model=LiteLlm(model=os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash")),
        name="restaurant_agent",
        description="Finds restaurants and helps book tables.",
        instruction=INSTRUCTION,
        # AGUIToolset surfaces client-injected tools (e.g. `render_a2ui`
        # added by CopilotRuntime's a2ui middleware) as long-running /
        # fire-and-forget tools so ADK doesn't try to execute them
        # server-side.
        tools=[get_restaurants, AGUIToolset()],
    )
