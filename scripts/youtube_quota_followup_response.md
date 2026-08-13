# YouTube API Quota Follow-up Response

Use this to reply to the YouTube API Services Team email within 7 business days.

---

## Email reply (copy/paste)

**Subject:** Re: YouTube API Services – Audit and Quota Extension – WishFox (follow-up materials)

Dear YouTube API Services Team,

Thank you for reviewing our quota extension request. Please find the requested information below.

### 1. Justification for multiple Google Cloud project numbers

WishFox is a creator workflow application that lets authenticated users schedule and publish video clips to **their own** YouTube channels via OAuth 2.0. We operate multiple GCP projects as a **quota and reliability pool**, not to circumvent YouTube policies.

**Why multiple projects (same API client, one product):**

- **Fault isolation:** If one GCP project hits its daily YouTube Data API unit limit, uploads continue via other linked projects without stopping the user’s queue.
- **Capacity at scale:** Each `videos.insert` call costs approximately 1,600 quota units. Our target is up to ~800 uploads per day across active creator workspaces. Default 10,000 units per project supports only ~6 uploads per day per project.
- **Operational separation:** OAuth client credentials are registered per GCP project per Google’s console model. Each project has its own OAuth client ID; users authorize the same WishFox app once per project during pool linking so tokens remain valid for that client.
- **No duplicate or abusive use:** All projects serve the **same** WishFox web application (https://mekesh.work.gd:8001/). We do not scrape, republish third-party content without user action, or upload without the channel owner’s OAuth grant.

**Project numbers and roles (API client: WishFox):**

| Project number | GCP project ID | Role in pool |
|----------------|----------------|--------------|
| 763909667755 | wishfox-clip-pool-c | Primary pool member – scheduled uploads |
| 103758406291 | wishfox-yt-extra-1-cd35 | Pool member – upload failover |
| 901293676068 | wishfox-clip-pool-h | Pool member – upload failover |
| 323342604529 | key-journal-505020-k7 | Pool member – upload failover |
| 716793063845 | wishfox-yt-pool-5 | Pool member – upload failover |
| 482084067432 | youtubelink-500704 | Pool member – upload failover |
| 604908311987 | copper-site-500801-i2 | Pool member – upload failover |
| 127631382630 | my-project-3-500802 | Pool member – upload failover |
| 663390508036 | my-project-4-500802 | Pool member – upload failover |

We request increased **Queries per day** quota on each project so the pool can support legitimate creator scheduling volume while staying within YouTube API Services Terms of Service.

### 2. Screen recording

We have attached / linked a screen recording that shows:

1. Logging into WishFox at https://mekesh.work.gd:8001/
2. Linking a YouTube channel via Google OAuth (consent screen and permissions)
3. Queueing or uploading a clip through the dashboard
4. The upload completing on YouTube (or the in-app success state)

**Demo video URL:** [INSERT LINK AFTER RECORDING – e.g. unlisted YouTube or Google Drive link]

(We also previously submitted: https://youtu.be/sQGnuckG128 for OAuth verification.)

### 3. Demo credentials for compliance inspection

Please use these credentials **only** for compliance review:

| Field | Value |
|-------|-------|
| Login URL | https://mekesh.work.gd:8001/ |
| Username | wishfox |
| Password | wishfox2626 |
| Notes | After login, use **Link YouTube** to connect a test channel. Dashboard shows upload queue, scheduling, and channel status. Privacy: https://mekesh.work.gd:8001/privacy/ Terms: https://mekesh.work.gd:8001/terms/ |

If your team needs a dedicated review account or temporary password change, contact us at davidmargret458@gmail.com.

Thank you for your time. We are happy to provide any additional detail.

Best regards,  
David Margret  
WishFox  
https://mekesh.work.gd:8001/  
davidmargret458@gmail.com

---

## Checklist before sending

- [ ] Attach or link screen recording (OAuth + upload full flow)
- [ ] Confirm demo login works (wishfox / wishfox2626)
- [ ] Reply within 7 business days from email date
- [ ] Keep tone factual; do not claim 800/day until quota is approved
