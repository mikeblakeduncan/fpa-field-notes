# FP&A Field Notes

Static site for [fpafieldnotes.seacloudconsulting.com](https://fpafieldnotes.seacloudconsulting.com).  
Published by [Sea Cloud Consulting](https://seacloudconsulting.com).

## Site structure

| File | Purpose |
|---|---|
| `index.html` | Homepage — intro + 3 most recent blog posts |
| `blog.html` | Blog listing — published posts + upcoming placeholders |
| `knowledge.html` | Knowledge Base — topic-organized reference content |
| `post-template.html` | Template for new blog posts (copy into `blog/`) |
| `style.css` | Shared stylesheet for all pages |
| `posts.json` | Blog post metadata (title, slug, date, excerpt, tags) |
| `blog/` | Individual post HTML files |
| `issues/` | Archived weekly digest pages (no longer linked; accessible by direct URL) |

## Adding a new post

1. Copy `post-template.html` to `blog/your-post-slug.html`
2. Fill in the title, date, read time, tags, body content, and related posts
3. Add an entry to `posts.json` — the homepage and blog listing pull from this file
4. Commit and push

## GitHub Actions workflows — DISABLED

The `.github/workflows/` directory contains two workflow files that have been
**intentionally disabled** as part of the April 2026 site rebuild:

- **`fpa_field_notes.yml`** — The weekly digest pipeline. Checked a Gmail inbox for
  article URLs, sent content to the Claude API to generate summaries, published
  digest pages to `issues/`, and updated `published_entries.json`.

- **`weekly-post.yml`** — The automated blog post generator. Ran every Tuesday and
  used the Claude API to draft a weekly blog post.

Both workflows have had their `schedule:` trigger removed. They will not run
automatically. They are kept in the repo for reference only — **do not re-enable
the schedule triggers**. The content pipeline has been replaced by a manual
editorial workflow: write posts directly and publish via `posts.json`.

The Gmail inbox used for article submission (`INBOX_GMAIL_ADDRESS`) can be
abandoned — no need to delete it, just stop checking it.
