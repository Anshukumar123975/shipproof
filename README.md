# ShipProof

**Know what actually works before you ship.**

A local release-verification application: React dashboard → FastAPI → PostgreSQL leased worker → Playwright / HTTPX → independently evaluated assertions → hashed evidence → release report.

## Run

Requires Docker Engine with Compose, internet access for image/dependency downloads, and roughly 3 GB available for images.

```sh
cp .env.example .env
docker compose up --build -d
```

- Dashboard: http://localhost:5178
- API documentation: http://localhost:8018/docs
- Demo staging app: http://localhost:8019/orders

Database migrations run before API/worker startup. PostgreSQL uses a persistent Docker volume; evidence is stored in `./artifacts`. Stop with `docker compose down` (preserves data). Ports bind only to loopback. This is a trusted, single-user local application, not a public service.

## First real verification

1. Open **New verification**, choose **Order history is visible**, and confirm authorization for the disposable demo environment.
2. Generate the plan. Inspect the seeded requirements, browser text assertion, JSON total assertion, and heuristic change mapping.
3. Start verification. Watch persisted execution events and independently evaluated evidence in the live view.
4. Open the report, expand the assertion evidence, inspect the screenshot and response artifact, and export Markdown.
5. Try **Order totals regression** for BLOCKED, **Accessibility audit gap** for UNKNOWN / NEEDS_REVIEW, and **Disposable staging interaction** to inspect a request-bound approval.
6. In **Policy comparison**, run the stagnating-selector scenario to execute all three stopping policies under equal ceilings.

The browser's `http://demo:8001` staging URL is a Docker-network destination used by the worker. Open the demo yourself at `http://localhost:8019`.

## OpenRouter

Set `OPENROUTER_API_KEY` in `.env`, then recreate API and worker:

```sh
docker compose up -d --force-recreate api worker
```

Use **Settings** to change the model ID. The server checks OpenRouter's model catalog and structured-output capabilities before calls. Enable OpenRouter in a new verification: the run pauses in DRAFT for a review of proposed structured checks. Choose the original plan or accept reviewed suggestions. AI advice is connected to planning, PR/impact analysis, replanning when triggered, and evidence-based reporting. All assertions and release decisions remain deterministic. Missing credentials activate a clearly labeled deterministic workflow. Model failures are recorded and fall back to deterministic checks. API keys are never returned to the frontend.

## Checks and demonstration

```sh
# Isolated unit/API tests (SQLite, not your persisted PostgreSQL data)
docker compose run --rm api pytest -q

# Real browser/API/approval/UI workflow against the running PostgreSQL stack
docker compose exec worker python integration/live_demo.py

# Actual process interruption and leased-job recovery
docker compose exec worker python integration/recovery.py

# Six-scenario, three-policy measured evaluation
docker compose exec worker python integration/benchmark.py

# TypeScript type checking and production bundle are required by the frontend image build
docker compose build frontend
```

Results from demonstrations are written to `artifacts/*-results.json`, alongside actual browser screenshots. Checked-in benchmark snapshots live in `benchmarks/`. Tests never insert fake completed runs or fabricated screenshots. Fixtures specify inputs only.

## Scope and safety

External staging execution is deliberately disabled: this MVP does not provide a hardened egress proxy capable of enforcing IP and redirect restrictions for arbitrary external hosts. Public GitHub PR ingestion is read-only and supported, but executable verification targets remain limited to the bundled demo. The worker rejects every other origin and any unsupported route. Browser contexts block service workers and non-GET/HEAD requests. HTTP redirects are not followed. The only POST is the demo acknowledgement simulation, guarded by an expiring, single-use approval tied to the canonical action, run, and environment.

A READY recommendation means all configured requirements passed within scope. Unknown requirements never count as passing. A terminal verification can be COMPLETE while the release needs review. See [architecture](docs/architecture.md), [decisions](docs/decisions.md), [security](docs/security.md), [evaluation](docs/evaluation.md), and [limitations](docs/limitations.md).

To exercise interruption during a real browser navigation as well as an API call:

```sh
docker compose exec worker python integration/recovery.py --browser
```
