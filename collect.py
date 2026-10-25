"""Collect download counts for every TechyGeeksHome application.

Run by .github/workflows/collect.yml. Writes data/downloads.json, which is the only
thing index.html reads.

Places that count a download, in order of importance:

  GitHub releases   the primary source, and the only one for most apps
  SourceForge       the mirror, which has its own counter
  Website           download-button clicks on techygeekshome.info
  Chocolatey        the package feed, which has its own counter

GitHub, SourceForge and Chocolatey are looked up by the repository name in lower case.

Repositories are discovered rather than listed, so a new app appears here on its own
once it has a release. The other two are probed by the repository name in lower case,
which is how both are named, and a 404 simply means the app is not there yet.

If a source cannot be reached, the figure from the last run is carried forward and the
source is marked unavailable. A number on this page must never quietly fall because
somebody else's website was down for a minute.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

OWNER = "techygeekshome"
API = "https://api.github.com"
OUT = "data/downloads.json"

SOURCEFORGE = "https://sourceforge.net/projects/{slug}/files/stats/json"
CHOCOLATEY = "https://community.chocolatey.org/api/v2/FindPackagesById()"
PACKAGES = "https://community.chocolatey.org/api/v2/Packages()"
CHOCO_PAGE = "https://community.chocolatey.org/packages/{pkg}"


UA = "techygeekshome-download-stats"

# The website keeps its own counter of download-button clicks, keyed by page slug.
TGH_COUNTS = "https://techygeekshome.info/wp-json/tgh/v1/download-counts"

# Where a repository's page on the website does not use the lower-case repo name as
# its counter key, the key is named here.
TGH_SLUG = {
    "BoardSaver": "board-saver", "GridSaver": "grid-saver", "ClipSaver": "clip-saver",
    "RedditSaver": "reddit-saver", "BlueskySaver": "bluesky-saver", "PageSaver": "page-saver",
    "ImageFormatSaver": "image-format-saver", "ScreenshotSaver": "screenshot-saver",
    "MarkdownSaver": "markdown-saver", "PDFGeekExtension": "pdfgeek",
    "CountryFilterForX": "country-filter-for-x",
    "Ultimate-Settings-Panel": "ultimate-settings-panel",
    "PDFGeek": None,      # the Windows app page has no counter; "pdfgeek" is the extension's
}

CATEGORIES = ["Windows tools", "Browser extensions", "WordPress"]

# Some sites refuse a bare urllib User-Agent, so those requests send a
# browser one. The GitHub and SourceForge APIs keep the honest identifier above.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def fetch(url, accept, timeout=30, browser=False):
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", BROWSER_UA if browser else UA)
    if browser:
        req.add_header("Accept-Language", "en-GB,en;q=0.9")
    if url.startswith(API):
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get(path):
    return json.loads(fetch(API + path, "application/vnd.github+json"))


def paged(path):
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        batch = get(f"{path}{sep}per_page=100&page={page}")
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < 100:
            return
        page += 1


def github_downloads(name):
    """Total across every published release asset, and how many releases there were."""
    releases = list(paged(f"/repos/{OWNER}/{name}/releases"))
    published = [r for r in releases if not r.get("draft")]

    assets = {}
    total = 0
    for release in published:
        for asset in release.get("assets", []):
            # A checksum file is not a download of the software.
            if asset["name"].lower().startswith("sha256sums"):
                continue
            count = asset.get("download_count", 0)
            total += count
            assets[asset["name"]] = assets.get(asset["name"], 0) + count

    return total, published, assets


def sourceforge_downloads(slug):
    """Every download the mirror has ever served, or None if there is no such project."""
    query = urllib.parse.urlencode({
        "start_date": "2010-01-01",
        "end_date": date.today().isoformat(),
    })
    try:
        raw = fetch(f"{SOURCEFORGE.format(slug=slug)}?{query}", "application/json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 0          # not mirrored there, which is a real answer, not a failure
        raise
    return int(json.loads(raw).get("total", 0))


def _choco_feed(url):
    """Largest DownloadCount in an OData feed, or 0 if the feed is empty."""
    raw = fetch(url, "application/atom+xml")
    ns = "http://schemas.microsoft.com/ado/2007/08/dataservices"
    counts = [int(el.text or 0) for el in ET.fromstring(raw).iter(f"{{{ns}}}DownloadCount")]
    return max(counts) if counts else 0


def chocolatey_downloads(package_id):
    """
    Downloads across every version of a package, or 0 if it is not published.

    Three ways of asking, and the largest answer wins, because they disagree and each
    one is wrong in a different direction. The page is the freshest but is behind a bot
    check that sometimes refuses us. FindPackagesById is the documented lookup.
    Packages()?$filter is the one that was here first and demonstrably returns a figure.
    Taking the maximum means a refusal from any one of them cannot pull the total down.
    """
    found = []

    try:
        html = fetch(CHOCO_PAGE.format(pkg=package_id), "text/html",
                     browser=True).decode("utf-8", "replace")
        m = re.search(r"Total\s*Downloads?\D{0,40}?([\d,]+)", html, re.I)
        if m:
            found.append(int(m.group(1).replace(",", "")))
    except Exception:                     # noqa: BLE001 - the feeds below are the fallback
        pass

    for url in (
        f"{CHOCOLATEY}?id=" + urllib.parse.quote(f"'{package_id}'", safe=""),
        f"{PACKAGES}?$filter=" + urllib.parse.quote(f"Id eq '{package_id}'", safe=""),
    ):
        try:
            found.append(_choco_feed(url))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        except Exception:                 # noqa: BLE001
            pass

    return max(found) if found else 0


_tgh_cache = {}


def tgh_table():
    """Every download-button click the website has counted, fetched once per run."""
    if not _tgh_cache:
        _tgh_cache.update(json.loads(fetch(TGH_COUNTS, "application/json", browser=True)))
        if not _tgh_cache:
            raise ValueError("the website returned no counts")
    return _tgh_cache


def tgh_downloads(name):
    key = TGH_SLUG.get(name, name.lower())
    if not key:
        return 0
    return int(tgh_table().get(key, 0))


def category(name):
    """Browser extension if it has a manifest.json, WordPress if it has a WP readme or
    theme stylesheet, otherwise a Windows tool."""
    def has(path):
        try:
            get(f"/repos/{OWNER}/{name}/contents/{path}")
            return True
        except urllib.error.HTTPError:
            return False
    if has("manifest.json"):
        return "Browser extensions"
    if has("readme.txt") or has("style.css") or has("trunk/readme.txt"):
        return "WordPress"
    return "Windows tools"


def previous():
    """Last run's figures, keyed by app name, so a source outage cannot lose a number."""
    try:
        with open(OUT) as f:
            return {a["name"]: a for a in json.load(f).get("apps", [])}
    except (OSError, ValueError, KeyError):
        return {}


def main():
    before = previous()
    apps = []
    unavailable = set()

    for repo in paged(f"/users/{OWNER}/repos"):
        if repo.get("fork") or repo.get("archived"):
            continue

        name = repo["name"]
        slug = name.lower()
        was = before.get(name, {})

        try:
            gh, published, assets = github_downloads(name)
        except urllib.error.HTTPError as e:
            print(f"{name}: releases unavailable ({e.code})", file=sys.stderr)
            continue

        if not published:
            continue

        counts = {"github": gh}

        for label, lookup, key in (
            ("sourceforge", sourceforge_downloads, slug),
            ("chocolatey",  chocolatey_downloads,  slug),
            ("tgh",         tgh_downloads,         name),
        ):
            try:
                got = lookup(key)
            except Exception as e:                      # noqa: BLE001 - any failure is the same failure
                counts[label] = was.get(label, 0)
                unavailable.add(label)
                print(f"{name}: {label} unavailable ({e}), carried {counts[label]} forward",
                      file=sys.stderr)
                continue

            # A counter that had a number and now reads zero has not been reset by the
            # other site; we have been refused, or their markup moved. Downloads do not
            # go backwards, so the old figure stands and the source is flagged. Without
            # this a silent 403 quietly wipes a real number off the dashboard.
            had = was.get(label, 0)
            if got == 0 and had > 0:
                counts[label] = had
                unavailable.add(label)
                print(f"{name}: {label} returned 0 but was {had}, keeping {had}",
                      file=sys.stderr)
            else:
                counts[label] = max(got, had)

        try:
            cat = category(name)
        except Exception:                               # noqa: BLE001
            cat = was.get("category", "Windows tools")

        apps.append({
            "name": name,
            "category": cat,
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            # Website clicks lead to a GitHub download, so they are already inside the
            # GitHub figure. They are reported, but not added to the total twice.
            "total": sum(v for k, v in counts.items() if k != "tgh"),
            "github": counts["github"],
            "sourceforge": counts["sourceforge"],
            "chocolatey": counts["chocolatey"],
            "tgh": counts["tgh"],
            "releases": len(published),
            "latest": published[0]["tag_name"],
            "assets": sorted(
                ({"name": k, "count": v} for k, v in assets.items()),
                key=lambda a: -a["count"],
            ),
        })

    apps.sort(key=lambda a: -a["total"])

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": sum(a["total"] for a in apps),
        "sources": {
            "github": sum(a["github"] for a in apps),
            "sourceforge": sum(a["sourceforge"] for a in apps),
            "chocolatey": sum(a["chocolatey"] for a in apps),
            "tgh": sum(a["tgh"] for a in apps),
        },
        "categories": CATEGORIES,
        "unavailable": sorted(unavailable),
        "apps": apps,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    s = payload["sources"]
    print(f"{payload['total']} downloads across {len(apps)} applications "
          f"(GitHub {s['github']}, SourceForge {s['sourceforge']}, "
          f"Chocolatey {s['chocolatey']}, website {s['tgh']})")


if __name__ == "__main__":
    main()
