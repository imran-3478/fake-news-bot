"""
app.py — FastAPI backend for the 'সত্যি নাকি গুজব?' Messenger bot.

Endpoints:
  GET  /health    → {"ok": true}
  GET  /webhook   → Meta webhook verification handshake (VERIFY_TOKEN)
  POST /webhook   → Messenger events: text / image / video → analysis → reply
  POST /analyze   → JSON test endpoint used by local tests and debugging

Environment variables (see .env.example):
  PAGE_ACCESS_TOKEN  Meta Page access token. If unset → DRY-RUN mode:
                     replies are logged, never sent (safe for local tests).
  VERIFY_TOKEN       any string you also paste into the Meta app dashboard.
  GEMINI_API_KEY     enables image/video AI analysis.
  GEMINI_MODEL       default gemini-3.5-flash.

Run locally:  uvicorn app:app --env-file .env --port 8000
"""

import logging
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import analyzer
import formatter

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fake-news-bot")

GRAPH_API = "https://graph.facebook.com/v21.0/me/messages"

GREETINGS = {
    "hi", "hii", "hello", "hey", "hlw", "start", "get started",
    "সালাম", "আসসালামু আলাইকুম", "আসসালামুআলাইকুম", "শুরু", "help", "সাহায্য",
}

app = FastAPI(title="সত্যি নাকি গুজব? — Messenger bot")


# ---------------------------------------------------------------------------
# Sending replies (Graph API, or dry-run logging when no token)
# ---------------------------------------------------------------------------

async def send_message(psid: str, text: str) -> None:
    token = os.environ.get("PAGE_ACCESS_TOKEN", "").strip()
    if not token:
        log.info("[DRY-RUN] would send to %s:\n%s", psid, text)
        return
    # Messenger text limit is 2000 chars — split long verdicts safely.
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]
    async with analyzer.make_http_client() as client:
        for chunk in chunks:
            try:
                r = await client.post(
                    GRAPH_API,
                    params={"access_token": token},
                    json={
                        "recipient": {"id": psid},
                        "messaging_type": "RESPONSE",
                        "message": {"text": chunk},
                    },
                    timeout=30,
                )
                if not r.is_success:
                    log.error("Graph API send failed: %s %s", r.status_code, r.text[:300])
            except Exception as e:
                log.error("Graph API send error: %s", e)


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

async def handle_text(psid: str, text: str) -> None:
    text = (text or "").strip()
    if not text or text.lower() in GREETINGS:
        await send_message(psid, formatter.HELP_TEXT)
        return
    url = analyzer.find_url(text)
    rest = text.replace(url, "").strip() if url else text
    if url and len(rest) < 40:
        # The message is (almost) just a link → full fetch pipeline,
        # exactly like the web app behaves.
        result = await analyzer.analyze_link(url)
    else:
        result = analyzer.analyze_text(text)
    await send_message(psid, formatter.format_analysis(result))


async def handle_attachment(psid: str, att: dict) -> None:
    att_type = att.get("type")
    payload = att.get("payload") or {}
    url = payload.get("url")
    if not url:
        return
    if att_type == "image":
        result = await analyzer.analyze_image(url)
        await send_message(psid, formatter.format_analysis(result))
    elif att_type == "video":
        result = await analyzer.analyze_video(url)
        if result.get("error"):
            await send_message(psid, result["message"])
        else:
            await send_message(psid, formatter.format_analysis(result))
    else:
        await send_message(
            psid,
            "এই ধরনের file আমি এখনো যাচাই করতে পারি না। খবরের লেখা, link, ছবি/screenshot অথবা ভিডিও পাঠাও।",
        )


async def process_event(event: dict) -> None:
    sender = (event.get("sender") or {}).get("id")
    if not sender:
        return
    # GET_STARTED / menu postbacks → help text
    if "postback" in event:
        await send_message(sender, formatter.HELP_TEXT)
        return
    message = event.get("message")
    if not message:
        return  # delivery / read receipts etc. — ignore
    if message.get("is_echo"):
        return  # our own message echoed back — ignore
    attachments = message.get("attachments") or []
    if attachments:
        for att in attachments:
            await handle_attachment(sender, att)
        return
    if "text" in message:
        await handle_text(sender, message.get("text", ""))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/webhook")
async def webhook_verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")
    expected = os.environ.get("VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def webhook_receive(request: Request, background: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored"}, status_code=200)
    if body.get("object") != "page":
        return JSONResponse({"status": "ignored"}, status_code=200)
    # Return 200 immediately (Meta retries slow webhooks); analyze in background.
    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            background.add_task(process_event, event)
    return JSONResponse({"status": "EVENT_RECEIVED"}, status_code=200)


@app.post("/analyze")
async def analyze_endpoint(payload: dict):
    """Test/debug endpoint. Body examples:
    {"type": "text",  "text": "..."}
    {"type": "link",  "url": "https://..."}
    {"type": "image", "image_url": "https://...", "text": "optional caption"}
    {"type": "video", "video_url": "https://...", "text": "optional caption"}
    """
    kind = (payload.get("type") or "text").lower()
    if kind == "link":
        return await analyzer.analyze_link(payload.get("url", ""))
    if kind == "image":
        return await analyzer.analyze_image(
            payload.get("image_url", ""), caption=payload.get("text", "")
        )
    if kind == "video":
        return await analyzer.analyze_video(
            payload.get("video_url", ""), caption=payload.get("text", "")
        )
    # default: text — but a bare link routes to the link pipeline, like chat
    text = payload.get("text", "")
    url = analyzer.find_url(text)
    rest = text.replace(url, "").strip() if url else text
    if url and len(rest) < 40:
        return await analyzer.analyze_link(url)
    return analyzer.analyze_text(text)
