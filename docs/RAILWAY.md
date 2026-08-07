# Railway worker — deployment

## The service was never connected to the repo

Found 2026-08-07. The worker ran an image uploaded by hand; Settings ->
Source showed the "Connect your service to a GitHub repo or Docker image"
prompt, meaning **no push had ever reached it**. Code changes only
arrived when someone manually deployed.

That was invisible for as long as the worker did its own work, because
doing work rebuilt the board as a side effect. It surfaced the day
GITHUB_TOKEN was added and the worker began dispatching every task to
GitHub Actions instead: it stopped rebuilding anything, and the site
froze on a seven-hour-old board (AUDIT A-025).

**Connect the service to `joey11600/MLB-Strikeouts`, branch `master`.**
Without that, nothing in this repo reaches production automatically.

## Why "Redeploy" does not pick up new code

`Dockerfile` ends with `COPY . .`, so the worker's code is baked into the
image at BUILD time. Railway's **Redeploy** re-runs the existing image —
same code. Only a new BUILD from a newer commit ships new code.

The worker `git pull`s at runtime, but that only refreshes *data*: a
running Python process keeps the code it started with, and the next
container is recreated from the image anyway. So the worker can update
what it knows, never what it does.

## Watch patterns (`railway.json`)

Config-as-code overrides the dashboard, and the file must sit at the repo
root regardless of Root Directory.

```json
"watchPatterns": ["**", "!/data/**", "/data/*.py", "!/dashboard/**"]
```

Read in order — these are gitignore-style patterns, so later rules win:

| Pattern | Effect |
|---|---|
| `**` | build on anything by default |
| `!/data/**` | except ledger churn — CI pushes 10-18 of these a day |
| `/data/*.py` | but `backfill_statcast.py`, `game_context.py` and `id_crosswalk.py` live in `data/` and ARE worker code |
| `!/dashboard/**` | frontend-only work never affects the worker |

**The leading `**` is load-bearing.** Railway's docs: *"negations will
only work if you include files in a preceding rule."* A bare
`!/data/**` matches nothing and silently does nothing — the failure mode
being a config that looks applied and is not.

`/data/*.py` is the other trap: `data/` mixes the ledger with three
Python modules the worker imports. Excluding the whole directory would
have stopped real code changes from ever deploying.

Without watch patterns every ledger commit rebuilds and restarts the
container, which also interrupts the live starter watcher mid-game.

## Verifying a deploy actually landed

Do not trust "Deployed". Check that the code is what you think:

```bash
curl -s https://worker-production-036c.up.railway.app/health | python -m json.tool
```

- `last_publish` present -> the A-025 publish pass is running
- `last_publish.served_generated_at` -> the board being served right now
- `jobs_run_today` -> which windows fired

And `python tools/watchdog.py` asserts the served board matches the
repo's, comparing the SLATE's stamp rather than the payload's — the
worker rewrites the payload wrapper on every boot, so that timestamp says
nothing about whether the board is current.
