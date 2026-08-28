# DevOps Challenge — minimal production-style stack

Node/Express backend + Postgres, deployed to a local `kind` Kubernetes cluster,
with a GitHub Actions CI/CD pipeline that builds the image, pushes it to
GHCR, and rolls it out to the cluster via a self-hosted runner.

## Stack

- **Backend**: [backend/server.js](backend/server.js) — Express API (`/items` GET/POST),
  talks to Postgres via `pg`.
- **Database**: Postgres 16, `Deployment` + `PersistentVolumeClaim` (not a StatefulSet —
  simplified to one replica for this exercise, see tradeoffs below).
- **Cluster**: `kind` (Kubernetes-in-Docker), config in [kind-config.yaml](kind-config.yaml).
- **Manifests**: [k8s/](k8s/) — plain YAML, applied with `kubectl apply -f k8s/`. No Helm,
  no operators, nothing hiding what's actually deployed.
- **Reliability feature**: readiness + liveness probes (see below).
- **CI/CD**: [.github/workflows/ci-cd.yaml](.github/workflows/ci-cd.yaml).

## One-time local setup (already done on this machine)

```bash
brew install colima docker kind kubectl
colima start --cpu 2 --memory 4 --disk 20
kind create cluster --config kind-config.yaml
docker build -t devops-challenge-backend:local ./backend
kind load docker-image devops-challenge-backend:local --name devops-challenge
kubectl apply -f k8s/
```

Verify:

```bash
kubectl -n devops-challenge get pods
curl http://localhost:30080/healthz/ready
curl http://localhost:30080/items
```

The `backend` Service is a `NodePort` on `30080`, and `kind-config.yaml` maps host
port `30080` → the node, so `localhost:30080` works directly with no port-forward.

## Reliability improvement: readiness + liveness probes

**Why this one:** it's the improvement with the highest payoff-to-effort ratio for a
service with an external dependency (the database), and it directly prevents the
most common failure mode in this stack — the app being "up" (process alive) but
unable to actually serve traffic because Postgres is unreachable.

**What it solves:** two health endpoints are used for two different purposes:

- `GET /healthz/live` — process-only check, never touches the DB. Backs the
  **liveness** probe. If this fails, Kubernetes kills and restarts the container —
  appropriate because it means the Node event loop itself is wedged.
- `GET /healthz/ready` — runs `SELECT 1` against Postgres. Backs the
  **readiness** probe. If this fails, Kubernetes removes the pod from the
  Service's endpoints (no traffic sent to it) but does **not** restart it —
  appropriate because restarting a pod won't fix a database outage, and a
  crash-loop would just add noise on top of an existing incident.

Splitting these matters: a naive single `/health` check wired to *both* probes
would cause Kubernetes to kill and restart every backend pod the moment the
database blips — which is exactly the wrong reaction to a dependency outage,
and can turn a brief DB hiccup into a full self-inflicted outage via
crash-loop-backoff.

**Tradeoff:** probes add latency to how fast a real crash is detected (probes run
every 5–10s, not instantly), and every probe interval is extra load against the
database (`SELECT 1` every 5s per pod). At real production scale, you'd tune the
intervals/thresholds per-service and likely add a lightweight in-process health
cache instead of hitting Postgres on every readiness check.

## CI/CD pipeline

`.github/workflows/ci-cd.yaml`, two jobs:

1. **build-and-push** (GitHub-hosted runner): builds `backend/Dockerfile`,
   pushes to `ghcr.io/<you>/devops-challenge-backend:<git-sha>` and `:latest`
   using the automatic `GITHUB_TOKEN` (no extra registry secret needed).
2. **deploy** (self-hosted runner — your laptop): applies `k8s/`, then
   `kubectl set image` to the new tag, waits for rollout, then curls
   `/healthz/ready` through the NodePort as a smoke test.

The self-hosted runner is what makes this a *real* automatic deployment rather
than "build an image and stop": it has `kubectl` access to the same local
`kind` cluster you're demoing from, so a `git push` visibly rolls pods on the
cluster you're showing on screen.

### To wire this up (steps only you can do — need your GitHub login)

```bash
gh auth login
```

Then, from this directory:

```bash
gh repo create devops-challenge --private --source=. --remote=origin
git add -A
git commit -m "Initial devops challenge stack"
git push -u origin main
```

Register a self-hosted runner (GitHub gives you a short-lived token in the UI —
Settings → Actions → Runners → New self-hosted runner — or via `gh api`):

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-osx-arm64.tar.gz
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/<you>/devops-challenge --token <TOKEN_FROM_GITHUB_UI>
./run.sh
```

Leave `./run.sh` running in a terminal during the demo — that's the "agent" that
picks up the `deploy` job.

**Important — GHCR package auth:** the first time the workflow pushes an image,
the package is private by default and your kind node has no registry
credentials, so `kubectl set image` pull-fails (`ImagePullBackOff`). Rather
than making the package public, the backend Deployment uses a real
`imagePullSecret` (`ghcr-pull-secret`) so the cluster authenticates to pull —
this is the actual production pattern for a private registry. One-time setup:

```bash
# read:packages-scoped PAT from https://github.com/settings/tokens/new
kubectl create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=<your-github-username> \
  --docker-password=<YOUR_PAT> \
  -n devops-challenge
```

`k8s/05-backend-deployment.yaml` already references it via
`spec.template.spec.imagePullSecrets`.

After that, any push to `backend/**` or `k8s/**` triggers a real build → push →
rollout, visible live with:

```bash
kubectl -n devops-challenge get pods -w
```

## Failure simulation: database connectivity

```bash
./scripts/break-db-connectivity.sh   # sets PGHOST to a host that doesn't exist
# ... debug live, see below ...
./scripts/fix-db-connectivity.sh     # restores PGHOST=postgres
```

See the narration script for the full debugging walkthrough — what actually
happens is more interesting than a simple crash: because the Deployment uses
`maxUnavailable: 0`, the bad rollout gets stuck as an under-provisioned new
ReplicaSet and **never touches the two healthy pods already serving traffic**.
Zero downtime during the entire incident — that's the rollout strategy earning
its keep, not just the probes.

## Tradeoffs / what's simplified for this exercise

- **Postgres is a single replica with no automated backups.** Fine for a demo;
  in real production this would be managed (RDS/Cloud SQL) or run via an
  operator (Zalando/CloudNativePG) with WAL archiving and a standby.
- **Secret is a plaintext `stringData` block committed to the repo.** Acceptable
  for a local demo cluster with fake credentials only; in production this would
  be Sealed Secrets / External Secrets Operator / Vault, never committed.
- **No Ingress/TLS** — using a NodePort instead. An Ingress controller +
  cert-manager would front this in any real deployment.
- **No autoscaling.** Fixed at 2 replicas. At real traffic this would need an
  HPA on CPU/custom metrics, plus load testing to size resource requests/limits
  correctly (mine are guessed, not measured).
- **Self-hosted runner is a single point of failure and runs unsandboxed on my
  laptop.** In production this would be a fleet of ephemeral, isolated runners
  (or a proper CD tool like Argo CD pulling from git instead of a runner pushing).
- **No centralized logging/metrics.** Logs are `console.log`/`kubectl logs`
  only — no Prometheus/Grafana/Loki. At scale you can't `kubectl logs` your way
  through an incident across dozens of pods.
