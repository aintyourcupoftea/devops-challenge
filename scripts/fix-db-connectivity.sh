#!/usr/bin/env bash
# Fix: restore the correct PGHOST value (the postgres Service name).
set -euo pipefail

kubectl -n devops-challenge set env deployment/backend PGHOST=postgres

echo ""
echo "PGHOST restored. Watch recovery with:"
echo "  kubectl -n devops-challenge rollout status deployment/backend"
echo "  kubectl -n devops-challenge get endpoints backend"
echo "  curl -sS http://localhost:30080/healthz/ready"
