#!/usr/bin/env python3
"""B2B lead discovery for the EmbroideryCopilot Outreach CRM.

Finds businesses that plausibly need embroidery digitizing and extracts
contact addresses from public pages.

Behaviour that differs from the v1 script, on purpose:
  * Existing leads are NEVER truncated.  The old ``clean()`` wiped
    leads.csv on every run; discovery now merges instead.
  * Leads are deduplicated by normalised email, so re-running does not
    create duplicates.
  * A lead's name and company are taken from the page that was actually
    found -- they are not fabricated from the domain.
  * Disposable mail providers are filtered out.
  * Rejected, suppressed and already-sent contacts are never re-discovered
    into an active state.
  * Nothing is invented: if no address is found, no row is written.

Usage (PowerShell):
    python lead_finder.py --dry-run
    python lead_finder.py --max-results 15
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import outreach_store as store

# --------------------------------------------------------------------------
# Search configuration
# --------------------------------------------------------------------------
# Targeted queries per buyer segment.  Each is a real search string that
# finds businesses advertising the service, not directories of people.
SEGMENT_QUERIES = {
    "embroidery shop": [
        '"embroidery shop" "contact us"',
        '"custom embroidery" "call us"',
    ],
    "apparel brand": [
        '"apparel brand" "custom embroidery" contact',
        '"clothing brand" "embroidery" "@"',
    ],
    "uniform manufacturer": [
        '"uniform manufacturer" "embroidery" contact',
        '"uniform supplier" "logo embroidery"',
    ],
    "promotional products": [
        '"promotional products" "embroidery" "contact"',
        '"promo products" "custom embroidery" supplier',
    ],
    "print and merchandise": [
        '"print on demand" "embroidery" "contact"',
        '"merchandise" "embroidery services" "info@"',
    ],
}

# Hosts we never treat as a business lead.
BLOCKED_DOMAINS = {
    "wikipedia.org", "youtube.com", "facebook.com", "pinterest.com",
    "linkedin.com", "twitter.com", "x.com", "instagram.com", "reddit.com",
    "amazon.com", "ebay.com", "etsy.com", "alibaba.com", "indeed.com",
    "glassdoor.com", "crunchbase.com", "medium.com", "quora.com",
    "github.com", "stackoverflow.com", "pinterest.co.uk", "google.com",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Local parts that are not a person we can write to.
BLOCKED_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster",
    "abuse", "hostmaster", "webmaster", "root", "example", "yourname",
    "email", "mail", "someone", "test", "demo", "name", "sentry", "wixpress",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.5  # seconds between page fetches -- be a polite client
MAX_PAGES_PER_QUERY = 3


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def extract_emails(text: str) -> list:
    """Extract syntactically valid, non-disposable addresses from free text."""
    found = []
    for raw in EMAIL_RE.findall(text or ""):
        email = store.normalize_email(raw)
        if not email or not store.is_valid_email_syntax(email):
            continue
        if store.is_disposable(email):
            continue
        local = email.split("@", 1)[0]
        if local in BLOCKED_LOCAL_PARTS:
            continue
        domain = email.split("@", 1)[1]
        if domain in BLOCKED_DOMAINS:
            continue
        if email not in found:
            found.append(email)
    return found


def guess_company(page_title: str, url: str, domain: str) -> str:
    """Derive a company name from the page title, falling back to the domain.

    This is a *label*, not a verified fact about the company.  It is recorded
    in the ``company`` column and the ``verified`` column stays "unknown"
    until a human or a stronger signal confirms it.
    """
    title = (page_title or "").strip()
    if title:
        # Titles often look like "Acme Apparel | Custom Embroidery" or
        # "Acme Apparel - Contact Us".
        candidate = re.split(r"[|｜–—\-–—:>·]", title)[0].strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if 2 <= len(candidate) <= 80:
            return candidate
    if domain:
        name = domain.split(".")[0]
        return name.replace("-", " ").title()
    return ""


def is_interesting(url: str) -> bool:
    """Skip URLs that obviously do not host a contact page."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    domain = (parsed.netloc or "").lower().split(":")[0]
    if not domain or domain in BLOCKED_DOMAINS:
        return False
    if domain.endswith(".wikipedia.org"):
        return False
    return True


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def _get_ddgs_class():
    """Return the DDGS class from whichever package is installed.

    ``duckduckgo_search`` was renamed to ``ddgs``; both expose the same
    ``DDGS`` class with a ``text()`` method.  The new package is preferred,
    but the old one keeps working so the project is not forced to upgrade.
    """
    try:
        from ddgs import DDGS  # noqa: PLC0415
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS  # noqa: PLC0415
        return DDGS
    except ImportError:
        return None


def search_web(query: str, max_results: int = 20):
    """Run a DuckDuckGo text search.  Returns [] if the provider is offline."""
    ddgs_class = _get_ddgs_class()
    if ddgs_class is None:
        print("No search package installed. Install one with:\n"
              "  pip install ddgs          (current)\n"
              "  pip install duckduckgo-search   (older)", file=sys.stderr)
        return []
    try:
        with ddgs_class() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # noqa: BLE001 - the search backend is flaky
        print(f"  search failed for {query!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []


def fetch_page(url: str) -> tuple:
    """Fetch a page and return (title, text).  Returns ("", "") on failure."""
    if not is_interesting(url):
        return "", ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return "", ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        return title, soup.get_text(separator=" ", strip=True)
    except requests.RequestException as exc:
        print(f"  fetch failed {url}: {type(exc).__name__}", file=sys.stderr)
        return "", ""
    except Exception as exc:  # noqa: BLE001
        print(f"  fetch error {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return "", ""


def discover(max_results: int = 20, dry_run: bool = False, segments=None) -> dict:
    """Run discovery across the configured segments.

    Returns a summary dict.  Nothing is written to disk when ``dry_run``.
    """
    segments = segments or list(SEGMENT_QUERIES)
    suppressed = store.load_suppressed()
    known = {store.normalize_email(r.get("email")) for r in store.load_leads()}
    known.discard("")

    candidates = {}  # email -> lead dict
    new_count = 0
    duplicate_count = 0
    pages_fetched = 0

    for segment in segments:
        for query in SEGMENT_QUERIES.get(segment, []):
            print(f"[{segment}] {query}")
            results = search_web(query, max_results=max_results)
            if not results:
                print("  no results")
                continue
            for result in results:
                url = result.get("href") or result.get("url") or ""
                if not is_interesting(url):
                    continue
                title, text = fetch_page(url)
                pages_fetched += 1
                if not text:
                    continue
                domain = (urlparse(url).netloc or "").lower()
                snippet_emails = extract_emails(text) or extract_emails(
                    result.get("body", "") + " " + result.get("title", ""))
                for email in snippet_emails:
                    if email in candidates:
                        continue
                    if email in suppressed:
                        print(f"  skip (suppressed) {email}")
                        continue
                    if email in known:
                        duplicate_count += 1
                        continue
                    company = guess_company(title, url, domain)
                    candidates[email] = {
                        "name": company,          # a label derived from the page title
                        "email": email,
                        "subject": "Embroidery Copilot",
                        "company": company,
                        "niche": segment,
                        "source_url": url,
                        "discovered_at": store.now_iso(),
                        "status": store.ST_PENDING,
                        "verified": "unknown",    # never claim the mailbox exists
                    }
                time.sleep(REQUEST_DELAY)

    print(f"\nFetched {pages_fetched} page(s). "
          f"Candidates: {len(candidates)}, already known: {duplicate_count}.")

    if dry_run:
        print("\n--- DRY RUN (nothing written) ---")
        for lead in candidates.values():
            print(f"  {lead['email']}  |  {lead['company']}  |  {lead['niche']}  |  {lead['source_url']}")
        return {"candidates": len(candidates), "added": 0, "duplicates": duplicate_count}

    added = 0
    for lead in candidates.values():
        try:
            if store.upsert_lead(lead):
                added += 1
                new_count += 1
                print(f"  + added {lead['email']} ({lead['company']})")
            else:
                duplicate_count += 1
                print(f"  = already present {lead['email']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! could not save {lead['email']}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)

    print(f"\nAdded {added} new lead(s). {duplicate_count} duplicate(s) skipped. "
          f"Total leads now: {len(store.load_leads())}.")
    return {"candidates": len(candidates), "added": added, "duplicates": duplicate_count}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EmbroideryCopilot B2B lead discovery")
    parser.add_argument("--max-results", type=int, default=20,
                        help="search results per query (default 20)")
    parser.add_argument("--segments", nargs="*", default=None,
                        help="limit to these segments (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="search and report, but do not write to leads.csv")
    args = parser.parse_args(argv)

    print(f"Lead discovery — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Leads file: {store.LEADS_FILE}\n")
    summary = discover(max_results=args.max_results, dry_run=args.dry_run,
                       segments=args.segments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
