#!/usr/bin/env python3
"""
Taylor County DEC - Early Vote & VBM scraper.

Pulls the Florida Division of Elections public statistics page and appends one
record per day to latest.json.

The page is fully server-rendered - no JavaScript, no session cookie, no
viewstate - which is why a plain requests.get works here. Structure verified
live against the page on 2026-08-07:

  #statewideTotal   table, 1 TH header row + 3 TD rows keyed by column 0:
                      "Vote-by-Mail Provided (Not Yet Returned)"
                      "Voted Vote-by-Mail"
                      "Voted Early"
  #county_ablnotyet table, 1 TH header row + 67 county rows  -> VBM outstanding
  #county_abl       table, same shape                        -> VBM returned
  #county_evs       table, same shape                        -> early voted

  County row cells: [County, Election, Republican, Democrat, Other, NPA, Total, Compiled]
  No nested tables, no colspans, uniform 8 cells per row.

Exits non-zero on any parse failure so the workflow goes red and GitHub emails
you, rather than silently publishing stale data.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats"

# The county Supervisor of Elections' own live feed (VR Systems "Turnout Quick
# View"). Refreshes every few minutes and, unlike the state file, shows in-person
# early votes the same day. It counts ballots CAST only - it has no measure of
# outstanding ballots, so it supplements the state file rather than replacing it.
# Snapshotted here so the dashboard has a stored history and a fallback if the
# live fetch is ever blocked browser-side.
TQV_BASE = "https://s3.amazonaws.com/turnoutquickview.electionsfl.org/data/FL/TAY/84/"
TQV_URL = TQV_BASE + "data.json"
TQV_OV_URL = TQV_BASE + "overrides.json"
COUNTY = os.environ.get("COUNTY", "Taylor")
OUT = os.environ.get("OUT", "latest.json")
TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Daily mail-return party split, backfilled.
#
# The dashboard splits each day's mail returns by party using the change between
# two consecutive state snapshots. That only works from the day we started
# snapshotting (Aug 7) - every earlier day could only be drawn as an all-party
# total, because the county's live feed breaks mail down by date OR by party,
# never both.
#
# The county's voter-level VBM file carries a BallotReturnDate on every record,
# so those earlier days can simply be counted. Done once, from the file pulled
# 2026-08-09, and hard-coded here because it never changes: nobody returns a
# ballot dated July 16 any more. Historical days are settled.
#
# Cross-checked against the snapshot-delta method on the two days both cover,
# and they agree exactly - Aug 7: D 10 / R 9; Aug 8: D 4 / R 4.
#
# COMPLETE_THROUGH matters. The file was pulled during the day on Aug 9, so its
# Aug 9 row is a partial count. The dashboard ignores backfill past this date
# and waits for the snapshot delta instead.
#
# Only these aggregate daily counts live here. The underlying file is a
# restricted record under s.101.62(2), F.S., released to the county party for
# political purposes; no voter-level data belongs in this public repo.
# ---------------------------------------------------------------------------
MAIL_BACKFILL_THROUGH = "08/08/2026"
MAIL_BACKFILL = {
    "07/10/2026": {"D": 1, "R": 0, "other": 0},
    "07/15/2026": {"D": 6, "R": 4, "other": 0},
    "07/16/2026": {"D": 5, "R": 9, "other": 0},
    "07/17/2026": {"D": 0, "R": 2, "other": 0},
    "07/19/2026": {"D": 5, "R": 0, "other": 0},
    "07/20/2026": {"D": 2, "R": 8, "other": 1},
    "07/21/2026": {"D": 1, "R": 12, "other": 1},
    "07/22/2026": {"D": 3, "R": 11, "other": 0},
    "07/23/2026": {"D": 3, "R": 6, "other": 0},
    "07/24/2026": {"D": 4, "R": 4, "other": 0},
    "07/27/2026": {"D": 6, "R": 8, "other": 0},
    "07/28/2026": {"D": 2, "R": 8, "other": 0},
    "07/29/2026": {"D": 7, "R": 4, "other": 0},
    "07/30/2026": {"D": 3, "R": 14, "other": 0},
    "07/31/2026": {"D": 6, "R": 7, "other": 0},
    "08/03/2026": {"D": 8, "R": 6, "other": 0},
    "08/04/2026": {"D": 6, "R": 8, "other": 0},
    "08/05/2026": {"D": 3, "R": 12, "other": 1},
    "08/06/2026": {"D": 3, "R": 8, "other": 0},
    "08/07/2026": {"D": 10, "R": 9, "other": 0},
    "08/08/2026": {"D": 4, "R": 4, "other": 0},
    "08/09/2026": {"D": 1, "R": 1, "other": 0},
}

# container id -> key in our record
SECTIONS = {
    "county_ablnotyet": "vbm_outstanding",
    "county_abl": "vbm_returned",
    "county_evs": "early_voted",
}
# statewide row label (lowercased, normalised) -> key
STATEWIDE_ROWS = {
    "vote-by-mail provided (not yet returned)": "vbm_outstanding",
    "voted vote-by-mail": "vbm_returned",
    "voted early": "early_voted",
}
# cell index -> party key, for both statewide and county tables
PARTY_COLS = {2: "R", 3: "D", 4: "O", 5: "NPA", 6: "T"}
STATE_PARTY_COLS = {1: "R", 2: "D", 3: "O", 4: "NPA", 5: "T"}


class ParseError(RuntimeError):
    pass


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def num(s: str) -> int:
    """'1,243,242' -> 1243242 ; '07' -> 7 ; '' -> 0"""
    s = norm(s).replace(",", "")
    if s in ("", "-", "N/A"):
        return 0
    if not re.fullmatch(r"\d+", s):
        raise ParseError(f"expected a number, got {s!r}")
    return int(s)


def cells(tr):
    return tr.find_all(["td", "th"], recursive=False)


def table_in(soup, container_id):
    div = soup.find(id=container_id)
    if div is None:
        raise ParseError(f"container #{container_id} not found")
    t = div.find("table")
    if t is None:
        raise ParseError(f"no <table> inside #{container_id}")
    return t


def county_row(table, county, container_id):
    for tr in table.find_all("tr"):
        cs = cells(tr)
        if cs and norm(cs[0].get_text()).lower() == county.lower():
            if len(cs) < 8:
                raise ParseError(f"#{container_id}: {county} row has {len(cs)} cells, expected 8")
            return cs
    raise ParseError(f"#{container_id}: no row for county {county!r}")


def parse(html, county=COUNTY):
    soup = BeautifulSoup(html, "html.parser")

    taylor, statewide, compiled, compiled_state = {}, {}, "", ""

    for cid, key in SECTIONS.items():
        cs = county_row(table_in(soup, cid), county, cid)
        taylor[key] = {p: num(cs[i].get_text()) for i, p in PARTY_COLS.items()}
        stamp = norm(cs[7].get_text())
        if stamp and not compiled:
            compiled = stamp

    sw = table_in(soup, "statewideTotal")
    seen = set()
    for tr in sw.find_all("tr"):
        cs = cells(tr)
        if len(cs) < 7:
            continue
        label = norm(cs[0].get_text()).lower()
        key = STATEWIDE_ROWS.get(label)
        if not key:
            continue
        statewide[key] = {p: num(cs[i].get_text()) for i, p in STATE_PARTY_COLS.items()}
        seen.add(key)
        stamp = norm(cs[6].get_text())
        if stamp and not compiled_state:
            compiled_state = stamp

    missing = set(SECTIONS.values()) - seen
    if missing:
        raise ParseError(f"#statewideTotal missing rows for: {sorted(missing)}")

    m = re.search(r"Election Number\s*-\s*(\d+)", soup.get_text())
    election_id = m.group(1) if m else None

    # Integrity: parties must sum to the stated total.
    for scope_name, scope in (("taylor", taylor), ("statewide", statewide)):
        for key, v in scope.items():
            s = v["R"] + v["D"] + v["O"] + v["NPA"]
            if s != v["T"]:
                raise ParseError(
                    f"{scope_name}.{key}: parties sum to {s} but Total says {v['T']}"
                )

    return {
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
        "compiled": compiled,
        "compiled_state": compiled_state,
        "taylor": taylor,
        "statewide": statewide,
        "election_id": election_id,
    }


def monotonic_check(prev, cur):
    """Cumulative counts must never go down. Catches a half-published file."""
    if not prev:
        return
    for key in ("vbm_returned", "early_voted"):
        for p in ("D", "R"):
            if cur["taylor"][key][p] < prev["taylor"][key][p]:
                raise ParseError(
                    f"taylor.{key}.{p} went backwards: "
                    f"{prev['taylor'][key][p]} -> {cur['taylor'][key][p]}"
                )


def soft_json(url):
    """Fetch optional JSON. Never raises - a county-feed outage must not fail the
    run, because the state figures are the ones the dashboard cannot do without."""
    try:
        r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"::warning::county live feed unavailable ({url}): {e}")
        return None


def main():
    r = requests.get(URL, timeout=45, headers={
        "User-Agent": "taylor-dec-ev-tracker/1.0 (+https://github.com/)",
        "Accept": "text/html",
    })
    r.raise_for_status()
    rec = parse(r.text)

    with open(OUT) as f:
        data = json.load(f)

    if rec["election_id"] and rec["election_id"] != data.get("election_id"):
        raise ParseError(
            f"page is showing election {rec['election_id']} but latest.json tracks "
            f"{data.get('election_id')}. Roll the file over deliberately."
        )
    rec.pop("election_id", None)

    days = data["days"]
    prev = days[-1] if days else None

    if prev and prev["compiled"] == rec["compiled"] and prev["date"] != rec["date"]:
        print(f"::warning::county file still stamped {rec['compiled']} - not re-reported yet")

    monotonic_check(prev, rec)

    if prev and prev["date"] == rec["date"]:
        days[-1] = rec
        print(f"replaced {rec['date']}")
    else:
        days.append(rec)
        print(f"appended {rec['date']}")

    data["days"] = days
    data["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")

    # Written every run rather than once by hand, so the key survives any future
    # rebuild of the file. The value is a constant, so this produces no diff
    # after the first run.
    data["mail_party_backfill"] = {
        "source": "Taylor County SOE voter-level vote-by-mail file, pulled 2026-08-09",
        "method": "count of BallotReturnDate by Party, per voter record",
        "complete_through": MAIL_BACKFILL_THROUGH,
        "note": (
            "Aggregate daily party counts only - the underlying file is restricted "
            "under s.101.62(2), F.S. and no voter-level data appears here. Verified "
            "against the snapshot-delta method where they overlap (Aug 7, Aug 8). "
            "Historical days are settled and do not change."
        ),
        "days": MAIL_BACKFILL,
    }

    # Snapshot the county live feed alongside the state figures.
    data["tqv_url"] = TQV_URL
    data["tqv_overrides_url"] = TQV_OV_URL
    tqv = soft_json(TQV_URL)
    tqv_ov = soft_json(TQV_OV_URL)
    if tqv:
        data["tqv"] = tqv
        party = (tqv.get("Turnout") or {}).get("PartyType") or {}
        dem, rep = party.get("DEM", {}), party.get("REP", {})
        print(f"  county feed   D early {dem.get('EarlyVoting', 0)} / mail {dem.get('Mail', 0)}"
              f"   R early {rep.get('EarlyVoting', 0)} / mail {rep.get('Mail', 0)}")
    if tqv_ov:
        data["tqv_overrides"] = tqv_ov

    with open(OUT, "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")

    t = rec["taylor"]
    d_out, d_ret = t["vbm_outstanding"]["D"], t["vbm_returned"]["D"]
    r_out, r_ret = t["vbm_outstanding"]["R"], t["vbm_returned"]["R"]
    dr = 100 * d_ret / (d_ret + d_out) if (d_ret + d_out) else 0
    rr = 100 * r_ret / (r_ret + r_out) if (r_ret + r_out) else 0
    print(f"  D outstanding {d_out}  return {dr:.1f}%   R return {rr:.1f}%   gap {dr-rr:+.1f} pts")
    print(f"  early voted   D {t['early_voted']['D']}  R {t['early_voted']['R']}")


if __name__ == "__main__":
    try:
        main()
    except (ParseError, requests.RequestException) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
