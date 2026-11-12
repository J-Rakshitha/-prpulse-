# PR Pulse bot setup (manual steps)

The code is finished and tested against a mocked GitHub client. These steps are the ones only you can do. Nothing here has been run against real GitHub from this repository.

## What the bot does

- Listens to `pull_request` (opened, reopened, synchronize, ready_for_review, closed) and `workflow_run` (completed) webhooks.
- Posts one comment per PR with the model's estimate, starting with the hidden marker `<!-- prpulse:marker -->`. Later events edit that same comment instead of adding another.
- Records the merge/close and first CI result in the `outcomes` table so live accuracy can be measured.
- Skips bot authors, opted-out PRs (see Opt-out below), and PRs that are not open.

## 1. Prerequisites

1. Train models: `python tasks.py train` (needs `data/features/stage_*.parquet`).
2. Install the packages in `requirements.txt` (FastAPI, uvicorn, prometheus-client, shap, lightgbm, streamlit, pandas, pyarrow, scikit-learn, joblib).
3. Start the API: `python tasks.py serve` (listens on `127.0.0.1:8000`; override with `PRPULSE_HOST` / `PRPULSE_PORT`).

## 2. Choose an authentication mode

**Option A: personal access token (quickest for a trial on a repo you own).**
Put `GITHUB_TOKEN` in `.env`. The token needs permission to comment on pull requests in the target repo (classic: `repo`, or `public_repo` for public repos; fine-grained: Pull requests: Read and write, Issues: Read and write, Contents: Read).

**Option B: GitHub App (recommended beyond a trial).**
This repository does not yet mint App installation tokens. `HttpGitHubClient` in `src/service/bot.py` takes a bearer token, so you would add a small token-minting step first. Create the App now if you want to go this way:

1. GitHub, Settings, Developer settings, GitHub Apps, New GitHub App.
2. Webhook URL: your public URL plus `/webhook` (see step 4). Webhook secret: the value you will also put in `WEBHOOK_SECRET`.
3. Repository permissions: Pull requests: Read and write. Issues: Read and write. Contents: Read-only. Actions: Read-only. Metadata: Read-only.
4. Subscribe to events: Pull request, Workflow run.
5. Generate a private key, save the `.pem` outside the repo, and put its path in `GITHUB_APP_PRIVATE_KEY_PATH`. Fill `GITHUB_APP_ID` and, after installing the App on the repo, `GITHUB_APP_INSTALLATION_ID`.

## 3. Secrets in `.env`

```
WEBHOOK_SECRET=<a long random string you invent>
GITHUB_TOKEN=<token, Option A only>
```

`.env` is gitignored. Never put these in `config.yaml`. If a token was ever committed or shared, revoke it on GitHub and create a new one.

Without `WEBHOOK_SECRET` the `/webhook` route answers 503. Without `GITHUB_TOKEN` (or with `PRPULSE_BOT_DRY_RUN=1`) the bot runs in dry-run mode: it builds the comment and writes it to the log, but posts nothing. Use dry-run first.

## 4. Expose your local server with ngrok

1. Install ngrok and run `ngrok http 8000`.
2. Copy the `https://....ngrok-free.app` address.
3. In the repo (Settings, Webhooks, Add webhook) or in the App settings:
   - Payload URL: `https://<ngrok-address>/webhook`
   - Content type: `application/json`
   - Secret: exactly the `WEBHOOK_SECRET` value
   - Events: Pull requests, Workflow runs
4. GitHub sends a `ping`. The Recent Deliveries tab should show a 200 response with `{"status":"pong"}`.

A 401 means the secret differs. A 503 means `WEBHOOK_SECRET` is not set in the server's environment.

## 5. Opt-out

A repository or PR can opt out in either way:

- Add the label `no-prpulse` (also accepted: `prpulse-ignore`, `prpulse:ignore`) to the PR.
- Commit `.prpulse.yml` to the base branch:

```yaml
enabled: false
```

(`opt_out: true` is also accepted.) An opted-out PR gets no new comment. An existing comment is left alone.

## 6. Limits to know about

- Live comments use the stage A model. Repo-state features (open PR count, recent merge time, reviewer activity) are not computed live and are filled with training medians, so live estimates are rougher than the offline evaluation.
- Duplicate deliveries are remembered in memory only. After a restart, a redelivery is still safe because the marker comment is found and edited rather than duplicated.
- PRs first seen through the API or bot are stored as `ingest_status = 'partial'` rows so predictions can reference them. The feature builder reads only `complete` rows, so they do not enter training until the collector has fetched them fully.
- Suggestions in the comment are model what-ifs. They are estimates of association, not guarantees.

## 7. Test locally without GitHub

```
python -m pytest tests/test_webhook.py -q
```

This signs payloads with a test secret and uses an in-memory fake GitHub client.
