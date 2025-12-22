FROM python:3.13-slim

# Install Node.js and uv
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    pip install uv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy everything
COPY . .

# Install dependencies and build frontend
RUN cd server && uv sync --frozen
RUN cd web && npm ci && npm run build

# Run the server
WORKDIR /app/server
CMD [".venv/bin/python", "-m", "src.graph.server"]
