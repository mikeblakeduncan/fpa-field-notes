#!/usr/bin/env python3
"""
generate_post.py — FP&A Field Notes weekly blog post generator.

Reads topics.md, finds this week's entry (keyed by the Monday of the current
week), calls the Anthropic API to write the post, generates an HTML file,
prepends the new entry to posts.json, then commits and pushes.

Environment variables:
  ANTHROPIC_API_KEY  — required, Anthropic API key
  FORCE_WEEK         — optional YYYY-MM-DD; use this date's Monday instead of today's
  OVERWRITE          — optional; set to "true" to overwrite an existing post
"""

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Writing style guide — embedded so GitHub Actions doesn't need local files.
# ---------------------------------------------------------------------------
STYLE_GUIDE = """\
# Writing Style Guide — Mike Duncan

**Last updated:** March 6, 2026
**Purpose:** This document defines Mike Duncan's writing voice and style. Use it whenever drafting content on Mike's behalf. Read the full document before writing anything.

---

## 1. Voice & Identity

Mike is an FP&A consultant (Sea Cloud Consulting) who provides fractional FP&A leadership and consulting services to growth-stage companies. He has 15+ years of experience leading FP&A teams at companies like Lyra Health, Teladoc, Included Health, and McKesson.

**His writing persona:** A senior finance practitioner who's been in the trenches and shares what he's actually learned. He doesn't overstate his credentials or pretend to have all the answers. He writes from experience, not from a pedestal.

**His relationship to the reader:** Depends on who's reading. With fellow FP&A directors and managers, he's peer-to-peer, talking like he's across the table over coffee. With CFOs and CEOs, the tone stays the same but the content should demonstrate sharp thinking and strategic awareness. Think of it as: always write something that would make a CFO think "this person gets it." Never write up or down to anyone.

**What makes his perspective different:** He bridges the gap between "old school" FP&A craft (modeling, planning processes, team building) and the new world of AI tools. Most people writing about AI in finance are either vendors selling something or junior people experimenting. Mike has the seniority to evaluate these tools against real enterprise complexity.

**What the reader should feel:** Informed. Maybe challenged. Like they just got a practical insight they can use this week.

---

## 2. Tone Rules

- Conversational but substantive. Not overly casual, not stiff.
- Confident on topics within his authority (FP&A leadership, modeling, team building, healthcare finance, board reporting). More exploratory on topics he's learning (AI tools, process automation, SaaS economics).
- Never preachy. Never talks down. Never lectures.
- Comfortable with business and financial language but doesn't hide behind jargon.
- Willing to take contrary positions if well-reasoned, even without hard data. Frame as "here's a view worth considering" rather than "everyone is wrong."
- Happy to admit past mistakes, but nothing too recent or that undermines credibility as a consultant.
- Dry humor is fine when it's natural. Never forced.

---

## 3. Sentence & Paragraph Patterns

- Mix of short punchy sentences and longer ones. The short sentences land the point. The longer ones set it up.
- Paragraphs are short. 2-4 sentences max. White space matters.
- Opens with one of these patterns (vary them):
  - **Personal anecdote:** "Last week, somebody asked me about..." / "I did something different..." (use when Mike has a real personal connection to the topic)
  - **Scenario setup:** "You're in FP&A and the CFO suddenly asks you to..." / "Imagine you just joined a company and..." (use when reacting to someone else's content or a general topic)
  - **Data/observation hook:** "Finance has seen an explosion of new software. The data suggests efficiency hasn't improved." (use for contrary takes)
- Closes with a clear takeaway or a forward-looking thought. No engagement-bait questions ("What do you think?", "Comment below!"). Let the piece stand on its own.
- Uses rhetorical questions occasionally within the body to guide the reader's thinking, but sparingly.
- **Always write complete sentences.** Fragments like "Nothing obviously broken." or "Good enough for now." may sound conversational, but they read as sloppy. If a thought is worth putting on the page, it's worth writing as a full sentence.

---

## 4. Vocabulary & Language

**Words and phrases Mike uses naturally:**
- "In the trenches"
- "The unlock is..."
- "Non-negotiable"
- "Drive value" / "drive results"
- "The goal of FP&A is to help the company make better decisions"
- "Start with..." / "The first goal is..."
- References to "the business" as a partner, not an adversary
- "I've seen various ways to..." (acknowledging multiple approaches)

**Words and phrases to AVOID:**
- See the full AI Anti-Patterns list in Section 8
- "Game-changer," "supercharge," "revolutionize," "unlock potential"
- "Leverage" as a verb (use "use" instead)
- "Stakeholders" when you can say "leaders" or "your CFO" or "the team"
- "Actionable insights" (just say what the insight is)
- "In today's rapidly evolving..." anything
- "It's important to note that..." (just say the thing)

**How he refers to himself:** "I" — direct, first person. No "we" unless referring to a specific team.

---

## 5. Structure Preferences

- **Length:** Driven by content. If the point takes 200 words, stop at 200. If it needs 800, that's fine. Never fill words.
- **Formatting:** Skimmable when it makes sense. Bold headers, short bullet points, numbered lists for sequential steps. But not everything should be a list post. If the piece is more of a narrative or opinion, let it flow as prose.
- **Emojis:** Use as section markers or accent (📉, 🛠️, 🔑, etc.). Keep to 3-5 per piece max. Don't use emojis inline within sentences.
- **Attribution:** When reacting to someone else's work, always credit the original author and source clearly. Frame as "building on" their work, not stealing it.
- **Bold text:** Use for section headers and occasional emphasis on key phrases. Don't bold full sentences.

---

## 6. Content Principles

- **Always need a point.** Every piece must answer: "Why should someone care about this?" If you can't answer that clearly, don't write it.
- **Experience over theory.** Prioritize "I did this and here's what happened" over "here are 5 best practices." When reacting to someone else's article, connect it to real-world implications.
- **Data when available, opinion when not.** Data makes a piece stronger, but a well-reasoned contrary take without data is fine. Frame it honestly: "I don't have data on this, but here's what I've observed."
- **Contrary takes are encouraged.** The prevailing LinkedIn narrative is often oversimplified. "AI is amazing" but nobody talks about the struggles. "New tools solve everything" but the data says efficiency hasn't improved. Find the gap between the hype and reality.
- **Don't be offensive, but be provocative.** Put a view out there. Let the reader wrestle with it.
- **Practical above all.** If a post doesn't give the reader something they can think about or do differently, it's not ready.

---

## 7. Channel-Specific Guidelines

### Blog Posts (FP&A Field Notes)

- **Length:** 300-800 words depending on topic. Don't pad.
- **Goal:** Establish editorial voice for the FP&A Field Notes brand. React to practitioner content with Mike's perspective. Build SEO over time.
- **Formatting:** More flowing than LinkedIn. Headers when they help structure, but not required. Prose paragraphs are fine. Keep skimmable where it makes sense.
- **Opening:** When reacting to an article, open with a scenario or observation hook (not "I read this article and..."). The editorial voice is slightly more removed than LinkedIn.
- **Voice:** Editorial style. Write as a commentator reacting to what's happening in the field, not as "I personally did this." Mike's experience informs the perspective but the writing is more editorial.
- **Attribution:** Always credit the original article, author, and source. Include a "Read the original" link. Frame as "building on" or "reacting to" their work.
- **Closing:** End with a clear takeaway or a forward-looking thought. No "comment below" or "what do you think?" questions.
- **Disclosure:** Footer on every post: "Directed by Mike Duncan, drafted by Claude."

---

## 8. AI Anti-Patterns — NEVER Do These

### Punctuation & Formatting
- **Never use em dashes (—).** Use commas, periods, or parentheses instead.
- **Never use semicolons in casual writing.** Break into two sentences.
- **Don't over-use colons to introduce lists.** Vary the lead-in.

### Sentence Patterns to Avoid
- **The "X, but Y" balancing act.** "AI is great at structure, but struggles with nuance." This is the most common AI tell. Rewrite as two separate thoughts or pick a side.
- **The staccato contrast.** "That is a start. It is not a standard." Two short back-to-back sentences used for rhetorical effect reads as AI rhythm. Combine into one sentence instead.
- **The triple structure.** "It's fast, efficient, and powerful." AI loves groups of three adjectives. Use one strong word instead.
- **"While X, Y" openers.** "While AI has made significant progress, challenges remain." This hedging pattern screams AI. Just say what you mean.
- **"It's worth noting that..."** Just say the thing.
- **"This is particularly relevant because..."** Cut and get to the point.
- **"Let's dive in" / "Let's explore" / "Let's unpack"** — never.
- **"In an era of..." / "In today's..." / "In the world of..."** — never.
- **"At its core..." / "At the end of the day..."** — avoid.
- **"The landscape of..."** — never.

### Word Choice to Avoid
- "Delve" / "delving"
- "Leverage" (as a verb)
- "Utilize" (just say "use")
- "Robust"
- "Seamless" / "seamlessly"
- "Comprehensive"
- "Facilitate"
- "Streamline" (unless describing a very specific process improvement)
- "Navigate" (as in "navigate challenges")
- "Landscape" (as in "the AI landscape")
- "Paradigm" / "paradigm shift"
- "Synergy" / "synergies"
- "Ecosystem" (unless literally talking about a tech ecosystem)
- "Holistic"
- "Cutting-edge"
- "Game-changer"
- "Supercharge"
- "Revolutionize"
- "Unlock potential"
- "Double-edged sword"
- "Interesting" or "fascinating" (be specific about why something matters)

### Structural Anti-Patterns
- **Don't write balanced "pros and cons" sections.** Take a position.
- **Don't use sentence fragments.** Every sentence needs a subject and a verb.
- **Don't use the staccato contrast pattern.** Combine short declarative back-to-back sentences or rewrite them.
- **Don't hedge every claim.** If you believe something, say it. Add nuance elsewhere.
- **Don't summarize what you just said** at the end. The reader just read it.
- **Don't open with a definition.** ("Financial planning and analysis, or FP&A, is...")
- **Don't write "In conclusion" or "To summarize."** Just end.
- **Don't use the word "I" in every sentence.** Vary sentence structure.

### Tone Anti-Patterns
- **Don't be relentlessly positive.** Real practitioners know things are hard. Acknowledge friction.
- **Don't use corporate-speak that Mike wouldn't say out loud.** If he wouldn't say it across a table, don't write it.
- **Don't be falsely humble.** "I'm no expert, but..." — if Mike knows the topic, own it.
- **Don't moralize.** No "we all need to..." or "it's our responsibility to..."
"""

# ---------------------------------------------------------------------------
# HTML page template.
# Placeholders use <<<NAME>>> syntax to avoid conflicts with CSS curly braces.
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><<<PAGE_TITLE>>></title>
    <meta name="description" content="<<<META_DESC>>>">
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-DTR4KSL0LS"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-DTR4KSL0LS');
    </script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,700;0,9..144,800;1,9..144,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --ink: #1a1a2e;
            --paper: #FDFBF7;
            --accent: #E07A5F;
            --sage: #81B29A;
            --navy: #3D405B;
            --warm-gray: #8D8D8D;
            --border: #E8E4DE;
            --card: #FFFFFF;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'DM Sans', sans-serif; background: var(--paper); color: var(--ink); line-height: 1.7; -webkit-font-smoothing: antialiased; }
        body::before { content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.03'/%3E%3C/svg%3E"); pointer-events: none; z-index: 100; }
        .container { max-width: 720px; margin: 0 auto; padding: 0 24px; }
        header { padding: 20px 0 16px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--border); margin-bottom: 32px; }
        .logo-mark { width: 36px; height: 36px; background: var(--accent); border-radius: 8px; transform: rotate(-3deg); position: relative; box-shadow: 2px 2px 0 var(--navy); flex-shrink: 0; }
        .logo-mark::after { content: '📐'; font-size: 18px; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
        .header-text h1 { font-family: 'Fraunces', serif; font-size: 1.25rem; font-weight: 800; color: var(--ink); line-height: 1.1; }
        .header-text h1 span { color: var(--accent); }
        .header-text p { font-size: 0.78rem; color: var(--warm-gray); margin-top: 1px; }
        .header-links { margin-left: auto; display: flex; gap: 16px; flex-shrink: 0; }
        .header-links a { font-size: 0.8rem; font-weight: 600; color: var(--navy); text-decoration: none; }
        article { margin-bottom: 48px; }
        .post-meta { font-size: 0.82rem; color: var(--warm-gray); margin-bottom: 12px; }
        .post-meta strong { color: var(--navy); }
        .post-title { font-family: 'Fraunces', serif; font-size: 2rem; font-weight: 800; line-height: 1.2; margin-bottom: 20px; color: var(--ink); }
        .post-tags { margin-bottom: 24px; }
        .post-tag { display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 3px 10px; border-radius: 4px; background: #FEF3C7; color: #92400E; margin-right: 6px; }
        .post-body p { font-size: 0.95rem; margin-bottom: 16px; color: #374151; }
        .post-body h2 { font-family: 'Fraunces', serif; font-size: 1.3rem; font-weight: 700; margin: 32px 0 12px; color: var(--ink); }
        .post-body a { color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--accent); }
        .tip-list { margin: 24px 0 32px; }
        .tip { display: flex; gap: 16px; margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }
        .tip:last-child { border-bottom: none; margin-bottom: 0; }
        .tip-number { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 800; color: var(--accent); flex-shrink: 0; line-height: 1.3; width: 28px; }
        .tip-content strong { display: block; font-size: 0.95rem; font-weight: 700; color: var(--ink); margin-bottom: 4px; }
        .tip-content p { font-size: 0.92rem; color: #374151; margin-bottom: 0; line-height: 1.6; }
        .post-footer { padding: 20px 0; border-top: 1px solid var(--border); margin-top: 32px; font-size: 0.8rem; color: var(--warm-gray); line-height: 1.6; }
        footer { text-align: center; padding: 28px 0; border-top: 1px solid var(--border); }
        footer p { font-size: 0.76rem; color: var(--warm-gray); line-height: 1.8; }
        footer a { color: var(--accent); text-decoration: none; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <a href="../index.html" style="text-decoration: none; display: flex; align-items: center; gap: 14px;">
            <div class="logo-mark"></div>
            <div class="header-text">
                <h1>FP&amp;A <span>Field Notes</span></h1>
                <p>Fresh practitioner insights every Tuesday.</p>
            </div>
        </a>
        <div class="header-links">
            <a href="../index.html">Home</a>
            <a href="../knowledge.html">📚 Knowledge Base</a>
        </div>
    </header>

    <article>
        <div class="post-meta"><strong>Mike Duncan</strong> · <<<DISPLAY_DATE>>> · <<<READ_TIME>>> min read</div>
        <h1 class="post-title"><<<POST_TITLE>>></h1>
        <div class="post-tags">
            <<<TAGS_HTML>>>
        </div>
        <div class="post-body">
<<<BODY_HTML>>>
        </div>
        <div class="post-footer">
            Directed by Mike Duncan, drafted by Claude.
            <br><br>
            <a href="../blog.html" style="font-size: 0.82rem; font-weight: 600; color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--accent);">View all posts &rarr;</a>
        </div>
    </article>
</div>

<footer>
    <div class="container">
        <p>FP&amp;A Field Notes &mdash; curated by AI, reviewed by humans.</p>
        <p>A <a href="https://seacloudconsulting.com">Sea Cloud Consulting</a> project.</p>
    </div>
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_monday_of(d: date) -> date:
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def parse_topics(path: Path, target_monday: date) -> "str | None":
    """Return the body text of the entry matching target_monday, or None."""
    content = path.read_text(encoding="utf-8")
    target_str = target_monday.strftime("%Y-%m-%d")

    # Split on '## YYYY-MM-DD' section headers
    parts = re.split(r"^## (\d{4}-\d{2}-\d{2})\s*$", content, flags=re.MULTILINE)

    for i in range(1, len(parts), 2):
        if parts[i].strip() == target_str:
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            return body

    return None


def title_to_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def format_date(d: date) -> str:
    """Return e.g. 'April 1, 2026' (no leading zero on day)."""
    return d.strftime(f"%B {d.day}, %Y")


def estimate_read_time(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    words = len(text.split())
    return max(1, round(words / 200))


def esc(s: str) -> str:
    """Escape a plain-text string for insertion into HTML."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def call_claude(topic_content: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are writing a blog post for FP&A Field Notes (fpafieldnotes.seacloudconsulting.com).
This is a weekly blog by Mike Duncan, a fractional FP&A consultant at Sea Cloud Consulting.

STYLE GUIDE — read every section carefully before writing. This is the sole source of truth for voice, tone, and structure:
---
{STYLE_GUIDE}
---

THIS WEEK'S TOPIC:
---
{topic_content}
---

Write a complete blog post based on the topic above. Follow the style guide exactly, especially the AI Anti-Patterns section.

Return ONLY a valid JSON object with no surrounding markdown, no code fences, no extra text. Use these exact fields:
{{
  "title": "The post title as plain text (no HTML entities)",
  "slug": "kebab-case-url-slug derived from the title",
  "excerpt": "One to two sentence summary for the post listing page. Plain text, no HTML.",
  "tags": ["Tag1", "Tag2", "Tag3"],
  "body_html": "<p>Full HTML content of the post body...</p>"
}}

Rules for body_html:
- Include only the inner content: paragraphs, headers, lists. No outer wrappers, no <article>, no <h1> title (it renders separately).
- Use <p> for paragraphs.
- Use <h2> for section headers when they help (include a relevant emoji in the header text).
- Use <strong> for emphasis on key phrases within sentences. Do not bold full sentences.
- For numbered tip/point lists, use this structure:
  <div class="tip-list">
    <div class="tip">
      <div class="tip-number">1</div>
      <div class="tip-content">
        <strong>Point title.</strong>
        <p>Explanation here.</p>
      </div>
    </div>
  </div>
- Target 300-800 words. Stop when the point is made.
- Use proper HTML entities in body text where needed (& becomes &amp;, etc.).
- The post footer ("Directed by Mike Duncan, drafted by Claude.") is added automatically — do not include it.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()

    # Try direct parse first (Claude often returns clean JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to extracting JSON block from surrounding text
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        print(f"ERROR: No JSON found in Claude response:\n{text[:800]}")
        sys.exit(1)

    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON parse failed ({e}).\nRaw response:\n{text[:800]}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def build_html(data: dict, post_date: date) -> str:
    title = data["title"]
    excerpt = data["excerpt"]
    body_html = data["body_html"]
    tags = data.get("tags", [])

    display_date = format_date(post_date)
    read_time = str(estimate_read_time(body_html))
    tags_html = "\n            ".join(
        f'<span class="post-tag">{esc(t)}</span>' for t in tags
    )

    html = _HTML_TEMPLATE
    html = html.replace("<<<PAGE_TITLE>>>", f"{esc(title)} \u2014 FP&amp;A Field Notes")
    html = html.replace("<<<META_DESC>>>", esc(excerpt))
    html = html.replace("<<<POST_TITLE>>>", esc(title))
    html = html.replace("<<<DISPLAY_DATE>>>", display_date)
    html = html.replace("<<<READ_TIME>>>", read_time)
    html = html.replace("<<<TAGS_HTML>>>", tags_html)
    html = html.replace("<<<BODY_HTML>>>", body_html)
    return html


# ---------------------------------------------------------------------------
# posts.json update
# ---------------------------------------------------------------------------

def update_posts_json(posts_path: Path, new_entry: dict) -> None:
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    posts.insert(0, new_entry)
    posts_path.write_text(
        json.dumps(posts, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def remove_from_posts_json(posts_path: Path, slug: str) -> None:
    """Remove all entries with the given slug from posts.json."""
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    posts = [p for p in posts if p.get("slug") != slug]
    posts_path.write_text(
        json.dumps(posts, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Git commit & push
# ---------------------------------------------------------------------------

def git_commit_push(files: list, message: str) -> None:
    def run(*args):
        subprocess.run(list(args), check=True, cwd=REPO_ROOT)

    run("git", "config", "user.email", "github-actions[bot]@users.noreply.github.com")
    run("git", "config", "user.name", "github-actions[bot]")
    for f in files:
        run("git", "add", str(f))
    run("git", "commit", "-m", message)
    run("git", "push")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # --- Read environment variables ---
    force_week_str = os.environ.get("FORCE_WEEK", "").strip()
    overwrite = os.environ.get("OVERWRITE", "").strip().lower() == "true"

    # Determine target Monday
    if force_week_str:
        try:
            anchor = date.fromisoformat(force_week_str)
        except ValueError:
            print(f"ERROR: FORCE_WEEK '{force_week_str}' is not a valid YYYY-MM-DD date.")
            sys.exit(1)
        target_monday = get_monday_of(anchor)
        print(f"FORCE_WEEK set: using week of {target_monday} (from '{force_week_str}')")
    else:
        target_monday = get_monday_of(date.today())
        print(f"Week of: {target_monday}")

    topics_path = REPO_ROOT / "topics.md"
    if not topics_path.exists():
        print("ERROR: topics.md not found in repo root.")
        sys.exit(1)

    topic_content = parse_topics(topics_path, target_monday)
    if topic_content is None:
        print(f"No entry in topics.md for week of {target_monday}. Nothing to do.")
        sys.exit(0)

    post_date = date.today()
    posts_path = REPO_ROOT / "posts.json"

    existing_posts = json.loads(posts_path.read_text(encoding="utf-8"))

    # We need the slug before generating the post to check for existence.
    # Run a quick title extraction pass — or generate and check after.
    # Strategy: generate first, then handle overwrite logic.

    print("Calling Claude to write the post...")
    data = call_claude(topic_content)

    slug = (data.get("slug") or title_to_slug(data["title"])).strip()
    if not slug:
        slug = title_to_slug(data["title"])

    html_path = REPO_ROOT / "blog" / f"{slug}.html"
    already_exists = any(p.get("slug") == slug for p in existing_posts)

    if already_exists:
        if not overwrite:
            print(f"Post '{slug}' already exists in posts.json. Set OVERWRITE=true to replace it.")
            sys.exit(0)

        # Remove old entry from posts.json and delete old HTML file
        print(f"OVERWRITE=true: removing existing post '{slug}'...")
        remove_from_posts_json(posts_path, slug)
        if html_path.exists():
            html_path.unlink()
            print(f"Deleted: {html_path}")

    html = build_html(data, post_date)
    html_path.write_text(html, encoding="utf-8")
    print(f"Wrote: {html_path}")

    new_entry = {
        "title": data["title"],
        "slug": slug,
        "date": post_date.strftime("%Y-%m-%d"),
        "excerpt": data["excerpt"],
        "tags": data.get("tags", []),
    }
    update_posts_json(posts_path, new_entry)
    print(f"Updated: {posts_path}")

    git_commit_push(
        [html_path, posts_path],
        f"{'Republish' if already_exists else 'Add'} weekly post: {data['title']}",
    )
    print("Done.")


if __name__ == "__main__":
    main()
