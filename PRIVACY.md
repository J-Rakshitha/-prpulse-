# Privacy

PR Pulse estimates merge timing and related risk from **public GitHub pull-request metadata**. It is not a code review and it does not claim access beyond what GitHub grants the installed App (or a configured token).

## What we read

From installed repositories (and from public APIs used for training):

- PR metadata: number, author login, timestamps, draft flag, labels, base/head refs, size fields GitHub exposes on the PR object
- File **paths** and per-file addition/deletion counts from the Pull Request Files API (not full file contents for modeling)
- Timeline-style events needed for early signals and outcomes (comments timing, CI workflow conclusions, merge/close)
- Optional repo file: `.prpulse.yml` (opt-out / config only)

We do **not** use private source code content as model features. Training uses public repository data only.

## What we store

- Normalized PR / event / CI rows in the application database
- Model predictions (quantiles, probabilities, optional explanation JSON) and live outcomes
- Webhook delivery envelopes (for idempotency and debugging) subject to retention
- GitHub App installation ids and repo full names

## Retention

Default raw webhook payload retention: **90 days** (`PAYLOAD_RETENTION_DAYS`). Older `webhook_deliveries.payload_json` values are deleted by the retention job. Aggregated predictions/outcomes may be kept longer for live-accuracy reporting unless you request deletion.

## What the bot posts

A single updating PR comment with a statistical estimate, confidence/coverage notice when applicable, a link to this privacy page, and opt-out instructions. Comments are estimates, not promises or reviews.

## Opt out

- Add label `no-prpulse` (or `prpulse-ignore` / `prpulse:ignore`), or
- Put `enabled: false` in `.prpulse.yml` on the default branch base ref

Opted-out repos receive **no bot comment** and **no new stored predictions** from the webhook worker.

## Request deletion

Contact the operator running your install, or run (operator):

```bash
python scripts/delete_repo_data.py --repo owner/name
python scripts/delete_repo_data.py --author some-login
```

That removes stored rows for the given repo or author from the application database.

## GitHub terms and rate limits

The bot only uses data from repositories where it is installed, plus public data collected for training. It respects GitHub API rate-limit headers (backoff / reschedule) and does not scrape private content outside the granted permissions.
