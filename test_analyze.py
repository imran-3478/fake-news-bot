"""
test_analyze.py — local smoke tests for the fake-news bot backend.

Boots the FastAPI app with TestClient. All tokens are cleared first, so the
app runs in DRY-RUN mode: no real Graph API calls, no Gemini calls.

Run:  .venv/bin/python test_analyze.py
"""

import os
import sys

# Dry-run: make sure no real credentials leak into the tests.
for var in ("PAGE_ACCESS_TOKEN", "VERIFY_TOKEN", "GEMINI_API_KEY"):
    os.environ.pop(var, None)

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402

client = TestClient(app)

FAKE_TEXT = "😱😱 চমকে যাবেন!! এক গ্লাস গরম পানি খেলেই ক্যান্সার ভালো হয়ে যায়! বড় বড় ডাক্তাররা এই গোপন সত্য এতদিন লুকিয়ে রেখেছিল! সবাইকে শেয়ার করে জানিয়ে দিন, দেরি করবেন না!!! এই খবরটা যত পারেন ছড়িয়ে দিন 🙏🙏"
REAL_TEXT = "আজ মঙ্গলবার সকালে ঢাকা বিশ্ববিদ্যালয়ে নতুন শিক্ষাবর্ষের ক্লাস শুরু হয়েছে বলে জানিয়েছেন বিশ্ববিদ্যালয়ের জনসংযোগ কর্মকর্তা। তিনি বলেন, এ বছর মোট সাত হাজার শিক্ষার্থী ভর্তি হয়েছেন। উপাচার্য বলেন, শিক্ষার মান বাড়াতে গবেষণা খাতে বরাদ্দ বাড়ানো হয়েছে।"
BLOG_LINK = "https://viral-khobor24.blogspot.com/2026/10/breaking-gopon-khobor.html"
PROTHOMALO_TEXT = "আবহাওয়া অফিসের কর্মকর্তা জানিয়েছেন, আজ ঢাকায় বৃষ্টি হতে পারে। বিস্তারিত পড়ুন https://www.prothomalo.com/bangladesh/environment/abc123"

failures = []


def check(name, cond, detail=""):
    print(("  ✅" if cond else "  ❌ FAIL") + f" {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def show(label, r):
    print(f"{label}: score={r.get('score')} | {r.get('verdict')} | mode={r.get('mode')}")


def main():
    print("GET /health")
    r = client.get("/health")
    check("health ok", r.status_code == 200 and r.json().get("ok") is True)

    print("\n(a) fake miracle-cure text")
    r = client.post("/analyze", json={"type": "text", "text": FAKE_TEXT}).json()
    show("(a)", r)
    check("(a) score >= 55", (r.get("score") or 0) >= 55, f"score={r.get('score')}")
    check("(a) red verdict", r.get("verdict", "").startswith("🔴"))
    check("(a) mode text", r.get("mode") == "text")

    print("\n(b) real-style text")
    r = client.post("/analyze", json={"type": "text", "text": REAL_TEXT}).json()
    show("(b)", r)
    check("(b) score < 30", (r["score"] if r.get("score") is not None else 99) < 30, f"score={r.get('score')}")
    check("(b) green verdict", r.get("verdict", "").startswith("🟢"))

    print("\n(c) dead blogspot link (partial expected)")
    r = client.post("/analyze", json={"type": "link", "url": BLOG_LINK}).json()
    show("(c)", r)
    check("(c) mode partial", r.get("mode") == "partial", f"mode={r.get('mode')}")
    check("(c) score >= 30", (r.get("score") or 0) >= 30, f"score={r.get('score')}")

    print("\n(d) text containing a prothomalo.com link")
    r = client.post("/analyze", json={"type": "text", "text": PROTHOMALO_TEXT}).json()
    show("(d)", r)
    check("(d) score < 30", (r["score"] if r.get("score") is not None else 99) < 30, f"score={r.get('score')}")
    check("(d) green verdict", r.get("verdict", "").startswith("🟢"))
    check("(d) domain detected", r.get("domain") == "prothomalo.com", f"domain={r.get('domain')}")

    print("\n(e) image without GEMINI_API_KEY (graceful note expected)")
    r = client.post("/analyze", json={"type": "image", "image_url": "https://example.com/fake.jpg"}).json()
    show("(e)", r)
    joined = " ".join(r.get("reasons") or [])
    check("(e) mentions GEMINI_API_KEY", "GEMINI_API_KEY" in joined)
    check("(e) mode image", r.get("mode") == "image")
    check("(e) ai_vision is None", (r.get("sub_scores") or {}).get("ai_vision") is None)

    print("\nwebhook verify handshake")
    os.environ["VERIFY_TOKEN"] = "test-verify-123"
    r = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-123", "hub.challenge": "CH42"},
    )
    check("verify ok", r.status_code == 200 and r.text == "CH42", f"{r.status_code} {r.text}")
    r = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "CH42"},
    )
    check("verify wrong token → 403", r.status_code == 403)
    os.environ.pop("VERIFY_TOKEN", None)

    print("\nwebhook text message (dry-run, background reply)")
    payload = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "TEST_USER_1"},
                        "message": {"mid": "m1", "text": FAKE_TEXT},
                    }
                ]
            }
        ],
    }
    r = client.post("/webhook", json=payload)
    check("webhook 200", r.status_code == 200 and r.json().get("status") == "EVENT_RECEIVED")

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) FAILED: {failures}")
        sys.exit(1)
    print("✅ All checks passed.")


if __name__ == "__main__":
    main()
