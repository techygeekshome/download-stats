"""Collect download counts for every TechyGeeksHome application.

Run by .github/workflows/collect.yml. Writes data/downloads.json, which is the only
thing index.html reads.

Three places count a download, and all three are asked:

  GitHub releases   the primary source, and the only one for most apps
  SourceForge       the mirror, which has its own counter
  Chocolatey        the package feed, which has its own counter

Repositories are discovered rather than listed, so a new app appears here on its own
once it has a release. The other two are probed by the repository name in lower case,
which is how both are named, and a 404 simply means the app is not there yet.

If a source cannot be reached, the figure from the last run is carried forward and the
source is marked unavailable. A number on this page must never quietly fall because
somebody else's website was down for a minute.
"""

import json
import os
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
CHOCOLATEY = "https://community.chocolatey.org/api/v2/Packages()"

UA = "techygeekshome-download-stats"


def fetch(url, accept, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", UA)
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


def chocolatey_downloads(package_id):
    """
    Downloads across every version of a package, or 0 if it is not published.

    The feed is OData and refuses to speak JSON, so this reads the Atom it does speak.
    DownloadCount is the all-version figure and is repeated on every entry, so the
    largest one is the answer rather than the sum.
    """
    # The dollar in $filter has to stay a dollar, so the query is built rather than encoded.
    query = "$filter=" + urllib.parse.quote(f"Id eq '{package_id}'", safe="")
    raw = fetch(f"{CHOCOLATEY}?{query}", "application/atom+xml")

    ns = {"d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    counts = [
        int(el.text or 0)
        for el in ET.fromstring(raw).iter(f"{{{ns['d']}}}DownloadCount")
    ]
    return max(counts) if counts else 0


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

        for label, lookup in (("sourceforge", sourceforge_downloads),
                              ("chocolatey", chocolatey_downloads)):
            try:
                counts[label] = lookup(slug)
            except Exception as e:                      # noqa: BLE001 - any failure is the same failure
                counts[label] = was.get(label, 0)
                unavailable.add(label)
                print(f"{name}: {label} unavailable ({e}), carried {counts[label]} forward",
                      file=sys.stderr)

        apps.append({
            "name": name,
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            "total": sum(counts.values()),
            "github": counts["github"],
            "sourceforge": counts["sourceforge"],
            "chocolatey": counts["chocolatey"],
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
          f"(GitHub {s['github']}, SourceForge {s['sourceforge']}, Chocolatey {s['chocolatey']})")


if __name__ == "__main__":
    main()
