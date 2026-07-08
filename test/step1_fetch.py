"""Step 1 — Fetch a website and capture raw HTML + metadata for step 2.

Usage:
    python test/step1_fetch.py example.com
    python test/step1_fetch.py              # interactive prompt

Outputs:
    test/last_fetch.html   raw HTML (input for step 2 OpenAI call)
    test/last_fetch.json   extracted metadata  (input for step 2)
"""
import sys, json, os, urllib.request, urllib.error
from html.parser import HTMLParser
from urllib.parse import urlparse


# ── HTML meta extractor ───────────────────────────────────────────────────────

class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content", "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href", "")

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


# ── helpers ───────────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SEO-test/1.0)"}


def normalise(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def _probe_status(url: str, timeout: int = 10) -> int | str:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=HEADERS), timeout=timeout
        ) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return "unreachable"


# ── core fetch ────────────────────────────────────────────────────────────────

def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            final_url = resp.geturl()
            status    = resp.status
            html      = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": str(e.reason)}

    parsed = urlparse(final_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    parser = _MetaParser()
    parser.feed(html)

    # robots.txt preview
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"{origin}/robots.txt", headers=HEADERS),
            timeout=10,
        ) as r:
            robots_txt = r.read(10_000).decode("utf-8", errors="replace")[:500]
    except Exception:
        robots_txt = "(not found)"

    return {
        "input_url":          url,
        "final_url":          final_url,
        "status":             status,
        "https":              final_url.startswith("https://"),
        "title":              parser.title.strip(),
        "meta_description":   parser.meta.get("description", ""),
        "meta_robots":        parser.meta.get("robots", ""),
        "canonical":          parser.canonical,
        "og_title":           parser.meta.get("og:title", ""),
        "og_description":     parser.meta.get("og:description", ""),
        "robots_txt_preview": robots_txt,
        "sitemap_xml_status": _probe_status(f"{origin}/sitemap.xml"),
        "html_bytes_sampled": len(html),
        "_html":              html,
    }


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else input("Website: ").strip()
    url = normalise(raw)

    print(f"\nFetching: {url}")
    print("─" * 60)

    result = fetch(url)

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    os.makedirs("test", exist_ok=True)

    with open("test/last_fetch.html", "w", encoding="utf-8") as f:
        f.write(result["_html"])

    exportable = {k: v for k, v in result.items() if k != "_html"}
    with open("test/last_fetch.json", "w", encoding="utf-8") as f:
        json.dump(exportable, f, indent=2, ensure_ascii=False)

    del result["_html"]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("─" * 60)
    print("HTML  →  test/last_fetch.html")
    print("Meta  →  test/last_fetch.json   (input for step 2)")


if __name__ == "__main__":
    main()
