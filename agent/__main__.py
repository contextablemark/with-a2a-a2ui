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

"""Pure AG-UI entrypoint for the restaurant agent.

Exposes the ADK LlmAgent as an AG-UI HTTP endpoint via `ag_ui_adk`.
The Node CopilotRuntime in front of this process adds the `render_a2ui`
tool and A2UI middleware; this server only needs to run the LLM and
stream standard AG-UI events (TEXT_MESSAGE_*, TOOL_CALL_*).
"""

import logging
import os

import click
import uvicorn
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from agent import build_agent
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000, type=int)
def main(host: str, port: int) -> None:
    try:
        if not os.getenv("GOOGLE_GENAI_USE_VERTEXAI") == "TRUE":
            if not os.getenv("GEMINI_API_KEY"):
                raise MissingAPIKeyError(
                    "GEMINI_API_KEY environment variable not set and"
                    " GOOGLE_GENAI_USE_VERTEXAI is not TRUE."
                )

        adk_agent = ADKAgent(
            adk_agent=build_agent(),
            app_name="restaurant_finder",
            user_id="client",
        )

        app = FastAPI()

        @app.get("/health")
        def health() -> dict:
            return {"status": "ok"}

        add_adk_fastapi_endpoint(app, adk_agent, path="/")

        uvicorn.run(app, host=host, port=port)
    except MissingAPIKeyError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        exit(1)


if __name__ == "__main__":
    main()
