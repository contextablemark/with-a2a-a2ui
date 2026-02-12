#!/bin/bash
set -e

echo "Starting Python A2A agent on port 10002..."
# Run from /tmp so the /app/agent/ source dir doesn't shadow the installed package
cd /tmp && python -m agent --host 0.0.0.0 --port 10002 &
AGENT_PID=$!

# Wait for the A2A agent to be ready
echo "Waiting for A2A agent..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:10002/.well-known/agent.json > /dev/null 2>&1; then
    echo "A2A agent is ready."
    break
  fi
  if ! kill -0 $AGENT_PID 2>/dev/null; then
    echo "A2A agent process died."
    exit 1
  fi
  sleep 1
done

if ! curl -sf http://localhost:10002/.well-known/agent.json > /dev/null 2>&1; then
  echo "A2A agent failed to start within 30 seconds."
  exit 1
fi

echo "Starting AG-UI adapter on port ${PORT:-3000}..."
exec node /app/ag-ui-server.mjs
