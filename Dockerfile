FROM python:3.13-slim

# Install Node.js 22 and curl (for health checks in start script)
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# -- Python dependencies --
COPY pyproject.toml ./
COPY agent/ ./agent/
RUN uv sync --all-packages && \
    .venv/bin/python -c "import ag_ui_adk; print('ag_ui_adk OK')"

# Put the venv on PATH so we don't need 'uv run' at runtime
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# -- Node.js dependencies --
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

# -- Application files --
COPY ag-ui-server.mjs ./
COPY scripts/start-railway.sh ./start-railway.sh
RUN chmod +x ./start-railway.sh

EXPOSE ${PORT:-3000}

CMD ["./start-railway.sh"]
