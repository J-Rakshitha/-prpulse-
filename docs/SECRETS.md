# Secrets management

All secrets come from environment variables (or a mounted key file). Never commit them. Never log them.

## Where each secret lives

| Secret | Env / path | Used by |
| --- | --- | --- |
| Webhook HMAC secret | `WEBHOOK_SECRET` | API `/webhook` |
| GitHub App ID | `GITHUB_APP_ID` | API / worker (App mode) |
| GitHub App private key | `GITHUB_APP_PRIVATE_KEY` or file at `GITHUB_APP_PRIVATE_KEY_PATH` | API / worker |
| Personal access token | `GITHUB_TOKEN` | Collector; bot only when `PRPULSE_AUTH_MODE=pat` |
| Database URL | `DATABASE_URL` | API, worker, dashboard jobs |
| Redis URL | `REDIS_URL` | API enqueue, worker |
| Dashboard basic-auth user | `DASHBOARD_USER` | Caddy |
| Dashboard basic-auth hash | `DASHBOARD_PASSWORD_HASH` | Caddy (bcrypt hash, not plaintext) |
| Alertmanager Slack/email | `ALERT_SLACK_WEBHOOK_URL` / SMTP vars | Alertmanager profile |
| Optional admin token | `PRPULSE_ADMIN_TOKEN` | Model switch admin route / CLI |

## Rotate without downtime

### Webhook secret

1. Generate a new secret.
2. In GitHub App webhook settings, set the new secret (GitHub applies it immediately for new deliveries).
3. Deploy API/worker with `WEBHOOK_SECRET` set to the **new** value in the same release window.
4. If you must dual-accept briefly, run two replicas is not supported in-app — rotate in one cutover during low traffic and redeliver any failed pings from the GitHub UI.

### App private key

1. In the GitHub App settings, generate a **new** private key (GitHub keeps prior keys until you delete them).
2. Roll out `GITHUB_APP_PRIVATE_KEY` / path with the new PEM.
3. Confirm `/health` and a test webhook.
4. Delete the old key in the GitHub UI.

### Dashboard password

1. Create a new bcrypt hash (`caddy hash-password` or `htpasswd -nB user`).
2. Update `DASHBOARD_PASSWORD_HASH` and reload Caddy (`docker compose exec proxy caddy reload` or recreate the proxy container).
3. Old sessions using basic auth simply use the new password on next prompt.

## If a secret leaks

1. **Revoke immediately** (GitHub token/App key/webhook secret; rotate DB/Redis passwords if exposed).
2. Rotate the webhook secret and App private key as above.
3. Check `webhook_deliveries` / GitHub audit log for unexpected traffic.
4. If a token was committed to git history: treat it as burned, rotate, and plan a history purge separately (do not rewrite history from the agent without an explicit request).
5. Recreate dashboard password hash if the plaintext or hash file leaked.
