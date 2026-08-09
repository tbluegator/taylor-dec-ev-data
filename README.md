# Taylor County DEC — Early Vote & VBM data

Daily early-voting and vote-by-mail figures for Taylor County, Florida, pulled
from two sources and published as one JSON file.

**The data:** https://raw.githubusercontent.com/tbluegator/taylor-dec-ev-data/main/latest.json

That URL is what the dashboard on our Solidarity Tech team page reads
([2026 Primary Stats](https://www.taylorfldems.org/2026-primary-stats)). Nothing
else needs to change for the dashboard to update — republish this file and the
page shows the new numbers on the next load.

---

## The two sources, and why both

| | Florida DOS state file | Taylor SOE live feed |
|---|---|---|
| Source | [PublicStats][src] | [Turnout Quick View][tqv] (JSON on S3) |
| Freshness | once daily ~8 a.m., covering through the **previous day** | every few minutes |
| **Outstanding ballots** | ✅ the chase universe | ❌ counts ballots *cast* only |
| Statewide benchmark | ✅ | ❌ |
| In-person early votes | a day late | same day, by party and date |
| Daily mail returns | derived from consecutive snapshots | ✅ back to July 10 |
| Registered-voter denominator | ❌ | ✅ |

**Neither replaces the other.** The chase number — Democratic ballots still
outstanding — exists only in the state file. Same-day in-person turnout exists
only in the county feed. `scrape.py` writes both into `latest.json`: the state
figures as a new entry in `days`, the county feed as a whole-object snapshot
under `tqv` / `tqv_overrides`.

[src]: https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats
[tqv]: https://tqv.vrswebapps.com/?state=FL&county=TAY&election=84

---

## ⚠️ The dating convention

**The state file is compiled ~8 a.m. and covers activity through the END OF THE
PREVIOUS DAY.** Verified exactly: the file stamped Aug 9 carried the county's
Aug 8 early-vote figures (D 19 / R 78 / total 100) and its mail total through
Aug 8 (233).

Records are **stored** keyed by snapshot date. The dashboard **displays** them
shifted one day earlier. A dashboard row dated Aug 8 built from the Aug 9
snapshot is correct — do not "fix" the stored dates.

This is also what makes the mail party split work: the delta between consecutive
snapshots lands in the county's bucket for the earlier date, and reconciles to
the ballot.

Not an anomaly: `compiled` and `compiled_state` are often identical early in the
morning and diverge later, because DOS refreshes the statewide aggregate
mid-morning (8:04AM vs 11:01AM on Aug 9).

---

## Why this repo exists

The dashboard is a static HTML block pasted into Solidarity Tech. Solidarity
Tech's public API is CRM-only — no endpoint writes page content — so the block
is pasted **once** and fetches its data at page load.

The alternative was storing a write credential so an external job could push.
This design avoids that: the scheduled job runs *inside* GitHub Actions using the
built-in `GITHUB_TOKEN`, scoped to this repo. **There are no secrets to configure.**

The repo is public because `raw.githubusercontent.com` only serves public files
unauthenticated and sends `access-control-allow-origin: *`, which is what lets
the browser fetch it cross-origin. Everything here is a public record under
§101.657, F.S.

---

## Files

| File | Purpose |
|---|---|
| `scrape.py` | Fetches both sources, appends a day, snapshots the county feed |
| `test_scrape.py` | 22 tests against a fixture of the real page structure |
| `latest.json` | The published artifact |
| `.github/workflows/daily.yml` | Runs at 9:47 a.m. and 12:19 p.m. Eastern |

## How the state parse works

The page is **fully server-rendered** — no JavaScript, no session cookie, no
ASP.NET viewstate — so a plain `requests.get` sees what a browser sees. The
parser targets stable container IDs rather than table position:

| Container | Meaning |
|---|---|
| `#county_ablnotyet` | Vote-by-Mail Provided (Not Yet Returned) — the chase universe |
| `#county_abl` | Voted Vote-by-Mail — returned |
| `#county_evs` | Voted Early — in person |
| `#statewideTotal` | All three statewide, keyed by row label |

County rows are `[County, Election, Republican, Democrat, Other, NPA, Total, Compiled]`.
No nested tables, no colspans, uniform 8 cells.

## County feed gotchas

- The displayed denominator is **`ActiveRegisteredVoters` in `overrides.json`**
  (11,649), *not* `Summary.TotalRegisteredVoters` (10,854). Using the obvious
  field overstates turnout by ~7% and looks entirely plausible.
- It breaks mail down by date **or** by party, never both. Early voting *does*
  have party-by-date, under `LocationDay`.
- Its `LocationParty` block has been observed lagging `LocationDay`. The
  dashboard doesn't read it.

---

## It fails loudly, on purpose

The job exits non-zero — red run, GitHub emails you — if a container ID or the
Taylor row disappears, party counts don't sum to their stated Total, a cumulative
count goes **down**, or the election ID changes.

A red run is recoverable. Silently publishing wrong numbers to the team page is
not, which is why every check aborts rather than guesses.

A county that simply hasn't re-reported is **not** an error — warning logged,
nothing committed, dashboard holds yesterday. A county-feed outage is also not
fatal: `soft_json` warns and the run continues on state figures alone.

---

## Operational notes

- **Cron is UTC and ignores daylight saving.** `47 13` / `19 16` are 9:47 a.m.
  and 12:19 p.m. Eastern through **Nov 1, 2026**, then an hour earlier. Change to
  `47 14` / `19 17` for the general.
- **GitHub schedules are best-effort.** They run 30–60 minutes late routinely and
  the very first firing on this repo was dropped entirely. Two firings a day is
  the safety net — `scrape.py` replaces a same-day entry rather than appending, so
  running twice is harmless. For a guaranteed pull on a specific day, use **Run
  workflow** by hand.
- **Nearly every run commits**, because the county snapshot changes every few
  minutes even when the state figures don't. Expect a noisy commit log; that is
  not a sign of churn in the actual election data.
- **GitHub disables schedules after 60 days of repo inactivity.** There's a gap
  between the Aug 18 primary and the Nov 3 general — push a commit in early
  October or re-enable from the Actions tab.
- **The parser depends on the state's markup.** Container IDs and column order
  verified Aug 7, 2026. A redesign turns the run red rather than guessing.

---

## Rolling over to the November general

1. New election ID from the [stats page][src] ("Election Number - NNNNN").
2. The county feed's election number in its URL path also changes (`/84/` for
   this primary) — read it off the Turnout Quick View link.
3. In `latest.json`: update `election`, `election_id`, `election_date`,
   `ev_start`, `ev_end`, and set `"days": []`. Update `TQV_BASE` in `scrape.py`.
4. Rebuild the dashboard block with the new feed URL and re-paste it.
5. Add an hour to both crons (DST ends Nov 1).
