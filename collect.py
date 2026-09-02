"""Collect download counts for every TechyGeeksHome application.

Run by .github/workflows/collect.yml. Writes data/downloads.json, which is the only
thing index.html reads.

Five places count a download, and all five are asked:

  GitHub releases   the primary source, and the only one for most apps
  SourceForge       the mirror, which has its own counter
  Chocolatey        the package feed, which has its own counter
  MajorGeeks        by far the largest external channel we have
  Softpedia         one publisher page carries every product's total

The first three are looked up by the repository name in lower case, which is how they
are all named. The last two do not use our naming, so they are listed explicitly in
DIRECTORY below. An app missing from that table is simply not on those sites yet.

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
MAJORGEEKS = "https://www.majorgeeks.com/files/details/{slug}.html"
SOFTPEDIA_PUBLISHER = "https://www.softpedia.com/publisher/Techy-Geeks-Home-95268.html"

# Neither site uses our repository names, so the ones we are actually listed on are
# named here. Anything absent is not on that site yet, which is a real answer.
DIRECTORY = {
    "Ultimate-Settings-Panel": {"majorgeeks": "ultimate_settings_panel",
                                "softpedia": "Ultimate Settings Panel"},
    "PDFGeek":                 {"majorgeeks": "pdfgeek",   "softpedia": "PDFGeek"},
    "DiskGeek":                {"majorgeeks": "diskgeek",  "softpedia": "DiskGeek"},
    "CleanGeek":               {"majorgeeks": "cleangeek", "softpedia": "CleanGeek"},
}

UA = "techygeekshome-download-stats"

# MajorGeeks and Softpedia both refuse a bare urllib User-Agent, so the scrapers send a
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


def majorgeeks_downloads(slug):
    """All-time downloads from the listing page, or 0 if we are not listed there."""
    if not slug:
        return 0
    try:
        html = fetch(MAJORGEEKS.format(slug=slug), "text/html",
                     browser=True).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 0
        raise
    m = re.search(r"Downloads?\s*:?\s*([\d,]+)\s*times", html, re.I)
    if not m:
        raise ValueError("no download count on the MajorGeeks page")
    return int(m.group(1).replace(",", ""))


_softpedia_cache = {}


def softpedia_table():
    """
    Every product's total, from the one publisher page.

    Scraped once per run rather than per app: the publisher page lists all of them, so
    thirty-odd separate fetches would be rude as well as slow.
    """
    if _softpedia_cache:
        return _softpedia_cache
    html = fetch(SOFTPEDIA_PUBLISHER, "text/html", browser=True).decode("utf-8", "replace")
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    for name, count in re.findall(r"([A-Za-z][\w .&-]{2,40}?)\s+[\d.]*\s*([\d,]+)\s+downloads", text, re.I):
        _softpedia_cache[name.strip().lower()] = int(count.replace(",", ""))
    if not _softpedia_cache:
        raise ValueError("no products found on the Softpedia publisher page")
    return _softpedia_cache


def softpedia_downloads(product):
    """All-time downloads for one product, or 0 if it is not listed."""
    if not product:
        return 0
    return softpedia_table().get(product.strip().lower(), 0)


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
        listed = DIRECTORY.get(name, {})

        for label, lookup, key in (
            ("sourceforge", sourceforge_downloads, slug),
            ("chocolatey",  chocolatey_downloads,  slug),
            ("majorgeeks",  majorgeeks_downloads,  listed.get("majorgeeks")),
            ("softpedia",   softpedia_downloads,   listed.get("softpedia")),
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

        apps.append({
            "name": name,
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            "total": sum(counts.values()),
            "github": counts["github"],
            "sourceforge": counts["sourceforge"],
            "chocolatey": counts["chocolatey"],
            "majorgeeks": counts["majorgeeks"],
            "softpedia": counts["softpedia"],
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
            "majorgeeks": sum(a["majorgeeks"] for a in apps),
            "softpedia": sum(a["softpedia"] for a in apps),
        },
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
          f"Chocolatey {s['chocolatey']}, MajorGeeks {s['majorgeeks']}, "
          f"Softpedia {s['softpedia']})")


if __name__ == "__main__":
    main()
