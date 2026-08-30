"""Collect release download counts for every TechyGeeksHome repository.

Run by .github/workflows/collect.yml. Writes data/downloads.json, which is the only
thing index.html reads. Repositories are discovered rather than listed, so a new app
appears here on its own once it has a release.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

OWNER = "techygeekshome"
API = "https://api.github.com"
OUT = "data/downloads.json"


def get(path):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


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


def main():
    apps = []
    for repo in paged(f"/users/{OWNER}/repos"):
        if repo.get("fork") or repo.get("archived"):
            continue
        name = repo["name"]

        try:
            releases = list(paged(f"/repos/{OWNER}/{name}/releases"))
        except urllib.error.HTTPError as e:
            print(f"{name}: releases unavailable ({e.code})", file=sys.stderr)
            continue

        assets = {}
        total = 0
        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                # Checksum files are not downloads of the product.
                if asset["name"].lower().startswith("sha256sums"):
                    continue
                count = asset.get("download_count", 0)
                total += count
                assets[asset["name"]] = assets.get(asset["name"], 0) + count

        if not releases:
            continue

        published = [r for r in releases if not r.get("draft")]
        apps.append({
            "name": name,
            "url": repo["html_url"],
            "description": repo.get("description") or "",
            "total": total,
            "releases": len(published),
            "latest": published[0]["tag_name"] if published else None,
            "assets": sorted(
                ({"name": k, "count": v} for k, v in assets.items()),
                key=lambda a: -a["count"],
            ),
        })

    apps.sort(key=lambda a: -a["total"])

    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": sum(a["total"] for a in apps),
        "apps": apps,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    print(f"{payload['total']} downloads across {len(apps)} repositories")


if __name__ == "__main__":
    main()
