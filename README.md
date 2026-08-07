# Taylor County DEC — Early Vote & VBM data

Public early-voting and vote-by-mail figures for Taylor County, Florida, pulled
once a day from the [Florida Division of Elections public statistics page][src]
and published as JSON.

**The data:** https://raw.githubusercontent.com/tbluegator/taylor-dec-ev-data/main/latest.json

That URL is what the dashboard on our Solidarity Tech team page reads. Nothing
else needs to change for the dashboard to update — republish this file and the
page shows the new numbers on the next load.

[src]: https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats

---

## Why this repo exists

The dashboard is a static HTML block pasted into Solidarity Tech. Solidarity
Tech's public API is CRM-only — there is no endpoint that writes page content —
so the block cannot be updated programmatically. Instead the block is pasted
**once** and fetches its data from here at page load.

The alternative was storing a write credential somewhere so an external job
could push. This design avoids that entirely: the scheduled job runs *inside*
GitHub Actions using the built-in `GITHUB_TOKEN`, which is scoped to this repo
and never leaves it. **There are no secrets to configure.**

The repo is public because `raw.githubusercontent.com` only serves public files
without authentication, and because it sends `access-control-allow-origin: *`,
which is what lets the browser fetch it cross-origin. Everything here is a
public record under §101.657, F.S. — nothing sensitive is exposed by this.

---

## Status

Set up and verified **Aug 7, 2026**. Run #1 was triggered manually and went green
in 17 seconds: the parser fetched the live page from CI, produced figures identical
to those read by hand from the live DOM, and the bot pushed the commit. The
schedule takes over from Aug 8.

## Files

| File | Purpose |
|---|---|
| `scrape.py` | Fetches the page, parses it, appends a day to `latest.json` |
| `test_scrape.py` | Tests the parser against a fixture of the real page structure |
| `latest.json` | The data. This is the published artifact |
| `.github/workflows/daily.yml` | Runs the pull at 9:30 a.m. Eastern |

## How the parse works

The page is **fully server-rendered** — no JavaScript, no session cookie, no
ASP.NET viewstate — verified against the live page on Aug 7, 2026. So a plain
`requests.get` sees the same HTML a browser does.

The three county tables live in stable container IDs, which is what the parser
targets rather than table position:

| Container | Meaning |
|---|---|
| `#county_ablnotyet` | Vote-by-Mail Provided (Not Yet Returned) — the chase universe |
| `#county_abl` | Voted Vote-by-Mail — returned |
| `#county_evs` | Voted Early — in-person |
| `#statewideTotal` | All three, statewide, keyed by row label |

County rows are `[County, Election, Republican, Democrat, Other, NPA, Total, Compiled]`.
No nested tables, no colspans, uniform 8 cells.

## It fails loudly, on purpose

The job exits non-zero — turning the Actions run red and emailing you — if:

- a container ID or the Taylor row disappears (the page was redesigned)
- party counts don't sum to the stated Total
- a cumulative count goes **down** versus the previous day (half-published file)
- the page starts showing a different election ID than `latest.json` tracks

A red run is recoverable. Silently publishing wrong numbers to the team page is
not, which is why every check aborts rather than guesses.

If the county simply hasn't re-reported yet, that is **not** an error — the job
logs a warning, commits nothing, and the dashboard keeps showing yesterday.

## Known operational gotchas

- **Cron is UTC and ignores daylight saving.** `30 13 * * *` is 9:30 a.m. Eastern
  through Nov 1, 2026, then 8:30 a.m. Change it to `30 14 * * *` for the general.
- **GitHub delays scheduled runs**, sometimes 20+ minutes at peak. Harmless here:
  the state refreshes the file at noon, 3 p.m. and 6 p.m. anyway.
- **GitHub disables schedules in repos with 60 days of no activity.** There is a
  gap between the Aug 18 primary and the Nov 3 general. Push a commit in early
  October, or re-enable the workflow from the Actions tab.
- **The parser depends on the state's markup.** Container IDs and column order are
  verified as of Aug 7, 2026. If Florida redesigns the page the job goes red rather
  than guessing — that is intended, but it means someone has to fix it.

## Rolling over to the November general

1. Get the new election ID from the [stats page][src] (it is in the
   "Election Number - NNNNN" line).
2. In `latest.json`: update `election`, `election_id`, `election_date`,
   `ev_start`, `ev_end`, and set `"days": []`.
3. Update the cron to `30 14 * * *`.

Nothing in `scrape.py` or the dashboard block needs to change.
