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

// --- A2UI extension negotiation patch --------------------------------------
// @ag-ui/a2a@0.0.6 hard-codes `X-A2A-Extensions: https://a2ui.org/a2a-extension/a2ui/v0.8`
// on every A2A call, so v0.9-capable agents never get asked for v0.9 surfaces.
// Override the prototype method with an identical implementation that
// advertises v0.9 preferred, v0.8 fallback. Drop this shim once @ag-ui/a2a
// grows a supported extension-list config.
const A2UI_EXT_URIS = [
  "https://a2ui.org/a2a-extension/a2ui/v0.9",
  "https://a2ui.org/a2a-extension/a2ui/v0.8",
];

A2AAgent.prototype.initializeExtension = function patchedInitializeExtension(
  client,
) {
  const addExtensionHeader = (headers) => {
    const existing = (headers.get("X-A2A-Extensions") ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
    for (const uri of A2UI_EXT_URIS) {
      if (!existing.includes(uri)) existing.push(uri);
    }
    headers.set("X-A2A-Extensions", existing.join(", "));
  };

  const patchFetch = () => {
    const originalFetch = globalThis.fetch;
    if (!originalFetch) return () => {};
    const extensionFetch = async (input, init) => {
      const headers = new Headers(init?.headers);
      addExtensionHeader(headers);
      return originalFetch(input, { ...init, headers });
    };
    globalThis.fetch = extensionFetch;
    return () => {
      globalThis.fetch = originalFetch;
    };
  };

  const wrapPromise = async (operation) => {
    const restore = patchFetch();
    try {
      return await operation();
    } finally {
      restore();
    }
  };

  const wrapStream = (original) => {
    if (!original) return undefined;
    return function wrapped(...args) {
      const restore = patchFetch();
      const iterator = original.apply(this, args);
      return (async function* () {
        try {
          for await (const value of iterator) yield value;
        } finally {
          restore();
        }
      })();
    };
  };

  const originalSendMessage = client.sendMessage.bind(client);
  client.sendMessage = (params) => wrapPromise(() => originalSendMessage(params));

  const originalSendMessageStream = client.sendMessageStream?.bind(client);
  const wrappedSendMessageStream = wrapStream(originalSendMessageStream);
  if (wrappedSendMessageStream) {
    client.sendMessageStream = wrappedSendMessageStream;
  }

  const originalResubscribeTask = client.resubscribeTask?.bind(client);
  const wrappedResubscribeTask = wrapStream(originalResubscribeTask);
  if (wrappedResubscribeTask) {
    client.resubscribeTask = wrappedResubscribeTask;
  }
};
// --- end patch ---------------------------------------------------------------

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
console.log(`  A2UI extensions requested: ${A2UI_EXT_URIS.join(", ")}`);

serve({ fetch: app.fetch, port: PORT });
