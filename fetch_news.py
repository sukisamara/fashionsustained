#!/usr/bin/env python3
"""
Fashion Sustained — sustainability intelligence for fashion, beauty & fragrance.

Sourcing model (v4):
  * CURATED SOURCES — a hand-picked list of credible trade publications and
    hubs. We pull each one's RSS feed (direct article URLs + inline images);
    if a feed is missing or dead, we fall back to a Google News `site:` query
    for that same domain so the source still contributes.
  * REGIONAL EDITIONS — Google News editions (US, UK, India, …) run a broad
    sustainability query to keep the regional dashboard (NAM/LATAM/EMEA/APAC)
    populated with wider coverage.

Everything is filtered and classified against a 200-term keyword set (50 per
sector: Fashion / Beauty / Fragrance / Regulation).

Images: taken from the RSS item (media:content / thumbnail / enclosure) when
present, otherwise fetched from the article's own og:image. Cards with no image
fall back to a styled tile in the front end.

Archive: this script keeps a ROLLING 90-DAY window. Each run loads the existing
data.json, merges in new stories, de-duplicates, drops anything older than 90
days, and writes it back — so the archive grows and self-prunes over time.

Standard library only. A total fetch failure leaves the last good data.json in
place rather than wiping it.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

# ============================================================================
# KEYWORDS — 50 per sector. Used to (a) filter feeds to on-topic stories and
# (b) classify each story into a sector.
# ============================================================================

SECTOR_KEYWORDS = {
    "Fashion": [
        "sustainable fashion", "circular fashion", "fashion circularity",
        "slow fashion", "ethical fashion", "eco fashion", "fashion sustainability",
        "textile recycling", "textile-to-textile recycling", "fiber-to-fiber recycling",
        "textile waste", "garment recycling", "clothing recycling", "deadstock fabric",
        "upcycled fashion", "upcycling clothing", "secondhand clothing", "resale fashion",
        "fashion resale", "rental fashion", "clothing rental", "pre-loved fashion",
        "thrifting", "recommerce", "recycled polyester", "recycled cotton",
        "organic cotton", "regenerative cotton", "regenerative agriculture fashion",
        "recycled wool", "hemp fabric", "sustainable linen", "next-gen materials",
        "mycelium leather", "vegan leather", "plant-based leather", "lab-grown leather",
        "biodegradable textiles", "bio-based materials", "closed-loop fashion",
        "zero-waste fashion", "fashion take-back", "garment durability",
        "clothing repair", "fashion overproduction", "fast fashion waste",
        "microplastics textiles", "waterless dyeing", "natural dyes", "low-impact dyeing",
    ],
    "Beauty": [
        "sustainable beauty", "clean beauty", "green beauty", "blue beauty",
        "refillable beauty", "beauty refills", "refillable packaging",
        "refill station beauty", "waterless beauty", "solid cosmetics",
        "plastic-free beauty", "plastic-free packaging", "recyclable beauty packaging",
        "mono-material packaging", "PCR packaging beauty", "post-consumer recycled beauty",
        "aluminium beauty packaging", "glass beauty packaging", "biodegradable beauty",
        "compostable beauty packaging", "beauty packaging waste", "zero-waste beauty",
        "cruelty-free beauty", "vegan beauty", "sustainable skincare", "sustainable makeup",
        "sustainable haircare", "clean ingredients", "biotech beauty",
        "lab-grown beauty ingredients", "upcycled beauty ingredients", "carbon neutral beauty",
        "beauty sustainability", "ethical beauty", "responsibly sourced mica",
        "palm oil free beauty", "microbead free", "reef-safe sunscreen",
        "sustainable deodorant", "refillable lipstick", "beauty recycling program",
        "connected packaging beauty", "beauty EPR", "cosmetic packaging sustainability",
        "waterless shampoo", "concentrated beauty formulas", "clean fragrance",
        "sustainable cosmetics", "beauty circular economy", "sustainable personal care",
    ],
    "Fragrance": [
        "sustainable fragrance", "sustainable perfume", "eco-friendly perfume",
        "natural perfume", "green fragrance", "refillable perfume", "refillable fragrance",
        "perfume refill", "upcycled fragrance ingredients", "upcycled perfume",
        "green chemistry fragrance", "green chemistry perfume", "biotech fragrance",
        "biotech perfume ingredients", "lab-grown fragrance", "fermentation fragrance",
        "precision fermentation scent", "biodegradable fragrance", "renewable carbon fragrance",
        "responsibly sourced sandalwood", "sustainable sandalwood", "sustainable vanilla",
        "sustainable patchouli", "sustainable vetiver", "ethical oud",
        "ethical sourcing perfume", "natural isolates", "captive molecules",
        "carbon neutral fragrance", "IFRA sustainability", "Givaudan sustainability",
        "Firmenich sustainability", "dsm-firmenich sustainability", "IFF sustainability",
        "Symrise sustainability", "sustainable perfumery", "perfume packaging sustainability",
        "refillable perfume bottle", "waterless perfume", "fragrance supply chain",
        "regenerative fragrance ingredients", "CO2 extraction perfume",
        "supercritical extraction fragrance", "essential oil sustainability",
        "botanical sourcing perfume", "fragrance biodiversity", "scent sustainability",
        "eco perfume packaging", "clean perfume", "conscious fragrance",
    ],
    "Regulation": [
        "fashion greenwashing", "beauty greenwashing", "greenwashing claims",
        "anti-greenwashing", "Green Claims Directive", "EU green claims",
        "EPR textiles", "extended producer responsibility textiles", "textile EPR",
        "digital product passport", "DPP textiles", "Ecodesign for Sustainable Products",
        "ESPR", "EU textile strategy", "unsold goods destruction ban",
        "waste framework directive", "packaging waste regulation", "PPWR",
        "PFAS apparel", "PFAS ban textiles", "forever chemicals clothing",
        "CSRD fashion", "CSDDD", "corporate sustainability due diligence",
        "supply chain transparency", "supply chain traceability", "forced labour fashion",
        "forced labour cotton", "living wage garment", "garment worker rights",
        "deforestation-free fashion", "EUDR", "science-based targets fashion",
        "net zero fashion", "carbon disclosure fashion", "Scope 3 emissions fashion",
        "textile labelling rules", "recycled content mandate", "AGEC law",
        "California textile EPR", "SB707", "New York Fashion Act",
        "microplastics regulation", "chemical management textiles", "ZDHC",
        "Higg Index", "ESG fashion", "sustainability reporting fashion",
        "import ban fast fashion", "circular economy regulation",
    ],
}

INDUSTRY_ORDER = ["Fashion", "Beauty", "Fragrance", "Regulation"]

# lowercased keyword -> sector, for filtering + classification
KW_INDEX = {}
for _sector, _kws in SECTOR_KEYWORDS.items():
    for _kw in _kws:
        KW_INDEX.setdefault(_kw.lower(), _sector)

# Compact query used for site:/edition searches (keeps URLs a sane length).
BROAD_TERMS = [
    "sustainable fashion", "circular fashion", "textile recycling",
    "resale", "vegan leather", "sustainable beauty", "clean beauty",
    "refillable packaging", "sustainable fragrance", "sustainable perfume",
    "greenwashing", "extended producer responsibility", "digital product passport",
    "recycled polyester", "secondhand",
]


def classify(text):
    """Return the best-matching sector for a headline, or None if off-topic."""
    t = " " + text.lower() + " "
    scores = {s: 0 for s in INDUSTRY_ORDER}
    for kw, sector in KW_INDEX.items():
        if kw in t:
            scores[sector] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


# ============================================================================
# SOURCES
# ============================================================================

# Curated publications. feed=None => use a Google News site: query instead.
# region is the source's home band (most trade press is treated as Global).
CURATED_SOURCES = [
    {"name": "Business of Fashion", "domain": "businessoffashion.com", "feed": None, "region": "Global"},
    {"name": "Vogue Business", "domain": "voguebusiness.com", "feed": None, "region": "Global"},
    {"name": "Sourcing Journal", "domain": "sourcingjournal.com", "feed": "https://sourcingjournal.com/feed/", "region": "Global"},
    {"name": "Ecotextile News", "domain": "ecotextile.com", "feed": "https://www.ecotextile.com/index.php?format=feed&type=rss", "region": "Global"},
    {"name": "Fashion Revolution", "domain": "fashionrevolution.org", "feed": "https://www.fashionrevolution.org/feed/", "region": "Global"},
    {"name": "Ellen MacArthur Foundation", "domain": "ellenmacarthurfoundation.org", "feed": None, "region": "Global"},
    {"name": "Textile Exchange", "domain": "textileexchange.org", "feed": "https://textileexchange.org/feed/", "region": "Global"},
    # Beauty & fragrance — added so those verticals aren't empty:
    {"name": "BeautyMatter", "domain": "beautymatter.com", "feed": "https://beautymatter.com/rss.xml", "region": "Global"},
    {"name": "Cosmetics Business", "domain": "cosmeticsbusiness.com", "feed": None, "region": "Global"},
    {"name": "Premium Beauty News", "domain": "premiumbeautynews.com", "feed": None, "region": "Global"},
    {"name": "Cosmetics Design", "domain": "cosmeticsdesign.com", "feed": None, "region": "Global"},
]

# Google News editions -> macro-region, for regional breadth.
EDITIONS = [
    ("US", "en-US", "US", "NAM"), ("CA", "en-CA", "CA", "NAM"),
    ("MX", "en-US", "MX", "LATAM"),
    ("GB", "en-GB", "GB", "EMEA"), ("ZA", "en-ZA", "ZA", "EMEA"),
    ("AE", "en-US", "AE", "EMEA"),
    ("IN", "en-IN", "IN", "APAC"), ("AU", "en-AU", "AU", "APAC"),
    ("SG", "en-SG", "SG", "APAC"),
]

REGION_ORDER = ["Global", "NAM", "LATAM", "EMEA", "APAC"]

TIME_WINDOW = "when:14d"        # feeds/queries lookback (archive retains 90d)
RETENTION_DAYS = 90
MAX_PER_REGION = 45
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5
USER_AGENT = "Mozilla/5.0 (compatible; TheLoop-Dashboard/1.0)"

ENABLE_ENRICHMENT = True
ENRICH_PER_REGION = 14
ENRICH_TIMEOUT = 8

MEDIA_NS = "{http://search.yahoo.com/mrss/}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


# --- Networking --------------------------------------------------------------


def fetch(url, cap=None, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(cap) if cap else resp.read()


def norm(title):
    return "".join(c.lower() for c in title if c.isalnum())


def parse_date(raw):
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


# --- RSS parsing (generic + Google News) ------------------------------------


def _text(node, tag):
    el = node.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def rss_image(item):
    """Pull an image URL from common RSS conventions."""
    for tag in (MEDIA_NS + "content", MEDIA_NS + "thumbnail"):
        el = item.find(tag)
        if el is not None and el.get("url"):
            return el.get("url")
    enc = item.find("enclosure")
    if enc is not None and enc.get("url") and "image" in (enc.get("type") or "image"):
        return enc.get("url")
    ce = item.find(CONTENT_NS + "encoded")
    if ce is not None and ce.text:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', ce.text)
        if m:
            return m.group(1)
    return ""


def parse_rss(xml_bytes, is_gnews=False):
    root = ElementTree.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = unescape(_text(item, "title"))
        link = _text(item, "link")
        if not title or not link:
            continue
        source = ""
        src_el = item.find("source")
        if src_el is not None and src_el.text:
            source = src_el.text.strip()
        if is_gnews:
            if source and title.endswith(" - " + source):
                title = title[: -(len(source) + 3)].strip()
            elif " - " in title and not source:
                title, _, source = title.rpartition(" - ")
                title, source = title.strip(), source.strip()
        desc = _text(item, "description")
        snippet = re.sub(r"<[^>]+>", "", unescape(desc)).strip() if desc else ""
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        out.append({
            "title": title, "source": source, "url": link,
            "published": parse_date(_text(item, "pubDate")),
            "image": "" if is_gnews else rss_image(item),
            "snippet": "" if is_gnews else snippet,
        })
    return out


def gnews_site_url(domain):
    q = f"site:{domain} (" + " OR ".join(f'"{t}"' for t in BROAD_TERMS) + ") " + TIME_WINDOW
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def gnews_edition_url(hl, gl):
    q = "(" + " OR ".join(f'"{t}"' for t in BROAD_TERMS) + ") " + TIME_WINDOW
    lang = hl.split("-")[0]
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": hl, "gl": gl, "ceid": f"{gl}:{lang}"})


# --- Enrichment: og:image + og:description ----------------------------------

_OG_IMG = [re.compile(p, re.I) for p in (
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)')]
_OG_DESC = [re.compile(p, re.I) for p in (
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|twitter:description|description)["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:description|twitter:description|description)')]


def _first(res, html):
    for rx in res:
        m = rx.search(html)
        if m:
            return unescape(m.group(1)).strip()
    return ""


def enrich(s):
    url = s.get("url", "")
    if not url or "news.google.com" in url:
        return
    if s.get("image") and s.get("snippet"):
        return
    try:
        html = fetch(url, cap=120000, timeout=ENRICH_TIMEOUT).decode("utf-8", "replace")
    except Exception:
        return
    if not s.get("image"):
        img = _first(_OG_IMG, html)
        if img.startswith("http"):
            s["image"] = img
    if not s.get("snippet"):
        d = _first(_OG_DESC, html)
        if d:
            s["snippet"] = (d[:220] + "…") if len(d) > 220 else d


# --- Collection --------------------------------------------------------------


def collect():
    store = {}      # norm-title -> story dict
    stats = {"curated_feed": 0, "curated_site": 0, "editions": 0, "fail": 0}

    def add(story, region, sector):
        key = norm(story["title"])
        if not key:
            return
        if not story.get("published"):
            story["published"] = datetime.now(timezone.utc).isoformat()
        if key not in store:
            story = dict(story)
            story["region"], story["industry"] = region, sector
            store[key] = story
        else:
            cur = store[key]
            if story.get("image") and not cur.get("image"):
                cur["image"] = story["image"]
            if story.get("snippet") and not cur.get("snippet"):
                cur["snippet"] = story["snippet"]

    # ---- 1) Retain everything already in the archive ----
    existing = 0
    if os.path.exists("data.json"):
        try:
            old = json.load(open("data.json", encoding="utf-8"))
            for r in old.get("regions", []):
                for a in r.get("articles", []):
                    add(a, a.get("region", "Global"), a.get("industry", "Fashion"))
                    existing += 1
        except Exception as exc:
            print(f"  ! could not read existing archive: {exc}", file=sys.stderr)
    print(f"  · retained {existing} stories from existing archive")

    # ---- 2) Curated sources: RSS feed, else Google News site: ----
    for src in CURATED_SOURCES:
        got = 0
        if src["feed"]:
            try:
                items = parse_rss(fetch(src["feed"]))
                for it in items:
                    sector = classify(it["title"] + " " + it.get("snippet", ""))
                    if sector:
                        it["source"] = src["name"]
                        add(it, src["region"], sector)
                        got += 1
                stats["curated_feed"] += 1
            except Exception as exc:
                print(f"  ! feed {src['name']}: {exc}", file=sys.stderr)
                stats["fail"] += 1
            time.sleep(REQUEST_DELAY)
        if got == 0:  # no feed, or feed dead/empty -> site: fallback
            try:
                for it in parse_rss(fetch(gnews_site_url(src["domain"])), is_gnews=True):
                    sector = classify(it["title"])
                    if sector:
                        add(it, src["region"], sector)
                        got += 1
                stats["curated_site"] += 1
            except Exception as exc:
                print(f"  ! site {src['name']}: {exc}", file=sys.stderr)
                stats["fail"] += 1
            time.sleep(REQUEST_DELAY)
        print(f"  · {src['name']}: +{got}")

    # ---- 3) Regional editions for breadth ----
    for label, hl, gl, region in EDITIONS:
        try:
            for it in parse_rss(fetch(gnews_edition_url(hl, gl)), is_gnews=True):
                sector = classify(it["title"])
                if sector:
                    add(it, region, sector)
            stats["editions"] += 1
        except Exception as exc:
            print(f"  ! edition {label}: {exc}", file=sys.stderr)
            stats["fail"] += 1
        time.sleep(REQUEST_DELAY)

    return list(store.values()), stats


# --- Main --------------------------------------------------------------------


def main():
    stories, stats = collect()

    # prune to the rolling window
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept = []
    for s in stories:
        try:
            if datetime.fromisoformat(s["published"]) >= cutoff:
                kept.append(s)
        except (ValueError, KeyError):
            kept.append(s)

    buckets = {r: [] for r in REGION_ORDER}
    for s in kept:
        buckets.get(s.get("region", "Global"), buckets["Global"]).append(s)

    regions_out, total = [], 0
    for region in REGION_ORDER:
        items = sorted(buckets[region], key=lambda s: s["published"], reverse=True)
        items = items[:MAX_PER_REGION]
        items.sort(key=lambda s: (0 if s.get("image") else 1))  # imaged cards first
        if items:
            regions_out.append({"name": region, "count": len(items), "articles": items})
            total += len(items)

    if total == 0:
        print("No stories collected — leaving data.json untouched.", file=sys.stderr)
        sys.exit(1)

    if ENABLE_ENRICHMENT:
        for r in regions_out:
            for s in r["articles"][:ENRICH_PER_REGION]:
                if not (s.get("image") and s.get("snippet")):
                    enrich(s)
                    time.sleep(0.25)
        for r in regions_out:
            r["articles"].sort(key=lambda s: (0 if s.get("image") else 1))

    counts = {i: 0 for i in INDUSTRY_ORDER}
    for r in regions_out:
        for a in r["articles"]:
            counts[a["industry"]] = counts.get(a["industry"], 0) + 1

    now = datetime.now(timezone.utc)
    data = {
        "updated_at": now.isoformat(),
        "edition": "Morning edition" if now.hour < 15 else "Evening edition",
        "total": total,
        "sources": len({a["source"] for r in regions_out for a in r["articles"] if a["source"]}),
        "industries": [{"name": i, "count": counts[i]} for i in INDUSTRY_ORDER if counts[i]],
        "regions": regions_out,
    }
    with open("data.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    print(f"\nWrote data.json — {total} stories across {len(regions_out)} regions "
          f"(rolling {RETENTION_DAYS}-day archive).")
    print(f"  curated feeds ok: {stats['curated_feed']} | site fallbacks: {stats['curated_site']}"
          f" | editions: {stats['editions']} | failures: {stats['fail']}")


if __name__ == "__main__":
    main()
