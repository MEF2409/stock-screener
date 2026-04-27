# Deploying Market Pulse

This guide takes you from local-only to **always-on, authed, alerted** on Fly.io.
Total time: ~1.5 hours.

## Prerequisites

- A [Fly.io](https://fly.io) account (`fly auth login`)
- A [Finnhub](https://finnhub.io) free API key (for fast earnings calendar)
- An ngrok / Cloudflare account if you want a custom domain (optional)
- A Slack/Discord webhook URL if you want alerts (optional)

---

## 1. Configure auth

Generate a bcrypt hash for your password:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
```

Copy `auth_config.example.yaml` to `auth_config.yaml`, replace:
- `cookie.key` — paste output of `python -c "import secrets; print(secrets.token_hex(16))"`
- `credentials.usernames.mason.password` — your bcrypt hash
- Add additional usernames as needed

Test locally:

```bash
MP_AUTH_CONFIG=$(pwd)/auth_config.yaml streamlit run stock_screener/dashboard/app.py
```

The login form should appear. If `MP_AUTH_CONFIG` is unset, auth is bypassed (handy for local dev).

---

## 2. Deploy to Fly.io

```bash
# First-time launch — accept defaults; DON'T deploy yet
fly launch --no-deploy --copy-config

# Pick a region close to you; this becomes primary_region in fly.toml
# Common: iad (Virginia), sjc (San Jose), ord (Chicago), lhr (London)

# Create the persistent volume that holds the SQLite DB and exported results
fly volumes create market_pulse_data --size 1 --region iad

# Push secrets
fly secrets set FINNHUB_API_KEY=your_key_here
fly secrets set MP_AUTH_CONFIG=/app/auth_config.yaml
fly secrets set SLACK_WEBHOOK_URL=https://hooks.slack.com/...   # optional

# Auth config has to be inside the image — copy it into place before deploy
# (We don't bake creds in via Dockerfile; we copy at runtime via a secret file)
fly secrets set --stage AUTH_CONFIG_YAML="$(cat auth_config.yaml)"

# First deploy
fly deploy
```

**To inject the auth YAML at startup**, add this to the top of `Dockerfile` CMD or a startup script:

```bash
# Inside the container, before streamlit runs
echo "$AUTH_CONFIG_YAML" > /app/auth_config.yaml
streamlit run stock_screener/dashboard/app.py
```

Or simpler: use Fly's `[mounts]` to put `auth_config.yaml` on the volume manually
(via `fly ssh sftp` or a one-time `fly ssh console`).

After deploy:

```bash
fly status        # see machine state
fly logs          # tail logs
fly open          # open the deployed app in your browser
```

---

## 3. First-time data load

SSH into the machine and run the universe builder + price refresh:

```bash
fly ssh console
cd /app
python scripts/daily_refresh.py
exit
```

This takes ~15 minutes for the full NYSE+NASDAQ universe and writes the SQLite DB
to `/data/db/screener.db` (which is on your persistent volume).

---

## 4. Schedule daily refresh

### Option A: GitHub Actions (recommended — free, no always-on VM)

1. In GitHub repo settings → Secrets:
   - `FLY_API_TOKEN`: get via `fly auth token`
   - `FLY_APP_NAME`: your Fly app name from `fly.toml`
2. The workflow at `.github/workflows/daily-refresh.yml` runs at 4:30pm ET on
   weekdays and SSH-triggers `daily_refresh.py` inside the Fly machine.
3. Manual trigger: GitHub UI → Actions → "Daily Refresh" → "Run workflow".

### Option B: Fly scheduled machine (lives inside Fly)

```bash
fly machine run --app market-pulse \
    --schedule "30 20 * * 1-5" \
    --region iad \
    "python /app/scripts/daily_refresh.py"
```

---

## 5. Add a custom domain + Cloudflare Access (optional but recommended)

This puts CF's auth in front of your app — even more secure than streamlit-authenticator.

```bash
# Point your domain at Fly
fly certs add yourdomain.com

# Add the DNS records Fly tells you to (CNAME to <app>.fly.dev)
```

Then in Cloudflare:
1. Zero Trust → Access → Applications → Add an application → Self-hosted
2. Domain: `yourdomain.com`
3. Policy: Allow → emails: `mef2409@gmail.com` (and anyone else)
4. Save. Hitting yourdomain.com now requires Cloudflare login first.

You can keep streamlit-authenticator on top, or remove it now that CF Access guards the door.

---

## 6. Verify alerts

Trigger a manual run:

```bash
fly ssh console --command "python /app/scripts/daily_refresh.py"
```

Watch the logs in `fly logs` — you should see the alert step at the bottom:

```
7. Sending alerts...
   ✓ slack: sent
   - discord: not configured
   - email: not configured
```

A formatted message with today's signals should land in your Slack channel.

---

## When to migrate from SQLite to Postgres

You can defer this until any of these are true:

- You want **multiple Fly machines** (SQLite on a single volume can't be shared)
- You want **horizontal scaling** for many concurrent users
- The DB grows past ~1 GB (SQLite is fine up to several GB but Postgres is more
  comfortable for analytical queries at that size)
- You want **point-in-time backups** (SQLite needs a manual snapshot routine)

When you're ready: [Neon](https://neon.tech) free tier gives 0.5 GB for free.
The migration is roughly:

1. `pip install sqlalchemy psycopg2-binary`
2. Wrap `db.py`'s `get_connection()` to dispatch on `DATABASE_URL`
3. Replace `INSERT OR REPLACE` with Postgres `ON CONFLICT DO UPDATE` everywhere
4. Run a one-time migration: `sqlite3 db/screener.db .dump | pg-import`

Plan ~4 hours for a clean migration. Not blocking for v1.

---

## Cost estimate (typical month)

| Item | Cost |
|---|---|
| Fly.io 1× shared-cpu-1x with 1 GB volume | ~$2-5 |
| GitHub Actions cron (5 mins/day × 22 weekdays) | $0 (within free tier) |
| Cloudflare Access | $0 (50 users free) |
| Neon Postgres (when you need it) | $0 (0.5 GB free) |
| Finnhub free key | $0 |
| **Total** | **~$2-5/mo** |

---

## Troubleshooting

- **"Connection refused" right after deploy**: machine is still starting. `fly logs` to watch.
- **SQLite errors after deploy**: check the volume mounted properly with `fly ssh console -C "ls -la /data/db"`.
- **Auth YAML not loading**: confirm `MP_AUTH_CONFIG` is set and the file exists at that path on the volume.
- **Cron not firing**: check `fly logs --app market-pulse` around 4:30pm ET, or manually trigger via GH Actions UI.
