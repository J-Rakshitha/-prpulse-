# Manual steps only you can do (live bot)

Agent/local stack side is ready when:

- `curl.exe http://127.0.0.1:8080/health` → `"status":"ok"`
- `.env` has `WEBHOOK_SECRET` + `GITHUB_TOKEN`
- `PRPULSE_AUTH_MODE=pat` (quick path)
- `PRPULSE_BOT_DRY_RUN` is empty / unset

Do **not** put secrets in git or chat.

---

## A. Public URL (ngrok)

1. Install ngrok: https://ngrok.com/download (sign up, add authtoken once).
2. With Docker already on `:8080`, run:

```powershell
ngrok http 8080
```

3. Copy the HTTPS URL, e.g. `https://abc123.ngrok-free.app`.
4. Your webhook URL is: `https://abc123.ngrok-free.app/webhook`

Keep this terminal open while testing.

---

## B. GitHub webhook on a test repo

Use a repo you own (public is fine).

1. Repo → **Settings** → **Webhooks** → **Add webhook**
2. Payload URL: `https://<your-ngrok>/webhook`
3. Content type: `application/json`
4. Secret: **exactly** the same string as `WEBHOOK_SECRET` in `.env`
5. Which events: **Let me select**
   - Pull requests
   - Workflow runs (optional, for CI outcomes)
6. Add webhook

**Ping check:** Webhook → Recent deliveries → `ping` should be **200** with body like `{"status":"pong"}`.

- **401** → secret mismatch  
- **503** → API missing `WEBHOOK_SECRET` (restart compose after fixing `.env`)  
- Connection error → ngrok not running or wrong port (must be **8080**, not 8000)

---

## C. Token permission

Classic PAT needs at least **`public_repo`** (public) or **`repo`** (private) so the bot can **post PR comments**.

Fine-grained: Pull requests R/W, Issues R/W, Contents Read, Metadata Read on that repo.

---

## D. Live test PR

1. Open a small PR on that repo (not from a bot account).
2. Wait ~10–60s. You should see **one** comment starting with PR Pulse estimate (`<!-- prpulse:marker -->` inside HTML).
3. Push another commit to the PR → same comment should **update**, not duplicate.
4. **Merge or close** the PR.
5. Open dashboard: http://127.0.0.1:8080/dashboard → refresh. Outcomes / live panels update from DB after close/merge (and CI workflow_run if enabled).

Opt-out check (optional): add label `no-prpulse` → no new comment.

---

## E. If no comment appears

On your machine:

```powershell
docker compose logs worker --tail 80
docker compose logs api --tail 40
```

Common causes:

- Dry-run still on → unset `PRPULSE_BOT_DRY_RUN`, recreate api/worker  
- Token cannot comment → 403 in worker logs  
- Webhook not reaching ngrok → GitHub Recent deliveries red  
- Unseen-repo banner only / model ok but GitHub post failed → check worker logs  

---

## Optional later: GitHub App

See `docs/GITHUB_APP.md`. Then set `PRPULSE_AUTH_MODE=app` plus App ID and private key path. Not required if PAT path works for your demo.
