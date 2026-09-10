# Production deployment (Kubernetes)

These manifests implement the deployment topology in the design document:

| Tier | Manifest | Scaling |
|------|----------|---------|
| API gateway / BFF | `api-deployment.yaml` | Stateless, HPA on CPU + concurrent connections |
| Optimisation worker | `worker-deployment.yaml` | Queue-based, HPA on queue depth |
| Scheduled jobs | `beat-deployment.yaml` | Singleton (retention, geocode retries, ETA refresh) |
| Frontend | `frontend-deployment.yaml` | Static assets behind nginx, CDN-frontable |
| PostgreSQL | managed (RDS/Cloud SQL) | Primary + read replica |
| Redis | managed (ElastiCache/Memorystore) | Cluster |

Secrets come from a `Secret` named `roe-secrets`, which in a real cluster is
projected from AWS Secrets Manager or an equivalent via External Secrets.

```bash
kubectl apply -k deploy/k8s
kubectl -n roe rollout status deploy/roe-api
```

## Sizing for the stated SLAs

* **50 concurrent dispatcher sessions** (Requirement 18.1) — the API tier is
  stateless and WebSocket fan-out goes through Redis pub/sub, so sessions
  spread across replicas. Three replicas with the resource requests below
  carry the load with headroom.
* **500 orders × 50 vehicles in 120 s** (Requirement 18.2) — the solver is
  CPU-bound and multi-threaded. Workers request 4 CPUs so OR-Tools has real
  parallelism; the wall-clock guard aborts at 120 s regardless.
