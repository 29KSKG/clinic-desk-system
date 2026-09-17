#!/usr/bin/env bash
# Starts the API and the UI together. Ctrl-C stops both.
set -e
export API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
uvicorn backend.main:app --reload --port 8000 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT
sleep 2
python -m backend.seed || true
streamlit run frontend/app.py --server.port 8501
