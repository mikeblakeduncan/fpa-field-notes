#!/usr/bin/env python3
"""
FP&A Field Notes — Weekly FP&A Practitioner Digest
=====================================================
Mike emails article URLs to a dedicated Gmail inbox (subject: FN) whenever
he finds something worth featuring. On Tuesday the script checks that inbox,
fetches each article, has Claude generate the digest entry, and publishes
to the site. If no articles are queued, a notification email is sent instead.

Content sections:
  1. Tools & Efficiency — AI tools, Excel tricks, automation workflows
  2. FP&A Practice — budgeting, forecasting, strategic planning, leadership

Environment variables:
  ANTHROPIC_API_KEY        - From console.anthropic.com
  INBOX_GMAIL_ADDRESS      - Gmail account used for both inbox (IMAP) and sending (SMTP)
  INBOX_GMAIL_APP_PASSWORD - App password for that account
"""

import os
import json
import re
import smtplib
import urllib.request
import urllib.parse
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime


# ─── Configuration ────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SOCIALDATA_API_KEY = os.environ.get("SOCIALDATA_API_KEY", "")
INBOX_GMAIL_ADDRESS      = os.environ.get("INBOX_GMAIL_ADDRESS", "")
INBOX_GMAIL_APP_PASSWORD = os.environ.get("INBOX_GMAIL_APP_PASSWORD", "")

# Aliases so the rest of the code (send_email, etc.) works unchanged
GMAIL_ADDRESS      = INBOX_GMAIL_ADDRESS
GMAIL_APP_PASSWORD = INBOX_GMAIL_APP_PASSWORD
PAGES_TOKEN           = os.environ.get("PAGES_TOKEN", "")
GITHUB_USERNAME       = os.environ.get("GITHUB_USERNAME", "")
PUBLISH_TO_WEB        = os.environ.get("PUBLISH_TO_WEB", "false").lower() == "true"
PREVIEW_MODE          = os.environ.get("PREVIEW_MODE",   "false").lower() == "true"

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
VENDOR_BLOCKLIST_FILE = os.environ.get("VENDOR_BLOCKLIST_FILE", "vendor_blocklist.txt")

# Stores (email_id, mailbox) tuples populated by fetch_queued_urls(),
# consumed by mark_emails_as_read() at the end of a successful run.
_pending_email_ids: list[bytes] = []


def read_vendor_blocklist() -> list[str]:
    """Read the vendor blocklist file. Returns list of vendor names."""
    try:
        with open(VENDOR_BLOCKLIST_FILE, "r") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        return []


# ─── API Helpers ──────────────────────────────────────────────────────────────

def call_claude(system: str, user_message: str, max_tokens: int = 4000) -> str:
    """Call the Claude API and return the text response."""
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_message}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST"
    )

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as response:
        data = json.loads(response.read().decode("utf-8"))

    text = "".join(
        block["text"] for block in data.get("content", [])
        if block.get("type") == "text"
    )

    usage = data.get("usage", {})
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cost = (inp * 1 + out * 5) / 1_000_000
    print(f"   Tokens: {inp} in / {out} out  (${cost:.4f})")

    return text


# ─── Inbox: Fetch Queued URLs ─────────────────────────────────────────────────

def fetch_queued_urls() -> list[str]:
    """
    Connect to the dedicated inbox via IMAP, find unread emails with subject
    containing 'FN', extract all URLs from the body, mark as read, and return
    a deduplicated list of URLs.
    """
    import imaplib
    import email as email_lib

    if not INBOX_GMAIL_ADDRESS or not INBOX_GMAIL_APP_PASSWORD:
        print("   ⚠ INBOX_GMAIL_ADDRESS or INBOX_GMAIL_APP_PASSWORD not set — skipping inbox check")
        return []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(INBOX_GMAIL_ADDRESS, INBOX_GMAIL_APP_PASSWORD)
        mail.select("INBOX")
    except Exception as e:
        print(f"   ⚠ IMAP connection failed: {e}")
        return []

    try:
        _, data = mail.search(None, 'UNSEEN SUBJECT "FN"')
        email_ids = data[0].split()
    except Exception as e:
        print(f"   ⚠ IMAP search failed: {e}")
        mail.logout()
        return []

    if not email_ids:
        mail.logout()
        return []

    url_pattern = re.compile(r'https?://[^\s<>"\')\]]+')
    all_urls: set[str] = set()

    for email_id in email_ids:
        try:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw_email)

            parts = msg.walk() if msg.is_multipart() else [msg]
            for part in parts:
                if part.get_content_type() not in ("text/plain", "text/html"):
                    continue
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    for url in url_pattern.findall(body):
                        url = url.rstrip(".,;:)>\"'")
                        all_urls.add(url)
                except Exception:
                    pass

            # Mark as read
            mail.store(email_id, "+FLAGS", "\\Seen")
        except Exception as e:
            print(f"   ⚠ Failed to process email {email_id}: {e}")

    mail.logout()
    return list(all_urls)


# ─── Search Queries (hardcoded, rotating weekly) ──────────────────────────────

def fetch_queued_urls() -> list[str]:
    """
    Connect to the dedicated inbox via IMAP, find all UNSEEN emails,
    extract URLs from the body, and return a deduplicated list.

    Does NOT mark emails as read — that happens in mark_emails_as_read()
    after the full pipeline succeeds.
    """
    import imaplib
    import email as email_lib

    global _pending_email_ids
    _pending_email_ids = []

    if not INBOX_GMAIL_ADDRESS or not INBOX_GMAIL_APP_PASSWORD:
        print("   ⚠ INBOX_GMAIL_ADDRESS or INBOX_GMAIL_APP_PASSWORD not set — skipping")
        return []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(INBOX_GMAIL_ADDRESS, INBOX_GMAIL_APP_PASSWORD)
        mail.select("INBOX")
    except Exception as e:
        print(f"   ⚠ IMAP connection failed: {e}")
        return []

    try:
        _, data = mail.search(None, 'UNSEEN SUBJECT "FN"')
        email_ids = data[0].split()
    except Exception as e:
        print(f"   ⚠ IMAP search failed: {e}")
        mail.logout()
        return []

    if not email_ids:
        print("   No unread emails with subject FN found")
        mail.logout()
        return []

    url_pattern = re.compile(r'https?://[^\s<>"\')\]]+')
    all_urls: set[str] = set()

    for email_id in email_ids:
        try:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw_email)

            parts = list(msg.walk()) if msg.is_multipart() else [msg]
            for part in parts:
                if part.get_content_type() not in ("text/plain", "text/html"):
                    continue
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    for url in url_pattern.findall(body):
                        # Strip trailing punctuation, angle brackets, tracking params
                        url = url.rstrip(".,;:)>\"'")
                        url = re.sub(r'[?&](utm_[^&]+|ref=[^&]+)(&|$)', '', url)
                        all_urls.add(url)
                except Exception:
                    pass

            _pending_email_ids.append(email_id)
        except Exception as e:
            print(f"   ⚠ Failed to process email {email_id}: {e}")

    mail.logout()
    print(f"   Found {len(all_urls)} unique URL(s) across {len(_pending_email_ids)} email(s)")
    return list(all_urls)


# ─── Friday: Queue Reminder ───────────────────────────────────────────────────

def check_queue_and_remind():
    """
    Friday job: count unread emails with URLs in the inbox.
    If fewer than 3, send Mike a reminder to queue more articles.
    """
    import imaplib
    import email as email_lib

    print("📬 Friday check: counting queued articles...")

    if not INBOX_GMAIL_ADDRESS or not INBOX_GMAIL_APP_PASSWORD:
        print("   ⚠ Inbox credentials not set — skipping")
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(INBOX_GMAIL_ADDRESS, INBOX_GMAIL_APP_PASSWORD)
        mail.select("INBOX")
    except Exception as e:
        print(f"   ⚠ IMAP connection failed: {e}")
        return

    try:
        _, data = mail.search(None, 'UNSEEN SUBJECT "FN"')
        email_ids = data[0].split()
    except Exception as e:
        print(f"   ⚠ IMAP search failed: {e}")
        mail.logout()
        return

    # Count emails that contain at least one URL
    url_pattern = re.compile(r'https?://\S+')
    emails_with_urls = 0

    for email_id in email_ids:
        try:
            _, msg_data = mail.fetch(email_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw_email)

            parts = list(msg.walk()) if msg.is_multipart() else [msg]
            for part in parts:
                if part.get_content_type() not in ("text/plain", "text/html"):
                    continue
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    if url_pattern.search(body):
                        emails_with_urls += 1
                        break
                except Exception:
                    pass
        except Exception:
            pass

    mail.logout()
    print(f"   {emails_with_urls} email(s) with URLs queued")

    if emails_with_urls < 3:
        print(f"   Queue is light — sending reminder to {GMAIL_ADDRESS}")
        _send_reminder_email(emails_with_urls)
    else:
        print(f"   Queue looks good — no reminder needed")


def _send_reminder_email(queued_count: int):
    """Send a light-queue reminder to Mike's main address."""
    subject = "FP&A Field Notes — Reminder: Queue is light"
    body = (
        f"You have {queued_count} article{'s' if queued_count != 1 else ''} queued for "
        f"Tuesday's digest. Send more links to the Field Notes inbox if you want a fuller issue."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = INBOX_GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS or INBOX_GMAIL_ADDRESS

    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(INBOX_GMAIL_ADDRESS, INBOX_GMAIL_APP_PASSWORD)
            server.sendmail(INBOX_GMAIL_ADDRESS, msg["To"], msg.as_string())
        print(f"   ✓ Reminder sent")
    except Exception as e:
        print(f"   ⚠ Failed to send reminder: {e}")


# ─── Process Queued Articles ──────────────────────────────────────────────────

INBOX_EVAL_PROMPT = """You are generating a digest entry for FP&A Field Notes, a weekly curated digest for FP&A practitioners.

Given an article's title and text, produce a single JSON object. Return valid JSON only, no markdown fences:
{
  "title": "A clear, specific headline (improve on the original if needed)",
  "source_name": "The author's name if identifiable in the text. If not identifiable, use the publication name.",
  "source_url": "(pass through the URL provided)",
  "summary": "2 sentences max. What this article covers and what the author did, found, or argued.",
  "takeaway": "1 sentence. The single most actionable thing a finance practitioner will get from reading this.",
  "credibility": "practitioner if written by a CFO/VP/Director sharing their experience. expert if by a consultant or researcher. journalist if by a reporter. marketing if vendor content.",
  "days_old": -1,
  "author_role": "Brief description of who wrote it if identifiable, otherwise Unknown"
}

Set days_old to an integer based on any visible publication date, or -1 if unclear.
If this is vendor marketing content, return: null"""


def process_queued_articles(urls: list[str], previously_featured_urls: set = None) -> list[dict]:
    """
    For each URL: check dedup, fetch the article, send to Claude for a digest entry.
    Returns a flat list of entry dicts.
    Drops entries where credibility is 'marketing'.
    """
    if previously_featured_urls is None:
        previously_featured_urls = set()

    print(f"📰 Processing {len(urls)} queued URL(s)...")

    # Filter out already-published URLs
    new_urls = [u for u in urls if u.rstrip("/").lower() not in previously_featured_urls]
    skipped = len(urls) - len(new_urls)
    if skipped:
        print(f"   Skipped {skipped} already-published URL(s)")

    entries: list[dict] = []

    for url in new_urls:
        print(f"   Fetching: {url[:80]}...")
        meta = fetch_article_metadata(url)
        if not meta:
            print(f"   ⚠ Could not fetch — skipping")
            continue

        article_input = json.dumps({
            "url":     url,
            "title":   meta["title"],
            "snippet": meta["snippet"],
        }, ensure_ascii=False)

        try:
            text = call_claude(
                INBOX_EVAL_PROMPT,
                f"Generate a digest entry for this article:\n\n{article_input}",
                max_tokens=1000,
            )
        except Exception as e:
            print(f"   ⚠ Claude error: {e} — skipping")
            continue

        # Handle explicit null (marketing rejection)
        stripped = text.strip()
        if stripped.lower() in ("null", "null;", "null."):
            print(f"   ⚠ Dropped (marketing): {url[:70]}")
            continue

        first_brace = text.find('{')
        last_brace  = text.rfind('}')
        if first_brace == -1 or last_brace == -1:
            print(f"   ⚠ No JSON in response — skipping")
            continue

        try:
            entry = json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            print(f"   ⚠ JSON parse failed — skipping")
            continue

        if entry.get("credibility") == "marketing":
            print(f"   ⚠ Dropped (marketing): {entry.get('title', url[:60])}")
            continue

        summary = (entry.get("summary") or "").strip()
        if not summary or len(summary) < 30:
            print(f"   ⚠ Dropped (bad summary): {entry.get('title', url[:60])}")
            continue

        entry["source_url"] = url
        entries.append(entry)
        print(f"   ✓ {entry.get('title', url[:60])}")

    print(f"   ✓ {len(entries)} valid entries")
    return entries


# ─── Synthesis Blog Draft ─────────────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = """You are drafting a weekly synthesis blog post for FP&A Field Notes. The author is Mike Duncan, a fractional FP&A consultant with 15+ years of experience at companies including Lyra Health, Teladoc, Included Health, and McKesson.

You will receive this week's curated articles with their summaries. Your job is to find the thread that connects them and write a short blog post (300-500 words) that pulls out themes, adds Mike's perspective, and gives practitioners something to think about.

WRITING RULES (from Mike's style guide):
- No em dashes
- No sentence fragments used for dramatic effect
- No staccato contrast patterns (short. then long. then short.)
- No triple structures (x. y. z. as a rhetorical device)
- Avoid: "genuinely", "straightforward", "landscape", "navigate", "leverage", "dive in", "let's be honest", "here's the thing"
- Write in complete sentences, clear and direct
- Tone: peer-to-peer, not lecturing. Like a smart colleague sharing observations.
- First person is fine. "I noticed..." "In my experience..." "What struck me this week..."

STRUCTURE:
- Open with 2-3 sentences framing what you noticed across this week's articles
- Middle section connecting the themes with Mike's practitioner perspective
- Close with a question or observation that invites reflection
- No title needed (Mike will add one during editing)

Do NOT just summarize each article sequentially. Find what connects them, what's missing from the conversation, or what practitioners should pay attention to that the articles don't say directly.

Return the blog post text only, no JSON, no markdown fences."""


def generate_synthesis_draft(digest_entries: list[dict]) -> str:
    """
    Generate a synthesis blog post connecting themes across this week's articles.
    Returns the draft text.
    """
    print("✍️  Generating synthesis blog draft...")

    if not digest_entries:
        return ""

    items_for_prompt = [
        {
            "title":       e.get("title", ""),
            "source_name": e.get("source_name", ""),
            "summary":     e.get("summary", ""),
            "takeaway":    e.get("takeaway", ""),
        }
        for e in digest_entries
    ]

    user_msg = (
        f"Here are this week's {len(items_for_prompt)} curated articles:\n\n"
        + json.dumps(items_for_prompt, indent=2, ensure_ascii=False)
    )

    try:
        draft = call_claude(SYNTHESIS_SYSTEM_PROMPT, user_msg, max_tokens=1200)
        print(f"   ✓ Draft generated ({len(draft)} chars)")
        return draft.strip()
    except Exception as e:
        print(f"   ⚠ Synthesis draft failed: {e}")
        return ""


# ─── Save Synthesis Draft to Repo ────────────────────────────────────────────

def save_synthesis_draft(draft_text: str, entry_count: int = 0):
    """Save the synthesis draft to drafts/{date}.md in the repo via GitHub API."""
    print("💾 Saving synthesis draft to repo...")

    if not draft_text:
        print("   No draft text — skipping")
        return

    if not PAGES_TOKEN or not GITHUB_USERNAME:
        print("   ⚠ PAGES_TOKEN or GITHUB_USERNAME not set — skipping")
        return

    repo       = f"{GITHUB_USERNAME}/fpa-field-notes"
    today_date = date.today().strftime("%Y-%m-%d")
    path       = f"drafts/synthesis-{today_date}.md"

    front_matter = f"""---
date: {today_date}
status: draft
source_articles: {entry_count}
seo_note: When publishing, read SEO_REQUIREMENTS.md for meta description and title guidance.
---

"""
    content = front_matter + draft_text

    # Check if file already exists (for sha)
    existing_content, existing_sha = fetch_github_file(repo, path, PAGES_TOKEN)

    try:
        push_github_file(
            repo, path, content, PAGES_TOKEN,
            f"Save synthesis draft — {today_date}", existing_sha
        )
        print(f"   ✓ Saved {path}")
    except Exception as e:
        print(f"   ⚠ Failed to save synthesis draft: {e}")


# ─── Mark Emails as Read ─────────────────────────────────────────────────────

def mark_emails_as_read():
    """
    Mark all emails collected by fetch_queued_urls() as read.
    Called as the final step after the pipeline succeeds.
    """
    import imaplib

    if not _pending_email_ids:
        return

    print(f"📬 Marking {len(_pending_email_ids)} email(s) as read...")

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(INBOX_GMAIL_ADDRESS, INBOX_GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        for email_id in _pending_email_ids:
            try:
                mail.store(email_id, "+FLAGS", "\\Seen")
            except Exception as e:
                print(f"   ⚠ Failed to mark email {email_id} as read: {e}")

        mail.logout()
        print(f"   ✓ Marked as read")
    except Exception as e:
        print(f"   ⚠ IMAP connection failed when marking as read: {e}")


# ─── Twitter Search (disabled — kept for reference) ───────────────────────────
#
# def generate_queries() -> list[dict]:
#     """Return hardcoded Twitter search queries, rotating weekly."""
#     ...
#
# def search_twitter(query: str, max_pages: int = 5) -> list[dict]:
#     """Search Twitter via SocialData API."""
#     ...
#
# def run_searches(queries: list[dict]) -> list[dict]:
#     """Run all Twitter queries and return deduplicated tweets."""
#     ...
#
# def filter_tweets(tweets: list[dict]) -> list[dict]:
#     """Remove noise and rank by engagement-to-follower ratio."""
#     ...
#
# def build_enriched_items(filtered_tweets: list[dict]) -> list[dict]:
#     """Resolve tweet URLs and fetch article metadata."""
#     ...


# ─── Article Fetching ─────────────────────────────────────────────────────────

def fetch_article_metadata(url: str) -> dict | None:
    """Fetch a URL and return title + text snippet, or None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0.0.0 Safari/537.36"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return None
            raw = response.read(200_000).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"   ⚠ Fetch failed {url[:70]}: {e}")
        return None

    # Extract <title>
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', raw, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else url

    # ── Content extraction ───────────────────────────────────────────────────
    # 1. Drop scripts, styles, and HTML comments wholesale
    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw,   flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>',   '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<!--.*?-->',                 '', clean, flags=re.DOTALL)

    # 2. Remove navigation chrome before any further processing
    for tag in ('nav', 'header', 'footer', 'aside', 'menu'):
        clean = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', clean,
                       flags=re.DOTALL | re.IGNORECASE)

    # 3. Try to isolate the main article body using semantic signals
    article_body = ""
    content_patterns = [
        r'<article[^>]*>(.*?)</article>',
        r'<main[^>]*>(.*?)</main>',
        r'<div[^>]*\brole=["\']main["\'][^>]*>(.*?)</div>',
        (r'<div[^>]*\bclass=["\'][^"\']*\b(?:post-content|entry-content|article-body|'
         r'article-content|article__body|post__body|single-content|'
         r'blog-post|story-body|content-body)[^"\']*["\'][^>]*>(.*?)</div>'),
    ]
    for pattern in content_patterns:
        m = re.search(pattern, clean, flags=re.DOTALL | re.IGNORECASE)
        if m:
            article_body = m.group(1)
            break

    # 4. Fall back to the full chrome-stripped page if no semantic container found
    text_source = article_body if len(article_body) > 300 else clean

    # 5. Strip remaining tags and collapse whitespace
    text = re.sub(r'<[^>]+>', ' ', text_source)
    text = re.sub(r'\s+', ' ', text).strip()

    # 6. Last-resort fallback — if we still have almost nothing, use full raw page
    if len(text) < 200:
        text = re.sub(r'<[^>]+>', ' ', clean)
        text = re.sub(r'\s+', ' ', text).strip()

    return {
        "url":     url,
        "title":   title[:200],
        "snippet": text[:3000],
    }


# ─── Evaluate Results (disabled — kept for reference) ─────────────────────────
#
# The old Twitter-based evaluation pipeline used evaluate_results() with a large
# batch prompt. The new email-based pipeline processes articles one at a time in
# process_queued_articles() using INBOX_EVAL_PROMPT above.
#
# def evaluate_results(enriched_items: list[dict]) -> dict:
#     ...
#
# def ground_summaries(...):
#     ...


# ─── Inbox: Process Queued Articles ──────────────────────────────────────────

INBOX_EVAL_PROMPT = """You are generating a digest entry for FP&A Field Notes, a weekly curated digest for FP&A practitioners.

Given an article's title and text, produce a single JSON object. Return valid JSON only, no markdown fences, no explanation:
{
  "title": "A clear, descriptive headline (can improve on the original title)",
  "source_name": "The author's name if identifiable, otherwise the publication name",
  "author_role": "Brief description, e.g. 'VP of FP&A at healthcare company'. Use 'Unknown' if unclear.",
  "summary": "2 sentences max. What the article covers and what the author did or learned.",
  "takeaway": "1 sentence. The single most actionable thing the reader will get from this.",
  "section": "tools if about AI, automation, Excel, Python, or efficiency. fpa_practice if about budgeting, forecasting, leadership, stakeholder management, board reporting, or FP&A craft.",
  "credibility": "practitioner if written by a CFO/VP/director sharing their own experience. expert if by a consultant/researcher. journalist if by a reporter. marketing if vendor promotional content.",
  "days_old": 0
}

Set days_old to an integer based on any visible publication date, or -1 if unclear.
If this is vendor marketing content, return: null"""


def process_queued_articles(urls: list[str], previously_featured_urls: set = None) -> dict:
    """
    For each URL: fetch the article, send to Claude to generate a digest entry.
    Returns a digest dict {"tools": [...], "fpa_practice": [...]} in the same
    format as evaluate_results().
    """
    if previously_featured_urls is None:
        previously_featured_urls = set()

    # Filter out already-published URLs before fetching
    new_urls = [u for u in urls if u.rstrip("/").lower() not in previously_featured_urls]
    skipped = len(urls) - len(new_urls)
    if skipped:
        print(f"   Skipped {skipped} already-published URL(s)")

    digest: dict = {"tools": [], "fpa_practice": []}

    for url in new_urls:
        print(f"   Fetching: {url[:80]}...")
        meta = fetch_article_metadata(url)
        if not meta:
            print(f"   ⚠ Could not fetch — skipping")
            continue

        article_input = json.dumps({
            "url":     url,
            "title":   meta["title"],
            "snippet": meta["snippet"],
        }, ensure_ascii=False)

        try:
            text = call_claude(
                INBOX_EVAL_PROMPT,
                f"Generate a digest entry for this article:\n\n{article_input}",
                max_tokens=1000,
            )
        except Exception as e:
            print(f"   ⚠ Claude error: {e} — skipping")
            continue

        # Handle explicit null (marketing rejection)
        stripped = text.strip()
        if stripped.lower() in ("null", "null;", "null."):
            print(f"   ⚠ Dropped (marketing): {url[:70]}")
            continue

        first_brace = text.find('{')
        last_brace  = text.rfind('}')
        if first_brace == -1 or last_brace == -1:
            print(f"   ⚠ No JSON in response — skipping")
            continue

        try:
            entry = json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            print(f"   ⚠ JSON parse failed — skipping")
            continue

        if entry.get("credibility") == "marketing":
            print(f"   ⚠ Dropped (marketing): {entry.get('title', url[:60])}")
            continue

        summary = (entry.get("summary") or "").strip()
        if not summary or len(summary) < 30:
            print(f"   ⚠ Dropped (bad summary): {entry.get('title', url[:60])}")
            continue

        section = entry.get("section", "fpa_practice")
        if section not in ("tools", "fpa_practice"):
            section = "fpa_practice"

        entry["source_url"] = url
        entry["section"]    = section
        digest[section].append(entry)
        print(f"   ✓ [{section}] {entry.get('title', url[:60])}")

    tools_count    = len(digest["tools"])
    practice_count = len(digest["fpa_practice"])
    print(f"   ✓ Digest built: {tools_count} tools, {practice_count} FP&A practice")
    return digest


# ─── GitHub Helpers ──────────────────────────────────────────────────────────

def fetch_github_file(repo: str, path: str, token: str) -> tuple:
    """Fetch a file from GitHub. Returns (content_string, sha) or (None, None)."""
    import base64
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]
    except Exception:
        return None, None


def push_github_file(repo: str, path: str, content: str, token: str, message: str, sha: str = None):
    """Create or update a file on GitHub."""
    import base64
    ctx = ssl.create_default_context()
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PUT"
    )
    with urllib.request.urlopen(req, context=ctx) as response:
        return json.loads(response.read().decode("utf-8"))


# ─── Format Email ─────────────────────────────────────────────────────────────

def render_section_items(items: list) -> str:
    """Render a list of digest items as HTML."""
    html = ""
    for item in items:
        source_name = item.get("source_name", "")
        source_url  = item.get("source_url", "#")
        source_link = f'<a href="{source_url}" style="color: #1A477A; text-decoration: underline;">{source_name}</a>'

        # Fallback for old "sources" array format
        if not source_name:
            sources = item.get("sources", [])
            if sources and isinstance(sources, list):
                s = sources[0]
                source_link = f'<a href="{s.get("url", "#")}" style="color: #1A477A; text-decoration: underline;">{s.get("name", "Source")}</a>'

        days_old = item.get("days_old", 7)

        # Freshness badge
        if isinstance(days_old, (int, float)) and days_old == -1:
            fresh_label, fresh_bg, fresh_fg = "📅 Recent", "#F3F4F6", "#6B7280"
        elif isinstance(days_old, (int, float)) and days_old <= 1:
            fresh_label, fresh_bg, fresh_fg = "🟢 Today", "#DCFCE7", "#166534"
        elif isinstance(days_old, (int, float)) and days_old <= 3:
            fresh_label, fresh_bg, fresh_fg = f"🟢 {int(days_old)} days ago", "#DCFCE7", "#166534"
        elif isinstance(days_old, (int, float)) and days_old <= 5:
            fresh_label, fresh_bg, fresh_fg = f"🟡 {int(days_old)} days ago", "#FEF3C7", "#92400E"
        else:
            fresh_label, fresh_bg, fresh_fg = f"🟠 ~{int(days_old) if isinstance(days_old, (int, float)) else 7} days ago", "#FFEDD5", "#9A3412"

        # Credibility badge
        credibility = item.get("credibility", "")
        cred_labels = {
            "practitioner": ("👤 Practitioner", "#DCFCE7", "#166534"),
            "expert":       ("🎓 Expert",        "#DBEAFE", "#1E40AF"),
            "journalist":   ("📰 Journalist",    "#F3F4F6", "#374151"),
        }
        cred_text, cred_bg, cred_fg = cred_labels.get(credibility, ("", "#F3F4F6", "#374151"))

        author_role  = item.get("author_role", "")
        author_html  = f' <span style="font-size: 11px; color: #6b7280;">— {author_role}</span>' if author_role and author_role != "Unknown" else ""

        html += f"""
<div style="padding: 14px 16px; border-radius: 8px; border: 1px solid #e5e7eb; background: white; margin-bottom: 10px;">
  <div style="margin-bottom: 6px;">
    <span style="font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 4px; background: {fresh_bg}; color: {fresh_fg};">{fresh_label}</span>
    <span style="font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 4px; background: {cred_bg}; color: {cred_fg}; margin-left: 4px;">{cred_text}</span>{author_html}
  </div>
  <div style="font-size: 15px; font-weight: 600; color: #111; margin-bottom: 6px;">{item.get('title', '')}</div>
  <div style="font-size: 12px; color: #6b7280; margin-bottom: 8px;">
    {source_link}
  </div>
  <p style="font-size: 14px; color: #374151; margin: 0 0 10px; line-height: 1.6;">{item.get('summary', '')}</p>
  <div style="padding: 8px 12px; background: #FFFBF5; border-radius: 6px; border: 1px solid #EDE0D4;">
    <span style="font-size: 10px; font-weight: 700; color: #E07A5F; text-transform: uppercase; letter-spacing: 0.04em; display: block; margin-bottom: 2px;">What you'll learn</span>
    <span style="font-size: 13px; color: #3D405B;">{item.get('takeaway', '')}</span>
  </div>
</div>"""
    return html


def format_html_email(digest: dict, synthesis_draft: str = "") -> str:
    """Build the full HTML email body."""
    today_str = date.today().strftime("%B %d, %Y")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; color: #1a1a1a; background: #f8f9fa;">

<div style="background: #7C3AED; color: white; padding: 24px; border-radius: 12px 12px 0 0;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; opacity: 0.7;">FP&A Field Notes</div>
  <h1 style="margin: 4px 0 0; font-size: 22px; font-weight: 700;">{today_str}</h1>
  <p style="margin: 8px 0 0; font-size: 13px; opacity: 0.8;">This week's curated reads for FP&A practitioners.</p>
</div>

<div style="background: white; padding: 24px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
"""

    articles = digest.get("articles", [])
    if articles:
        html += render_section_items(articles)
    else:
        html += '<p style="font-size: 13px; color: #94a3b8; font-style: italic; margin: 8px 0 16px;">Nothing queued this week.</p>'

    raw = digest.get("_raw_text", "")
    if raw:
        html += f"""
<h2 style="font-size: 16px; font-weight: 700; color: #111; margin: 24px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #EF4444;">Raw Output (parsing failed)</h2>
<pre style="font-size: 12px; background: #f8fafc; padding: 16px; border-radius: 8px; white-space: pre-wrap; word-wrap: break-word;">{raw}</pre>"""

    # Synthesis draft section (added at the end of the body content area)
    if synthesis_draft:
        escaped_draft = synthesis_draft.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += f"""
<div style="background: #FFFBEB; padding: 24px; border-radius: 12px; border: 1px solid #FDE68A; margin-top: 16px;">
  <h2 style="font-size: 18px; font-weight: 700; color: #92400E; margin: 0 0 4px;">✏️ Synthesis Draft</h2>
  <p style="font-size: 12px; color: #B45309; margin: 0 0 16px;">Review and edit in Cowork, then publish as this week's blog post.</p>
  <div style="font-size: 14px; color: #374151; line-height: 1.7; white-space: pre-wrap;">{escaped_draft}</div>
</div>"""

    html += """
</div>
<div style="text-align: center; padding: 16px; font-size: 11px; color: #94a3b8;">
  FP&A Field Notes — curated by AI, reviewed by humans.
</div>
</body></html>"""

    return html


# ─── Tweet Generation ─────────────────────────────────────────────────────────

TWEET_PROMPT = """You generate tweets for a curated FP&A digest called FP&A Field Notes. The tweets should make it OBVIOUS that you are recommending someone else's content — you are a curator sharing great finds, not claiming credit.

TWEET TYPES:
1. One tweet per individual entry — recommends the original author's work
2. One summary tweet for the full digest — ties together the week's themes

TWEET RULES:
- Maximum 200 characters for the tweet text. This is a HARD LIMIT — count carefully. The link will be appended separately, so do NOT include any URL in your tweet text.
- Be concise. One sentence about what the author did or found, one brief reason to read it. That's it.
- ALWAYS frame as a recommendation of someone else's work. Use language like:
  "Worth reading:" "Great piece by [Author] on..." "Found this useful breakdown from [Author]:" "[Author] shares how they..." "Helpful walkthrough from [Author] on..." "Check out [Author]'s take on..."
- NEVER frame it as your own content or insight. You are pointing to their work.
- Credit the original author/source by name prominently
- If you know their Twitter handle, use it. If not, just use their name.
- Add a brief reason WHY it's worth reading
- Tone: curious, practitioner-focused, no hype. Write like a finance person sharing something useful they found.
- Don't use hashtags excessively — one at most, and only if natural
- Don't use "🧵" or thread language
- No em dashes
- Each tweet should make someone want to click

SUMMARY TWEET:
- Frame as "This week in FP&A Field Notes..." or "N practitioner takes worth reading this week..."
- Make clear you're curating other people's insights

Return valid JSON only, no markdown fences:
[
  {
    "type": "entry",
    "title": "Original article title",
    "source_name": "Author name",
    "tweet": "The tweet text (without the link — it will be appended automatically)"
  },
  {
    "type": "summary",
    "tweet": "The summary tweet text"
  }
]"""


def generate_tweets(digest: dict, website_url: str = None) -> list[dict]:
    """Generate tweets for each digest entry and section summaries."""
    print("🐦 Generating tweets...")

    items_for_prompt = []
    for item in digest.get("articles", []):
        items_for_prompt.append({
            "title":       item.get("title", ""),
            "source_name": item.get("source_name", ""),
            "source_url":  item.get("source_url", ""),
            "summary":     item.get("summary", ""),
            "takeaway":    item.get("takeaway", ""),
        })

    if not items_for_prompt:
        print("   No items to generate tweets for")
        return []

    text = call_claude(
        TWEET_PROMPT,
        f"Generate tweets for these items:\n{json.dumps(items_for_prompt, indent=2, ensure_ascii=False)}"
    )

    first_bracket = text.find('[')
    last_bracket  = text.rfind(']')
    if first_bracket != -1 and last_bracket != -1:
        clean = text[first_bracket:last_bracket + 1]
    else:
        clean = text.strip()

    try:
        tweets = json.loads(clean)
        print(f"   ✓ Generated {len(tweets)} tweets")
        return tweets
    except json.JSONDecodeError:
        print("   ⚠ Failed to parse tweet JSON")
        return []


def format_tweet_email_section(tweets: list[dict], website_url: str = None) -> str:
    """Build HTML for the tweets section of the email with Tweet This buttons."""
    if not tweets:
        return ""

    if not website_url:
        website_url = "https://fpafieldnotes.seacloudconsulting.com/"

    html = """
<div style="margin-top: 32px; padding-top: 24px; border-top: 3px solid #1DA1F2;">
  <h2 style="font-size: 18px; font-weight: 700; color: #1DA1F2; margin: 0 0 4px;">🐦 Ready-to-Tweet</h2>
  <p style="font-size: 12px; color: #94a3b8; margin: 0 0 20px;">Click the button to open Twitter with the tweet pre-filled. Edit if needed, then post.</p>
"""

    for i, tweet in enumerate(tweets):
        tweet_text = tweet.get("tweet", "")
        is_summary = tweet.get("type") == "summary"

        full_tweet   = f"{tweet_text}\n\n{website_url}" if website_url else tweet_text
        encoded      = urllib.parse.quote(full_tweet, safe='')
        intent_url   = f"https://twitter.com/intent/tweet?text={encoded}"

        label        = "📊 Week Summary" if is_summary else tweet.get("title", f"Tweet {i + 1}")
        badge_bg     = "#E8F5FD" if is_summary else "#F8FAFC"
        badge_border = "#1DA1F2" if is_summary else "#E2E8F0"

        html += f"""
  <div style="padding: 14px; margin-bottom: 10px; border: 1px solid {badge_border}; border-radius: 8px; background: {badge_bg};">
    <div style="font-size: 11px; font-weight: 600; color: #64748B; margin-bottom: 6px;">{label}</div>
    <p style="font-size: 13px; color: #1a1a1a; line-height: 1.5; margin: 0 0 10px;">{tweet_text}</p>
    <a href="{intent_url}" style="display: inline-block; padding: 8px 20px; background: #1DA1F2; color: white; font-size: 13px; font-weight: 700; text-decoration: none; border-radius: 20px;">🐦 Tweet This</a>
  </div>"""

    html += "\n</div>"
    return html


def send_email(digest: dict, tweets: list[dict] = None, synthesis_draft: str = ""):
    """Send the formatted digest via Gmail SMTP."""
    print("📧 Sending email...")

    today_str  = date.today().strftime("%B %d, %Y")
    subject    = f"FP&A Field Notes — {today_str}"
    html_body  = format_html_email(digest, synthesis_draft)

    if tweets:
        tweet_html = format_tweet_email_section(tweets)
        # Insert tweets before the synthesis draft and footer
        html_body  = html_body.replace(
            '<div style="text-align: center; padding: 16px; font-size: 11px; color: #94a3b8;">',
            tweet_html + '\n<div style="text-align: center; padding: 16px; font-size: 11px; color: #94a3b8;">'
        )

    recipient = GMAIL_ADDRESS or INBOX_GMAIL_ADDRESS

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = INBOX_GMAIL_ADDRESS
    msg["To"]      = recipient

    plain = f"FP&A Field Notes — {today_str}\n\n{json.dumps(digest, indent=2, ensure_ascii=False)}"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    smtp_user     = GMAIL_ADDRESS      if GMAIL_ADDRESS      else INBOX_GMAIL_ADDRESS
    smtp_password = GMAIL_APP_PASSWORD if GMAIL_APP_PASSWORD else INBOX_GMAIL_APP_PASSWORD

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipient, msg.as_string())

    print(f"   ✓ Sent to {recipient}")


# ─── Empty Queue Notification ─────────────────────────────────────────────────

def send_empty_queue_email():
    """Send a brief notification when the inbox has no queued articles."""
    print("📧 Sending empty-queue notification...")
    today_str = date.today().strftime("%B %d, %Y")
    subject   = "FP&A Field Notes — No articles queued this week"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #1a1a1a; background: #f8f9fa;">
<div style="background: #7C3AED; color: white; padding: 20px; border-radius: 10px 10px 0 0;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: .1em; opacity: .7;">FP&A Field Notes</div>
  <h1 style="margin: 4px 0 0; font-size: 20px; font-weight: 700;">{today_str}</h1>
</div>
<div style="background: white; padding: 24px; border-radius: 0 0 10px 10px; border: 1px solid #e2e8f0; border-top: none;">
  <p style="font-size: 15px; color: #374151;">No articles were in the queue for this week's digest. Nothing was published.</p>
  <p style="font-size: 13px; color: #6b7280;">Send links to the Field Notes inbox for next week.</p>
</div>
</body></html>"""

    recipient = GMAIL_ADDRESS or INBOX_GMAIL_ADDRESS

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = INBOX_GMAIL_ADDRESS
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))

    smtp_user     = GMAIL_ADDRESS      if GMAIL_ADDRESS      else INBOX_GMAIL_ADDRESS
    smtp_password = GMAIL_APP_PASSWORD if GMAIL_APP_PASSWORD else INBOX_GMAIL_APP_PASSWORD

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipient, msg.as_string())
        print(f"   ✓ Sent empty-queue notification to {recipient}")
    except Exception as e:
        print(f"   ⚠ Failed to send notification: {e}")


# ─── Beehiiv Newsletter Copy ──────────────────────────────────────────────────

def generate_beehiiv_html(digest: dict) -> str:
    """Generate a clean HTML version of the digest for pasting into Beehiiv."""
    today_str = date.today().strftime("%B %d, %Y")

    articles = digest.get("articles", [])

    html = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; color: #1a1a2e;">
"""

    if not articles:
        html += '<p style="font-size: 14px; color: #94a3b8; font-style: italic;">Nothing queued this week.</p>\n'
    else:
        for item in articles:
            source_name = item.get("source_name", "")
            source_url  = item.get("source_url", "#")
            html += f"""<div style="padding: 14px 16px; border: 1px solid #e5e7eb; border-radius: 8px; background: white; margin-bottom: 10px;">
  <div style="font-size: 15px; font-weight: 600; margin-bottom: 4px;"><a href="{source_url}" style="color: #1a1a2e; text-decoration: none;">{item.get('title', '')}</a></div>
  <div style="font-size: 12px; color: #8D8D8D; margin-bottom: 8px;">by {source_name}</div>
  <p style="font-size: 14px; color: #374151; line-height: 1.6; margin: 0 0 10px;">{item.get('summary', '')}</p>
  <div style="padding: 8px 12px; background: #FFFBF5; border-radius: 6px; border: 1px solid #EDE0D4;">
    <span style="font-size: 10px; font-weight: 700; color: #E07A5F; text-transform: uppercase; letter-spacing: 0.04em; display: block; margin-bottom: 2px;">What you'll learn</span>
    <span style="font-size: 13px; color: #3D405B;">{item.get('takeaway', '')}</span>
  </div>
  <div style="margin-top: 8px;"><a href="{source_url}" style="font-size: 13px; font-weight: 600; color: #E07A5F; text-decoration: none;">Read the original →</a></div>
</div>\n"""

    html += """<div style="text-align: center; padding: 20px 0; font-size: 12px; color: #8D8D8D;">
  <p>FP&amp;A Field Notes — curated by AI, reviewed by humans.</p>
  <p><a href="https://fpafieldnotes.seacloudconsulting.com" style="color: #E07A5F;">Visit the archive</a> · A <a href="https://seacloudconsulting.com" style="color: #E07A5F;">Sea Cloud Consulting</a> project.</p>
</div>
</div>"""

    return html


def publish_beehiiv_copy(digest: dict):
    """Save a Beehiiv-ready HTML file to the repo for easy copy/paste."""
    print("📰 Saving Beehiiv newsletter copy...")

    if not PAGES_TOKEN or not GITHUB_USERNAME:
        print("   ⚠ PAGES_TOKEN or GITHUB_USERNAME not set — skipping")
        return

    repo = f"{GITHUB_USERNAME}/fpa-field-notes"
    beehiiv_html = generate_beehiiv_html(digest)

    existing_content, existing_sha = fetch_github_file(repo, "beehiiv-latest.html", PAGES_TOKEN)

    try:
        push_github_file(
            repo, "beehiiv-latest.html", beehiiv_html, PAGES_TOKEN,
            f"Update Beehiiv newsletter — {date.today().strftime('%B %d, %Y')}", existing_sha
        )
        print("   ✓ Saved beehiiv-latest.html")
        print("   📋 Copy from: https://fpafieldnotes.seacloudconsulting.com/beehiiv-latest.html")
    except Exception as e:
        print(f"   ⚠ Failed to save Beehiiv copy: {e}")


# ─── Generate and Publish Web Page ────────────────────────────────────────────

def _make_meta_description(articles: list[dict]) -> str:
    """Generate a concise SEO meta description from this week's articles."""
    titles = [a.get("title", "") for a in articles if a.get("title")][:3]
    if not titles:
        return "Weekly curated reads for FP&A practitioners — tools, forecasting, and finance craft."
    joined = " · ".join(titles)
    return f"This week in FP&A Field Notes: {joined}. Curated practitioner insights for finance teams."


def generate_issue_page(digest: dict) -> str:
    """Generate a standalone HTML page for this week's issue."""
    today_str  = date.today().strftime("%B %d, %Y")
    issue_date = date.today().strftime("%Y-%m-%d")
    base_url   = "https://fpafieldnotes.seacloudconsulting.com"
    page_url   = f"{base_url}/issues/{issue_date}.html"

    articles   = digest.get("articles", [])
    meta_desc  = _make_meta_description(articles)

    items_html = ""
    if not articles:
        items_html = '<p style="color: #8D8D8D; font-style: italic;">Nothing queued this week.</p>'
    else:
        for item in articles:
            source_name  = item.get("source_name", "")
            source_url   = item.get("source_url", "#")
            days_old     = item.get("days_old", 7)
            credibility  = item.get("credibility", "")
            author_role  = item.get("author_role", "")

            if isinstance(days_old, (int, float)) and days_old == -1:
                fresh_label, fresh_bg, fresh_fg = "📅 Recent", "#F3F4F6", "#6B7280"
            elif isinstance(days_old, (int, float)) and days_old <= 3:
                fresh_label, fresh_bg, fresh_fg = (f"🟢 {int(days_old)}d ago" if days_old > 1 else "🟢 Today"), "#DCFCE7", "#166534"
            elif isinstance(days_old, (int, float)) and days_old <= 5:
                fresh_label, fresh_bg, fresh_fg = f"🟡 {int(days_old)}d ago", "#FEF3C7", "#92400E"
            else:
                fresh_label, fresh_bg, fresh_fg = f"🟠 ~{int(days_old)}d ago", "#FFEDD5", "#9A3412"

            cred_map = {
                "practitioner": ("👤 Practitioner", "#DCFCE7", "#166534"),
                "expert":       ("🎓 Expert",        "#DBEAFE", "#1E40AF"),
                "journalist":   ("📰 Journalist",    "#F3F4F6", "#374151"),
            }
            cred_text, cred_bg, cred_fg = cred_map.get(credibility, ("", "#F3F4F6", "#374151"))
            author_html = f' <span style="font-size: 0.8rem; color: #8D8D8D;">— {author_role}</span>' if author_role and author_role != "Unknown" else ""

            items_html += f'''
<div style="padding: 18px; border: 1px solid #E8E4DE; border-radius: 12px; background: white; margin-bottom: 14px;">
  <div style="margin-bottom: 8px;">
    <span style="font-size: 0.75rem; font-weight: 700; padding: 3px 10px; border-radius: 4px; background: {fresh_bg}; color: {fresh_fg};">{fresh_label}</span>
    <span style="font-size: 0.75rem; font-weight: 700; padding: 3px 10px; border-radius: 4px; background: {cred_bg}; color: {cred_fg}; margin-left: 4px;">{cred_text}</span>{author_html}
  </div>
  <h3 style="font-family: Fraunces, serif; font-size: 1.1rem; margin: 8px 0 6px;"><a href="{source_url}" style="color: #1a1a2e; text-decoration: none; border-bottom: 2px solid #E07A5F;">{item.get("title", "")}</a></h3>
  <div style="font-size: 0.82rem; color: #8D8D8D; margin-bottom: 10px;">by {source_name}</div>
  <p style="font-size: 0.95rem; line-height: 1.6; margin-bottom: 12px;">{item.get("summary", "")}</p>
  <div style="padding: 10px 14px; background: #FFFBF5; border-radius: 8px; border: 1px solid #EDE0D4; margin-bottom: 10px;">
    <span style="font-size: 0.7rem; font-weight: 700; color: #E07A5F; text-transform: uppercase; letter-spacing: 0.04em; display: block; margin-bottom: 2px;">What you\'ll learn</span>
    <span style="font-size: 0.88rem; color: #3D405B; line-height: 1.5;">{item.get("takeaway", "")}</span>
  </div>
  <a href="{source_url}" style="font-size: 0.85rem; font-weight: 600; color: #E07A5F; text-decoration: none;">Read the original →</a>
</div>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FP&A Field Notes — {today_str}</title>
    <meta name="description" content="{meta_desc}">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{page_url}">
    <meta property="og:type" content="article">
    <meta property="og:title" content="FP&A Field Notes — {today_str}">
    <meta property="og:description" content="{meta_desc}">
    <meta property="og:url" content="{page_url}">
    <meta property="og:site_name" content="FP&A Field Notes">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="FP&A Field Notes — {today_str}">
    <meta name="twitter:description" content="{meta_desc}">
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-DTR4KSL0LS"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', 'G-DTR4KSL0LS');
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,700;9..144,800&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body style="font-family: DM Sans, sans-serif; max-width: 720px; margin: 0 auto; padding: 24px; background: #FDFBF7; color: #1a1a2e;">
    <div style="margin-bottom: 32px;">
        <a href="../" style="font-size: 0.85rem; color: #E07A5F; text-decoration: none;">← Back to FP&A Field Notes</a>
    </div>
    <div style="margin-bottom: 40px;">
        <div style="font-size: 0.8rem; font-weight: 600; color: #E07A5F; text-transform: uppercase; letter-spacing: 0.05em;">FP&A Field Notes</div>
        <h1 style="font-family: Fraunces, serif; font-size: 2rem; margin: 8px 0 4px;">{today_str}</h1>
        <p style="color: #8D8D8D; font-size: 0.9rem;">Weekly curated reads for FP&A practitioners.</p>
    </div>
    {items_html}
    <footer style="text-align: center; padding: 40px 0; margin-top: 40px; border-top: 1px solid #E8E4DE;">
        <p style="font-size: 0.82rem; color: #8D8D8D;">FP&A Field Notes — curated by AI, reviewed by humans.</p>
        <p style="font-size: 0.82rem; color: #8D8D8D; margin-top: 4px;"><a href="../" style="color: #E07A5F;">Subscribe</a> for weekly updates.</p>
    </footer>
</body>
</html>'''


def count_items(digest: dict) -> str:
    """Return a summary like '5 articles'"""
    n = len(digest.get("articles", []))
    return f"{n} article{'s' if n != 1 else ''}" if n else "No items"


def publish_to_website(digest: dict):
    """Push the issue page to the fpa-field-notes repo via GitHub API."""
    print("🌐 Publishing to website...")

    if not PAGES_TOKEN or not GITHUB_USERNAME:
        print("   ⚠ PAGES_TOKEN or GITHUB_USERNAME not set — skipping publish")
        return

    repo       = f"{GITHUB_USERNAME}/fpa-field-notes"
    today_str  = date.today().strftime("%B %d, %Y")
    issue_date = date.today().strftime("%Y-%m-%d")

    issue_html = generate_issue_page(digest)
    meta       = count_items(digest)

    import base64
    issue_path = f"issues/{issue_date}.html"
    issue_b64  = base64.b64encode(issue_html.encode("utf-8")).decode("utf-8")

    ctx = ssl.create_default_context()

    # Check if file already exists
    existing_sha = None
    try:
        check_req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/{issue_path}",
            headers={
                "Authorization": f"Bearer {PAGES_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(check_req, context=ctx) as response:
            existing_data = json.loads(response.read().decode("utf-8"))
            existing_sha  = existing_data["sha"]
            print(f"   File exists, will update: {issue_path}")
    except Exception:
        print(f"   New file: {issue_path}")

    put_body = {"message": f"Add FP&A Field Notes — {today_str}", "content": issue_b64}
    if existing_sha:
        put_body["sha"] = existing_sha

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{issue_path}",
        data=json.dumps(put_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {PAGES_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PUT"
    )

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            print(f"   ✓ Published issue page: {issue_path}")
    except Exception as e:
        print(f"   ⚠ Failed to publish issue page: {e}")
        return

    # Fetch and update index.html
    try:
        get_req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/index.html",
            headers={
                "Authorization": f"Bearer {PAGES_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(get_req, context=ctx) as response:
            index_data = json.loads(response.read().decode("utf-8"))

        index_content = base64.b64decode(index_data["content"]).decode("utf-8")
        index_sha     = index_data["sha"]
    except Exception as e:
        print(f"   ⚠ Failed to fetch index.html: {e}")
        return

    def render_entry_html(item):
        days_old    = item.get("days_old", 7)
        credibility = item.get("credibility", "")
        if isinstance(days_old, (int, float)) and days_old == -1:
            fresh_label, fresh_bg, fresh_fg = "📅 Recent", "#F3F4F6", "#6B7280"
        elif isinstance(days_old, (int, float)) and days_old <= 3:
            fresh_label, fresh_bg, fresh_fg = (f"🟢 {int(days_old)}d ago" if days_old > 1 else "🟢 Today"), "#DCFCE7", "#166534"
        elif isinstance(days_old, (int, float)) and days_old <= 5:
            fresh_label, fresh_bg, fresh_fg = f"🟡 {int(days_old)}d ago", "#FEF3C7", "#92400E"
        else:
            fresh_label, fresh_bg, fresh_fg = f"🟠 ~{int(days_old)}d ago", "#FFEDD5", "#9A3412"

        cred_map = {
            "practitioner": ("👤 Practitioner", "#DCFCE7", "#166534"),
            "expert":       ("🎓 Expert",        "#DBEAFE", "#1E40AF"),
            "journalist":   ("📰 Journalist",    "#F3F4F6", "#374151"),
        }
        cred_text, cred_bg, cred_fg = cred_map.get(credibility, ("", "#F3F4F6", "#374151"))

        return f'''<div class="entry">
              <div class="entry-badges">
                <span class="entry-badge" style="background: {fresh_bg}; color: {fresh_fg};">{fresh_label}</span>
                <span class="entry-badge" style="background: {cred_bg}; color: {cred_fg};">{cred_text}</span>
              </div>
              <div class="entry-title"><a href="{item.get("source_url", "#")}">{item.get("title", "")}</a></div>
              <div class="entry-source">by {item.get("source_name", "Unknown")}{(" · " + item.get("author_role", "")) if item.get("author_role") and item.get("author_role") != "Unknown" else ""}</div>
              <div class="entry-summary">{item.get("summary", "")}</div>
              <div class="entry-takeaway"><strong>WHAT YOU'LL LEARN →</strong> <span>{item.get("takeaway", "")}</span></div>
              <div style="margin-top: 8px;"><a href="{item.get("source_url", "#")}" style="font-size: 0.78rem; font-weight: 600; color: #E07A5F; text-decoration: none;">Read the original →</a></div>
            </div>'''

    articles_html = "\n".join(render_entry_html(i) for i in digest.get("articles", []))

    import re as _re
    if articles_html:
        index_content = _re.sub(
            r'<div id="articles-entries">.*?<!--/articles-entries-->',
            f'<div id="articles-entries">\n{articles_html}\n            <!--/articles-entries-->',
            index_content, flags=_re.DOTALL
        )

    index_content = _re.sub(
        r'(<span class="latest-date" id="latest-date">)[^<]*(</span>)',
        f'\\1Week of {today_str}\\2',
        index_content
    )
    index_content = _re.sub(
        r'(<div id="digest-date-fallback" class="latest-date">)[^<]*(</div>)',
        f'\\1Week of {today_str}\\2',
        index_content
    )
    index_content = _re.sub(
        r'<div class="latest-date" id="latest-date">[^<]*</div>',
        f'<div class="latest-date" id="latest-date">Week of {today_str}</div>',
        index_content
    )

    new_issue_card = f'''<a href="issues/{issue_date}.html" class="issue-card">
            <div>
                <div class="issue-date">{today_str}</div>
                <div class="issue-title">FP&A Field Notes — {today_str}</div>
                <div class="issue-meta">{meta}</div>
            </div>
            <span class="issue-arrow">→</span>
        </a>
        '''

    if f"issues/{issue_date}.html" not in index_content:
        if '<div class="empty-state"' in index_content:
            index_content = _re.sub(
                r'<div class="empty-state"[^>]*>.*?</div>',
                new_issue_card,
                index_content, flags=_re.DOTALL
            )
        elif '<div id="issues-list">' in index_content:
            index_content = index_content.replace(
                '<div id="issues-list">',
                '<div id="issues-list">\n        ' + new_issue_card
            )

    index_b64  = base64.b64encode(index_content.encode("utf-8")).decode("utf-8")
    put_index  = json.dumps({
        "message": f"Update archive with {today_str} issue",
        "content": index_b64,
        "sha":     index_sha,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/index.html",
        data=put_index,
        headers={
            "Authorization": f"Bearer {PAGES_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PUT"
    )

    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            print("   ✓ Updated archive on index.html")
    except Exception as e:
        print(f"   ⚠ Failed to update index.html: {e}")


# ─── Update Knowledge Base ────────────────────────────────────────────────────

KB_CLASSIFY_PROMPT = """You maintain a knowledge base taxonomy for FP&A practitioners. You will receive:
1. The current taxonomy (a nested JSON tree)
2. New items from this week's digest

Your job: place each new item into the correct spot in the taxonomy tree. You may create new categories or subcategories as needed.

TAXONOMY RULES:
- The top-level node is "articles" with a label of "FP&A Field Notes"
- Below that, build a MECE (mutually exclusive, collectively exhaustive) hierarchy by topic
- Aim for 2-3 levels deep. Example paths: articles > ai_tools > excel_automation, articles > forecasting > rolling_forecasts
- Category keys should be lowercase_snake_case
- Category labels should be human-readable
- Don't create a subcategory for just one item — group it at the parent level until there are 2+ items that warrant splitting
- Each leaf category has an "entries" array of items

ENTRY FORMAT:
{
  "title": "Short descriptive title",
  "source_name": "Author or publication",
  "source_url": "https://...",
  "summary": "1 sentence — what the reader will learn",
  "date": "YYYY-MM-DD"
}

Return the COMPLETE updated taxonomy as valid JSON. No markdown fences. Include ALL existing entries — never remove old ones, only add new ones. If a new item duplicates an existing entry (same source URL), skip it."""


def render_knowledge_html(taxonomy: dict) -> str:
    """Render the taxonomy as a browsable HTML page."""

    def render_node(node, depth=0):
        html  = ""
        label    = node.get("label", "")
        entries  = node.get("entries", [])
        children = node.get("children", {})

        if depth == 0:
            html += f'<h2 style="font-family: Fraunces, serif; font-size: 1.5rem; margin: 40px 0 16px; padding-bottom: 8px; border-bottom: 3px solid #E07A5F;">{label}</h2>\n'
        elif depth == 1:
            html += f'<h3 style="font-family: Fraunces, serif; font-size: 1.15rem; margin: 28px 0 12px; color: #3D405B;">{label}</h3>\n'
        elif depth == 2:
            html += f'<h4 style="font-size: 1rem; font-weight: 600; margin: 20px 0 8px; color: #3D405B;">{label}</h4>\n'
        else:
            html += f'<h5 style="font-size: 0.9rem; font-weight: 600; margin: 16px 0 6px; color: #6b7280;">{label}</h5>\n'

        for entry in entries:
            html += f'''<div style="padding: 10px 14px; margin: 6px 0 6px {depth * 12}px; border-left: 3px solid #E8E4DE; background: white; border-radius: 0 8px 8px 0;">
  <a href="{entry.get('source_url', '#')}" style="font-size: 0.95rem; font-weight: 600; color: #1a1a2e; text-decoration: none; border-bottom: 1px solid #E07A5F;">{entry.get('title', '')}</a>
  <div style="font-size: 0.8rem; color: #8D8D8D; margin-top: 2px;">by {entry.get('source_name', 'Unknown')} · {entry.get('date', '')}</div>
  <p style="font-size: 0.88rem; color: #374151; margin: 4px 0 0; line-height: 1.5;">{entry.get('summary', '')}</p>
  <a href="{entry.get('source_url', '#')}" style="font-size: 0.78rem; font-weight: 600; color: #E07A5F; text-decoration: none; margin-top: 4px; display: inline-block;">Read the original →</a>
</div>\n'''

        for child_key in children:
            html += render_node(children[child_key], depth + 1)

        return html

    sections_html = ""
    if "articles" in taxonomy:
        sections_html = render_node(taxonomy["articles"], depth=0)

    kb_url = "https://fpafieldnotes.seacloudconsulting.com/knowledge.html"
    kb_desc = "A growing library of FP&A practitioner insights organized by topic — AI tools, Excel automation, forecasting, budgeting, and finance leadership. Updated weekly."

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FP&A Knowledge Base — Practitioner Insights by Topic</title>
    <meta name="description" content="{kb_desc}">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{kb_url}">
    <meta property="og:type" content="website">
    <meta property="og:title" content="FP&A Knowledge Base — Practitioner Insights by Topic">
    <meta property="og:description" content="{kb_desc}">
    <meta property="og:url" content="{kb_url}">
    <meta property="og:site_name" content="FP&A Field Notes">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="FP&A Knowledge Base — Practitioner Insights by Topic">
    <meta name="twitter:description" content="{kb_desc}">
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-DTR4KSL0LS"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', 'G-DTR4KSL0LS');
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,700;9..144,800&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body style="font-family: DM Sans, sans-serif; max-width: 720px; margin: 0 auto; padding: 24px; background: #FDFBF7; color: #1a1a2e;">
    <div style="margin-bottom: 32px;">
        <a href="./" style="font-size: 0.85rem; color: #E07A5F; text-decoration: none;">← Back to FP&A Field Notes</a>
    </div>
    <div style="margin-bottom: 40px;">
        <div style="font-size: 0.8rem; font-weight: 600; color: #E07A5F; text-transform: uppercase; letter-spacing: 0.05em;">FP&A Field Notes</div>
        <h1 style="font-family: Fraunces, serif; font-size: 2rem; margin: 8px 0 4px;">Knowledge Base</h1>
        <p style="color: #8D8D8D; font-size: 0.9rem;">A growing collection of practitioner insights, organized by topic. Updated weekly.</p>
    </div>
    {sections_html}
    <footer style="text-align: center; padding: 40px 0; margin-top: 40px; border-top: 1px solid #E8E4DE;">
        <p style="font-size: 0.82rem; color: #8D8D8D;">FP&A Field Notes — curated by AI, reviewed by humans.</p>
    </footer>
</body>
</html>'''


def update_knowledge_base(digest: dict):
    """Update the knowledge base with new items from the digest."""
    print("📚 Updating knowledge base...")

    if not PAGES_TOKEN or not GITHUB_USERNAME:
        print("   ⚠ PAGES_TOKEN or GITHUB_USERNAME not set — skipping")
        return

    repo       = f"{GITHUB_USERNAME}/fpa-field-notes"
    today_date = date.today().strftime("%Y-%m-%d")

    kb_content, kb_sha = fetch_github_file(repo, "knowledge_base.json", PAGES_TOKEN)
    if kb_content:
        try:
            taxonomy = json.loads(kb_content)
        except json.JSONDecodeError:
            print("   ⚠ Failed to parse knowledge_base.json, starting fresh")
            taxonomy = {
                "articles": {"label": "FP&A Field Notes", "children": {}},
            }
    else:
        print("   knowledge_base.json not found, creating new")
        taxonomy = {
            "articles": {"label": "FP&A Field Notes", "children": {}},
        }
        kb_sha = None

    def collect_urls(node):
        urls = set()
        for entry in node.get("entries", []):
            url = entry.get("source_url", "").rstrip("/").lower()
            if url:
                urls.add(url)
        for child in node.get("children", {}).values():
            urls.update(collect_urls(child))
        return urls

    existing_urls = set()
    if "articles" in taxonomy:
        existing_urls.update(collect_urls(taxonomy["articles"]))

    print(f"   {len(existing_urls)} existing URLs in knowledge base")

    new_items = []
    skipped   = 0
    for item in digest.get("articles", []):
        url = item.get("source_url", "").rstrip("/").lower()
        if url in existing_urls:
            skipped += 1
            print(f"   Skipping duplicate URL: {url}")
            continue
        new_items.append({
            "title":       item.get("title", ""),
            "source_name": item.get("source_name", ""),
            "source_url":  item.get("source_url", ""),
            "summary":     item.get("takeaway", item.get("summary", "")),
            "date":        today_date,
        })

    if skipped:
        print(f"   Skipped {skipped} items already in knowledge base")

    if not new_items:
        print("   No new items to add")
        return

    print(f"   {len(new_items)} new items to classify")

    user_msg = f"""Current taxonomy:
{json.dumps(taxonomy, indent=2, ensure_ascii=False)}

New items to classify:
{json.dumps(new_items, indent=2, ensure_ascii=False)}

Return the complete updated taxonomy with new items placed in the correct categories. Create subcategories as needed. Remember: never remove existing entries."""

    text = call_claude(KB_CLASSIFY_PROMPT, user_msg, max_tokens=6000)

    first_brace = text.find('{')
    last_brace  = text.rfind('}')
    if first_brace != -1 and last_brace != -1:
        clean = text[first_brace:last_brace + 1]
    else:
        clean = text.strip()

    try:
        updated_taxonomy = json.loads(clean)
    except json.JSONDecodeError:
        print("   ⚠ Failed to parse Claude's taxonomy response")
        return

    try:
        push_github_file(
            repo, "knowledge_base.json",
            json.dumps(updated_taxonomy, indent=2, ensure_ascii=False),
            PAGES_TOKEN, f"Update knowledge base — {today_date}", kb_sha
        )
        print("   ✓ Updated knowledge_base.json")
    except Exception as e:
        print(f"   ⚠ Failed to push knowledge_base.json: {e}")
        return

    kb_html = render_knowledge_html(updated_taxonomy)
    kb_html_content, kb_html_sha = fetch_github_file(repo, "knowledge.html", PAGES_TOKEN)

    try:
        push_github_file(
            repo, "knowledge.html", kb_html, PAGES_TOKEN,
            f"Update knowledge base page — {today_date}", kb_html_sha
        )
        print("   ✓ Updated knowledge.html")
    except Exception as e:
        print(f"   ⚠ Failed to push knowledge.html: {e}")


# ─── Save Published Entries for Dedup ────────────────────────────────────────

def save_published_entries(digest: dict):
    """Append this week's entries to published_entries.json for future dedup."""
    print("📋 Saving published entries for dedup...")

    if not PAGES_TOKEN or not GITHUB_USERNAME:
        print("   ⚠ PAGES_TOKEN or GITHUB_USERNAME not set — skipping")
        return

    repo       = f"{GITHUB_USERNAME}/fpa-field-notes"
    today_date = date.today().strftime("%Y-%m-%d")

    pf_content, pf_sha = fetch_github_file(repo, "published_entries.json", PAGES_TOKEN)
    if pf_content:
        try:
            entries = json.loads(pf_content)
        except json.JSONDecodeError:
            entries = []
    else:
        entries = []
        pf_sha  = None

    new_count = 0
    for item in digest.get("articles", []):
        entries.append({
            "title":          item.get("title", ""),
            "source_name":    item.get("source_name", ""),
            "source_url":     item.get("source_url", ""),
            "date_published": today_date,
        })
        new_count += 1

    if new_count == 0:
        print("   No new entries to save")
        return

    try:
        push_github_file(
            repo, "published_entries.json",
            json.dumps(entries, indent=2, ensure_ascii=False),
            PAGES_TOKEN, f"Update published entries — {today_date}", pf_sha
        )
        print(f"   ✓ Saved {new_count} entries (total: {len(entries)})")
    except Exception as e:
        print(f"   ⚠ Failed to save published entries: {e}")


# ─── No-Articles Notification ────────────────────────────────────────────────

def send_no_articles_email():
    """Send a brief notification when the inbox has no queued articles."""
    print("📧 Sending no-articles notification...")
    today_str = date.today().strftime("%B %d, %Y")
    subject   = f"FP&A Field Notes — No articles queued ({today_str})"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #1a1a1a; background: #f8f9fa;">
<div style="background: #7C3AED; color: white; padding: 20px; border-radius: 10px 10px 0 0;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: .1em; opacity: .7;">FP&A Field Notes</div>
  <h1 style="margin: 4px 0 0; font-size: 20px; font-weight: 700;">{today_str}</h1>
</div>
<div style="background: white; padding: 24px; border-radius: 0 0 10px 10px; border: 1px solid #e2e8f0; border-top: none;">
  <p style="font-size: 15px; color: #374151;">No articles queued this week — nothing to publish.</p>
  <p style="font-size: 13px; color: #6b7280;">Email article URLs to the inbox with subject <strong>FN</strong> to queue them for next week's digest.</p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())

    print(f"   ✓ Sent no-articles notification to {GMAIL_ADDRESS}")


# ─── Preview Mode ─────────────────────────────────────────────────────────────

def send_preview_email(filtered_tweets: list[dict], enriched_items: list[dict]):
    """
    Send a pre-evaluation preview email showing exactly what the pipeline
    found before Claude gets involved.  Set PREVIEW_MODE=true to use.
    """
    print("🔍 PREVIEW MODE: Sending raw pipeline output email...")

    today_str = date.today().strftime("%B %d, %Y")
    subject   = f"[PREVIEW] FP&A Field Notes pipeline — {today_str}"

    # ── Section A: Filtered tweets ────────────────────────────────────────────
    section_labels = {"tools": "🛠️ Tools & Efficiency", "fpa_practice": "📐 FP&A Practice"}
    tweets_by_section = {}
    for t in filtered_tweets:
        s = t.get("_section", "tools")
        tweets_by_section.setdefault(s, []).append(t)

    tweets_html = ""
    for section_key in ["tools", "fpa_practice"]:
        items = tweets_by_section.get(section_key, [])
        tweets_html += f"""
<h3 style="font-size:14px;font-weight:700;color:#111;margin:20px 0 8px;
           border-bottom:2px solid #e5e7eb;padding-bottom:4px;">
  {section_labels.get(section_key, section_key)} — {len(items)} tweets
</h3>"""
        for t in items:
            user      = t.get("user", {})
            handle    = user.get("screen_name", "?")
            followers = user.get("followers_count", 0)
            likes     = t.get("favorite_count", 0)
            rts       = t.get("retweet_count", 0)
            ratio     = t.get("_engagement_ratio", 0)
            text      = t.get("full_text", t.get("text", ""))[:280]
            urls      = [
                u.get("expanded_url", "")
                for u in t.get("entities", {}).get("urls", [])
                if u.get("expanded_url") and "twitter.com" not in u.get("expanded_url", "")
                   and "x.com" not in u.get("expanded_url", "")
            ]
            url_html = "".join(
                f'<div style="font-size:11px;margin-top:3px;">'
                f'<a href="{u}" style="color:#1A477A;">{u[:80]}</a></div>'
                for u in urls
            )
            tweets_html += f"""
<div style="padding:10px 12px;margin-bottom:8px;border:1px solid #e5e7eb;
            border-radius:6px;background:#fafafa;">
  <div style="font-size:11px;color:#6b7280;margin-bottom:4px;">
    <strong>@{handle}</strong> · {followers:,} followers ·
    ❤ {likes} · 🔁 {rts} · ratio {ratio:.4f}
  </div>
  <div style="font-size:13px;color:#111;line-height:1.5;">{text}</div>
  {url_html}
</div>"""

    # ── Section B: Enriched items (what Claude will see) ──────────────────────
    enriched_by_section = {}
    for item in enriched_items:
        s = item.get("section", "tools")
        enriched_by_section.setdefault(s, []).append(item)

    enriched_html = ""
    for section_key in ["tools", "fpa_practice"]:
        items = enriched_by_section.get(section_key, [])
        enriched_html += f"""
<h3 style="font-size:14px;font-weight:700;color:#111;margin:20px 0 8px;
           border-bottom:2px solid #e5e7eb;padding-bottom:4px;">
  {section_labels.get(section_key, section_key)} — {len(items)} articles
</h3>"""
        for item in items:
            is_fallback = item["article_snippet"].startswith("[Article could not be fetched")
            snippet_preview = item["article_snippet"][:400]
            fetch_badge = (
                '<span style="font-size:10px;background:#FEF3C7;color:#92400E;'
                'padding:2px 6px;border-radius:4px;margin-left:6px;">⚠ tweet fallback</span>'
                if is_fallback else
                '<span style="font-size:10px;background:#DCFCE7;color:#166534;'
                'padding:2px 6px;border-radius:4px;margin-left:6px;">✓ article fetched</span>'
            )
            enriched_html += f"""
<div style="padding:12px 14px;margin-bottom:10px;border:1px solid #e5e7eb;
            border-radius:6px;background:white;">
  <div style="font-size:12px;font-weight:700;color:#111;margin-bottom:4px;">
    {item['article_title'][:120]}{fetch_badge}
  </div>
  <div style="font-size:11px;color:#1A477A;margin-bottom:6px;">
    <a href="{item['article_url']}" style="color:#1A477A;">{item['article_url'][:100]}</a>
  </div>
  <div style="font-size:11px;color:#6b7280;margin-bottom:6px;">
    Shared by {item['tweet_author']} ({item['tweet_followers']:,} followers) ·
    ❤ {item['tweet_likes']} · 🔁 {item['tweet_retweets']}
  </div>
  <div style="font-size:11px;color:#374151;font-style:italic;
              background:#f8fafc;padding:8px;border-radius:4px;line-height:1.5;">
    {snippet_preview}…
  </div>
</div>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             max-width:700px;margin:0 auto;padding:20px;color:#1a1a1a;background:#f8f9fa;">

<div style="background:#7C3AED;color:white;padding:20px;border-radius:10px 10px 0 0;">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;opacity:.7;">
    FP&A Field Notes — PREVIEW MODE
  </div>
  <h1 style="margin:4px 0 0;font-size:20px;font-weight:700;">{today_str}</h1>
  <p style="margin:8px 0 0;font-size:13px;opacity:.8;">
    Pipeline output BEFORE Claude evaluation.
    {len(filtered_tweets)} tweets → {len(enriched_items)} enriched items.
  </p>
</div>

<div style="background:white;padding:24px;border-radius:0 0 10px 10px;
            border:1px solid #e2e8f0;border-top:none;">

  <h2 style="font-size:16px;font-weight:700;color:#111;margin:0 0 4px;">
    Stage A — Filtered Tweets ({len(filtered_tweets)} total, sorted by engagement ratio)
  </h2>
  <p style="font-size:12px;color:#6b7280;margin:0 0 12px;">
    These passed: has URL · ≤100K followers · not a vendor handle · ≥2 likes.
    Sorted by (likes + retweets×2) ÷ followers.
  </p>
  {tweets_html}

  <hr style="border:none;border-top:2px solid #e5e7eb;margin:28px 0;">

  <h2 style="font-size:16px;font-weight:700;color:#111;margin:0 0 4px;">
    Stage B — Enriched Items ({len(enriched_items)} total) — what Claude will evaluate
  </h2>
  <p style="font-size:12px;color:#6b7280;margin:0 0 12px;">
    Each unique article URL fetched. ⚠ tweet fallback = article unreachable
    (Cloudflare / paywall / JS-rendered).
  </p>
  {enriched_html}

</div>
<div style="text-align:center;padding:14px;font-size:11px;color:#94a3b8;">
  FP&A Field Notes — PREVIEW MODE · Pipeline stopped before Claude evaluation.
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())

    print(f"   ✓ Preview email sent to {GMAIL_ADDRESS}")
    print(f"   Pipeline stopped. Re-run without PREVIEW_MODE=true to produce the digest.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  FP&A Field Notes — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)
    print()

    missing = []
    if not INBOX_GMAIL_ADDRESS:      missing.append("INBOX_GMAIL_ADDRESS")
    if not INBOX_GMAIL_APP_PASSWORD: missing.append("INBOX_GMAIL_APP_PASSWORD")
    if not ANTHROPIC_API_KEY:        missing.append("ANTHROPIC_API_KEY")

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        return

    try:
        # Step 0: Check inbox for queued URLs
        print("📬 Step 0: Checking inbox for queued articles...")
        queued_urls = fetch_queued_urls()

        if not queued_urls:
            print("📬 No articles queued this week — sending notification and stopping.")
            send_no_articles_email()
            print("\n✅ Done.")
            return

        print(f"📬 Found {len(queued_urls)} queued URL(s)")

        # Load previously featured URLs for dedup
        previously_featured_urls: set[str] = set()
        if PAGES_TOKEN and GITHUB_USERNAME:
            repo = f"{GITHUB_USERNAME}/fpa-field-notes"
            pf_content, _ = fetch_github_file(repo, "published_entries.json", PAGES_TOKEN)
            if pf_content:
                try:
                    entries = json.loads(pf_content)
                    for e in entries:
                        url = e.get("source_url", "").rstrip("/").lower()
                        if url:
                            previously_featured_urls.add(url)
                    print(f"   {len(previously_featured_urls)} previously featured URLs loaded")
                except json.JSONDecodeError:
                    pass

        # Step 1: Process queued articles into digest entries
        digest = process_queued_articles(queued_urls, previously_featured_urls)

        total_items = len(digest.get("tools", [])) + len(digest.get("fpa_practice", []))
        if total_items == 0:
            print("⚠ No valid entries after processing — sending notification and stopping.")
            send_no_articles_email()
            return

        # Step 2: Send email + generate tweets
        tweets = generate_tweets(digest)

        # Step 3-4: Publish to website (optional)
        if PUBLISH_TO_WEB:
            publish_to_website(digest)
            publish_beehiiv_copy(digest)
            update_knowledge_base(digest)
        else:
            print("\n📋 Skipping website publish (PUBLISH_TO_WEB not set)")

        # Step 5: Always save published entries for dedup
        save_published_entries(digest)

        # Step 9: Save synthesis draft to repo
        save_synthesis_draft(synthesis_draft, entry_count=len(entries))

        # Step 10: Mark inbox emails as read (last — only after everything succeeds)
        mark_emails_as_read()

        print("\n✅ Pipeline complete!")
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
