import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import { cors } from "hono/cors";
import {
  CopilotRuntime,
  createCopilotEndpoint,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";
import { A2AAgent } from "@ag-ui/a2a";
import { A2AClient } from "@a2a-js/sdk/client";

const A2A_AGENT_URL =
  process.env.A2A_AGENT_URL || "http://localhost:10002";
const PORT = parseInt(process.env.PORT || "3000", 10);

const a2aClient = new A2AClient(A2A_AGENT_URL);
const agent = new A2AAgent({ a2aClient });

const runtime = new CopilotRuntime({
  agents: { default: agent },
  runner: new InMemoryAgentRunner(),
});

// createCopilotEndpoint internally calls app.basePath(basePath),
// so mount at "/" to avoid doubling the prefix.
const copilotApp = createCopilotEndpoint({
  runtime,
  basePath: "/api/copilotkit",
});

const app = new Hono();

app.use("/*", cors());

// Health check for Railway
app.get("/health", (c) => c.json({ status: "ok" }));

// Serve restaurant images
app.use(
  "/static/*",
  serveStatic({
    root: "./agent/images",
    rewriteRequestPath: (path) => path.replace("/static", ""),
  })
);

// Mount at root — copilotApp already has basePath="/api/copilotkit" set
app.route("/", copilotApp);

console.log(`AG-UI server listening on port ${PORT}`);
console.log(`  AG-UI endpoint: http://localhost:${PORT}/api/copilotkit`);
console.log(`  A2A agent:      ${A2A_AGENT_URL}`);

serve({ fetch: app.fetch, port: PORT });
