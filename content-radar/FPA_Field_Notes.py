#!/usr/bin/env python3
"""
FP&A Field Notes — Weekly FP&A Practitioner Digest
=====================================================
Searches for practitioner-written content across two sections:
  1. Tools & Efficiency — AI tools, Excel tricks, automation workflows
  2. FP&A Practice — budgeting, forecasting, strategic planning, leadership

Runs weekly (Tuesday). Uses Twitter/X search via SocialData API to find
URLs that practitioners are sharing, then fetches and evaluates the articles.

Environment variables:
  ANTHROPIC_API_KEY    - From console.anthropic.com
  SOCIALDATA_API_KEY   - From socialdata.tools
  GMAIL_ADDRESS        - Your Gmail address
  GMAIL_APP_PASSWORD   - Gmail App Password (not your regular password)
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
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
PAGES_TOKEN        = os.environ.get("PAGES_TOKEN", "")
GITHUB_USERNAME    = os.environ.get("GITHUB_USERNAME", "")
PUBLISH_TO_WEB     = os.environ.get("PUBLISH_TO_WEB", "false").lower() == "true"

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
VENDOR_BLOCKLIST_FILE = os.environ.get("VENDOR_BLOCKLIST_FILE", "vendor_blocklist.txt")

# Twitter handles of known FP&A vendors to filter out
TWITTER_VENDOR_HANDLES = {
    "anaaborysova", "planfulinc", "datarailshq", "cubefinance", "venasolutions",
    "jedoxag", "prophix", "workdayinc", "oraclecloud", "sapanalytics",
    "adaptiveplan", "pigmenthq", "abacumhq", "onestreaamsw",
    "adaptiveinsights", "onestreamsw", "vena_solutions", "planful",
    "datarails", "cube_finance",
}


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


# ─── Search Queries (hardcoded, rotating weekly) ──────────────────────────────

def generate_queries() -> list[dict]:
    """Return hardcoded Twitter search queries, rotating weekly."""
    week = date.today().isocalendar()[1]

    all_queries = [
        # ── Tools & Efficiency ──────────────────────────────────────────────
        {
            "query": '(FP&A OR "financial planning" OR "finance team") (AI OR ChatGPT OR Claude OR automation OR Excel OR Python) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "tools",
        },
        {
            "query": '(CFO OR "VP Finance" OR "finance leader") (workflow OR tool OR built OR automated OR saved) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "tools",
        },
        {
            "query": '"FP&A" (Excel OR "Power BI" OR Python OR SQL) (trick OR tip OR formula OR built OR automated) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "tools",
        },
        {
            "query": '("finance analyst" OR "FP&A analyst") (AI OR ChatGPT OR Claude) (use OR using OR built OR tried OR saved) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "tools",
        },
        {
            "query": '(CFO OR "finance director" OR controller) (automation OR workflow OR model OR reporting) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "tools",
        },
        # ── FP&A Practice ───────────────────────────────────────────────────
        {
            "query": '(budget OR forecast OR "variance analysis" OR "board deck" OR "headcount plan") filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "fpa_practice",
        },
        {
            "query": '(CFO OR "FP&A" OR "finance director") (lessons OR learned OR mistake OR changed OR approach) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "fpa_practice",
        },
        {
            "query": '("FP&A" OR "financial planning") (stakeholder OR "business partner" OR board OR executive) (presentation OR communicate OR influence OR pushback) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "fpa_practice",
        },
        {
            "query": '(CFO OR "VP Finance" OR "FP&A director") ("rolling forecast" OR "zero-based" OR "driver-based" OR scenario OR planning) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "fpa_practice",
        },
        {
            "query": '("finance leader" OR CFO OR "FP&A") (career OR leadership OR team OR hire OR manage) filter:links filter:has_engagement -filter:nativeretweets lang:en within_time:7d',
            "section": "fpa_practice",
        },
    ]

    tools_queries    = [q for q in all_queries if q["section"] == "tools"]
    practice_queries = [q for q in all_queries if q["section"] == "fpa_practice"]

    # Rotate start index by week number; pick 2-3 from each section
    n_tools    = 3 if week % 2 == 0 else 2
    n_practice = 2 if week % 2 == 0 else 3

    selected = []
    tools_start    = week % len(tools_queries)
    practice_start = week % len(practice_queries)

    for i in range(n_tools):
        selected.append(tools_queries[(tools_start + i) % len(tools_queries)])
    for i in range(n_practice):
        selected.append(practice_queries[(practice_start + i) % len(practice_queries)])

    print(f"🔍 Step 1: Using {len(selected)} Twitter search queries (week {week})")
    return selected


# ─── Twitter Search ───────────────────────────────────────────────────────────

def search_twitter(query: str, max_pages: int = 5) -> list[dict]:
    """Search Twitter via SocialData API. Returns a list of tweet objects."""
    tweets = []
    next_cursor = None
    ctx = ssl.create_default_context()

    for page in range(max_pages):
        params = {"query": query, "type": "Latest"}
        if next_cursor:
            params["cursor"] = next_cursor

        url = "https://api.socialdata.tools/twitter/search?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {SOCIALDATA_API_KEY}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            },
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            print(f"   ⚠ Twitter search error (page {page + 1}): {e}")
            break

        page_tweets = data.get("tweets", [])
        if not page_tweets:
            break

        tweets.extend(page_tweets)
        next_cursor = data.get("next_cursor")
        if not next_cursor:
            break

    return tweets


def run_searches(queries: list[dict]) -> list[dict]:
    """Run all Twitter queries and return deduplicated, section-tagged tweets."""
    print(f"🌐 Step 2: Searching Twitter ({len(queries)} queries)...")

    all_tweets = {}          # tweet_id → tweet object
    tweet_section_map = {}   # tweet_id → section

    for i, q in enumerate(queries):
        query_text = q.get("query", "")
        section    = q.get("section", "tools")
        print(f"   [{i + 1}/{len(queries)}] {section}: searching...")

        page_tweets = search_twitter(query_text)
        print(f"   → {len(page_tweets)} tweets")

        for tweet in page_tweets:
            tweet_id = tweet.get("id_str") or str(tweet.get("id", ""))
            if tweet_id and tweet_id not in all_tweets:
                all_tweets[tweet_id] = tweet
                tweet_section_map[tweet_id] = section

    print(f"   ✓ {len(all_tweets)} unique tweets across all queries")

    tweet_list = []
    for tweet_id, tweet in all_tweets.items():
        tweet["_section"] = tweet_section_map.get(tweet_id, "tools")
        tweet_list.append(tweet)

    return tweet_list


# ─── Tweet Filtering ──────────────────────────────────────────────────────────

def filter_tweets(tweets: list[dict]) -> list[dict]:
    """
    Remove noise and rank remaining tweets by engagement-to-follower ratio.
    Returns top 75 tweets, each with extracted article URLs attached.
    """
    filtered = []

    for tweet in tweets:
        # Must have at least one URL
        urls = tweet.get("entities", {}).get("urls", [])
        if not urls:
            continue

        user      = tweet.get("user", {})
        followers = user.get("followers_count", 0)
        handle    = user.get("screen_name", "").lower()

        # Skip very large accounts — user already sees them
        if followers > 100_000:
            continue

        # Skip known vendor handles
        if handle in TWITTER_VENDOR_HANDLES:
            continue

        # Minimum engagement signal
        if tweet.get("favorite_count", 0) < 2:
            continue

        faves    = tweet.get("favorite_count", 0)
        retweets = tweet.get("retweet_count", 0)
        ratio    = (faves + retweets * 2) / max(followers, 1)
        tweet["_engagement_ratio"] = ratio

        filtered.append(tweet)

    filtered.sort(key=lambda t: t["_engagement_ratio"], reverse=True)
    top = filtered[:75]

    print(f"   ✓ {len(top)} tweets after filtering (from {len(tweets)} raw)")
    return top


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
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
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
        # Common CMS / blog class names for the post body
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
        "snippet": text[:2000],
    }


def build_enriched_items(filtered_tweets: list[dict]) -> list[dict]:
    """
    For each filtered tweet, resolve its linked URLs and fetch article metadata.
    Returns a list of enriched items ready for Claude evaluation.
    """
    print("📰 Step 3: Fetching linked articles...")

    # Collect unique expanded URLs, mapped back to tweet data
    url_to_tweet = {}  # expanded_url → tweet
    for tweet in filtered_tweets:
        for url_obj in tweet.get("entities", {}).get("urls", []):
            expanded = url_obj.get("expanded_url", "") or url_obj.get("url", "")
            # Skip Twitter own links (profile pages, tweet links, etc.)
            if not expanded or "twitter.com" in expanded or "x.com" in expanded or "t.co" == expanded[:4]:
                continue
            if expanded not in url_to_tweet:
                url_to_tweet[expanded] = tweet

    print(f"   {len(url_to_tweet)} unique article URLs to fetch")

    enriched = []
    fetched  = 0
    for url, tweet in url_to_tweet.items():
        meta = fetch_article_metadata(url)
        if not meta:
            continue

        user = tweet.get("user", {})
        enriched.append({
            "section":          tweet.get("_section", "tools"),
            "tweet_text":       tweet.get("full_text", tweet.get("text", "")),
            "tweet_author":     f"@{user.get('screen_name', '')}",
            "tweet_likes":      tweet.get("favorite_count", 0),
            "tweet_retweets":   tweet.get("retweet_count", 0),
            "tweet_followers":  user.get("followers_count", 0),
            "article_url":      url,
            "article_title":    meta["title"],
            "article_snippet":  meta["snippet"],
        })
        fetched += 1

    print(f"   ✓ {fetched} articles successfully fetched")
    return enriched


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


# ─── Evaluate Results ─────────────────────────────────────────────────────────

EVAL_SYSTEM_PROMPT = """Evaluate a set of articles discovered via Twitter and build a practitioner digest for an FP&A professional. Be highly selective — only include genuinely useful, practitioner-written content.

Each item you receive includes:
- The tweet that linked to the article (for context on who shared it and why)
- The article URL, title, and a text snippet from the actual article

Your job is to evaluate the ARTICLE content, not the tweet. The tweet is just discovery context.

The results come from two sections:
- "tools": AI tools, Excel, automation — practitioners describing what they actually use
- "fpa_practice": the craft and human side of FP&A — budgeting, forecasting, variance analysis, strategic planning, stakeholder management, presenting to boards, leadership

HARD FILTERS — reject ANY content that:
- Is authored by a COMPANY rather than a NAMED INDIVIDUAL. If the byline is a company name (e.g. "Vena Solutions", "FinSmart", "Anthropic") rather than a person's name, reject it. Real practitioners have names.
- Is from any of these blocked vendors: {vendor_blocklist}
- Is from any software vendor, SaaS company, AI provider, or outsourced services company website — even if not on the blocklist above
- Is a "best tools" or "top 10" listicle
- Is generic advice with no specific examples or personal experience
- Is purely theoretical with no actionable takeaway

PREVIOUSLY FEATURED — skip any content that is the SAME ARTICLE by the SAME AUTHOR that appears in the list below. Be specific: two different authors writing about the same topic are NOT duplicates. Only skip if it's clearly the same piece (same author + same article).
{previously_featured}

PRIORITIZE:
- First-person practitioner accounts ("I did this," "here's what worked")
- Specific techniques, workflows, or frameworks described in detail
- Podcast episodes or blog posts from named finance practitioners
- Content with a clear "you can try this" takeaway
- The most recently published content available

Return valid JSON only, no markdown fences:
{
  "tools": [
    {
      "title": "Article/episode title",
      "source_name": "Author or publication",
      "source_url": "https://...",
      "author_role": "CFO at Series B SaaS company",
      "credibility": "practitioner",
      "days_old": 2,
      "summary": "2 sentences max. What they did and what happened.",
      "takeaway": "1 sentence. What the reader will learn from this piece."
    }
  ],
  "fpa_practice": [
    {
      "title": "...",
      "source_name": "...",
      "source_url": "...",
      "author_role": "...",
      "credibility": "...",
      "days_old": 3,
      "summary": "2 sentences max.",
      "takeaway": "1 sentence. What the reader will learn."
    }
  ]
}

RULES:
- Maximum 3 items per section. Fewer is fine if quality is low.
- ONE source per item. Use the article_url as source_url. Do not invent URLs.
- "days_old" is a number: how many days ago the content was published. Estimate from any date clues in the article snippet. If only "this week," use 4. If "this month," use 14. If completely unclear but content seems recent, use -1 (displayed as "Recent").
- Prefer content from the last 7 days. Content up to 14 days old is acceptable. Do NOT include anything with days_old greater than 14 (except -1 for unclear dates).
- "author_role" is a brief description of who wrote it (e.g. "VP of FP&A at healthcare company"). If unknown, write "Unknown".
- "credibility" rates the source:
  - "practitioner" — written by a CFO, VP Finance, FP&A Director, Controller, or finance team member sharing their own experience. HIGHEST credibility.
  - "expert" — written by a known consultant, analyst, researcher, or educator with finance domain expertise.
  - "journalist" — written by a reporter or publication covering finance.
  - "marketing" — written by or for a software vendor. REJECT these — do not include.
- If a section truly has nothing worth sharing, return an empty array."""


def evaluate_results(enriched_items: list[dict]) -> dict:
    """Evaluate enriched tweet+article items and produce the digest."""
    print("📊 Step 4: Evaluating articles and building digest...")

    # Fetch previously featured entries for dedup
    previously_featured = "None yet."
    previously_featured_urls = set()
    previously_featured_domains = {}
    if PAGES_TOKEN and GITHUB_USERNAME:
        repo = f"{GITHUB_USERNAME}/fpa-field-notes"
        pf_content, _ = fetch_github_file(repo, "published_entries.json", PAGES_TOKEN)
        if pf_content:
            try:
                entries = json.loads(pf_content)
                if entries:
                    for e in entries:
                        url = e.get("source_url", "").rstrip("/").lower()
                        if url:
                            previously_featured_urls.add(url)
                            try:
                                domain = url.split("//")[1].split("/")[0].replace("www.", "")
                            except (IndexError, AttributeError):
                                domain = ""
                            if domain:
                                previously_featured_domains[domain] = previously_featured_domains.get(domain, 0) + 1

                    lines = [
                        f'- "{e.get("title", "")}" by {e.get("source_name", "")} — {e.get("source_url", "")}'
                        for e in entries[-30:]
                    ]
                    previously_featured = "\n".join(lines)
                    print(f"   {len(previously_featured_urls)} previously featured URLs loaded")

                    repeat_sources = [
                        f"{d} ({c}x)" for d, c in sorted(previously_featured_domains.items(), key=lambda x: -x[1])
                        if c >= 2
                    ]
                    if repeat_sources:
                        print(f"   Frequent sources: {', '.join(repeat_sources[:5])}")
            except json.JSONDecodeError:
                pass

    # HARD PRE-FILTER: Remove items whose article URL is already featured
    if previously_featured_urls:
        original_count = len(enriched_items)
        enriched_items = [
            item for item in enriched_items
            if item.get("article_url", "").rstrip("/").lower() not in previously_featured_urls
        ]
        removed = original_count - len(enriched_items)
        if removed:
            print(f"   Pre-filtered {removed} already-featured URL(s)")

    if not enriched_items:
        print("   ⚠ No new items to evaluate after dedup")
        return {"tools": [], "fpa_practice": []}

    # Build prompt
    vendors = read_vendor_blocklist()
    vendor_list = ", ".join(vendors) if vendors else "None specified"
    eval_prompt = (
        EVAL_SYSTEM_PROMPT
        .replace("{previously_featured}", previously_featured)
        .replace("{vendor_blocklist}", vendor_list)
    )

    # Add repeat source warning
    if previously_featured_domains:
        repeat_warning = "\n\nSOURCES TO DEPRIORITIZE — these sources have appeared multiple times recently. Strongly prefer NEW sources:\n"
        for domain, count in sorted(previously_featured_domains.items(), key=lambda x: -x[1]):
            if count >= 2:
                repeat_warning += f"- {domain} (featured {count} times)\n"
        eval_prompt = eval_prompt.replace("PRIORITIZE:", repeat_warning + "\nPRIORITIZE:")

    # Trim snippets to save tokens
    trimmed = []
    for item in enriched_items:
        trimmed.append({
            "section":         item.get("section", ""),
            "tweet_text":      item.get("tweet_text", "")[:280],
            "tweet_author":    item.get("tweet_author", ""),
            "tweet_likes":     item.get("tweet_likes", 0),
            "tweet_followers": item.get("tweet_followers", 0),
            "article_url":     item.get("article_url", ""),
            "article_title":   item.get("article_title", ""),
            "article_snippet": item.get("article_snippet", "")[:1200],
        })

    results_text = json.dumps(trimmed, indent=2, ensure_ascii=False)
    user_msg = f"Evaluate these articles and produce the practitioner digest:\n\n{results_text}"

    text = call_claude(eval_prompt, user_msg, max_tokens=4000)

    # Extract JSON
    first_brace = text.find('{')
    last_brace  = text.rfind('}')
    if first_brace != -1 and last_brace != -1:
        clean = text[first_brace:last_brace + 1]
    else:
        clean = text.strip()

    try:
        digest = json.loads(clean)
    except json.JSONDecodeError:
        print("   ⚠ JSON parse failed, attempting repair...")
        for fix in ['"}]}', ']}', '}']:
            try:
                digest = json.loads(clean + fix)
                break
            except json.JSONDecodeError:
                continue
        else:
            digest = {"tools": [], "fpa_practice": [], "_raw_text": text[:3000]}

    tools_count    = len(digest.get("tools", []))
    practice_count = len(digest.get("fpa_practice", []))
    print(f"   ✓ Digest: {tools_count} tools, {practice_count} FP&A practice")

    # HARD POST-FILTER: catch any duplicate URLs Claude included anyway
    if previously_featured_urls:
        for section in ["tools", "fpa_practice"]:
            original = digest.get(section, [])
            filtered = [
                item for item in original
                if item.get("source_url", "").rstrip("/").lower() not in previously_featured_urls
            ]
            removed = len(original) - len(filtered)
            if removed:
                print(f"   ⚠ Post-filter removed {removed} duplicate(s) from {section}")
            digest[section] = filtered

        final_tools    = len(digest.get("tools", []))
        final_practice = len(digest.get("fpa_practice", []))
        if final_tools != tools_count or final_practice != practice_count:
            print(f"   Final count: {final_tools} tools, {final_practice} FP&A practice")

    # HARD POST-FILTER: drop any item with a missing or placeholder summary
    _bad_summary_phrases = (
        "unable to provide",
        "no summary available",
        "could not summarize",
        "navigation elements",
        "no content available",
        "page content",
    )
    for section in ["tools", "fpa_practice"]:
        before = digest.get(section, [])
        after  = []
        for item in before:
            summary = (item.get("summary") or "").strip()
            if not summary:
                print(f"   ⚠ Dropped (empty summary): {item.get('title', 'untitled')}")
                continue
            if len(summary) < 30:
                print(f"   ⚠ Dropped (summary too short): {item.get('title', 'untitled')}")
                continue
            if any(phrase in summary.lower() for phrase in _bad_summary_phrases):
                print(f"   ⚠ Dropped (bad summary): {item.get('title', 'untitled')}")
                continue
            after.append(item)
        digest[section] = after

    return digest


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
  <div style="padding: 8px 12px; background: #F0F9FF; border-radius: 6px; border: 1px solid #BAE6FD;">
    <span style="font-size: 11px; font-weight: 700; color: #0369A1;">WHAT YOU'LL LEARN →</span>
    <span style="font-size: 13px; color: #0C4A6E; margin-left: 4px;">{item.get('takeaway', '')}</span>
  </div>
</div>"""
    return html


def format_html_email(digest: dict) -> str:
    """Build the full HTML email."""
    today_str = date.today().strftime("%B %d, %Y")

    sections = [
        ("🛠️ Tools & Efficiency", "tools",        "#F59E0B", "AI, Excel, automation — practitioners sharing what actually works"),
        ("📐 FP&A Practice",      "fpa_practice",  "#22C55E", "The craft and human side — budgeting, forecasting, leadership, stakeholder management"),
    ]

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; color: #1a1a1a; background: #f8f9fa;">

<div style="background: #7C3AED; color: white; padding: 24px; border-radius: 12px 12px 0 0;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; opacity: 0.7;">FP&A Field Notes</div>
  <h1 style="margin: 4px 0 0; font-size: 22px; font-weight: 700;">{today_str}</h1>
  <p style="margin: 8px 0 0; font-size: 13px; opacity: 0.8;">What finance practitioners shared this week — tools, skills, and craft.</p>
</div>

<div style="background: white; padding: 24px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
"""

    for label, key, color, subtitle in sections:
        items = digest.get(key, [])
        html += f"""
<h2 style="font-size: 16px; font-weight: 700; color: #111; margin: 24px 0 4px; padding-bottom: 8px; border-bottom: 3px solid {color};">{label}</h2>
<p style="font-size: 12px; color: #6b7280; margin: 0 0 14px;">{subtitle}</p>
"""
        if items:
            html += render_section_items(items)
        else:
            html += '<p style="font-size: 13px; color: #94a3b8; font-style: italic; margin: 8px 0 16px;">Nothing strong enough this week. Quality over quantity.</p>'

    raw = digest.get("_raw_text", "")
    if raw:
        html += f"""
<h2 style="font-size: 16px; font-weight: 700; color: #111; margin: 24px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #EF4444;">Raw Output (parsing failed)</h2>
<pre style="font-size: 12px; background: #f8fafc; padding: 16px; border-radius: 8px; white-space: pre-wrap; word-wrap: break-word;">{raw}</pre>"""

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
2. One summary tweet per section — ties together the themes from that section

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

SUMMARY TWEETS:
- Frame as "This week in FP&A Field Notes..." or "3 practitioner takes worth reading this week..."
- Make clear you're curating other people's insights

Return valid JSON only, no markdown fences:
[
  {
    "type": "entry",
    "section": "tools" or "fpa_practice",
    "title": "Original article title",
    "source_name": "Author name",
    "tweet": "The tweet text (without the link — it will be appended automatically)"
  },
  {
    "type": "summary",
    "section": "tools" or "fpa_practice",
    "tweet": "The summary tweet text"
  }
]"""


def generate_tweets(digest: dict, website_url: str = None) -> list[dict]:
    """Generate tweets for each digest entry and section summaries."""
    print("🐦 Step 5b: Generating tweets...")

    items_for_prompt = []
    for section in ["tools", "fpa_practice"]:
        for item in digest.get(section, []):
            items_for_prompt.append({
                "section":     section,
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

    section_labels = {"tools": "🛠️ Tools & Efficiency", "fpa_practice": "📐 FP&A Practice"}

    html = """
<div style="margin-top: 32px; padding-top: 24px; border-top: 3px solid #1DA1F2;">
  <h2 style="font-size: 18px; font-weight: 700; color: #1DA1F2; margin: 0 0 4px;">🐦 Ready-to-Tweet</h2>
  <p style="font-size: 12px; color: #94a3b8; margin: 0 0 20px;">Click the button to open Twitter with the tweet pre-filled. Edit if needed, then post.</p>
"""

    for section_key in ["tools", "fpa_practice"]:
        section_tweets = [t for t in tweets if t.get("section") == section_key]
        if not section_tweets:
            continue

        html += f'<h3 style="font-size: 14px; font-weight: 700; color: #111; margin: 20px 0 12px;">{section_labels.get(section_key, section_key)}</h3>'

        for i, tweet in enumerate(section_tweets):
            tweet_text = tweet.get("tweet", "")
            is_summary = tweet.get("type") == "summary"

            full_tweet  = f"{tweet_text}\n\n{website_url}" if website_url else tweet_text
            encoded     = urllib.parse.quote(full_tweet, safe='')
            intent_url  = f"https://twitter.com/intent/tweet?text={encoded}"

            label       = "📊 Section Summary" if is_summary else tweet.get("title", f"Tweet {i + 1}")
            badge_bg    = "#E8F5FD" if is_summary else "#F8FAFC"
            badge_border = "#1DA1F2" if is_summary else "#E2E8F0"

            html += f"""
  <div style="padding: 14px; margin-bottom: 10px; border: 1px solid {badge_border}; border-radius: 8px; background: {badge_bg};">
    <div style="font-size: 11px; font-weight: 600; color: #64748B; margin-bottom: 6px;">{label}</div>
    <p style="font-size: 13px; color: #1a1a1a; line-height: 1.5; margin: 0 0 10px;">{tweet_text}</p>
    <a href="{intent_url}" style="display: inline-block; padding: 8px 20px; background: #1DA1F2; color: white; font-size: 13px; font-weight: 700; text-decoration: none; border-radius: 20px;">🐦 Tweet This</a>
  </div>"""

    html += "\n</div>"
    return html


def send_email(digest: dict, tweets: list[dict] = None):
    """Send the formatted digest via Gmail SMTP."""
    print("📧 Step 5: Sending email...")

    today_str  = date.today().strftime("%B %d, %Y")
    subject    = f"FP&A Field Notes — {today_str}"
    html_body  = format_html_email(digest)

    if tweets:
        tweet_html = format_tweet_email_section(tweets)
        html_body  = html_body.replace(
            '<div style="text-align: center; padding: 16px; font-size: 11px; color: #94a3b8;">',
            tweet_html + '\n<div style="text-align: center; padding: 16px; font-size: 11px; color: #94a3b8;">'
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS

    plain = f"FP&A Field Notes — {today_str}\n\n{json.dumps(digest, indent=2, ensure_ascii=False)}"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())

    print(f"   ✓ Sent to {GMAIL_ADDRESS}")


# ─── Beehiiv Newsletter Copy ──────────────────────────────────────────────────

def generate_beehiiv_html(digest: dict) -> str:
    """Generate a clean HTML version of the digest for pasting into Beehiiv."""
    today_str = date.today().strftime("%B %d, %Y")

    sections = [
        ("🛠️ Tools & Efficiency", "tools",       "#E07A5F"),
        ("📐 FP&A Practice",      "fpa_practice", "#81B29A"),
    ]

    html = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; color: #1a1a2e;">
"""

    for label, key, color in sections:
        items = digest.get(key, [])
        html += f'<h2 style="font-size: 17px; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 3px solid {color};">{label}</h2>\n'

        if not items:
            html += '<p style="font-size: 14px; color: #94a3b8; font-style: italic;">Nothing strong enough this week.</p>\n'
            continue

        for item in items:
            source_name = item.get("source_name", "")
            source_url  = item.get("source_url", "#")
            html += f"""<div style="padding: 14px 16px; border: 1px solid #e5e7eb; border-radius: 8px; background: white; margin-bottom: 10px;">
  <div style="font-size: 15px; font-weight: 600; margin-bottom: 4px;"><a href="{source_url}" style="color: #1a1a2e; text-decoration: none;">{item.get('title', '')}</a></div>
  <div style="font-size: 12px; color: #8D8D8D; margin-bottom: 8px;">by {source_name}</div>
  <p style="font-size: 14px; color: #374151; line-height: 1.6; margin: 0 0 10px;">{item.get('summary', '')}</p>
  <div style="padding: 8px 12px; background: #F0F9FF; border-radius: 6px; border: 1px solid #BAE6FD;">
    <span style="font-size: 11px; font-weight: 700; color: #0369A1;">WHAT YOU'LL LEARN →</span>
    <span style="font-size: 13px; color: #0C4A6E; margin-left: 4px;">{item.get('takeaway', '')}</span>
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
    print("📰 Step 6b: Saving Beehiiv newsletter copy...")

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


# ─── Step 6: Generate and Publish Web Page ────────────────────────────────────

def generate_issue_page(digest: dict) -> str:
    """Generate a standalone HTML page for this week's issue."""
    today_str  = date.today().strftime("%B %d, %Y")

    sections = [
        ("🛠️ Tools & Efficiency", "tools",       "#E07A5F"),
        ("📐 FP&A Practice",      "fpa_practice", "#81B29A"),
    ]

    items_html = ""
    for label, key, color in sections:
        items = digest.get(key, [])
        items_html += f'<h2 style="font-family: Fraunces, serif; font-size: 1.4rem; margin: 36px 0 16px; padding-bottom: 8px; border-bottom: 3px solid {color};">{label}</h2>'

        if not items:
            items_html += '<p style="color: #8D8D8D; font-style: italic;">Nothing strong enough this week.</p>'
            continue

        for item in items:
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
  <div style="padding: 10px 14px; background: #F0F9FF; border-radius: 8px; border: 1px solid #BAE6FD; margin-bottom: 10px;">
    <span style="font-size: 0.75rem; font-weight: 700; color: #0369A1;">WHAT YOU\'LL LEARN →</span>
    <span style="font-size: 0.88rem; color: #0C4A6E; margin-left: 4px;">{item.get("takeaway", "")}</span>
  </div>
  <a href="{source_url}" style="font-size: 0.85rem; font-weight: 600; color: #E07A5F; text-decoration: none;">Read the original →</a>
</div>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FP&A Field Notes — {today_str}</title>
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
        <p style="color: #8D8D8D; font-size: 0.9rem;">Weekly practitioner insights — tools, soft skills, and craft.</p>
    </div>
    {items_html}
    <footer style="text-align: center; padding: 40px 0; margin-top: 40px; border-top: 1px solid #E8E4DE;">
        <p style="font-size: 0.82rem; color: #8D8D8D;">FP&A Field Notes — curated by AI, reviewed by humans.</p>
        <p style="font-size: 0.82rem; color: #8D8D8D; margin-top: 4px;"><a href="../" style="color: #E07A5F;">Subscribe</a> for weekly updates.</p>
    </footer>
</body>
</html>'''


def count_items(digest: dict) -> str:
    """Return a summary like '3 tools · 2 FP&A practice'"""
    parts = []
    t = len(digest.get("tools", []))
    p = len(digest.get("fpa_practice", []))
    if t: parts.append(f"{t} tools")
    if p: parts.append(f"{p} FP&amp;A practice")
    return " · ".join(parts) if parts else "No items"


def publish_to_website(digest: dict):
    """Push the issue page to the fpa-field-notes repo via GitHub API."""
    print("🌐 Step 6: Publishing to website...")

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

    tools_html    = "\n".join(render_entry_html(i) for i in digest.get("tools", []))
    practice_html = "\n".join(render_entry_html(i) for i in digest.get("fpa_practice", []))

    import re as _re
    if tools_html:
        index_content = _re.sub(
            r'<div id="tools-entries">.*?<!--/tools-entries-->',
            f'<div id="tools-entries">\n{tools_html}\n            <!--/tools-entries-->',
            index_content, flags=_re.DOTALL
        )

    if practice_html:
        index_content = _re.sub(
            r'<div id="practice-entries">.*?<!--/practice-entries-->',
            f'<div id="practice-entries">\n{practice_html}\n            <!--/practice-entries-->',
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


# ─── Step 7: Update Knowledge Base ───────────────────────────────────────────

KB_CLASSIFY_PROMPT = """You maintain a knowledge base taxonomy for FP&A practitioners. You will receive:
1. The current taxonomy (a nested JSON tree)
2. New items from this week's digest

Your job: place each new item into the correct spot in the taxonomy tree. You may create new categories or subcategories as needed.

TAXONOMY RULES:
- The top 2 nodes are fixed: "tools", "fpa_practice"
- Below that, build a MECE (mutually exclusive, collectively exhaustive) hierarchy
- Aim for 2-4 levels deep. Example path: fpa_practice > analytical_techniques > variance_analysis
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
    for key in ["tools", "fpa_practice"]:
        if key in taxonomy:
            sections_html += render_node(taxonomy[key], depth=0)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FP&A Field Notes — Knowledge Base</title>
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
    print("📚 Step 7: Updating knowledge base...")

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
                "tools":        {"label": "🛠️ Tools & Efficiency", "children": {}},
                "fpa_practice": {"label": "📐 FP&A Practice",      "children": {}},
            }
    else:
        print("   knowledge_base.json not found, creating new")
        taxonomy = {
            "tools":        {"label": "🛠️ Tools & Efficiency", "children": {}},
            "fpa_practice": {"label": "📐 FP&A Practice",      "children": {}},
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
    for section_key in ["tools", "fpa_practice"]:
        if section_key in taxonomy:
            existing_urls.update(collect_urls(taxonomy[section_key]))

    print(f"   {len(existing_urls)} existing URLs in knowledge base")

    new_items = []
    skipped   = 0
    for section in ["tools", "fpa_practice"]:
        for item in digest.get(section, []):
            url = item.get("source_url", "").rstrip("/").lower()
            if url in existing_urls:
                skipped += 1
                print(f"   Skipping duplicate URL: {url}")
                continue
            new_items.append({
                "section":     section,
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


# ─── Step 8: Save Published Entries for Dedup ────────────────────────────────

def save_published_entries(digest: dict):
    """Append this week's entries to published_entries.json for future dedup."""
    print("📋 Step 8: Saving published entries for dedup...")

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
    for section in ["tools", "fpa_practice"]:
        for item in digest.get(section, []):
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


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  FP&A Field Notes — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)
    print()

    missing = []
    if not ANTHROPIC_API_KEY:  missing.append("ANTHROPIC_API_KEY")
    if not SOCIALDATA_API_KEY: missing.append("SOCIALDATA_API_KEY")
    if not GMAIL_ADDRESS:      missing.append("GMAIL_ADDRESS")
    if not GMAIL_APP_PASSWORD: missing.append("GMAIL_APP_PASSWORD")

    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        return

    try:
        # Step 1: Generate Twitter search queries
        queries = generate_queries()

        # Step 2: Search Twitter via SocialData API
        raw_tweets = run_searches(queries)

        # Step 3: Filter tweets and fetch linked articles
        filtered = filter_tweets(raw_tweets)
        enriched = build_enriched_items(filtered)

        # Step 4: Evaluate articles with Claude
        digest = evaluate_results(enriched)

        # Step 5: Send email + generate tweets
        tweets = generate_tweets(digest)
        send_email(digest, tweets)

        # Step 6-7: Publish to website (optional)
        if PUBLISH_TO_WEB:
            publish_to_website(digest)
            publish_beehiiv_copy(digest)
            update_knowledge_base(digest)
        else:
            print("\n📋 Skipping website publish (PUBLISH_TO_WEB not set)")

        # Step 8: Always save published entries for dedup
        save_published_entries(digest)

        print("\n✅ Pipeline complete!")
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
