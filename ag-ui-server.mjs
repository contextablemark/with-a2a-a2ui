import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import { cors } from "hono/cors";
import {
  CopilotRuntime,
  createCopilotEndpoint,
  InMemoryAgentRunner,
} from "@copilotkit/runtime/v2";
import { A2AAgent, convertA2AEventToAGUIEvents } from "@ag-ui/a2a";
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
// --- A2UI v0.9 DataPart pass-through patch ----------------------------------
// @ag-ui/a2a@0.0.6's converter only recognises v0.8 op names
// (beginRendering / surfaceUpdate / dataModelUpdate) on inbound DataParts;
// v0.9 envelopes ({version:"v0.9", createSurface:{…}} etc.) are silently
// dropped. Override streamMessage / blockingMessage to additionally emit an
// ACTIVITY_SNAPSHOT per v0.9 DataPart with the raw envelope as `content` so
// a2ui-4k-v0.9 clients can pass it straight to SurfaceStateManager.processMessage.
// The stock converter still runs first and handles text, tool calls, and any
// v0.8 a2ui ops exactly as before. Drop this shim once @ag-ui/a2a natively
// supports v0.9.
const A2UI_V09_OP_NAMES = [
  "createSurface",
  "updateComponents",
  "updateDataModel",
  "deleteSurface",
];

const extractV09SurfaceOp = (data) => {
  if (!data || typeof data !== "object") return null;
  if (data.version !== "v0.9") return null;
  for (const op of A2UI_V09_OP_NAMES) {
    const inner = data[op];
    if (
      inner &&
      typeof inner === "object" &&
      typeof inner.surfaceId === "string" &&
      inner.surfaceId.length > 0
    ) {
      return { surfaceId: inner.surfaceId, envelope: data };
    }
  }
  return null;
};

const extractParts = (event) => {
  if (!event || typeof event !== "object") return [];
  if (event.kind === "message") return event.parts ?? [];
  if (event.kind === "status-update") return event.status?.message?.parts ?? [];
  if (event.kind === "artifact-update") return event.artifact?.parts ?? [];
  return [];
};

const emitV09SnapshotsFor = (event, subscriber) => {
  for (const part of extractParts(event)) {
    if (part.kind !== "data") continue;
    const op = extractV09SurfaceOp(part.data);
    if (!op) continue;
    subscriber.next({
      type: "ACTIVITY_SNAPSHOT",
      messageId: op.surfaceId,
      activityType: "a2ui-surface",
      content: op.envelope,
      replace: false,
    });
  }
};

const buildConverterContext = function (tracker, aggregatedText) {
  return {
    role: "assistant",
    messageIdMap: this.messageIdMap,
    onTextDelta: ({ messageId, delta }) => {
      aggregatedText.set(
        messageId,
        (aggregatedText.get(messageId) ?? "") + delta,
      );
    },
    getCurrentText: (id) => aggregatedText.get(id),
    source: "a2a",
    surfaceTracker: tracker,
  };
};

A2AAgent.prototype.streamMessage = async function patchedStreamMessage(
  sendParams,
  subscriber,
  surfaceTracker,
) {
  const aggregatedText = new Map();
  const rawEvents = [];
  const tracker = surfaceTracker ?? this.createSurfaceTracker();

  const stream = this.a2aClient.sendMessageStream(sendParams);
  for await (const event of stream) {
    rawEvents.push(event);

    const aguiEvents = convertA2AEventToAGUIEvents(
      event,
      buildConverterContext.call(this, tracker, aggregatedText),
    );
    for (const e of aguiEvents) subscriber.next(e);

    emitV09SnapshotsFor(event, subscriber);
  }

  return { messages: [], rawEvents };
};

A2AAgent.prototype.blockingMessage = async function patchedBlockingMessage(
  sendParams,
  subscriber,
  surfaceTracker,
) {
  const result = await this.a2aClient.sendMessage(sendParams);
  if (this.a2aClient.isErrorResponse(result)) {
    const msg = result.error?.message ?? "Unknown error from A2A agent";
    console.error("A2A sendMessage error", result.error);
    throw new Error(msg);
  }

  const aggregatedText = new Map();
  const rawEvents = [];
  const tracker = surfaceTracker ?? this.createSurfaceTracker();
  const event = result.result;
  rawEvents.push(event);

  const aguiEvents = convertA2AEventToAGUIEvents(
    event,
    buildConverterContext.call(this, tracker, aggregatedText),
  );
  for (const e of aguiEvents) subscriber.next(e);

  emitV09SnapshotsFor(event, subscriber);

  return { messages: [], rawEvents };
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
