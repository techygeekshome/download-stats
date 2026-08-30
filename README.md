# download-stats

Total release download counts for every TechyGeeksHome application, collected from the
GitHub API once a day and published as a page.

- **Dashboard:** https://techygeekshome.github.io/download-stats/
- **Raw data:** [`data/downloads.json`](data/downloads.json)

`collect.py` discovers repositories rather than reading a list, so a new application shows up
on its own as soon as it has a published release. `SHA256SUMS.txt` is excluded from the counts,
since downloading a checksum file is not downloading the software.

To refresh outside the daily schedule, run the **collect** workflow from the Actions tab.
