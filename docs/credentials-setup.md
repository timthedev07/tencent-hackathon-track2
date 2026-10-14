# Credentials Setup Guide

This document explains how to obtain every credential referenced in `.env.example` for local development and demo deployment. Each section links to the official documentation it was verified against.

## Hosted demo: GitHub Actions + Render

This section is the shortest path for the current short-lived demo deployment. The workflow is [`.github/workflows/ci-cd.yml`](../.github/workflows/ci-cd.yml) and the Render infrastructure is [`render.yaml`](../render.yaml).

### What you need

For the minimum hosted demo, obtain or configure:

| Where | Name | Secret? | Purpose |
|---|---|---:|---|
| GitHub Actions environment `demo` | `RENDER_API_KEY` | Yes | Allows CI to trigger Render deployments. |
| GitHub Actions environment `demo` | `RENDER_API_SERVICE_ID` | No | Identifies the FastAPI service. |
| GitHub Actions environment `demo` | `RENDER_WEB_SERVICE_ID` | No | Identifies the Next.js service. |
| GitHub Actions environment `demo` | `RENDER_WORKSPACE_ID` | No | Tells Render which workspace to validate the Blueprint against. |
| GitHub Actions environment `demo` | `DEMO_API_URL` | No | API URL used by the smoke test. |
| GitHub Actions environment `demo` | `DEMO_WEB_URL` | No | Web URL used by the smoke test. |
| Render API service | `MODEL_API_KEY` | Yes | Lets the model gateway generate and embed answers. |

PostgreSQL and Redis connection strings, the encryption key, and the audit signing key are generated or connected by the Render Blueprint. They do not need to be copied into GitHub.

### Where each value goes

| Value type | Exact destination |
|---|---|
| Local API settings and provider credentials | Copy `.env.example` to `.env` in the repository root, then replace the relevant values. The API loads this file automatically. Never commit `.env`. |
| Local web API URL | Create `apps/web/.env.local` and set `NEXT_PUBLIC_API_URL=http://localhost:8000`. |
| Hosted API settings and provider credentials | Render Dashboard → `relay-demo-api` → **Environment**. Add each variable as a separate environment variable, then save and redeploy. |
| Hosted web API URL | Render Dashboard → `relay-demo-web` → **Environment** → `NEXT_PUBLIC_API_URL`. |
| Deployment credential | GitHub repository → **Settings → Environments → demo** → **Environment secrets** → `RENDER_API_KEY`. |
| Render service IDs | GitHub repository → **Settings → Environments → demo** → **Environment secrets** → `RENDER_API_SERVICE_ID` and `RENDER_WEB_SERVICE_ID`. |
| Render workspace ID | GitHub repository → **Settings → Environments → demo** → **Environment variables** → `RENDER_WORKSPACE_ID`. |
| Smoke-test URLs | GitHub repository → **Settings → Environments → demo** → **Environment variables** → `DEMO_API_URL` and `DEMO_WEB_URL`. |

For hosted deployment, do not put application credentials in GitHub Actions. GitHub only needs the Render deployment key and identifiers; the API reads runtime credentials from Render.

### 1. Connect Render to GitHub

1. Sign in to [Render](https://dashboard.render.com/).
2. If the repository is private, connect the repository through Render's [Git provider integration](https://render.com/docs/git-provider).
3. Push this repository, including `render.yaml`, to the branch you intend to deploy (`dev` or `main`).
4. In Render, select **New → Blueprint**, choose this repository and branch, and apply `render.yaml`. See [Render Blueprints](https://render.com/docs/infrastructure-as-code).
5. When prompted, provide `MODEL_API_KEY`. Leave generated values for `ENCRYPTION_MASTER_KEY` and `AUDIT_CHECKPOINT_SIGNING_KEY` enabled.
6. Set these values on the API service after the two service URLs are known:
   - `WEB_BASE_URL` = the web service URL, for example `https://relay-demo-web.onrender.com`.
   - `CORS_ALLOWED_ORIGINS` = the same web service URL.
7. Set `NEXT_PUBLIC_API_URL` on the web service to the API service URL, for example `https://relay-demo-api.onrender.com`.

The Blueprint sets service auto-deploy to off so GitHub Actions remains the deployment gate. The `sync: false` values are prompted for during initial Blueprint creation; Render documents that new values with `sync: false` must be added manually later if the Blueprint already exists. See [Render environment variables](https://render.com/docs/configure-environment-variables).

### 2. Create the Render API key

1. Open Render **Account Settings → API Keys**, or follow the [Render API setup guide](https://render.com/docs/api).
2. Select **Create API key**.
3. Give it a descriptive name such as `github-actions-demo-deploy`.
4. Copy the value immediately. Render displays the full key only when it is created.
5. Treat it as a password. Do not put it in `render.yaml`, `.env.example`, a commit, an issue, or a workflow log.

The workflow sends this key as a Bearer token to Render's [Trigger Deploy endpoint](https://api-docs.render.com/reference/create-deploy).

### 3. Find both Render service IDs

After the Blueprint creates the services:

1. Open the `relay-demo-api` service in Render.
2. Copy the service ID from the service URL or service settings. It has the form `srv-...`.
3. Repeat for `relay-demo-web`.

The service IDs are identifiers rather than passwords, but keep them in the protected `demo` environment because the workflow reads them alongside the API key.

Also obtain the Render workspace ID used by the Blueprint:

1. Authenticate the Render CLI locally with `render login`, or use the Render account that owns the Blueprint.
2. Run `render workspaces -o json` and copy the workspace `id` for the workspace containing `relay-demo-api`.
3. Save it as the GitHub environment variable `RENDER_WORKSPACE_ID` in the next step. It is an identifier, not a secret.

### 4. Configure GitHub Actions

Create a GitHub environment named `demo`:

1. Open the repository on GitHub.
2. Go to **Settings → Environments → New environment**.
3. Name it `demo`.
4. Optionally add a required reviewer if deployments should be approved manually. See [GitHub deployment environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment).

Add the following under **Environment secrets**:

1. `RENDER_API_KEY` — paste the Render API key from step 2.
2. `RENDER_API_SERVICE_ID` — paste the `srv-...` API service ID.
3. `RENDER_WEB_SERVICE_ID` — paste the `srv-...` web service ID.

Add the following under **Environment variables**:

1. `DEMO_API_URL` — the public API URL, without a trailing slash.
2. `DEMO_WEB_URL` — the public web URL, without a trailing slash.
3. `RENDER_WORKSPACE_ID` — the Render workspace ID from step 3.

GitHub's automatically provided `GITHUB_TOKEN` is sufficient for checkout. No GitHub personal access token is needed. See [GitHub Actions secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions) and [GitHub Actions variables](https://docs.github.com/en/actions/learn-github-actions/variables).

### 5. Create the model API key

The current Blueprint uses `MODEL_PROVIDER=openai`.

1. Sign in to the [OpenAI API platform](https://platform.openai.com/).
2. Create or select a project, then open [API keys](https://platform.openai.com/api-keys).
3. Select **Create new secret key** and copy it immediately.
4. Ensure the project has an active payment method or available credit in [Billing](https://platform.openai.com/settings/organization/billing/overview).
5. In Render, open `relay-demo-api → Environment` and set `MODEL_API_KEY` to the key.
6. Redeploy the API service, or push a commit after the value is saved.

Do not store this runtime key in GitHub unless the workflow itself needs to call the model. The application reads it from Render at runtime. Check [OpenAI model documentation](https://platform.openai.com/docs/models) before changing `GENERATION_MODEL`, `EMBEDDING_MODEL`, or `RERANK_MODEL`.

### 6. Verify the deployment

1. Push a commit to `dev` or `main`, or run the workflow manually from **Actions → CI/CD → Run workflow**.
2. Confirm the **Quality checks** job passes.
3. Confirm the **Deploy demo to Render** job reaches `live` for both services.
4. Open `DEMO_WEB_URL` and use the development sign-in screen.
5. If the web app cannot call the API, recheck `NEXT_PUBLIC_API_URL`, `WEB_BASE_URL`, and `CORS_ALLOWED_ORIGINS` for exact spelling and no trailing slash.

The demo deliberately uses `APP_ENV=development`, so its built-in sign-in accepts development tokens. Do not expose it to real users or sensitive data.

## Optional source-integration credentials

The following credentials are only needed if the demo connects to those providers. OAuth access and refresh tokens are obtained during the user authorisation flow and are stored by the application; they are not manually placed in `.env`.

### Atlassian: Jira and Confluence

1. Open the [Atlassian Developer Console](https://developer.atlassian.com/console/myapps/).
2. Create an **OAuth 2.0 (3LO)** app.
3. Copy the app's **Client ID** to `ATLASSIAN_CLIENT_ID` and **Client secret** to `ATLASSIAN_CLIENT_SECRET`.
4. Add this callback URL, replacing `<api-host>` with the deployed API hostname:
   ```text
   https://<api-host>/v1/oauth/atlassian/callback
   ```
5. In Render Dashboard → `relay-demo-api` → **Environment**, add:
   ```text
   ATLASSIAN_CLIENT_ID=<client-id>
   ATLASSIAN_CLIENT_SECRET=<client-secret>
   ATLASSIAN_REDIRECT_URI=https://<api-host>/v1/oauth/atlassian/callback
   ATLASSIAN_WEBHOOK_SECRET=<self-generated-secret-if-used>
   ```
6. Add only the read scopes required by the demo, then save and authorise the app.

Read [Atlassian OAuth 2.0 (3LO) apps](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/) and [Atlassian OAuth security guidance](https://developer.atlassian.com/cloud/jira/platform/security-for-other-integrations/).

### Slack

1. Open [Slack API → Your Apps](https://api.slack.com/apps).
2. Select **Create New App → From scratch**, then choose the workspace.
3. From **Basic Information**, copy **Client ID**, **Client Secret**, and **Signing Secret** to:
   - `SLACK_CLIENT_ID`
   - `SLACK_CLIENT_SECRET`
   - `SLACK_SIGNING_SECRET`
4. Under **OAuth & Permissions**, add this redirect URL:
   ```text
   https://<api-host>/v1/oauth/slack/callback
   ```
5. In Render Dashboard → `relay-demo-api` → **Environment**, add:
   ```text
   SLACK_CLIENT_ID=<client-id>
   SLACK_CLIENT_SECRET=<client-secret>
   SLACK_SIGNING_SECRET=<signing-secret>
   SLACK_REDIRECT_URI=https://<api-host>/v1/oauth/slack/callback
   ```
6. Add the minimum bot scopes required by the demo and install the app to the workspace.
7. If event ingestion is enabled, set the request URL to:
   ```text
   https://<api-host>/v1/webhooks/slack
   ```

Read [Slack OAuth installation](https://docs.slack.dev/authentication/installing-with-oauth/), [request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/), and the [Slack scopes reference](https://docs.slack.dev/reference/scopes/).

### Google Drive

1. Create or select a project in the [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com).
3. Configure the OAuth consent screen under **Google Auth Platform**. Use **Internal** for a Google Workspace-only demo where available; otherwise use **External** and add test users.
4. Open [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
5. Create an **OAuth client ID** with application type **Web application**.
6. Add this authorised redirect URI:
   ```text
   https://<api-host>/v1/oauth/google/callback
   ```
7. In Render Dashboard → `relay-demo-api` → **Environment**, add:
   ```text
   GOOGLE_CLIENT_ID=<client-id>
   GOOGLE_CLIENT_SECRET=<client-secret>
   GOOGLE_REDIRECT_URI=https://<api-host>/v1/oauth/google/callback
   GOOGLE_WEBHOOK_TOKEN=<self-generated-token-if-used>
   ```
8. Generate a high-entropy channel token yourself and set it as `GOOGLE_WEBHOOK_TOKEN` if Drive push notifications are enabled.

Read Google's [web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server), [OAuth scopes](https://developers.google.com/identity/protocols/oauth2/scopes), and [Drive API overview](https://developers.google.com/drive/api/guides/about-sdk).

### OIDC identity provider

OIDC is not required for this demo because `APP_ENV=development` enables the built-in development sign-in. For a real deployment:

1. Create an OIDC client in your identity provider, such as [Keycloak](https://www.keycloak.org/getting-started/getting-started-docker), [Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), or [Auth0](https://auth0.com/docs/get-started/authentication-and-authorization-flow).
2. Set:
   - `OIDC_ISSUER` to the provider's issuer URL.
   - `OIDC_AUDIENCE` to the API audience/client identifier.
   - `OIDC_JWKS_URL` to the provider's JSON Web Key Set URL.
   - `OIDC_TENANT_CLAIM` to the claim containing the organisation identifier, normally `org_id` for this application.
   Put these four variables in Render Dashboard → `relay-demo-api` → **Environment** for a hosted deployment, or in the repository-root `.env` for local development.
3. Configure the provider to issue a stable `sub` claim and the tenant claim expected by the API.
4. Change `APP_ENV` to `production` only after all production-required settings in `services/api/src/app/settings.py` are present.

### S3-compatible object storage and KMS

Object storage is only needed when audit checkpoints are uploaded. For a hosted demo, you may leave the local MinIO defaults unused unless the checkpoint task is exercised.

For AWS:

1. Create an S3 bucket in the [AWS S3 console](https://console.aws.amazon.com/s3/).
2. Create a least-privilege IAM user or role with access only to that bucket, following [AWS IAM best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html).
3. Set `OBJECT_STORE_ENDPOINT`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_ACCESS_KEY`, and `OBJECT_STORE_SECRET_KEY` in Render.
4. For envelope-key management, create a key in [AWS KMS](https://console.aws.amazon.com/kms/) and set `KMS_KEY_ID`.

These values belong in Render Dashboard → `relay-demo-api` → **Environment**. For local MinIO, put them in the repository-root `.env` instead.

For Cloudflare R2, create a bucket and an API token from [R2 Object Storage](https://developers.cloudflare.com/r2/), then use the S3-compatible endpoint and access-key pair for the same four `OBJECT_STORE_*` values.

## Values generated by Render or the Blueprint

Do not invent or commit these values:

- `DATABASE_URL` — supplied from the Render PostgreSQL resource.
- `REDIS_URL` — supplied from the Render Key Value resource.
- `ENCRYPTION_MASTER_KEY` — generated by `render.yaml`.
- `AUDIT_CHECKPOINT_SIGNING_KEY` — generated by `render.yaml`.

If any of these values are exposed, rotate or recreate the affected resource and update the service environment before redeploying.

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
DATABASE_URL=postgresql+asyncpg://assistant:assistant@localhost:5432/assistant
```

### Local setup (Docker Compose)

The `docker-compose.yml` in the repo root starts a PostgreSQL + pgvector container. The default credentials are:

- **Host:** `localhost`
- **Port:** `5432`
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
REDIS_URL=redis://localhost:6382/0
```

### Local setup (Docker Compose)

The `docker-compose.yml` starts a Redis container. Defaults:

- **Host:** `localhost`
- **Port:** `6382` on the host (`6379` inside the Redis container)
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

6. Create test users (e.g., `alice`, `bob`, `carol`) with passwords. Use each user's `sub` claim as the persisted `oidc_subject` when that user first connects a provider.

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
| `read:page:confluence` | Read Confluence pages through the v2 API |
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
| `openid email profile` | Verify the Google account identity used for delegated access |
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

6. Start the API and worker:
   ```bash
   uv run uvicorn services.api.src.app.main:app --reload --port 8000
   make worker
   ```

7. Start the frontend:
   ```bash
   cd apps/web && yarn install && yarn dev
   ```

---

## Security Reminders

- **Never** commit `.env` to Git. The `.gitignore` should exclude it.
- **Never** log tokens, secrets, refresh tokens, or authorisation headers.
- Store production secrets in a managed secret store (AWS Secrets Manager, Google Secret Manager, HashiCorp Vault).
- Rotate credentials if you suspect any leak.
- Use separate Atlassian/Slack/Google apps for development and production.
- The `.env.example` file contains **names only, never values**.
