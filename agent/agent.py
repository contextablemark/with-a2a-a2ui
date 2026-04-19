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

import json
import logging
import os
from collections import OrderedDict
from collections.abc import AsyncIterable
from typing import Any, Dict, Optional

import jsonschema
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    TextPart,
)
from google.adk.agents import run_config
from google.adk.agents.llm_agent import LlmAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from prompt_builder import (
    ROLE_DESCRIPTION,
    UI_DESCRIPTION,
    get_text_prompt,
)
from tools import get_restaurants

from a2ui.a2a.extension import get_a2ui_agent_extension
from a2ui.a2a.parts import parse_response_to_parts, stream_response_to_parts
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.parser.parser import parse_response
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import (
    A2UI_CLOSE_TAG,
    A2UI_OPEN_TAG,
    VERSION_0_8,
    VERSION_0_9,
)
from a2ui.schema.manager import A2uiSchemaManager

logger = logging.getLogger(__name__)

# Examples are shipped alongside this module so they resolve regardless of CWD
# (Railway starts the process from a tmp dir; see scripts/start-railway.sh).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class RestaurantAgent:
    """An agent that finds restaurants based on user criteria.

    Builds one schema manager and one ADK runner per supported A2UI version;
    the active version is selected per request by the executor via the A2A
    extension negotiation result.

    base_url is split from agent_url for Railway: agent_card carries the
    internal URL (how other A2A agents reach this one), while tool output
    uses the public URL (where the browser/client fetches images).
    """

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self, agent_url: str, public_url: str):
        self.agent_url = agent_url
        self.public_url = public_url
        self._agent_name = "Restaurant Agent"
        self._user_id = "remote_agent"
        self._text_runner: Optional[Runner] = self._build_runner(
            self._build_llm_agent()
        )

        self._schema_managers: Dict[str, A2uiSchemaManager] = {}
        self._ui_runners: Dict[str, Runner] = {}
        self._parsers: "OrderedDict[str, Any]" = OrderedDict()
        self._max_parsers = 1000

        for version in [VERSION_0_8, VERSION_0_9]:
            schema_manager = self._build_schema_manager(version)
            self._schema_managers[version] = schema_manager
            agent = self._build_llm_agent(schema_manager)
            self._ui_runners[version] = self._build_runner(agent)

        self._agent_card = self._build_agent_card()

    @property
    def agent_card(self) -> AgentCard:
        return self._agent_card

    def _build_schema_manager(self, version: str) -> A2uiSchemaManager:
        examples_path = os.path.join(_SCRIPT_DIR, "examples", version)
        return A2uiSchemaManager(
            version=version,
            catalogs=[
                BasicCatalog.get_config(
                    version=version, examples_path=examples_path
                )
            ],
            schema_modifiers=[remove_strict_validation],
        )

    def _build_agent_card(self) -> AgentCard:
        extensions = [
            get_a2ui_agent_extension(
                version,
                sm.accepts_inline_catalogs,
                sm.supported_catalog_ids,
            )
            for version, sm in self._schema_managers.items()
        ]

        capabilities = AgentCapabilities(
            streaming=True,
            extensions=extensions,
        )
        skill = AgentSkill(
            id="find_restaurants",
            name="Find Restaurants Tool",
            description=(
                "Helps find restaurants based on user criteria"
                " (e.g., cuisine, location)."
            ),
            tags=["restaurant", "finder"],
            examples=["Find me the top 10 chinese restaurants in the US"],
        )

        return AgentCard(
            name="Restaurant Agent",
            description="This agent helps find restaurants based on user criteria.",
            url=self.agent_url,
            version="1.0.0",
            default_input_modes=RestaurantAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=RestaurantAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

    def _build_runner(self, agent: LlmAgent) -> Runner:
        return Runner(
            app_name=self._agent_name,
            agent=agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

    def get_processing_message(self) -> str:
        return "Finding restaurants that match your criteria..."

    def _build_llm_agent(
        self, schema_manager: Optional[A2uiSchemaManager] = None
    ) -> LlmAgent:
        """Builds the LLM agent for the restaurant agent."""
        litellm_model = os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash")

        instruction = (
            schema_manager.generate_system_prompt(
                role_description=ROLE_DESCRIPTION,
                ui_description=UI_DESCRIPTION,
                include_schema=True,
                include_examples=True,
                validate_examples=True,
            )
            if schema_manager
            else get_text_prompt()
        )

        return LlmAgent(
            model=LiteLlm(model=litellm_model),
            name="restaurant_agent",
            description="An agent that finds restaurants and helps book tables.",
            instruction=instruction,
            tools=[get_restaurants],
        )

    async def stream(
        self, query, session_id, ui_version: Optional[str] = None
    ) -> AsyncIterable[dict[str, Any]]:
        # `base_url` lives in session state because tools.get_restaurants reads
        # it from tool_context.state to rewrite image URLs to the Railway host.
        session_state = {"base_url": self.public_url, "expression": "{expression}"}

        if ui_version:
            runner = self._ui_runners[ui_version]
            schema_manager = self._schema_managers[ui_version]
            selected_catalog = (
                schema_manager.get_selected_catalog() if schema_manager else None
            )
        else:
            runner = self._text_runner
            schema_manager = None
            selected_catalog = None

        session = await runner.session_service.get_session(
            app_name=self._agent_name,
            user_id=self._user_id,
            session_id=session_id,
        )
        if session is None:
            session = await runner.session_service.create_session(
                app_name=self._agent_name,
                user_id=self._user_id,
                state=session_state,
                session_id=session_id,
            )
        elif "base_url" not in session.state:
            session.state["base_url"] = self.public_url

        # One-shot interstitial. Under v0.9 subsequent yields are `parts`
        # DataParts and the LLM no longer emits a prose prefix (the
        # schema-manager prompt wraps output in <a2ui-json>…</a2ui-json>),
        # so without this the client sees nothing between RUN_STARTED and
        # the first surface snapshot 4-8 s later.
        yield {
            "is_task_complete": False,
            "updates": self.get_processing_message(),
        }

        max_retries = 1
        attempt = 0
        current_query_text = query

        if ui_version and (
            not selected_catalog or not selected_catalog.catalog_schema
        ):
            logger.error(
                "--- RestaurantAgent.stream: A2UI catalog schema not loaded for"
                f" version {ui_version}. Cannot perform UI validation. ---"
            )
            yield {
                "is_task_complete": True,
                "parts": [
                    Part(
                        root=TextPart(
                            text=(
                                "I'm sorry, I'm facing an internal configuration"
                                " error with my UI components. Please contact"
                                " support."
                            )
                        )
                    )
                ],
            }
            return

        while attempt <= max_retries:
            attempt += 1
            logger.info(
                f"--- RestaurantAgent.stream: Attempt {attempt}/{max_retries + 1}"
                f" for session {session_id} (ui_version={ui_version}) ---"
            )

            current_message = types.Content(
                role="user",
                parts=[types.Part.from_text(text=current_query_text)],
            )

            full_content_list: list[str] = []

            async def token_stream():
                async for event in runner.run_async(
                    user_id=self._user_id,
                    session_id=session.id,
                    run_config=run_config.RunConfig(
                        streaming_mode=run_config.StreamingMode.SSE
                    ),
                    new_message=current_message,
                ):
                    if event.content and event.content.parts:
                        for p in event.content.parts:
                            if p.text:
                                full_content_list.append(p.text)
                                yield p.text

            if selected_catalog:
                from a2ui.parser.streaming import A2uiStreamParser

                if session_id in self._parsers:
                    self._parsers.move_to_end(session_id)
                else:
                    self._parsers[session_id] = A2uiStreamParser(
                        catalog=selected_catalog
                    )
                    if len(self._parsers) > self._max_parsers:
                        self._parsers.popitem(last=False)

                async for part in stream_response_to_parts(
                    self._parsers[session_id],
                    token_stream(),
                ):
                    yield {
                        "is_task_complete": False,
                        "parts": [part],
                    }
            else:
                async for token in token_stream():
                    yield {
                        "is_task_complete": False,
                        "updates": token,
                    }

            final_response_content = "".join(full_content_list)

            is_valid = False
            error_message = ""

            if ui_version:
                logger.info(
                    "--- RestaurantAgent.stream: Validating UI response (Attempt"
                    f" {attempt})... ---"
                )
                try:
                    response_parts = parse_response(final_response_content)

                    for part in response_parts:
                        if not part.a2ui_json:
                            continue
                        selected_catalog.validator.validate(part.a2ui_json)
                        logger.info(
                            "--- RestaurantAgent.stream: UI JSON validated against"
                            f" schema (Attempt {attempt}). ---"
                        )
                        is_valid = True
                except (
                    ValueError,
                    json.JSONDecodeError,
                    jsonschema.exceptions.ValidationError,
                ) as e:
                    logger.warning(
                        f"--- RestaurantAgent.stream: A2UI validation failed: {e}"
                        f" (Attempt {attempt}) ---"
                    )
                    logger.warning(
                        "--- Failed response content:"
                        f" {final_response_content[:500]}... ---"
                    )
                    error_message = f"Validation failed: {e}."
            else:
                is_valid = True

            if is_valid:
                logger.info(
                    "--- RestaurantAgent.stream: Response is valid. Sending final"
                    f" response (Attempt {attempt}). ---"
                )
                final_parts = parse_response_to_parts(
                    final_response_content, fallback_text="OK."
                )
                yield {
                    "is_task_complete": True,
                    "parts": final_parts,
                }
                return

            if attempt <= max_retries:
                logger.warning(
                    "--- RestaurantAgent.stream: Retrying..."
                    f" ({attempt}/{max_retries + 1}) ---"
                )
                current_query_text = (
                    f"Your previous response was invalid. {error_message} You"
                    " MUST generate a valid response that strictly follows the"
                    " A2UI JSON SCHEMA. The response MUST be a JSON list of"
                    " A2UI messages. Ensure each JSON part is wrapped in"
                    f" '{A2UI_OPEN_TAG}' and '{A2UI_CLOSE_TAG}' tags. Please"
                    f" retry the original request: '{query}'"
                )

        logger.error(
            "--- RestaurantAgent.stream: Max retries exhausted. Sending text-only"
            " error. ---"
        )
        yield {
            "is_task_complete": True,
            "parts": [
                Part(
                    root=TextPart(
                        text=(
                            "I'm sorry, I'm having trouble generating the interface"
                            " for that request right now. Please try again in a"
                            " moment."
                        )
                    )
                )
            ],
        }
