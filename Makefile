.PHONY: dev build install api web clean

# Run both backend and frontend with hot reload
dev:
	cd web && npm run dev

# Run only the API server
api:
	cd server && uv run python -m src.graph.server

# Run only the frontend dev server
web:
	cd web && npm run dev:web

# Build frontend for production
build:
	cd web && npm run build

# Install all dependencies
install:
	cd server && uv sync
	cd web && npm install

# Clean build artifacts
clean:
	rm -rf static/assets static/index.html
	rm -rf web/node_modules/.vite
