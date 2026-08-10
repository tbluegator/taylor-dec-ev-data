#!/usr/bin/env python3
"""
Tests for scrape.py, run against a fixture that reproduces the live page's
structure exactly as observed on 2026-08-07: container ids, a TH header row,
TD data rows, 8 cells, comma-grouped and zero-padded numbers, and the two
different 'Compiled' timestamp formats (county uses a space, statewide a
newline).

Run: python3 test_scrape.py
"""
import json
import sys
import tempfile
import os

import scrape
from scrape import parse, ParseError, monotonic_check

HDR = ("<tr>" + "".join(f"<th>{h}</th>" for h in
       ["County", "Election", "Republican", "Democrat", "Other",
        "No Party Affiliation", "Total", "Compiled"]) + "</tr>")

SW_HDR = ("<tr>" + "".join(f"<th>{h}</th>" for h in
          ["Stats Type", "Republican", "Democrat", "Other",
           "No Party Affiliation", "Total", "Compiled", ""]) + "</tr>")


def county_tbl(cid, rows):
    body = HDR + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div id="{cid}" class="ui-tabs-panel"><table><tbody>{body}</tbody></table></div>'


def page(taylor_out, taylor_ret, taylor_ev, sw=None, election="49893"):
    sw = sw or [
        ["Vote-by-Mail Provided (Not Yet Returned)", "416,178", "540,681", "29,557",
         "256,826", "1,243,242", "08/07/2026\n11:01AM", "<a href='#'>Download File</a>"],
        ["Voted Vote-by-Mail", "265,010", "300,560", "12,732", "103,937", "682,239",
         "08/07/2026\n11:01AM", "<a href='#'>Download File</a>"],
        ["Voted Early", "20,101", "16,313", "359", "3,001", "39,774",
         "08/07/2026\n11:01AM", "<a href='#'>Download File</a>"],
    ]
    swbody = SW_HDR + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in sw)
    other = [["Alachua", "49893 - Primary", "1,000", "2,000", "50", "300", "3,350", "08/07/2026 8:00AM"]]
    return f"""<!DOCTYPE html><html><body>
<span>Election Number - {election}       Election Date - 08/18/2026</span>
<h2>Statewide Totals</h2>
<div id="statewideTotal"><table><tbody>{swbody}</tbody></table></div>
<h2>County Totals</h2>
{county_tbl("county_ablnotyet", other + [taylor_out])}
{county_tbl("county_abl", other + [taylor_ret])}
{county_tbl("county_evs", other + [taylor_ev])}
</body></html>"""


AUG7 = page(
    ["Taylor", "49893 - Primary", "217", "174", "01", "07", "399", "08/07/2026 8:03AM"],
    ["Taylor", "49893 - Primary", "130", "73", "02", "01", "206", "08/07/2026 8:03AM"],
    ["Taylor", "49893 - Primary", "0", "0", "0", "0", "0", ""],
)

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   {detail}"))
    if not cond:
        fails.append(name)


def expect_raise(name, fn, frag):
    try:
        fn()
        check(name, False, "expected ParseError, none raised")
    except ParseError as e:
        check(name, frag.lower() in str(e).lower(), f"message was: {e}")


print("parse() on the Aug 7 fixture")
r = parse(AUG7)
check("D outstanding = 174", r["taylor"]["vbm_outstanding"]["D"] == 174, r["taylor"]["vbm_outstanding"])
check("R outstanding = 217", r["taylor"]["vbm_outstanding"]["R"] == 217)
check("zero-padded '01' -> 1", r["taylor"]["vbm_outstanding"]["O"] == 1)
check("zero-padded '07' -> 7", r["taylor"]["vbm_outstanding"]["NPA"] == 7)
check("D returned = 73", r["taylor"]["vbm_returned"]["D"] == 73)
check("early voted all zero", r["taylor"]["early_voted"]["T"] == 0)
check("county compiled stamp", r["compiled"] == "08/07/2026 8:03AM", r["compiled"])
check("statewide newline stamp normalised",
      r["compiled_state"] == "08/07/2026 11:01AM", r["compiled_state"])
check("statewide D outstanding = 540681",
      r["statewide"]["vbm_outstanding"]["D"] == 540681)
check("comma parsing 1,243,242", r["statewide"]["vbm_outstanding"]["T"] == 1243242)
check("statewide early D = 16313", r["statewide"]["early_voted"]["D"] == 16313)
check("election id extracted", r["election_id"] == "49893", r["election_id"])
check("picks Taylor, not Alachua", r["taylor"]["vbm_returned"]["R"] == 130)

print("\nderived numbers match the dashboard")
d_out, d_ret = r["taylor"]["vbm_outstanding"]["D"], r["taylor"]["vbm_returned"]["D"]
rt = 100 * d_ret / (d_ret + d_out)
check("D return rate 29.55%", abs(rt - 29.5547) < 0.01, f"{rt:.4f}")

print("\nfailure modes")
expect_raise("missing county container",
             lambda: parse(AUG7.replace('id="county_abl"', 'id="county_xxx"')), "county_abl")
expect_raise("county absent from table",
             lambda: parse(AUG7.replace(">Taylor<", ">Suwannee<")), "no row for county")
bad = page(
    ["Taylor", "49893 - Primary", "217", "174", "01", "07", "999", "08/07/2026 8:03AM"],
    ["Taylor", "49893 - Primary", "130", "73", "02", "01", "206", "08/07/2026 8:03AM"],
    ["Taylor", "49893 - Primary", "0", "0", "0", "0", "0", ""])
expect_raise("party sum != stated total", lambda: parse(bad), "parties sum to")
expect_raise("non-numeric cell",
             lambda: parse(AUG7.replace("<td>217</td>", "<td>n/a?</td>")), "expected a number")
expect_raise("missing statewide row",
             lambda: parse(AUG7.replace("Voted Early", "Voted Sometime")), "missing rows")

print("\nmonotonic guard")
prev = {"taylor": {"vbm_returned": {"D": 100, "R": 200}, "early_voted": {"D": 10, "R": 20}}}
ok = {"taylor": {"vbm_returned": {"D": 110, "R": 210}, "early_voted": {"D": 12, "R": 25}}}
back = {"taylor": {"vbm_returned": {"D": 90, "R": 210}, "early_voted": {"D": 12, "R": 25}}}
try:
    monotonic_check(prev, ok)
    check("increasing counts accepted", True)
except ParseError as e:
    check("increasing counts accepted", False, str(e))
expect_raise("returned count going backwards", lambda: monotonic_check(prev, back), "backwards")
try:
    monotonic_check(None, ok)
    check("no previous day is fine", True)
except Exception as e:
    check("no previous day is fine", False, str(e))

print("\nmail party backfill")
from scrape import MAIL_BACKFILL as MB, MAIL_BACKFILL_THROUGH as MBT
import re as _re
check("every key is MM/DD/YYYY",
      all(_re.fullmatch(r"\d{2}/\d{2}/\d{4}", k) for k in MB),
      [k for k in MB if not _re.fullmatch(r"\d{2}/\d{2}/\d{4}", k)])
check("no negative counts",
      all(v >= 0 for r in MB.values() for v in r.values()))
check("every row has D, R, other",
      all(set(r) == {"D", "R", "other"} for r in MB.values()))
check("complete_through is a real date in the table", MBT in MB, MBT)
tD = sum(r["D"] for r in MB.values()); tR = sum(r["R"] for r in MB.values())
check("D total = 89", tD == 89, tD)
check("R total = 145", tR == 145, tR)

# The whole point of the backfill is that it reproduces the method it replaces.
# These two days are the only ones both methods can see; if they ever diverge,
# the file was misread and every earlier day is suspect.
for day, D, R in (("08/07/2026", 10, 9), ("08/08/2026", 4, 4)):
    check(f"backfill {day} matches snapshot delta (D{D}/R{R})",
          MB[day]["D"] == D and MB[day]["R"] == R, MB[day])

# A day past complete_through is a partial count and must never be trusted.
check("Aug 9 is past complete_through",
      _re.sub(r"(\d\d)/(\d\d)/(\d{4})", r"\3\1\2", "08/09/2026") >
      _re.sub(r"(\d\d)/(\d\d)/(\d{4})", r"\3\1\2", MBT))

print("\nbackfill survives a latest.json round-trip")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump({"days": [], "election_id": "49893", "hand_edited": "keep me"}, f)
    tmp = f.name
_d = json.load(open(tmp))
_d["mail_party_backfill"] = {"days": MB, "complete_through": MBT}
json.dump(_d, open(tmp, "w"))
_r = json.load(open(tmp))
check("unrelated keys preserved", _r.get("hand_edited") == "keep me")
check("backfill readable after write", _r["mail_party_backfill"]["days"]["07/10/2026"]["D"] == 1)
os.unlink(tmp)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all tests passed")
