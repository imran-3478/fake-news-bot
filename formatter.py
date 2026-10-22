"""
formatter.py — turns an analyzer result into the Bengali Messenger reply.

Every analysis reply carries Imran's branding footer (same as the web app's
share text), so each verdict shared in chats advertises the project.
"""

WEB_APP_URL = "https://imran-3478.github.io/fake-news-checker/"
FOOTER = "— 'সত্যি নাকি গুজব?' তৈরি করেছেন Imran Akanda"

HELP_TEXT = """আসসালামু আলাইকুম! 🔍 আমি 'সত্যি নাকি গুজব?' — শেয়ার করার আগেই খবর যাচাই করে দিই।

যাচাই করতে চাইলে যেকোনো একটা পাঠাও:
📝 খবরের লেখাটা copy করে paste করো
🔗 খবরের link-টা পাঠাও
🖼️ Post-এর ছবি বা screenshot পাঠাও
🎬 ভিডিও পাঠাও (ছোট হলে ভালো হয়)

ফলাফল এই chat-এই পেয়ে যাবে — ভুয়া হওয়ার ঝুঁকি কত %, আর কেন।

— 'সত্যি নাকি গুজব?' তৈরি করেছেন Imran Akanda
🔗 """ + WEB_APP_URL


def format_analysis(a: dict) -> str:
    """Analysis dict → Messenger reply text (Bengali)."""
    lines = []
    lines.append(f"{a['verdict']} (ঝুঁকি {a['score']}%)")
    if a.get("advice"):
        lines.append(a["advice"])

    sub = a.get("sub_scores") or {}
    if sub.get("ai_vision") is not None and sub.get("signal_engine") is not None:
        lines.append(
            f"📊 বিস্তারিত score — লেখার সংকেত: {sub['signal_engine']}%, AI ছবি/ভিডিও বিশ্লেষণ: {sub['ai_vision']}%"
        )

    reasons = (a.get("reasons") or [])[:5]
    if reasons:
        lines.append("")
        lines.append("কেন এই ফলাফল:")
        for r in reasons:
            lines.append(f"• {r}")

    if a.get("mode") == "full" and a.get("title"):
        lines.append("")
        lines.append(f"📰 শিরোনাম: {a['title']}")

    lines.append("")
    lines.append("নিজে আরও যাচাই: Rumor Scanner / Dismislab")
    lines.append(FOOTER)
    lines.append(f"🔗 {WEB_APP_URL}")
    return "\n".join(lines)
