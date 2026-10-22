"""
analyzer.py — 'সত্যি নাকি গুজব?' analysis pipeline.

The signal engine here is a faithful Python port of the JavaScript engine in
the web app (~/workspace/your_files/fake-news-checker/index.html): same word
lists, same scoring, same verdict thresholds (🔴 >= 55, 🟡 30-54, 🟢 < 30)
and the same Bengali reason/verdict strings.

On top of the engine this module adds the server-side extras a Messenger bot
can do but a static web page cannot:
  * fetch the article behind a link (r.jina.ai, then allorigins fallback)
  * image analysis with Gemini vision (OCR + claim + fake signs + ai_risk)
  * video analysis with Gemini Files API (transcript + claims + ai_risk)

All credentials come from environment variables. Nothing is hardcoded.
"""

import base64
import json
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

import httpx

# ---------------------------------------------------------------------------
# Word lists — copied 1:1 from the web app's index.html (do not "improve"
# them here; the web app and the bot must agree).
# ---------------------------------------------------------------------------

CLICKBAIT = ["চমকে", "বিশ্বাস হবে না", "অবিশ্বাস্য", "গোপন সত্য", "গোপন রহস্য", "ফাঁস হলো", "চাঞ্চল্যকর", "তোলপাড়", "কেউ জানে না", "লুকিয়ে রেখেছিল", "ভাইরাল", "অবাক কাণ্ড", "শেষ পর্যন্ত দেখুন", "দেখুন ভিডিওতে", "জানলে অবাক", "হইচই", "মাথা ঘুরে যাবে", "shocking", "you won't believe", "unbelievable", "secret truth", "exposed", "must watch", "watch till end", "breaking news", "viral"]
SHARE_PRESSURE = ["শেয়ার করুন", "শেয়ার করো", "শেয়ার করে জানিয়ে", "সবাইকে জানিয়ে দিন", "সবাইকে জানান", "ছড়িয়ে দিন", "please share", "share this", "share করে", "forward করুন", "দেরি না করে শেয়ার"]
MONEY = ["ফ্রি পাবেন", "ফ্রিতে পাবেন", "টাকা ইনকাম", "ইনকাম করুন", "লটারি", "জিতে নিন", "মোবাইল রিচার্জ ফ্রি", "বিকাশে টাকা পাবেন", "free money", "lottery", "win cash", "cash prize"]
MIRACLE = ["ক্যান্সার ভালো", "একদিনেই ভালো", "গ্যারান্টি দিয়ে", "১০০% কার্যকর", "100% কার্যকর", "সব রোগ ভালো", "ডায়াবেটিস ভালো"]
ATTRIBUTION = ["জানিয়েছেন", "জানান", "বলেন", "বলেছেন", "প্রতিবেদক", "সংবাদদাতা", "সূত্র", "কর্মকর্তা", "মুখপাত্র", "সংবাদ সম্মেলনে"]
CREDIBLE_DOMAINS = ["prothomalo.com", "kalerkantho.com", "bdnews24.com", "thedailystar.net", "dhakatribune.com", "jugantor.com", "ittefaq.com.bd", "somoynews.tv", "jamuna.tv", "channel24bd.tv", "independent24.com", "banglanews24.com", "ntvbd.com", "rtvonline.com", "ekattor.tv", "samakal.com", "manabzamin.com", "dailyjanakantha.com", "banglatribune.com", "jagonews24.com", "risingbd.com", "unb.com.bd", "bssnews.net", "tbsnews.net", "newagebd.net", "daily-sun.com", "dbcnews.tv", "ekushey-tv.com", "bbc.com", "reuters.com", "apnews.com", "aljazeera.com", "dw.com", "theguardian.com", "rumorscanner.com", "dismislab.com", "factwatch.info", "boomlive.in", "snopes.com"]
CREDIBLE_NAMES = ["প্রথম আলো", "কালের কণ্ঠ", "বিবিসি", "যুগান্তর", "ইত্তেফাক", "সময় টিভি", "ডেইলি স্টার", "বিডিনিউজ", "Rumor Scanner", "Dismislab"]
SATIRE = ["motikontho.com", "earki"]
FREEHOST = ["blogspot.com", "wordpress.com", "weebly.com", "sites.google.com", "wixsite.com", "vercel.app", "netlify.app", "github.io"]
SHORTENERS = ["bit.ly", "tinyurl.com", "rb.gy", "cutt.ly", "is.gd", "shorturl.at", "tiny.cc", "s.id", "ow.ly", "rebrand.ly"]
SUS_TLDS = [".xyz", ".top", ".click", ".buzz", ".icu", ".monster", ".fun", ".work", ".online", ".site", ".club", ".vip"]
BRAND_KEYS = ["prothomalo", "kalerkantho", "bdnews", "dailystar", "dhakatribune", "jugantor", "ittefaq", "somoynews", "jamuna", "channel24", "banglanews", "bbc", "reuters", "aljazeera"]
SOCIAL_DOMAINS = ["facebook.com", "fb.com", "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "x.com", "twitter.com"]
FB_DOMAINS = ["facebook.com", "fb.com", "fb.watch"]
SLUG_BAIT = ["gopon", "viral", "breaking", "shocking", "secret", "chomok", "chomke", "exposed", "unbelievable", "gujob", "scandal"]
DATE_HINT = re.compile(r"(জানুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|জুন|জুলাই|আগস্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর|২০[০-৯]{2}|20[0-9]{2}|প্রকাশিত|আপডেট|published|updated)")

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(
    r"\b([a-z0-9-]+\.(?:com|net|org|tv|bd|info|xyz|top|online|site|io|me|watch|ly|gy|cc|at|id)(?:\.[a-z]{2})?(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)

FETCH_TIMEOUT = 14.0  # seconds, mirrors the web app
MAX_VIDEO_BYTES = 18 * 1024 * 1024  # Gemini Files API comfort limit for this bot


def make_http_client(**kwargs):
    """httpx.AsyncClient that works both behind a TLS-intercepting HTTP
    proxy (local VM — the default env-based setup handles its CA via the
    system store) and on Render (no proxy env vars there).

    One quirk handled here: httpx builds proxy mounts from the environment
    at construction time and crashes (InvalidURL) on unusual NO_PROXY
    entries such as bare IPv6 hosts. If the default construction fails,
    retry once with NO_PROXY temporarily removed — this bot only fetches
    public URLs, so bypass rules are irrelevant. On Render the first try
    succeeds and the environment is never touched.
    """
    try:
        return httpx.AsyncClient(**kwargs)
    except Exception:
        saved = {}
        for k in ("NO_PROXY", "no_proxy"):
            if k in os.environ:
                saved[k] = os.environ.pop(k)
        try:
            return httpx.AsyncClient(**kwargs)
        finally:
            os.environ.update(saved)


# ---------------------------------------------------------------------------
# Small helpers (ports of the JS helpers)
# ---------------------------------------------------------------------------

def find_url(text: str):
    """Port of JS findUrl(). Returns the first URL in text, or None."""
    if not text:
        return None
    m = URL_RE.search(text)
    if m:
        return re.sub(r"[),.;]+$", "", m.group(0))
    m = BARE_DOMAIN_RE.search(text)
    if m:
        return "https://" + m.group(1)
    return None


def domain_of(url: str):
    """Port of JS domainOf(). Lowercase hostname without a leading www."""
    if not url:
        return None
    host = None
    try:
        host = urlparse(url).hostname
    except Exception:
        host = None
    if not host:
        m = re.search(r"//([^\s/]+)", str(url))
        host = m.group(1) if m else None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def is_credible_domain(d: str) -> bool:
    return any(d == c or d.endswith("." + c) for c in CREDIBLE_DOMAINS)


def lookalike_brand(d: str):
    """Return the brand key if the domain impersonates a known news brand."""
    if is_credible_domain(d):
        return None
    flat = re.sub(r"[^a-z]", "", d)
    for b in BRAND_KEYS:
        if b in flat:
            return b
    return None


def count_hits(words, lower_text):
    return [w for w in words if w.lower() in lower_text]


def verdict_block(score: int, mode: str):
    """The exact verdict/advice/color strings from the web app."""
    if score >= 55:
        return (
            "🔴 সাবধান! ভুয়া হওয়ার জোরালো লক্ষণ",
            "এই খবরটা শেয়ার না করাই ভালো। আগে Rumor Scanner / Dismislab-এ মিলিয়ে দেখো।",
            "#dc2626",
        )
    if score >= 30:
        return (
            "🟡 সন্দেহজনক — নিশ্চিত হওয়া যাচ্ছে না",
            "শেয়ার করার আগে অন্য নির্ভরযোগ্য মাধ্যমে খবরটা আছে কিনা যাচাই করো।",
            "#d97706",
        )
    if mode == "partial":
        advice = "বড় কোনো সতর্ক সংকেত পাইনি — তবে এটা শুধু link দেখে বলা। পুরোপুরি নিশ্চিত হতে খবরের লেখাটা copy করে paste করে আবার যাচাই করো।"
    else:
        advice = "ভুয়া হওয়ার বড় লক্ষণ পাইনি। তবু খুব বড় দাবি হলে সূত্রটা একবার মিলিয়ে দেখো।"
    return ("🟢 আপাতত নির্ভরযোগ্য মনে হচ্ছে", advice, "#16a34a")


# ---------------------------------------------------------------------------
# The signal engine — faithful port of the web app's analyze()
# ---------------------------------------------------------------------------

def analyze_engine(raw: str, mode: str = "text", url=None, title: str = "", body: str = "") -> dict:
    if url is None:
        url = find_url(raw or "")
    domain = domain_of(url) if url else None
    text = (title + " " + (body if body else (raw or ""))).strip()
    lower = text.lower()
    score = 0
    reasons = []
    credible_hit = False

    if mode == "full":
        reasons.append("🔬 Link-এর ভেতরের আসল খবরটা পড়ে বিশ্লেষণ করা হয়েছে — শুধু link-এর চেহারা নয়, শিরোনাম আর মূল লেখা মিলিয়ে।")
    if mode == "partial":
        reasons.append("📎 Link-এর ভেতরের লেখা সরাসরি পড়া যায়নি — তাই শুধু link-এর বৈশিষ্ট্য দেখে প্রাথমিক ধারণা দেওয়া হলো। নিখুঁত ফলাফলের জন্য খবরের লেখাটা copy করে paste করো।")

    if domain:
        fake_brand = lookalike_brand(domain)
        if any(d in domain for d in SATIRE):
            score += 70
            reasons.append("🎭 এই site-টা ব্যঙ্গ/স্যাটায়ার (মজা করে বানানো) — এখানকার 'খবর' আসল নয়।")
        elif fake_brand:
            score += 45
            reasons.append(f"🚨 সাবধান! Domain-টা দেখতে নামকরা সংবাদ মাধ্যমের মতো হলেও এটা আসল site নয় ({domain}) — নকল site বানিয়ে ভুয়া খবর ছড়ানো পরিচিত প্রতারণা।")
            if any(domain.endswith(t) for t in SUS_TLDS):
                score += 12
                reasons.append("⚠️ তার ওপর domain-এর শেষ অংশটাও অস্বাভাবিক — আসল সংবাদ মাধ্যম এমন ending ব্যবহার করে না।")
        elif is_credible_domain(domain):
            score -= 35
            credible_hit = True
            reasons.append(f"✅ খবরটা পরিচিত ও নির্ভরযোগ্য সংবাদ মাধ্যমের website থেকে এসেছে ({domain})।")
        elif any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS):
            score += 4
            reasons.append("📱 এটা social media-র একটা post/video link — সেখানে যে কেউ যা খুশি পোস্ট করতে পারে, তাই post-এর লেখা দিয়েই যাচাই করা ভালো।")
        elif any(domain == d for d in SHORTENERS):
            score += 16
            reasons.append("🔗 এটা ছোট করা (short) link — আসল website-এর নাম লুকানো থাকে। ভুয়া খবর ছড়াতে এই কৌশল বেশি ব্যবহার হয়।")
        elif any(d in domain for d in FREEHOST):
            score += 16
            reasons.append("⚠️ Link-টা একটা ফ্রি ব্লগ/site থেকে — এমন site যে কেউ মিনিটে বানাতে পারে, সম্পাদকীয় যাচাই থাকে না।")
        else:
            score += 8
            reasons.append(f"❓ Website-টা ({domain}) খুব একটা পরিচিত নয় — source হিসেবে সতর্ক থাকা ভালো।")
            if any(domain.endswith(t) for t in SUS_TLDS):
                score += 12
                reasons.append("⚠️ Domain-এর শেষ অংশটা অস্বাভাবিক — নামকরা সংবাদ মাধ্যম সাধারণত এমন ending ব্যবহার করে না।")
            digits = len(re.findall(r"[0-9]", domain))
            hyphens = domain.count("-")
            if digits >= 2 or hyphens >= 2:
                score += 6
                reasons.append("🔢 Domain-এর নামে অনেক সংখ্যা/dash আছে — তাড়াহুড়ো করে বানানো অস্থায়ী site-এর লক্ষণ।")

        if url:
            try:
                path = urlparse(url).path.lower()
            except Exception:
                path = ""
            tokens = [t for t in re.split(r"[-_/\.]+", path) if t]
            slug_hits = [t for t in tokens if t in SLUG_BAIT]
            if len(slug_hits) >= 2:
                score += 8
                reasons.append(f"🏷️ Link-এর নামের মধ্যেই উত্তেজনাকর শব্দ ({', '.join(slug_hits[:3])}) — click টানার জন্য বানানো link-এর লক্ষণ।")

    if not domain and any(n in text for n in CREDIBLE_NAMES):
        score -= 20
        credible_hit = True
        reasons.append("✅ লেখায় নির্ভরযোগ্য সংবাদ মাধ্যমের নাম উল্লেখ আছে।")

    if title:
        t_hits = count_hits(CLICKBAIT, title.lower())
        if t_hits:
            score += min(len(t_hits) * 7, 24)
            reasons.append(f"📰 শিরোনামেই clickbait ভাষা ({', '.join(t_hits[:3])}) — পাঠক টানতে অতিরঞ্জিত শিরোনাম দেওয়া হয়েছে।")

    cb_found = count_hits(CLICKBAIT, lower)
    if cb_found:
        score += min(len(cb_found) * 6, 30)
        reasons.append(f"🎣 পুরো লেখায় clickbait/অতিরঞ্জিত ভাষা পাওয়া গেছে ({', '.join(cb_found[:3])}) — এমন ভাষা ভুয়া খবরের বড় লক্ষণ।")

    if any(w.lower() in lower for w in SHARE_PRESSURE):
        score += 14
        reasons.append("📢 লেখায় জোর করে শেয়ার করতে বলা হয়েছে — আসল খবর সাধারণত এভাবে শেয়ার চায় না।")
    if any(w.lower() in lower for w in MONEY):
        score += 14
        reasons.append("💸 ফ্রি টাকা/লটারি/ইনকামের লোভ দেখানো হয়েছে — এটা প্রতারণামূলক পোস্টের পরিচিত কৌশল।")
    if any(w.lower() in lower for w in MIRACLE):
        score += 14
        reasons.append("💊 অবিশ্বাস্য চিকিৎসা/ফলাফলের দাবি — প্রমাণ ছাড়া এমন বড় দাবি বিশ্বাসযোগ্য নয়।")

    if text.count("!") >= 3:
        score += 10
        reasons.append("‼️ অতিরিক্ত বিস্ময়সূচক চিহ্ন (!) — উত্তেজনা তৈরি করে ছড়ানোই উদ্দেশ্য মনে হচ্ছে।")

    latin_words = re.findall(r"[A-Za-z]{3,}", text)
    if len(latin_words) >= 4:
        caps = len([w for w in latin_words if w == w.upper()])
        if caps / len(latin_words) > 0.35:
            score += 8
            reasons.append("🔠 অনেক শব্দ বড় হাতের অক্ষরে (ALL CAPS) — চিৎকার করে বিশ্বাস করানোর চেষ্টা।")

    if len(EMOJI_RE.findall(text)) >= 4:
        score += 6
        reasons.append("😱 প্রচুর ইমোজি ব্যবহার করা হয়েছে — আবেগ দিয়ে ভাইরাল করার কৌশল।")

    has_attribution = any(w in text for w in ATTRIBUTION)
    if mode != "full" and not domain and 0 < len(text) < 70:
        score += 8
        reasons.append("✂️ লেখাটা খুবই ছোট — কোনো বিস্তারিত তথ্য বা সূত্র নেই।")
    if has_attribution:
        score -= 10
        reasons.append("🗣️ লেখায় বক্তব্য/সূত্রের উল্লেখ আছে — প্রতিবেদনের ধাঁচ, এটা ভালো লক্ষণ।")
    elif len(text) >= 70:
        score += 8
        reasons.append("🔎 এত বড় দাবি, অথচ কারো বক্তব্য বা সূত্রের উল্লেখ নেই।")

    if body and len(body) > 300:
        if DATE_HINT.search(body) or DATE_HINT.search(title):
            score -= 6
            reasons.append("📅 খবরে তারিখ/প্রকাশের তথ্যের উল্লেখ আছে — প্রতিবেদনের ভালো লক্ষণ।")
        else:
            score += 6
            reasons.append("📅 খবরটায় কোনো তারিখের উল্লেখ নেই — পুরনো খবর নতুন ঘটনা বলে চালানো গুজবের পরিচিত কৌশল।")

    score = max(0, min(100, score))
    verdict, advice, color = verdict_block(score, mode)

    if len(reasons) < 3 and not credible_hit:
        reasons.append("🧐 বড় কোনো সতর্ক সংকেত পাইনি, তবে source-টা নিজে একবার দেখে নেওয়া ভালো।")

    return {
        "score": score,
        "verdict": verdict,
        "advice": advice,
        "color": color,
        "reasons": reasons,
        "mode": mode,
        "title": title,
        "domain": domain,
        "sub_scores": {},
    }


def analyze_text(text: str) -> dict:
    """Plain pasted text (a URL inside the text is still used for signals)."""
    return analyze_engine(text or "", mode="text")


# ---------------------------------------------------------------------------
# Link fetching — port of the web app's fetchArticle()/parseJina()/parseHtml()
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    """Port of JS cleanText() for Jina reader markdown."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://[^\s)]+", " ", s)
    s = re.sub(r"[#>*`~|]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_jina(t: str):
    """Port of JS parseJina(). Returns (title, body)."""
    title = ""
    m = re.search(r"^Title:\s*(.+)$", t, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    body = t
    idx = t.find("Markdown Content:")
    if idx >= 0:
        body = t[idx + len("Markdown Content:"):]
    return title, clean_text(body)[:7000]


class _TextExtractor(HTMLParser):
    """Minimal HTML → text, skipping the same tags the web app removes."""

    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def parse_html(html: str):
    """Port of JS parseHtml(). Returns (title, body)."""
    title = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
    ex = _TextExtractor()
    try:
        ex.feed(html)
    except Exception:
        pass
    body = re.sub(r"\s+", " ", " ".join(ex.parts)).strip()
    return title, body[:7000]


# Reader/proxy services answer HTTP 200 even for dead pages (e.g. Blogspot's
# "Blog not found"), serving an error page long enough to pass a naive
# length check. Detect those so a dead link stays in 'partial' mode.
_ERROR_TITLE_MARKERS = (
    "blog not found", "page not found", "404 not found", "not found",
    "site not found", "account suspended", "just a moment",
    "attention required", "domain parked", "website unavailable",
)
_ERROR_BODY_MARKERS = (
    "the blog you were looking for does not exist",
    "the page you were looking for does not exist",
    "sorry, the blog you were looking for",
    "this blog has been removed",
    "the requested url was not found",
)


def _looks_like_error_page(title: str, body: str) -> bool:
    t = (title or "").lower()
    if any(m in t for m in _ERROR_TITLE_MARKERS):
        return True
    head = (body or "")[:400].lower()
    return any(m in head for m in _ERROR_BODY_MARKERS)


async def fetch_article(url: str):
    """Fetch the article behind a URL. Returns (title, body) or None.

    Route 1: r.jina.ai reader (clean markdown). Route 2: allorigins proxy
    (raw HTML → text). Mirrors the web app, server-side so no CORS limits.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SotyiNakiGujobBot/1.0)"}
    async with make_http_client(follow_redirects=True, headers=headers) as client:
        # Route 1: Jina reader
        try:
            r = await client.get("https://r.jina.ai/" + url, timeout=FETCH_TIMEOUT)
            if r.is_success:
                title, body = parse_jina(r.text)
                if body and len(body) > 200 and not _looks_like_error_page(title, body):
                    return title, body
        except Exception:
            pass
        # Route 2: allorigins
        try:
            r = await client.get(
                "https://api.allorigins.win/get?url=" + quote(url, safe=""),
                timeout=FETCH_TIMEOUT,
            )
            if r.is_success:
                data = r.json()
                # allorigins reports the origin status; a 404 page is not an article
                origin_status = (data.get("status") or {}).get("http_code")
                contents = data.get("contents") or ""
                if contents and (origin_status is None or int(origin_status) < 400):
                    title, body = parse_html(contents)
                    if body and len(body) > 200 and not _looks_like_error_page(title, body):
                        return title, body
        except Exception:
            pass
    return None


async def analyze_link(url: str) -> dict:
    """Full link pipeline: fetch → 'full' analysis, else URL-only 'partial'."""
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    domain = domain_of(url)
    art = await fetch_article(url) if url else None
    if art:
        title, body = art
        return analyze_engine(url, mode="full", url=url, title=title, body=body)
    result = analyze_engine(url, mode="partial", url=url)
    # Facebook walls off its posts (login required), so the fetch almost
    # always fails there — say exactly what would work instead.
    if domain and any(domain == d or domain.endswith("." + d) for d in FB_DOMAINS):
        result["reasons"].insert(
            1,
            "📘 Facebook post-এর ভেতরের লেখা সরাসরি পড়া যায় না (login লাগে)। সবচেয়ে ভালো ফল পেতে post-এর লেখাটা copy করে এখানে পাঠাও, অথবা post-এর screenshot পাঠাও — ছবি পড়েও যাচাই করা যাবে।",
        )
    return result


# ---------------------------------------------------------------------------
# Gemini helpers (REST, key from env — never stored anywhere else)
# ---------------------------------------------------------------------------

def _gemini_key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _gemini_model():
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"


def _parse_gemini_json(text: str):
    """Gemini sometimes wraps JSON in ``` fences — strip and parse."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


async def _gemini_generate(parts: list, key: str):
    """POST generateContent. Returns the model's text, or None on failure.

    Sends thinkingBudget 0 (the portfolio chatbot learned newer flash models
    are slow/503-prone with default thinking); if the model rejects that
    config with a 400, retries once without it.
    """
    model = _gemini_model()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    base_payload = {"contents": [{"parts": parts}]}
    payloads = [
        {**base_payload, "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}, "responseMimeType": "application/json"}},
        {**base_payload, "generationConfig": {"responseMimeType": "application/json"}},
        base_payload,
    ]
    async with make_http_client() as client:
        for i, payload in enumerate(payloads):
            try:
                r = await client.post(endpoint, json=payload, timeout=120)
            except Exception:
                return None
            if r.is_success:
                try:
                    data = r.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    return None
            if r.status_code == 400 and i < len(payloads) - 1:
                continue  # config not supported by this model — retry simpler
            return None
    return None


IMAGE_PROMPT = """তুমি একজন অভিজ্ঞ fact-check সহকারী। এই ছবিটা মনোযোগ দিয়ে দেখো — ছবিটা কোনো খবর/দাবির সঙ্গে social media-তে ছড়ানো হয়েছে।
শুধু নিচের JSON format-এ উত্তর দাও, অন্য কোনো লেখা নয়:
{"extracted_text": "ছবিতে দৃশ্যমান সব লেখা হুবহু (বাংলা/ইংরেজি যেমন আছে)", "claim": "ছবিটা দিয়ে যে খবর বা দাবি করা হচ্ছে, এক বাক্যে", "scene": "ছবিতে আসলে যা দেখা যাচ্ছে তার বর্ণনা", "fake_signs": ["সম্পাদনা, AI দিয়ে তৈরি, বা পুরনো/অন্য ঘটনার ছবি নতুন বলে চালানোর যেসব লক্ষণ দেখছো"], "ai_risk": 0}
ai_risk = ছবিটা ভুয়া/বিভ্রান্তিকর হওয়ার ঝুঁকি 0 থেকে 100-এর মধ্যে একটি সংখ্যা। fake_signs না থাকলে খালি list [] দাও।"""

VIDEO_PROMPT = """তুমি একজন অভিজ্ঞ fact-check সহকারী। এই ভিডিওটা মনোযোগ দিয়ে দেখো ও শোনো — ভিডিওটা কোনো খবর/দাবির সঙ্গে social media-তে ছড়ানো হয়েছে।
শুধু নিচের JSON format-এ উত্তর দাও, অন্য কোনো লেখা নয়:
{"transcript": "ভিডিওতে বলা কথাগুলোর সারাংশ/transcript (বাংলায়)", "claims": ["ভিডিওতে করা মূল দাবিগুলো, একটা করে"], "scene": "ভিডিওতে আসলে যা দেখা যাচ্ছে তার বর্ণনা", "fake_signs": ["সম্পাদনা, AI দিয়ে তৈরি, বা পুরনো/অন্য ঘটনার ভিডিও নতুন বলে চালানোর যেসব লক্ষণ দেখছো"], "ai_risk": 0}
ai_risk = ভিডিওটা ভুয়া/বিভ্রান্তিকর হওয়ার ঝুঁকি 0 থেকে 100-এর মধ্যে একটি সংখ্যা। fake_signs না থাকলে খালি list [] দাও।"""


def _combine_with_ai(engine: dict, ai: dict, mode: str) -> dict:
    """Blend the signal-engine score with Gemini's ai_risk (50/50) and
    rebuild the verdict from the combined score."""
    engine_score = engine["score"]
    try:
        ai_risk = int(ai.get("ai_risk", 0))
    except Exception:
        ai_risk = 0
    ai_risk = max(0, min(100, ai_risk))
    combined = int(round((engine_score + ai_risk) / 2))
    verdict, advice, color = verdict_block(combined, mode)

    reasons = list(engine["reasons"])
    extra = []
    scene = (ai.get("scene") or "").strip()
    claim = (ai.get("claim") or "").strip()
    if claim:
        extra.append(f"📌 ছবি/ভিডিওর দাবি: {claim}")
    if scene:
        extra.append(f"👁️ বাস্তবে যা দেখা যাচ্ছে: {scene}")
    for sign in (ai.get("fake_signs") or []):
        sign = str(sign).strip()
        if sign:
            extra.append(f"🤖 AI বিশ্লেষণে সন্দেহজনক লক্ষণ: {sign}")
    if not extra:
        extra.append("🤖 AI বিশ্লেষণ: ছবি/ভিডিওতে স্পষ্ট সম্পাদনা বা AI-তৈরির লক্ষণ চোখে পড়েনি।")

    return {
        "score": combined,
        "verdict": verdict,
        "advice": advice,
        "color": color,
        "reasons": extra + reasons,
        "mode": mode,
        "title": engine.get("title", ""),
        "domain": engine.get("domain"),
        "sub_scores": {"signal_engine": engine_score, "ai_vision": ai_risk},
        "ai": {
            "claim": claim,
            "scene": scene,
            "fake_signs": ai.get("fake_signs") or [],
        },
    }


# ---------------------------------------------------------------------------
# Image pipeline
# ---------------------------------------------------------------------------

async def analyze_image(image_url: str, caption: str = "") -> dict:
    key = _gemini_key()
    if not key:
        # No vision available — still run the engine on any caption text.
        result = analyze_engine(caption or "", mode="image")
        result["reasons"].insert(
            0,
            "⚠️ ছবির AI বিশ্লেষণ এখন চালু নেই (GEMINI_API_KEY সেট করা হয়নি) — তাই শুধু সাথে দেওয়া লেখাটুকু যাচাই করা হলো। ছবিটা যাচাই করতে হলে ছবির লেখা/দাবিটা লিখে পাঠাও।",
        )
        result["sub_scores"] = {"signal_engine": result["score"], "ai_vision": None}
        return result

    # Download the image bytes
    try:
        async with make_http_client(follow_redirects=True) as client:
            r = await client.get(image_url, timeout=30)
            r.raise_for_status()
            img_bytes = r.content
            mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    except Exception:
        result = analyze_engine(caption or "", mode="image")
        result["reasons"].insert(0, "⚠️ ছবিটা download করা যায়নি — link-টা মেয়াদোত্তীর্ণ হতে পারে। ছবিটা আবার পাঠাও।")
        result["sub_scores"] = {"signal_engine": result["score"], "ai_vision": None}
        return result
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    parts = [
        {"text": IMAGE_PROMPT},
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(img_bytes).decode()}},
    ]
    text_out = await _gemini_generate(parts, key)
    ai = _parse_gemini_json(text_out) if text_out else None
    if not ai:
        result = analyze_engine(caption or "", mode="image")
        result["reasons"].insert(0, "⚠️ AI ছবি-বিশ্লেষণ এই মুহূর্তে উত্তর দেয়নি — শুধু সাথে দেওয়া লেখাটুকু যাচাই করা হলো। একটু পরে আবার ছবিটা পাঠিয়ে দেখো।")
        result["sub_scores"] = {"signal_engine": result["score"], "ai_vision": None}
        return result

    extracted = (ai.get("extracted_text") or "").strip()
    claim = (ai.get("claim") or "").strip()
    engine_text = " ".join(x for x in [caption, extracted, claim] if x).strip()
    engine = analyze_engine(engine_text, mode="image")
    return _combine_with_ai(engine, ai, mode="image")


# ---------------------------------------------------------------------------
# Video pipeline (Gemini Files API)
# ---------------------------------------------------------------------------

def _video_unavailable(message: str) -> dict:
    """Returned when a video cannot be analyzed — app.py replies with
    `message` directly instead of a formatted verdict."""
    return {
        "mode": "video",
        "error": "video_unavailable",
        "message": message,
        "score": None,
        "verdict": "",
        "advice": "",
        "color": "",
        "reasons": [],
        "sub_scores": {},
    }


SCREENSHOT_REQUEST = (
    "🎬 দুঃখিত, এই ভিডিওটা সরাসরি বিশ্লেষণ করা গেল না। "
    "ভিডিওটার গুরুত্বপূর্ণ মুহূর্তের একটা screenshot পাঠাও — ছবি পড়ে যাচাই করে দিচ্ছি। "
    "সাথে ভিডিওর caption/লেখাটা copy করে পাঠালেও ভালো ফল পাবে।"
)


async def _gemini_upload_video(data: bytes, mime: str, key: str):
    """Resumable Files API upload. Returns the file object or None."""
    base = "https://generativelanguage.googleapis.com"
    async with make_http_client() as client:
        try:
            r = await client.post(
                f"{base}/upload/v1beta/files?key={key}",
                headers={
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(len(data)),
                    "X-Goog-Upload-Header-Content-Type": mime,
                    "Content-Type": "application/json",
                },
                json={"file": {"display_name": "messenger-video"}},
                timeout=60,
            )
            upload_url = r.headers.get("x-goog-upload-url")
            if not upload_url:
                return None
            r = await client.post(
                upload_url,
                headers={
                    "X-Goog-Upload-Command": "upload, finalize",
                    "X-Goog-Upload-Offset": "0",
                    "Content-Type": mime,
                },
                content=data,
                timeout=300,
            )
            if not r.is_success:
                return None
            return (r.json() or {}).get("file")
        except Exception:
            return None


async def _gemini_wait_active(file_obj: dict, key: str, tries: int = 20):
    """Poll the file until state == ACTIVE (videos need processing time)."""
    name = (file_obj or {}).get("name")
    if not name:
        return None
    async with make_http_client() as client:
        for _ in range(tries):
            try:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/{name}?key={key}",
                    timeout=30,
                )
                if r.is_success:
                    f = r.json()
                    state = f.get("state")
                    if state == "ACTIVE":
                        return f
                    if state == "FAILED":
                        return None
            except Exception:
                return None
            # small sleep without blocking imports at module top
            import asyncio

            await asyncio.sleep(3)
    return None


async def analyze_video(video_url: str, caption: str = "") -> dict:
    key = _gemini_key()
    if not key:
        return _video_unavailable(
            "🎬 ভিডিও বিশ্লেষণের জন্য AI সেটআপ এখনো সম্পূর্ণ হয়নি (GEMINI_API_KEY দরকার)। "
            "আপাতত ভিডিওটার একটা screenshot পাঠাও — ছবি যাচাই করে দিচ্ছি। সাথে caption-এর লেখাটা copy করে পাঠালেও যাচাই করা যাবে।"
        )

    # Download with a hard size cap (~18MB)
    try:
        async with make_http_client(follow_redirects=True) as client:
            async with client.stream("GET", video_url, timeout=120) as r:
                r.raise_for_status()
                mime = (r.headers.get("content-type") or "video/mp4").split(";")[0].strip()
                declared = r.headers.get("content-length")
                if declared and int(declared) > MAX_VIDEO_BYTES:
                    return _video_unavailable(SCREENSHOT_REQUEST)
                chunks = []
                total = 0
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        return _video_unavailable(SCREENSHOT_REQUEST)
                    chunks.append(chunk)
        data = b"".join(chunks)
    except Exception:
        return _video_unavailable(SCREENSHOT_REQUEST)
    if not mime.startswith("video/"):
        mime = "video/mp4"

    file_obj = await _gemini_upload_video(data, mime, key)
    if not file_obj:
        return _video_unavailable(SCREENSHOT_REQUEST)
    active = await _gemini_wait_active(file_obj, key)
    if not active:
        return _video_unavailable(SCREENSHOT_REQUEST)

    parts = [
        {"text": VIDEO_PROMPT},
        {"fileData": {"mimeType": active.get("mimeType", mime), "fileUri": active.get("uri")}},
    ]
    text_out = await _gemini_generate(parts, key)
    ai = _parse_gemini_json(text_out) if text_out else None
    if not ai:
        return _video_unavailable(SCREENSHOT_REQUEST)

    transcript = (ai.get("transcript") or "").strip()
    claims = " ".join(str(c).strip() for c in (ai.get("claims") or []))
    engine_text = " ".join(x for x in [caption, transcript, claims] if x).strip()
    engine = analyze_engine(engine_text, mode="video")
    # _combine_with_ai expects a 'claim' key — map the claims list into it
    ai_for_combine = dict(ai)
    if not ai_for_combine.get("claim"):
        ai_for_combine["claim"] = claims
    combined = _combine_with_ai(engine, ai_for_combine, mode="video")
    combined["ai"]["transcript"] = transcript
    combined["ai"]["claims"] = ai.get("claims") or []
    return combined
