# download-stats

Total download counts for every TechyGeeksHome application, collected once a day and published
as a page.

- **Dashboard:** https://techygeekshome.github.io/download-stats/
- **Raw data:** [`data/downloads.json`](data/downloads.json)

## Where the numbers come from

Three places serve downloads, and all three are counted:

| Source | How it is read |
|---|---|
| GitHub releases | `assets[].download_count` summed over every published release |
| SourceForge | the project statistics feed, all time |
| Chocolatey | `DownloadCount` from the package feed, which is the all version figure |

`collect.py` discovers repositories rather than reading a list, so a new application shows up on
its own as soon as it has a published release. The SourceForge project and the Chocolatey package
are looked up by the repository name in lower case, which is how both are named, and a 404 just
means the application is not published there yet.

`SHA256SUMS.txt` is excluded, since downloading a checksum file is not downloading the software.

## When a source is down

If SourceForge or Chocolatey cannot be reached, the figure from the last run is carried forward
and the page says which source was unavailable. A number on this page must never quietly fall
because somebody else's website was down for a minute.

## Refreshing

Run the **collect** workflow from the Actions tab. It commits `data/downloads.json` only when the
numbers have actually changed.

winget publishes no download statistics at all, so there is nothing to count there.
