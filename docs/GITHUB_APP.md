# Registering the PR Pulse GitHub App

You register the App yourself. The service defaults to App auth in production (`PRPULSE_AUTH_MODE=app`). Local/dev can keep `PRPULSE_AUTH_MODE=pat` with a personal token.

## Minimum permissions

Repository permissions:

| Permission | Access | Why |
| --- | --- | --- |
| Metadata | Read-only | Required for all Apps |
| Pull requests | Read-only | PR payload fields + file list |
| Contents | Read-only | Read `.prpulse.yml` opt-out config |
| Checks | Read-only | CI signal context (when available) |
| Actions | Read-only | `workflow_run` conclusions for live outcomes |
| Issues | Read & write | Issue comments are the PR comment API |

Account permissions: none required beyond installing on target repos.

## Subscribe to events

- `ping`
- `pull_request`
- `workflow_run`
- `installation`
- `installation_repositories`

## Registration steps

1. GitHub → **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**.
2. **GitHub App name**: e.g. `PR Pulse` (must be unique on GitHub).
3. **Homepage URL**: your service URL or repo URL.
4. **Webhook**:
   - Active: yes
   - Webhook URL: `https://<your-host>/webhook`
   - Webhook secret: generate a long random string; put the same value in `WEBHOOK_SECRET`.
5. Set the permissions and events listed above.
6. **Where can this GitHub App be installed?** Only on this account, or any account if you will distribute it.
7. Create the App. Note the **App ID** → `GITHUB_APP_ID`.
8. **Generate a private key**. Download the `.pem`. Either:
   - set `GITHUB_APP_PRIVATE_KEY_PATH` to the mounted file path, or
   - paste the PEM into `GITHUB_APP_PRIVATE_KEY` (including `BEGIN/END` lines).
9. Install the App on the repos that should receive comments. Note each installation id (Installations page) if you need it for debugging; the service tracks installations from webhook events.
10. Set `PRPULSE_AUTH_MODE=app` and restart API + worker.

## Dev mode (personal token)

```env
PRPULSE_AUTH_MODE=pat
GITHUB_TOKEN=ghp_...
WEBHOOK_SECRET=...
```

Use only for local testing. Do not use a PAT in production multi-repo installs.

## Live App test

A live end-to-end App test needs your registration (App ID, private key, installation on a test repo). Unit tests mock GitHub and do not replace that step.
