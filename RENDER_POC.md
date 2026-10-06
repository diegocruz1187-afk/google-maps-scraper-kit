# ExpoRadar — Render POC

This fork keeps the original local workflow unchanged and adds a separate Render deployment path.

## Security model

The upstream Google Maps scraper has no built-in authentication. The Render deployment therefore:

1. runs the scraper only on `127.0.0.1:8080`;
2. exposes a small proxy on Render's public `PORT`;
3. requires `X-API-Key` for every scraper API request;
4. leaves only `/health` unauthenticated for Render health checks.

Never expose the upstream scraper directly on a public interface.

## Deploy

Create a Render Blueprint from this repository and use branch:

`feat/exporadar-render-poc`

Render reads `render.yaml` and builds `Dockerfile.render`.

The Blueprint generates `SCRAPER_API_KEY` automatically. Copy its value from the Render dashboard after the service is created; do not commit it to Git.

## Smoke checks

Health:

```bash
curl https://<service>.onrender.com/health
```

Expected:

```json
{"status":"ok"}
```

Unauthorized access must fail:

```bash
curl -i https://<service>.onrender.com/api/v1/jobs
```

Expected: HTTP 401.

Authorized access:

```bash
curl -H "X-API-Key: $SCRAPER_API_KEY" \
  https://<service>.onrender.com/api/v1/jobs
```

Expected: HTTP 200 and a JSON job list.

## POC scope

Do not connect this service to ExpoRadar/Supabase yet. First validate four known companies:

- LGM Lasers
- Power Dent
- MIXPAC
- Oral-B

For each test, compare business identity, website, phone, address, email and social links against the current ExpoRadar enrichment pipeline.

## Data persistence

The POC uses `/tmp/gmapsdata` intentionally. Job artifacts can disappear on restart/redeploy. This is acceptable for the validation phase and avoids adding persistent-disk cost before the scraper proves useful.

## Responsible use

Use low depth and one job at a time during the POC. Scraping Google Maps can be rate-limited and may conflict with Google's Terms of Service. Treat discovered contact data as evidence/leads to verify, not as authoritative identity data.
