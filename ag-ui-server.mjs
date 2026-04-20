import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import { cors } from "hono/cors";
import {
  CopilotRuntime,
  createCopilotEndpoint,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8000/";
const PORT = parseInt(process.env.PORT || "3000", 10);

const agent = new HttpAgent({ url: AGENT_URL });

// CopilotRuntime's a2ui middleware injects the `render_a2ui` tool plus its
// usage guide into RunAgentInput, watches for TOOL_CALL_ARGS deltas on that
// tool, and emits ACTIVITY_SNAPSHOT events wrapping the resulting v0.9
// A2UI ops for the client to render.
const runtime = new CopilotRuntime({
  agents: { default: agent },
  runner: new InMemoryAgentRunner(),
  a2ui: { enabled: true, injectA2UITool: true },
});

const copilotApp = createCopilotEndpoint({
  runtime,
  basePath: "/api/copilotkit",
});

const app = new Hono();

app.use("/*", cors());

app.get("/health", (c) => c.json({ status: "ok" }));

// Restaurant images referenced by get_restaurants' JSON data.
app.use(
  "/static/*",
  serveStatic({
    root: "./agent/images",
    rewriteRequestPath: (path) => path.replace("/static", ""),
  })
);

app.route("/", copilotApp);

console.log(`AG-UI server listening on port ${PORT}`);
console.log(`  Agent upstream: ${AGENT_URL}`);
console.log(`  Client endpoint: http://localhost:${PORT}/api/copilotkit/agent/default/run`);

serve({ fetch: app.fetch, port: PORT });
