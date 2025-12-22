#!/bin/bash
set -e
cd server && uv sync --frozen
cd ../web && npm ci && npm run build
