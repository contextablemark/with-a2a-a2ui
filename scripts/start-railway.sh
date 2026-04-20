#!/bin/bash
set -e

AGENT_PORT=${AGENT_PORT:-8000}
export AGENT_URL="http://localhost:${AGENT_PORT}/"

echo "Starting AG-UI Python agent on port ${AGENT_PORT}..."
# Run from inside agent/ dir so sibling imports (agent.py, tools.py) resolve
# the same way as local 'uv run .' from agent/.
cd /app/agent && python __main__.py --host 0.0.0.0 --port "${AGENT_PORT}" &
AGENT_PID=$!

# Wait for the Python agent's /health to come up before we accept client traffic.
echo "Waiting for AG-UI agent..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:${AGENT_PORT}/health" > /dev/null 2>&1; then
    echo "AG-UI agent is ready."
    break
  fi
  if ! kill -0 $AGENT_PID 2>/dev/null; then
    echo "AG-UI agent process died."
    exit 1
  fi
  sleep 1
done

if ! curl -sf "http://localhost:${AGENT_PORT}/health" > /dev/null 2>&1; then
  echo "AG-UI agent failed to start within 30 seconds."
  exit 1
fi

echo "Starting AG-UI adapter on port ${PORT:-3000}..."
exec node /app/ag-ui-server.mjs
