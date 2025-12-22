#!/bin/bash
set -e
uv sync --frozen --directory server
cd web && npm ci && npm run build
