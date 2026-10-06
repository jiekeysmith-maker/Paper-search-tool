# TPAMI public rendering development

The HTTP-only pipeline cannot yet enumerate the live SPA inventories. The isolated
`accept_tpami_rendered.py` entry uses the existing `run_year`, `FetchCache`, job
lock, corpus audit and frozen screening gate. It adds an optional foreground
Playwright transport, with a fresh unauthenticated context. It does not use the
user's browser profile, cookies, credentials, direct REST calls or stealth flags.

The optional environment needs the official `playwright` Python package and its
Chromium runtime. Nothing is installed automatically. Browser failures are
reported, never replaced by empty successful inventories. The transport closes
its browser on completion/interruption and does not create a daemon.

Public navigation checks robots before every top-level URL. Authentication
redirects and HTTP 202/401/403/429 stop that source; HTTP failures are never
cached as successful HTML. Browser HTTP response bodies are retained under
`raw/rendered_navigation`; rendered DOM is stored by the ordinary hash-verified
FetchCache. Its request history records the raw navigation evidence and capture
year. Normal issue page URLs (including observed `pageNumber`) are separate cache
entries, so successfully captured pages remain reusable after interruption.

Use a new attempt name when switching from the old HTTP-only acquisition mode.
Existing successful snapshots within a rendered attempt are reused. `--offline`
uses those snapshots without importing Playwright or making network requests.
No manual browser copying is part of this program's acquisition path.

Development invocation (not yet a production-readiness claim):

```text
python accept_tpami_rendered.py --years 2024 --attempt tpami_rendered_2024
```

The parser distinguishes `publicationDate` (final issue month) from
`displayPublicationDate` (first reported publication date) in real Xplore HTML.
The final-year policy is unchanged. Inventory closure requires a hash-bound
capture witness, the selected target year, all displayed issue links, no pending
loading or pagination, and matching independently enumerated official inventories.
This is the published inventory at capture time, not a forecast of future issues.
A rendered page or a green unit test alone is not sufficient. The renderer
transport currently has offline/mocked tests and public
UI selector investigation, not a completed annual live transport acceptance.

Implementation API references: [Playwright Page](https://playwright.dev/python/docs/api/class-page)
and [BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext).
