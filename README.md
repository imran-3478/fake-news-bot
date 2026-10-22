# সত্যি নাকি গুজব? — Messenger Bot Backend 🔍

The backend "brain" of the Bangla Fake News Checker for **Messenger**.
The web version lives at https://imran-3478.github.io/fake-news-checker/ —
this bot uses the *same* signal engine (ported faithfully from the web
app's `index.html`), plus server-side powers a static page cannot have:

- 📝 **Text** — paste a news post → signal analysis (clickbait, source, style)
- 🔗 **Link** — the server fetches the real article (r.jina.ai → allorigins)
  and analyzes the headline + body, not just the URL
- 🖼️ **Photo / screenshot** — Gemini vision reads the image (OCR), extracts
  the claim, and flags editing / AI-generation / out-of-context signs
- 🎬 **Video** — Gemini transcribes and checks claims (≤ ~18MB; larger →
  the bot asks for a screenshot instead)

Every reply ends with: `— 'সত্যি নাকি গুজব?' তৈরি করেছেন Imran Akanda`

> Honest limit: Facebook post links themselves cannot be fetched anonymously
> (Meta login wall — verified). That is exactly why the bot lives *inside*
> Messenger: users forward the text, photo or video instead of a link.

## Files

| File | What it does |
|---|---|
| `app.py` | FastAPI app: `/webhook` (Meta verify + events), `/analyze`, `/health` |
| `analyzer.py` | Signal engine (web-app port) + link fetch + Gemini image/video |
| `formatter.py` | Analysis → Bengali Messenger reply text |
| `test_analyze.py` | Local smoke tests (dry-run, no real API calls) |

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Needed for |
|---|---|
| `PAGE_ACCESS_TOKEN` | Sending Messenger replies. **Empty = dry-run mode** (replies are logged, nothing is sent — safe for local testing) |
| `VERIFY_TOKEN` | Webhook verification — any secret string, paste the same in the Meta dashboard |
| `GEMINI_API_KEY` | Photo/screenshot/video AI analysis (Google AI Studio key). Empty = text + link checking only |
| `GEMINI_MODEL` | Default `gemini-3.5-flash` |

No secrets are stored in code — everything comes from the environment.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in what you have; empty is fine (dry-run)
.venv/bin/uvicorn app:app --env-file .env --port 8000
```

Test it:

```bash
.venv/bin/python test_analyze.py

curl -X POST localhost:8000/analyze -H 'Content-Type: application/json' \
  -d '{"type":"text","text":"চমকে যাবেন!! এক গ্লাস গরম পানি খেলেই ক্যান্সার ভালো হয়ে যায়!"}'
```

## Deploy on Render

1. Push this folder to a GitHub repo (keep `.env` out — it is gitignored).
2. Render → **New → Web Service** → connect the repo.
3. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Add the four environment variables above in the Render dashboard.
5. Health check path: `/health`. Your webhook URL will be
   `https://<your-service>.onrender.com/webhook`.

## Meta app + Page setup (short)

1. Create a **Facebook Page** from your main account, e.g. "সত্যি নাকি গুজব?"
   (2 minutes — this Page is what users will chat with).
2. Go to **developers.facebook.com** → Create App → type **Business** →
   add the **Messenger** product.
3. In Messenger settings: connect the Page → generate a **Page Access
   Token** → put it in `PAGE_ACCESS_TOKEN`.
4. In Webhooks: Callback URL = `https://<your-service>.onrender.com/webhook`,
   Verify Token = your `VERIFY_TOKEN`. Subscribe to the **`messages`** and
   **`messaging_postbacks`** fields, and subscribe the Page to the app.
5. Message the Page — text, a link, a photo/screenshot, or a short video.
6. Before the public can use it, Meta **App Review** for `pages_messaging`
   is required (a few days); admins/developers/testers of the app can use
   it immediately in development mode.

## Notes

- The scoring is a **signal-based first impression** (same disclaimer as the
  web app) — not a final fact-check verdict. Replies always point users to
  Rumor Scanner / Dismislab for confirmation.
- Gemini free quota is limited (~20 requests/day on a free AI Studio key);
  photo/video checking will pause when the quota runs out — text/link
  checking keeps working.
