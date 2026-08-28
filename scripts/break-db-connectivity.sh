#!/usr/bin/env bash
# Intentional failure: point the backend at a Postgres host that doesn't exist.
# Simulates a bad config push / typo'd env var - a very common real incident.
set -euo pipefail

kubectl -n devops-challenge set env deployment/backend PGHOST=postgres-typo

echo ""
echo "Bad env var applied. Watch it fail with:"
echo "  kubectl -n devops-challenge get pods -w"
echo "  kubectl -n devops-challenge get endpoints backend"
echo "  curl -sS http://localhost:30080/healthz/ready"
