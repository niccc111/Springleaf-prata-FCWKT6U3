# Deploying the Route Optimisation Engine to AWS Lightsail

This guide runs the full stack (frontend, API, Celery worker + beat, Postgres,
Redis) on a **single Lightsail VM instance** using Docker Compose. It is the
simplest hosting path because the app needs Postgres, Redis, and background
workers together, which the container-only Lightsail service does not support
well.

## 1. Create the Lightsail instance

1. In the Lightsail console, create an instance:
   - Platform: **Linux/Unix**
   - Blueprint: **OS Only → Ubuntu 22.04 LTS** (or 24.04)
   - Plan: at least **2 GB RAM / 2 vCPU** (the OR-Tools solver is CPU-bound; 4 GB
     is recommended for real workloads).
2. Attach a **static IP** to the instance (Networking tab).
3. Open the firewall for **HTTP (port 80)**. Add **HTTPS (443)** too if you plan
   to terminate TLS (see step 6). Leave SSH (22) as is.

## 2. Get the code onto the instance

SSH in (browser SSH or your own key), then clone the repo:

```bash
git clone <your-repo-url> roe
cd roe
```

## 3. Install Docker

```bash
bash deploy/lightsail/provision.sh
# log out and back in (or: newgrp docker) so the docker group applies
```

## 4. Configure secrets

```bash
cp backend/.env.prod.example backend/.env.prod
nano backend/.env.prod
```

Fill in every `CHANGE_ME` value. At minimum:

- `CORS_ORIGINS` — your public origin, e.g. `http://<static-ip>` or
  `https://roe.example.com`
- `POSTGRES_PASSWORD` — a strong database password
- `JWT_SECRET_KEY` — generate one:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- `BOOTSTRAP_ADMIN_PASSWORD` / `BOOTSTRAP_DISPATCHER_PASSWORD`

The API **refuses to start** in `production` if the JWT secret is still the
placeholder, `AUTH_DISABLED=true`, the demo passwords are unchanged, or
`CORS_ORIGINS` still points at localhost. This is intentional.

`.env.prod` is gitignored so your secrets are never committed.

## 5. Build and start

```bash
bash deploy/lightsail/deploy.sh
```

This builds the images, runs database migrations (`alembic upgrade head`)
automatically, and starts everything. Only **port 80** is published; Postgres
and Redis stay on the internal Docker network.

Verify:

```bash
curl -f http://localhost/health      # {"status":"ok",...}
curl -f http://localhost/ready       # {"status":"ready"}
```

Then open `http://<your-static-ip>/` in a browser. Log in with the bootstrap
admin account you configured.

## 6. (Optional) HTTPS with a domain

Point your domain's A record at the static IP. Then the simplest option is to
put a TLS terminator in front. Two common choices:

- **Caddy** on the host (auto Let's Encrypt) reverse-proxying to `localhost:80`.
- Add a `certbot`/nginx sidecar. Once TLS is set up, update `CORS_ORIGINS` to
  the `https://` origin and re-run `deploy.sh`.

## Operations

```bash
# View logs
docker compose -f docker-compose.prod.yml --env-file backend/.env.prod logs -f api

# Update after pulling new code
git pull && bash deploy/lightsail/deploy.sh

# Stop everything (data volumes are preserved)
docker compose -f docker-compose.prod.yml --env-file backend/.env.prod down

# Back up the database
docker compose -f docker-compose.prod.yml --env-file backend/.env.prod \
  exec postgres pg_dump -U roe roe > roe-backup-$(date +%F).sql
```

## Notes

- Data persists in the `postgres-data` and `redis-data` named volumes. `down`
  keeps them; `down -v` deletes them.
- All external integrations default to `mock`, so the system runs end-to-end
  with no third-party credentials. Switch an adapter to `http` and provide its
  base URL/API key in `.env.prod` to connect a real system.
- For heavier optimisation workloads, scale the instance up rather than out;
  this single-host setup does not load-balance across machines.
