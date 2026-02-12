# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Full-stack AI agent starter combining CopilotKit, A2UI, and Google ADK. It implements a restaurant finder and reservation agent with rich UI components.

- **Frontend**: Next.js 16 (App Router, React 19) with CopilotKit and A2UI Lit web components
- **Backend**: Python 3.13+ agent using Google ADK + LiteLLM, communicating via A2A protocol
- **Ports**: Next.js on `localhost:3000`, Python agent on `localhost:10002`

## Commands

```bash
# Development (starts both UI and agent concurrently)
npm run dev
npm run dev:debug        # with LOG_LEVEL=debug
npm run dev:ui           # Next.js only (Turbopack)
npm run dev:agent        # Python agent only

# Build & production
npm run build            # next build
npm run start            # next start

# Lint
npm run lint             # ESLint (flat config)

# Python agent (manual)
cd agent && uv sync      # install deps
cd agent && uv run .     # run agent server
```

## Architecture

### Frontend (`app/`)

`app/page.tsx` renders `<CopilotChat>` inside a `<CopilotKit>` provider. The API route at `app/api/copilotkit/[[...slug]]/route.tsx` bridges CopilotKit to the Python agent by creating a `CopilotRuntime` wrapping an `A2AAgent` pointed at `http://localhost:10002`. The page is forced dynamic (`export const dynamic = "force-dynamic"`).

A2UI theme and styles are configured in `app/theme.ts` and `app/a2ui-theme.css`.

### Backend (`agent/`)

- `__main__.py` — Server entry point, sets up A2A server on port 10002
- `agent.py` — `RestaurantAgent` class using Google ADK with LiteLLM for LLM calls
- `agent_executor.py` — Handles A2A protocol requests
- `tools.py` — `get_restaurants()` tool that queries `restaurant_data.json`
- `prompt_builder.py` — A2UI component templates and prompt construction (large file with nested template examples for restaurant cards, booking forms, confirmations)

### A2UI Extension (`a2ui_extension/`)

Custom Python package that wraps A2A protocol for rendering UI components. Has its own `pyproject.toml` and test suite.

### Python Workspace

The root `pyproject.toml` defines a UV workspace with members `a2ui_extension` and `agent`.

## Environment Variables

Create `agent/.env`:
```
GEMINI_API_KEY=<your-key>
```
Optional: `GOOGLE_GENAI_USE_VERTEXAI=TRUE` for Vertex AI instead of API key auth.

## Key Dependencies

- **Node**: `@copilotkit/react-core`, `@copilotkit/runtime`, `@a2a-js/sdk`, `@a2ui/lit`, `@ag-ui/a2a`, `hono`, `zod`
- **Python**: `google-adk`, `google-genai`, `litellm`, `a2a-sdk`, `a2ui`
- **Package managers**: npm/pnpm/yarn/bun for Node; `uv` for Python
