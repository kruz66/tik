# Switch OAuth consent: Production → Testing (all 9 projects)

Open each link while signed into Google Cloud Console, then click **Back to testing** on the OAuth consent screen.

| # | Project ID | Direct link |
|---|------------|-------------|
| 1 | wishfox-clip-pool-c | https://console.cloud.google.com/apis/credentials/consent?project=wishfox-clip-pool-c |
| 2 | wishfox-yt-extra-1-cd35 | https://console.cloud.google.com/apis/credentials/consent?project=wishfox-yt-extra-1-cd35 |
| 3 | wishfox-clip-pool-h | https://console.cloud.google.com/apis/credentials/consent?project=wishfox-clip-pool-h |
| 4 | key-journal-505020-k7 | https://console.cloud.google.com/apis/credentials/consent?project=key-journal-505020-k7 |
| 5 | wishfox-yt-pool-5 | https://console.cloud.google.com/apis/credentials/consent?project=wishfox-yt-pool-5 |
| 6 | youtubelink-500704 | https://console.cloud.google.com/apis/credentials/consent?project=youtubelink-500704 |
| 7 | copper-site-500801-i2 | https://console.cloud.google.com/apis/credentials/consent?project=copper-site-500801-i2 |
| 8 | my-project-3-500802 | https://console.cloud.google.com/apis/credentials/consent?project=my-project-3-500802 |
| 9 | my-project-4-500802 | https://console.cloud.google.com/apis/credentials/consent?project=my-project-4-500802 |

## Steps per project

1. Open link above
2. Find **Publishing status**
3. Click **Back to testing** (or **Revert to testing**)
4. Confirm
5. Verify status shows **Testing**

## Test users (keep these while in Testing)

- badmandog66@gmail.com
- davidmargret458@gmail.com
- skyblaick@gmail.com

## Automated (after Chrome sign-in)

```bash
python3 scripts/revert_oauth_to_testing.py
```

Requires Chrome with CDP on port 9222 and an active Google Cloud Console login.
