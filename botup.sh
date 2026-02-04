#!/usr/bin/env bash
set -euo pipefail

SERVICE="role-lister-bot"
MODE="${1:-normal}"

if [[ "$MODE" == "clean" ]]; then
  echo "🧹 Clean rebuild + restart ($SERVICE)"
  docker compose build --no-cache "$SERVICE"
  docker compose up -d --force-recreate "$SERVICE"
else
  echo "🚀 Rebuild (cached) + restart ($SERVICE)"
  docker compose up -d --build "$SERVICE"
fi

echo
echo "📋 Last 50 log lines:"
docker compose logs --tail=50 "$SERVICE"
