# Fashion Sustained

**Sustainability intelligence for fashion, beauty & fragrance.**

A self-updating news dashboard that tracks sustainability across three verticals —
fashion, beauty and fragrance — and organises the coverage by region and sector.
Static front end, no build step, no server: a scheduled job refreshes the data
twice a day and the page reads it on load.

**Live:** https://sukisamara.github.io/<your-repo-name>/

---

## How it works

1. **`fetch_news.py`** runs on a schedule and pulls stories from two places:
   - a curated set of trade publications (Business of Fashion, Vogue Business,
     Sourcing Journal, Ecotextile News, BeautyMatter and more) via their RSS
     feeds, with a Google News `site:` fallback if a feed is dead;
   - Google News regional editions (US, UK, India and others) for wider regional
     breadth.
2. Every story is filtered and classified against a **200-term keyword set**
   (50 terms each for Fashion, Beauty, Fragrance and Regulation), tagged with a
   region (Global / NAM / EMEA / APAC), and de-duplicated by title.
3. Results are written to **`data.json`** as a **rolling 90-day archive** — each
   run merges new stories in, drops anything older than 90 days, and keeps the
   last good file if a fetch fails.
4. **`index.html`** fetches `data.json` on load and renders the region-by-region
   grid, with sector and region filters plus a headline search.

Everything in `fetch_news.py` is Python standard library only — nothing to
`pip install`.

## Automation

`.github/workflows/update-news.yml` runs `fetch_news.py` twice daily via GitHub
Actions (11:00 and 23:00 UTC) and commits the updated `data.json` back to the
repo. It also has a **Run workflow** button for manual refreshes. GitHub Pages
then serves the updated file.

## Run it locally

```bash
python3 fetch_news.py     # writes / updates data.json
```

Then open `index.html` in a browser (or serve the folder with
`python3 -m http.server` and visit http://localhost:8000).

## Project structure

```
.
├── index.html                       # front end (reads data.json)
├── data.json                        # generated news archive (rolling 90 days)
├── fetch_news.py                    # fetch + classify + write data.json
├── logo.png                         # masthead logo
└── .github/
    └── workflows/
        └── update-news.yml          # twice-daily GitHub Actions job
```

## Design notes

- **Palette:** deep forest `#203409` with mint `#E4F4D7` — high contrast, single
  accent, everything else quiet.
- **Type:** Fraunces (display, with its `SOFT` + `WONK` axes dialled up to echo
  the hand-drawn logo) paired with Inter for all UI and body text.
- **Fallback tiles:** cards without an image get a striped mint tile carrying the
  source name, so the grid stays even.

---

Built by [Suki Samara](https://github.com/sukisamara).
