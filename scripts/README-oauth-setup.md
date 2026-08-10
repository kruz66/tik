# GCP OAuth pool setup (local Playwright)

Automates: create GCP projects → enable YouTube Data API v3 → Auth Platform (External, scopes, test users) → Web OAuth client → download `client_secrets{N}.json`.

## Quick start (Windows + Brave, recommended)

### 1. Install

```powershell
cd path\to\tik
pip install -r scripts/requirements-oauth-setup.txt
playwright install chromium
```

### 2. Config

```powershell
copy scripts\oauth_setup_config.example.json oauth_setup_config.json
notepad oauth_setup_config.json
```

Edit:
- `email` — your Google account (e.g. `skyblaick@gmail.com`)
- `test_users` — OAuth test users list
- `num_projects` — default `9`
- `redirect_uri` — `https://mekesh.work.gd:8001/api/youtube/callback/`

### 3. Start Brave with CDP (keeps your login)

Close Brave completely, then:

```powershell
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data"
```

Sign in to https://console.cloud.google.com if needed.

Verify:

```powershell
curl http://127.0.0.1:9222/json/version
```

### 4. Run

```powershell
python scripts/gcp_oauth_setup.py --config oauth_setup_config.json
```

Outputs land in `oauth_output/`:
- `client_secrets1.json` … `client_secrets9.json`
- `setup_results.json`
- `screenshots/` on errors

## Options

```powershell
# Use existing projects (skip creation)
python scripts/gcp_oauth_setup.py --projects wishfox-clip-pool-c,wishfox-yt-pool-5

# Override CDP URL
python scripts/gcp_oauth_setup.py --cdp http://127.0.0.1:9222

# Preview plan
python scripts/gcp_oauth_setup.py --dry-run
```

## Deploy secrets to server

```powershell
scp oauth_output/client_secrets*.json root@98.142.250.176:/root/tiktok-cliper/
ssh root@98.142.250.176 "cd /root/tiktok-cliper && source venv/bin/activate && for f in client_secrets*.json; do python3 scripts/add_oauth_projects.py \$f; done && pkill -HUP -f gunicorn"
```

## Notes

- **Use CDP mode** (`cdp_url` in config) so Playwright attaches to your real browser session — no password storage.
- Google Console UI changes often; if a step fails, check `oauth_output/screenshots/`.
- Some Console versions hide JSON download — the script falls back to reading client id/secret from the create dialog.
- Creating 9 projects may hit GCP project quotas on free accounts.
