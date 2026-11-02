# SEO tools — what Semrush, Screaming Frog & co. do, and what MulteAgent adds (BACKLOG L15)

_Researched 2026-11-02. Sources: Screaming Frog [Issues](https://www.screamingfrog.co.uk/seo-spider/issues/),
[v17 release](https://www.screamingfrog.co.uk/blog/seo-spider-17/); Semrush
[Site Audit KB](https://www.semrush.com/kb/31-site-audit), [audit tools overview](https://www.semrush.com/blog/seo-audit-tools/),
[WebTonic guide](https://www.webtonic.io/blog/semrush-site-audit); PageSpeed Insights API v5
([client docs](https://googleapis.dev/nodejs/googleapis/latest/pagespeedonline/classes/Resource$Pagespeedapi.html))._

## How they work

| Tool | How it works | Data it owns |
|---|---|---|
| **Screaming Frog SEO Spider** | Desktop crawler: follows every link like a search bot, records each URL's response, head tags, headings, links, images, directives; 300+ issues/warnings/opportunities in an Issues tab; thresholds configurable; optional JS rendering; pulls PageSpeed / Search Console / Analytics by API | none — everything comes from the crawl (+ the APIs you connect) |
| **Semrush** | Cloud suite. *Site Audit*: ~140 checks in categories (crawlability, HTTPS, international/hreflang, performance + Core Web Vitals, internal linking, markup), projects re-crawled on a schedule with trends; plus keyword research, position tracking, backlinks, competitor/domain analytics, content tools (SEO Writing Assistant, topic research) | huge keyword DB (search volume, difficulty, intent), backlink index, SERP history, clickstream traffic estimates |
| **Ahrefs / Moz / Ubersuggest** | Same model as Semrush: crawler + proprietary keyword and backlink indexes | backlink + keyword indexes |
| **PageSpeed Insights / Lighthouse** | Google runs Lighthouse on a URL (lab data) and returns real-user Core Web Vitals (LCP, INP, CLS) from the Chrome UX Report when enough data exists; free API with a basic anonymous quota, key for more | CrUX field data |
| **Google Search Console** | The site owner's real clicks, impressions, queries and positions in Google | the only true ranking/traffic source — needs the owner's login (OAuth) |
| **Yoast / Rank Math** | On-page content grader for a focus keyphrase: keyphrase in title/intro/slug, density, readability, links | none |

## Capability matrix

✅ have · 🟡 partial · ➕ planned (phase) · ⛔ not possible locally (needs a proprietary index)

| Capability | SF | Semrush | MulteAgent before | Plan |
|---|---|---|---|---|
| Crawl with robots.txt, sitemap | ✅ | ✅ | ✅ (L13) | |
| Titles / descriptions / H1 / headings / canonical / lang / viewport / OG / schema | ✅ | ✅ | ✅ (L13) | |
| Broken pages | ✅ | ✅ | ✅ | |
| Redirects + chains, links pointing to redirects | ✅ | ✅ | ⛔ hidden by the fetcher | ➕ P1 |
| Response time, page size | ✅ | ✅ | — | ➕ P1 |
| Crawl depth, inlinks, orphan pages (in sitemap, not linked) | ✅ | ✅ | — | ➕ P1 |
| Anchor text (non-descriptive / reused for many targets) | ✅ | ✅ | — | ➕ P1 |
| Exact + near-duplicate content | ✅ | ✅ | — | ➕ P1 |
| Canonical to another / broken URL | ✅ | ✅ | 🟡 present only | ➕ P1 |
| Security headers, mixed content | ✅ | ✅ | 🟡 HTTPS only | ➕ P1 |
| URL hygiene (uppercase, underscores, params, length) | ✅ | ✅ | — | ➕ P1 |
| Images without width/height (layout shift) | ✅ | 🟡 | — | ➕ P1 |
| Full crawl export (spreadsheet of every page) | ✅ | ✅ | — | ➕ P1 |
| Core Web Vitals (lab + real users) | ✅ API | ✅ | — | ➕ P2 PageSpeed API |
| SERP overview for a keyword (who ranks, what they wrote) | — | ✅ | 🟡 web_search | ➕ P3 via SearXNG |
| Rank check of a domain for keywords | — | ✅ exact | — | ➕ P3 approximate (SearXNG mixes engines) |
| Keyword ideas | — | ✅ with volume | — | ➕ P3 ideas, **no volume** |
| Keyword difficulty | — | ✅ from backlinks | — | ➕ P3 estimate from the SERP |
| Competitor / content gap (topics they cover, you don't) | — | ✅ | — | ➕ P4 crawl both + compare |
| SEO writing assistant (recommended terms, length from top pages) | — | ✅ | 🟡 write_content | ➕ P4 SERP terms into the brief |
| Projects: saved audits, scheduled re-crawl, trend, new/fixed issues | 🟡 | ✅ | — | ➕ P5 |
| Search volume, traffic estimates, backlink index | — | ✅ | — | ⛔ needs a paid data API (Semrush/Ahrefs/DataForSEO) — user's choice |
| Real clicks / queries / positions for the user's own site | ✅ API | ✅ API | — | ➕ later: Search Console OAuth |
| JavaScript rendering | ✅ | ✅ | — | later (headless browser = heavy) |

## Phases

- ✅ **P1 Crawler depth (Screaming Frog parity), local, no keys** (done 2026-11-02) — the items marked P1, a Links and a Performance
  area in the score, every page in a downloadable spreadsheet.
- **P2 Performance** — PageSpeed Insights API for the home page + a few key pages (`PAGESPEED_API_KEY` optional;
  only public URLs are sent to Google).
- **P3 Keywords & SERP** — SearXNG: SERP overview, approximate rank check, keyword ideas and a difficulty estimate.
  Honest about what is missing: no search volume.
- **P4 Competitors & writing** — side-by-side audit, content gap, SERP-informed briefs for `write_content`.
- **P5 Projects** — save audits per site, re-run on the brain scheduler, trend and issue diff.
