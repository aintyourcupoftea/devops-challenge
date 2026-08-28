# Video script — DevOps Engineer 90-Minute Infra Challenge

Target: 8-12 min total. Section timings below are guides, not hard stops.
Everything described here has already been built and verified working on
this machine (kind cluster, images, manifests, CI/CD workflow, failure
scripts) — you're narrating something real, not something staged.

Cluster/app state right now: namespace `devops-challenge`, 2 backend pods +
1 postgres pod, all Running/Ready, reachable at `http://localhost:30080`.

---

## 1. Live Demo (3-4 min)

**Say:** "This is a small backend + Postgres stack running on a local
Kubernetes cluster — kind, Kubernetes-in-Docker, running on Docker via
Colima. Everything you'll see is real kubectl output against a real
cluster, no dashboards hiding what's happening."

Show the app working:

```bash
curl http://localhost:30080/
curl http://localhost:30080/healthz/live
curl http://localhost:30080/healthz/ready
curl -X POST http://localhost:30080/items -H 'Content-Type: application/json' -d '{"name":"demo item"}'
curl http://localhost:30080/items
```

**Say while it runs:** "That POST just went through the backend, into
Postgres, and back out — GET /items shows the row actually persisted in the
database, not an in-memory mock."

Show the Kubernetes resources:

```bash
kubectl -n devops-challenge get all
kubectl -n devops-challenge get pods -o wide
kubectl -n devops-challenge describe deployment backend | head -20
```

**Say:** "Two backend replicas, one Postgres pod backed by a
PersistentVolumeClaim, a NodePort service exposing the backend on 30080.
Namespace-scoped, nothing shared with kube-system."

Show CI/CD execution (push a trivial change, e.g. bump `APP_VERSION` in
`k8s/05-backend-deployment.yaml` or a comment in `app.py`):

```bash
git add -A && git commit -m "demo: trigger pipeline"
git push
```

Switch to the GitHub Actions tab (or `gh run watch`) and narrate the two
jobs running: **build-and-push** on GitHub's runner, then **deploy** on your
self-hosted runner. When it finishes:

```bash
kubectl -n devops-challenge get pods -w
```

**Say:** "That's the self-hosted runner — it's registered to this repo and
has kubectl access to this exact cluster, so the push you just saw becomes a
rolling update on screen. That's the difference between 'we build an image'
and an actually automatic deploy."

---

## 2. Architecture Walkthrough (2-3 min)

**Say, roughly in this order:**

- "Cluster: kind, a real multi-node-capable Kubernetes distribution running
  each 'node' as a Docker container — not a hosted abstraction. I picked
  kind over minikube mainly for speed and because its config file makes
  port-mapping into the cluster trivial for local demos."
- "Deployment flow: Dockerfile builds a two-stage Python image, `kind load
  docker-image` gets it onto the cluster node without needing a registry for
  local dev; the CI pipeline instead pushes to GHCR so the deploy step pulls
  a real versioned image, same as it would against ECR or GCR in the cloud."
- "Everything is plain kubectl-apply-able YAML in `k8s/` — no Helm chart, no
  operator, no one-click platform between me and the cluster state. That's
  intentional per the requirements, but it's also just how I'd want to debug
  this at 3am: `kubectl get` and `describe` should tell the whole story."
- "Reliability decision: readiness and liveness probes, split across two
  endpoints — `/healthz/live` (process alive) and `/healthz/ready` (DB
  reachable). [Explain the why/what/tradeoff — see README `Reliability
  improvement` section, say it in your own words:] The problem this solves
  is a backend pod that's technically running but can't serve real traffic
  because Postgres is down — without this, Kubernetes would keep routing
  traffic to a pod that just errors on every DB-touching request. The
  tradeoff is probe overhead — a `SELECT 1` against Postgres every 5 seconds
  per pod — and probes are not instant, so there's a few seconds of lag
  between a real failure and Kubernetes reacting to it."
- "I also set `maxUnavailable: 0` on the rolling update strategy — turns out
  that decision mattered a lot in the failure scenario, more on that next."

---

## 3. Failure Debugging Walkthrough (2-3 min) — the important part

**Say:** "I'm going to break database connectivity on purpose — a bad
PGHOST value, which is a very real category of incident: someone typos a
config value or an env var during a deploy."

Show the "before" state:

```bash
kubectl -n devops-challenge get pods
kubectl -n devops-challenge get endpoints backend
curl http://localhost:30080/healthz/ready
```

Trigger it:

```bash
./scripts/break-db-connectivity.sh
```

**Say (symptoms):** "That just did `kubectl set env` with a host that
doesn't resolve. Let's see what happens."

```bash
kubectl -n devops-challenge get pods -w
```

**Say while watching:** "A new pod spins up on the new ReplicaSet — and it
never goes Ready. Let's check why instead of guessing."

```bash
kubectl -n devops-challenge get deploy backend
```

**Say:** "READY says 2/2, but UP-TO-DATE only says 1 — that mismatch is the
tell. Two pods are healthy and serving traffic, one new pod is stuck. My
first instinct might be 'the app crashed' — let's confirm or rule that out
instead of assuming."

```bash
POD=$(kubectl -n devops-challenge get pods -l app=backend --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl -n devops-challenge describe pod "$POD" | tail -15
kubectl -n devops-challenge logs "$POD" --tail=15
```

**Say (root cause):** "Container's not crashing — it's Running, not
CrashLoopBackOff. The readiness probe is failing with a 503, and the logs
show why: `getaddrinfo ENOTFOUND postgres-typo`. That rules out 'app bug' or
'OOM' and confirms it's a config/DNS problem — the app is trying to resolve
a Service name that doesn't exist in this namespace."

**Say (what DIDN'T happen, and why that matters):** "Notice the other two
pods are untouched — still Running, still in the Service's endpoint list.
That's `maxUnavailable: 0` doing its job: Kubernetes won't kill a healthy
pod to make room for a replacement until the replacement proves it's
Ready. So this bad config never caused an outage — it got stuck as a
canary that never went live."

```bash
curl http://localhost:30080/healthz/ready
kubectl -n devops-challenge get endpoints backend
```

**Say:** "Confirmed — still serving traffic the whole time, zero dropped
requests, because of that rollout strategy plus the readiness probe pulling
the bad pod out of rotation instead of it silently serving errors."

Fix it:

```bash
./scripts/fix-db-connectivity.sh
kubectl -n devops-challenge rollout status deployment/backend
kubectl -n devops-challenge get pods
kubectl -n devops-challenge get rs
```

**Say:** "Restoring the correct PGHOST — and because that value now matches
the original pod template exactly, Kubernetes recognizes it as the same
ReplicaSet hash as before and just scales that one back up, tearing down
the broken canary. Fully recovered, and I never had to manually roll back
or delete anything."

```bash
curl http://localhost:30080/items
```

**Say:** "Confirmed — reads and writes working again."

---

## 4. Tradeoff Discussion (1-2 min)

**Say (pick 3-4 of these, don't read the whole README list verbatim):**

- "I simplified Postgres to a single pod with a PVC and no backups. In
  production I'd run it managed — RDS or Cloud SQL — or via an operator
  like CloudNativePG that handles failover and WAL archiving. At scale, a
  single Postgres pod is a hard single point of failure."
- "The Secret here is a plaintext value committed to the repo, which is
  fine for a throwaway local cluster with a fake password, but is exactly
  the kind of thing that becomes a real incident in production. That'd move
  to Sealed Secrets or Vault."
- "No Ingress or TLS — I'm using a NodePort for simplicity. Any real
  deployment needs an ingress controller and cert-manager in front of this."
- "No autoscaling and no load testing behind my resource requests/limits —
  they're reasonable guesses, not measured numbers. At real traffic I'd
  want an HPA and actual load test data before trusting those values."
- "The CI/CD deploy step runs on a self-hosted runner on my own laptop,
  which is a single point of failure and isn't sandboxed. In a real team
  setup I'd either use a fleet of ephemeral runners or flip this to a
  pull-based GitOps model — Argo CD watching the repo — instead of the
  pipeline pushing changes directly."
- "No centralized logging or metrics — I'm reading `kubectl logs` on one
  pod at a time, which does not scale past a handful of pods. Real
  production needs Prometheus/Grafana and centralized log aggregation, or
  debugging an incident across dozens of replicas becomes guesswork."

**Close:** "The one thing I'd underline: none of this was hidden behind a
platform. Every failure I hit, I found and fixed with plain kubectl —
that's the property I actually optimized for."
