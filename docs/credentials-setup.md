# Credentials Setup Guide

This document explains how to obtain every credential referenced in `.env.example` for local development and demo deployment. Each section links to the official documentation it was verified against.

---

## 1. Application Configuration

```env
APP_ENV=development
APP_BASE_URL=http://localhost:8000
WEB_BASE_URL=http://localhost:3000
```

No external credentials needed. Set `APP_ENV=production` for hosted deployments. `APP_BASE_URL` is the FastAPI origin; `WEB_BASE_URL` is the Next.js origin. These are used as redirect URI bases for OAuth flows.

---

## 2. PostgreSQL + pgvector

```env
DATABASE_URL=postgresql+asyncpg://assistant:assistant@localhost:5434/assistant
```

### Local setup (Docker Compose)

The `docker-compose.yml` in the repo root starts a PostgreSQL + pgvector container. The default credentials are:

- **Host:** `localhost`
- **Port:** `5434` (published by Docker Compose; PostgreSQL remains on port `5432` inside the container network)
- **Database:** `assistant`
- **User:** `assistant`
- **Password:** `assistant`

The connection string format is:
```
postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
```

For production, use a managed PostgreSQL instance with pgvector support (e.g., AWS RDS with the pgvector extension, Supabase, or Aiven). Create a dedicated database user with the minimum required privileges.

### Required extensions

After connecting, enable pgvector:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Alembic migrations handle the rest.

**Source:** [pgvector GitHub — Installation](https://github.com/pgvector/pgvector#installation)

---

## 3. Redis

```env
REDIS_URL=redis://localhost:6379/0
```

### Local setup (Docker Compose)

The `docker-compose.yml` starts a Redis container. Defaults:

- **Host:** `localhost`
- **Port:** `6379`
- **DB index:** `0`

For production, use a managed Redis instance (e.g., AWS ElastiCache, Upstash, or Redis Cloud).

**Source:** [Redis Documentation](https://redis.io/docs/)

---

## 4. OIDC (Enterprise Identity Provider)

```env
OIDC_ISSUER=http://localhost:8080/realms/enterprise
OIDC_AUDIENCE=ai-assistant
OIDC_JWKS_URL=http://localhost:8080/realms/enterprise/protocol/openid-connect/certs
```

### Local setup — Keycloak (recommended for dev)

The spec calls for enterprise OIDC. For local development, use **Keycloak** in Docker:

1. Pull and start Keycloak:
   ```bash
   docker run -p 8080:8080 -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin quay.io/keycloak/keycloak:latest start-dev
   ```

2. Open the admin console at `http://localhost:8080/admin` (admin/admin).

3. Create a realm named `enterprise`.

4. Create a client:
   - **Client ID:** `ai-assistant`
   - **Client type:** OpenID Connect
   - **Client authentication:** Enabled (confidential access)
   - **Valid redirect URIs:** `http://localhost:3000/*`, `http://localhost:8000/*`
   - **Web origins:** `http://localhost:3000`

5. The values map to:
   - `OIDC_ISSUER` = `http://localhost:8080/realms/enterprise`
   - `OIDC_AUDIENCE` = `ai-assistant` (the client ID)
   - `OIDC_JWKS_URL` = `http://localhost:8080/realms/enterprise/protocol/openid-connect/certs`

6. Create test users (e.g., `alice`, `bob`, `carol`) with passwords. The `oidc_subject` in the seed dataset must match the `sub` claim in each user's JWT. Note each user's `sub` UUID from the Keycloak admin console and update `users[].oidc_subject` in `packages/fixtures/seed_dataset.json` accordingly.

### Production

Use your enterprise IdP (Azure AD / Entra ID, Okta, Auth0, Google Workspace, or Keycloak). Configure the three variables to point to your production issuer, audience, and JWKS URL.

**Source:** [Keycloak Documentation — Getting Started](https://www.keycloak.org/getting-started/getting-started-docker)

---

## 5. Atlassian (Confluence + Jira)

```env
ATLASSIAN_CLIENT_ID=
ATLASSIAN_CLIENT_SECRET=
ATLASSIAN_REDIRECT_URI=http://localhost:8000/v1/oauth/atlassian/callback
```

### Step 1 — Create an OAuth 2.0 (3LO) app

1. Go to the **Atlassian Developer Console**: <https://developer.atlassian.com/console/myapps/>
2. Sign in with your Atlassian account.
3. Click **Create app** (or select an existing app).
4. Choose **OAuth 2.0 (3LO)** as the app type.
5. Enter an app name (e.g., "Enterprise AI Assistant").

### Step 2 — Retrieve credentials

In the app's **Settings** tab (or **Credentials** section):
- **Client ID** → copy to `ATLASSIAN_CLIENT_ID`
- **Client Secret** → copy to `ATLASSIAN_CLIENT_SECRET` (treat as a password; you may not see it again)

### Step 3 — Configure redirect URI

In the app's **Authorization** tab, add:
```
http://localhost:8000/v1/oauth/atlassian/callback
```

Rules (verified from Atlassian docs):
- Must **exactly match** (including trailing slashes, ports, and path).
- `localhost` is allowed for development; production requires HTTPS.
- No wildcards.

### Step 4 — Select API scopes (Permissions tab)

Under the **Permissions** tab, enable these scopes:

**Confluence (read access):**
| Scope | Description |
|---|---|
| `read:confluence-content.all` | Read all Confluence content (pages, blog posts) |
| `read:confluence-space.summary` | Read space details |
| `search:confluence` | Search Confluence content |

**Jira (read access):**
| Scope | Description |
|---|---|
| `read:jira-work` | Read Jira issues, projects (work-related data) |
| `read:jira-user` | Read user information |

> Scopes must be declared before users authorise. Changing scopes later requires re-authorisation.

### Step 5 — OAuth flow endpoints (for reference)

- **Authorisation endpoint:** `https://auth.atlassian.com/authorize`
- **Token endpoint:** `https://auth.atlassian.com/oauth/token`
- Access tokens expire after 1 hour; use the **refresh token** to get a new one automatically (handled server-side by the app).

### Step 6 — Get your Atlassian Cloud ID

After OAuth, call the accessible-resources endpoint to find your `cloudId`:
```
GET https://api.atlassian.com/me/accessible-resources
Authorization: Bearer <access_token>
```
The `cloudId` is needed for all Confluence/Jira API calls.

**Source:** [Atlassian — OAuth 2.0 (3LO) apps](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/) (last updated September 2026)

---

## 6. Slack

```env
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
SLACK_SIGNING_SECRET=
```

### Step 1 — Create a Slack app

1. Go to **Slack App Management**: <https://api.slack.com/apps>
2. Click **Create New App** → **From scratch**.
3. Enter an app name (e.g., "Enterprise AI Assistant").
4. Select your workspace.

### Step 2 — Retrieve credentials

On the **Basic Information** page:
- **App ID** — for reference only
- **Client ID** → copy to `SLACK_CLIENT_ID`
- **Client Secret** → copy to `SLACK_CLIENT_SECRET`
- **Signing Secret** → copy to `SLACK_SIGNING_SECRET`

### Step 3 — Configure OAuth redirect URI

Under **OAuth & Permissions**:
1. Scroll to **Redirect URLs**.
2. Add: `http://localhost:8000/v1/oauth/slack/callback`
3. Save.

Rules (verified from Slack docs):
- HTTPS required in production (localhost HTTP is allowed for dev).
- Must exactly match or be a subdirectory of a configured URL.
- No anchors (`#`).

### Step 4 — Select bot token scopes

Under **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**, add:

| Scope | Description |
|---|---|
| `channels:history` | Read messages in public channels the bot is a member of |
| `channels:read` | View basic info about public channels |
| `groups:history` | Read messages in private channels the bot is a member of |
| `groups:read` | View basic info about private channels |
| `mpim:history` | Read messages in multiparty direct messages |
| `mpim:read` | View multiparty direct messages |
| `im:history` | Read messages in direct messages |
| `im:read` | View direct messages |
| `team:read` | View workspace/team info |
| `users:read` | View user info |
| `users.profile:read` | View user profile |

### Step 5 — Configure event subscriptions (webhooks)

Under **Event Subscriptions**:
1. Toggle **Enable Events** to On.
2. Set the **Request URL** to: `http://localhost:8000/v1/webhooks/slack`
3. Slack will send a URL verification challenge — your server must respond correctly.
4. Subscribe to these bot events:
   - `message.channels` — messages in public channels
   - `message.groups` — messages in private channels
   - `channel_created` — new public channel
   - `channel_archive` — channel archived
   - `channel_unarchive` — channel unarchived

### Step 6 — Install the app to your workspace

Click **Install to Workspace** and authorise. This generates:
- **Bot User OAuth Token** (`xoxb-...`) — used for API calls
- **User OAuth Token** (`xoxp-...`) — if user scopes were requested

> The app stores these tokens encrypted at runtime via the OAuth flow; you do not put them in `.env`. The `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and `SLACK_SIGNING_SECRET` are the only Slack values in `.env`.

### Signing Secret usage

The `SLACK_SIGNING_SECRET` is used to verify that incoming webhook requests are genuinely from Slack. Slack sends an `X-Slack-Signature` header; the server computes `v0=HMAC-SHA256(signing_secret, "v0:{timestamp}:{raw_body}")` and compares it in constant time.

**Sources:**
- [Slack — Installing via OAuth](https://docs.slack.dev/authentication/installing-with-oauth/)
- [Slack — Verifying requests from Slack](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack — Scopes reference](https://docs.slack.dev/reference/scopes/)

---

## 7. Google Drive

```env
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/v1/oauth/google/callback
```

### Step 1 — Create a Google Cloud Project

1. Go to the **Google Cloud Console**: <https://console.cloud.google.com/>
2. Click the project selector (top bar) → **New Project**.
3. Enter a project name (e.g., "Enterprise AI Assistant") → **Create**.

### Step 2 — Enable the Google Drive API

1. Go to **API Library**: <https://console.cloud.google.com/apis/library>
2. Search for **Google Drive API**.
3. Select it → click **Enable**.

### Step 3 — Configure OAuth consent screen

1. Go to **APIs & Services** → **OAuth consent screen**.
2. Choose **User type: Internal** (for Google Workspace) or **External** (for testing).
3. Fill in required fields (app name, support email, developer email).
4. Add the scopes you need (see Step 5).
5. Save and continue.

### Step 4 — Create OAuth 2.0 Client ID credentials

1. Go to **APIs & Services** → **Credentials**: <https://console.cloud.google.com/apis/credentials>
2. Click **+ Create Credentials** → **OAuth client ID**.
3. Application type: **Web application**.
4. Name: "Enterprise AI Assistant".
5. Under **Authorised redirect URIs**, add:
   ```
   http://localhost:8000/v1/oauth/google/callback
   ```
6. Click **Create**.
7. You will see:
   - **Client ID** (ends in `.apps.googleusercontent.com`) → copy to `GOOGLE_CLIENT_ID`
   - **Client Secret** → copy to `GOOGLE_CLIENT_SECRET`

> The client secret is only shown once. Download the `client_secret.json` file for backup.

### Redirect URI rules (verified from Google docs)

- HTTPS required in production (localhost HTTP is exempt).
- Cannot be raw IP addresses (localhost IPs exempt).
- Must exactly match scheme, host, path, and trailing slash.
- No wildcards, fragments, or URL shorteners.

### Step 5 — Required OAuth scopes

For the consent screen and the app's authorisation request, use:

| Scope URL | Description |
|---|---|
| `https://www.googleapis.com/auth/drive.readonly` | View and download all Google Drive files (file content + metadata) |

> Use `drive.readonly` (not `drive.metadata.readonly`) because the assistant needs to read file **content** for indexing and retrieval, not just metadata.

### OAuth flow endpoints (for reference)

- **Authorisation endpoint:** `https://accounts.google.com/o/oauth2/v2/auth`
- **Token endpoint:** `https://oauth2.googleapis.com/token`
- **Revocation endpoint:** `https://oauth2.googleapis.com/revoke`

**Sources:**
- [Google — Using OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google — OAuth 2.0 Scopes](https://developers.google.com/identity/protocols/oauth2/scopes)
- [Google Cloud Console — Credentials](https://console.cloud.google.com/apis/credentials)

---

## 8. Model Gateway (Embeddings, Reranking, Generation)

```env
MODEL_PROVIDER=openai
MODEL_API_KEY=
GENERATION_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
RERANK_MODEL=gpt-4o-mini
```

### OpenAI (default)

1. Go to **OpenAI Platform**: <https://platform.openai.com/api-keys>
2. Click **Create new secret key**.
3. Copy the key (starts with `sk-...`) to `MODEL_API_KEY`.
4. Verify the model names are current:
   - `GENERATION_MODEL`: `gpt-4o-mini` (fast, cheap, supports structured output)
   - `EMBEDDING_MODEL`: `text-embedding-3-small` (1536 dimensions)
   - `RERANK_MODEL`: `gpt-4o-mini` (reranking via prompt-based scoring)

> Check current model availability at <https://platform.openai.com/docs/models>. If a model is deprecated, update both `.env` and the `embedding_model` / `generation_model` version tracking in the database.

**Source:** [OpenAI — API Keys](https://platform.openai.com/api-keys)

### Alternative providers

The `MODEL_PROVIDER` abstraction supports swapping providers. To use a different provider (e.g., Cohere, Anthropic, a local model), update the `ModelGateway` implementation in `services/api/src/app/models/gateway.py` and set the corresponding environment variables.

---

## 9. Object Store (S3-compatible / MinIO)

```env
OBJECT_STORE_ENDPOINT=http://localhost:9000
OBJECT_STORE_BUCKET=audit-checkpoints
OBJECT_STORE_ACCESS_KEY=minioadmin
OBJECT_STORE_SECRET_KEY=minioadmin
```

### Local setup — MinIO (Docker)

1. Start MinIO via Docker Compose (included in `docker-compose.yml`):
   ```bash
   docker compose up -d minio
   ```

2. MinIO will be available at:
   - **Console:** `http://localhost:9001` (login: `minioadmin` / `minioadmin`)
   - **S3 API endpoint:** `http://localhost:9000`

3. Create the audit bucket:
   - Open the MinIO console at `http://localhost:9001`.
   - Go to **Buckets** → **Create Bucket**.
   - Name it `audit-checkpoints`.
   - Or via CLI:
     ```bash
     docker exec -it minio mc alias set local http://localhost:9000 minioadmin minioadmin
     docker exec -it minio mc mb local/audit-checkpoints
     ```

### Defaults

| Variable | Default dev value |
|---|---|
| `OBJECT_STORE_ENDPOINT` | `http://localhost:9000` |
| `OBJECT_STORE_BUCKET` | `audit-checkpoints` |
| `OBJECT_STORE_ACCESS_KEY` | `minioadmin` |
| `OBJECT_STORE_SECRET_KEY` | `minioadmin` |

### Production

Use AWS S3, Google Cloud Storage, or a managed S3-compatible service. Set the endpoint, bucket, and credentials to your production values. Ensure the bucket has encryption at rest enabled and access is restricted to the application service account only.

**Source:** [MinIO — Quick Start](https://min.io/docs/minio/linux/index.html)

---

## 10. KMS (Key Management Service)

```env
KMS_KEY_ID=dev-local-key
```

### Local development

The app uses envelope encryption for storing source credentials and audit data. For local development, a derived key from `ENCRYPTION_MASTER_KEY` is used (see Section 11 below). Set `KMS_KEY_ID=dev-local-key` — this is a label only; no actual KMS call is made in dev.

### Production

Use a managed KMS:
- **AWS KMS:** Create a key at <https://console.aws.amazon.com/kms> → **Create key** → copy the Key ID (ARN) to `KMS_KEY_ID`.
- **Google Cloud KMS:** Create a key ring and key at <https://console.cloud.google.com/security/kms> → copy the full key path.
- **HashiCorp Vault:** Configure a transit engine key and set `KMS_KEY_ID` to the key name.

The key is used to unwrap data encryption keys (DEKs) at runtime. The plaintext DEK never touches disk and is never logged.

---

## 11. Encryption (dev only)

```env
ENCRYPTION_MASTER_KEY=dev-only-do-not-use-in-production-32bytes!!
```

This is a **development-only** master key used to derive DEKs when a real KMS is not available. It must be exactly 32 bytes (256 bits).

- **Never** use this in production.
- **Never** commit a real master key to Git.
- In production, set `KMS_KEY_ID` to a managed key and remove `ENCRYPTION_MASTER_KEY` entirely — the app will use the KMS to unwrap keys.

To generate a fresh dev key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 12. Query Deadlines / Intervals

```env
QUERY_AUTH_DEADLINE_MS=1500
QUERY_GENERATION_DEADLINE_MS=6000
RECONCILIATION_INTERVAL_MINUTES=15
```

These are tuning parameters, not credentials:
- `QUERY_AUTH_DEADLINE_MS` — total budget for live provider access checks (ms).
- `QUERY_GENERATION_DEADLINE_MS` — timeout for the model generation call (ms).
- `RECONCILIATION_INTERVAL_MINUTES` — how often the background reconciliation worker runs.

Adjust based on your provider latency and freshness requirements. The spec targets < 10 seconds end-to-end.

---

## Quick Start Checklist

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in the provider credentials you obtained above:
   - [ ] `ATLASSIAN_CLIENT_ID` / `ATLASSIAN_CLIENT_SECRET`
   - [ ] `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` / `SLACK_SIGNING_SECRET`
   - [ ] `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
   - [ ] `MODEL_API_KEY`
   - [ ] `OIDC_ISSUER` / `OIDC_AUDIENCE` / `OIDC_JWKS_URL` (if using Keycloak, start it first)

3. The following have sensible dev defaults and need no changes for local development:
   - `DATABASE_URL` (Docker Compose PostgreSQL)
   - `REDIS_URL` (Docker Compose Redis)
   - `OBJECT_STORE_*` (Docker Compose MinIO)
   - `KMS_KEY_ID` / `ENCRYPTION_MASTER_KEY` (local-only encryption)
   - `QUERY_*` / `RECONCILIATION_*` (tuning defaults)

4. Start infrastructure:
   ```bash
   docker compose up -d
   ```

5. Run migrations:
   ```bash
   uv run alembic upgrade head
   ```

6. Seed fixtures:
   ```bash
   uv run python scripts/seed_fixtures.py
   ```

7. Start the API and worker:
   ```bash
   uv run uvicorn services.api.src.app.main:app --reload --port 8000
   uv run celery -A worker.celery_app worker --loglevel=info
   ```

8. Start the frontend:
   ```bash
   cd apps/web && npm install && npm run dev
   ```

---

## Security Reminders

- **Never** commit `.env` to Git. The `.gitignore` should exclude it.
- **Never** log tokens, secrets, refresh tokens, or authorisation headers.
- Store production secrets in a managed secret store (AWS Secrets Manager, Google Secret Manager, HashiCorp Vault).
- Rotate credentials if you suspect any leak.
- Use separate Atlassian/Slack/Google apps for development and production.
- The `.env.example` file contains **names only, never values**.
