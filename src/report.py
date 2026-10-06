"""Rewrite the README's results section from results/*.csv.

The tables are generated so the prose and the committed data cannot disagree.
It also prints an OBSERVATIONS block saying what the numbers claim, so you can
check the written findings against them.
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "results")
README = os.path.join(ROOT, "README.md")

START = "<!-- RESULTS:START -->"
END = "<!-- RESULTS:END -->"

PRETTY = {
    "none": "No failures",
    "crash_after_commit": "Service dies between saving the order and publishing",
    "crash_before_ack": "Worker dies after the work, before acknowledging",
    "relay_crash": "Relay dies after publishing, before marking it sent",
    "duplicate_delivery": "Broker delivers every message twice",
    "everything": "All of the above, together",
}


def read(name):
    path = os.path.join(RES, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def section_scenarios(obs):
    rows = read("chaos.csv")
    if not rows:
        return ""

    order, by = [], {}
    for r in rows:
        if r["scenario"] not in by:
            by[r["scenario"]] = {}
            order.append(r["scenario"])
        by[r["scenario"]][r["mode"]] = r

    n = int(rows[0]["orders"])
    out = [f"## Six failures, {n} orders, two versions of the same system", "",
           "Every number is a customer outcome. **Missing** means an order was "
           "placed and no confirmation ever went out. **Duplicate** means the "
           "same confirmation went out more than once — if it were a charge "
           "instead of an email, that is real money.", "",
           "| What breaks | Naive: missing | Naive: duplicate | Guarded: missing | "
           "Guarded: duplicate |", "|---|---|---|---|---|"]

    naive_bad = guarded_bad = 0
    for s in order:
        nv, gd = by[s].get("naive"), by[s].get("guarded")
        if not nv or not gd:
            continue

        def cell(v):
            v = int(v)
            return f"**{v}**" if v else "0"

        out.append(f"| {PRETTY.get(s, s)} | {cell(nv['missing'])} | "
                   f"{cell(nv['duplicated'])} | {cell(gd['missing'])} | "
                   f"{cell(gd['duplicated'])} |")
        if int(nv["missing"]) or int(nv["duplicated"]):
            naive_bad += 1
        if int(gd["missing"]) or int(gd["duplicated"]):
            guarded_bad += 1

    out += ["", "![what each failure costs](results/1_scenarios.png)", ""]
    out.append(f"**The naive version broke in {naive_bad} of {len(order)} "
               f"scenarios. The guarded version broke in {guarded_bad}.**")
    obs.append(f"naive broke in {naive_bad}/{len(order)} scenarios, "
               f"guarded in {guarded_bad}")

    worst_missing = max((int(by[s]['naive']['missing']) for s in order
                         if 'naive' in by[s]), default=0)
    worst_dupes = max((int(by[s]['naive']['duplicated']) for s in order
                       if 'naive' in by[s]), default=0)
    if worst_missing:
        obs.append(f"worst case the naive version lost {worst_missing} of {n} "
                   f"confirmations ({worst_missing / n * 100:.0f}%)")
    if worst_dupes:
        obs.append(f"worst case the naive version sent {worst_dupes} duplicate "
                   f"emails on {n} orders")

    rc = by.get("relay_crash", {}).get("guarded")
    if rc and int(rc["published"]) > int(rc["orders"]):
        extra = int(rc["published"]) - int(rc["orders"])
        out += ["", f"**The row to look at is the relay one.** The guarded "
                    f"version published **{rc['published']} events for "
                    f"{rc['orders']} orders** in that scenario — "
                    f"{extra} of them duplicates that the outbox itself "
                    f"created. Zero duplicate emails came out the other end, "
                    f"because the consumer deduplicates. Neither pattern would "
                    f"have been enough alone."]
        obs.append(f"outbox created {extra} duplicate publishes on "
                   f"{rc['orders']} orders, all absorbed by dedupe")
    return "\n".join(out)


def section_relay(obs):
    rows = read("relay.csv")
    if not rows:
        return ""
    out = ["## The relay bug, measured", "",
           "Both relays do the same job under the same 30% crash rate. The "
           "only difference is how often they commit.", "",
           "| Relay | Events published | Emails delivered | Orders with no email | "
           "Stuck in the outbox |", "|---|---|---|---|---|"]
    for r in rows:
        ok = r["ok"] == "True"
        out.append(f"| {r['relay']} | {int(r['published']):,} | {r['emails']} | "
                   f"{'**' + r['missing'] + '**' if int(r['missing']) else '0'} | "
                   f"{'**' + r['outbox_unpublished'] + '**' if int(r['outbox_unpublished']) else '0'} |")
        if not ok:
            obs.append(f"the batched relay published {int(r['published']):,} "
                       f"events for {r['orders']} orders and still failed")
    out += ["", "![relay comparison](results/2_relay.png)", ""]

    bad = next((r for r in rows if r["ok"] != "True"), None)
    good = next((r for r in rows if r["ok"] == "True"), None)
    if bad and good:
        ratio = int(bad["published"]) / max(int(good["published"]), 1)
        out.append(f"The batched relay did **{ratio:.0f}× the work** and still "
                   f"left {bad['missing']} orders without a confirmation. That "
                   f"publish count is not a final figure — it is where the "
                   f"harness stopped restarting it after 5,000 crashes, and it "
                   f"had not converged. Committing one row at a time is not an "
                   f"optimisation; it is the difference between working and not.")
        obs.append(f"batched relay did {ratio:.0f}x the work and still failed")
    return "\n".join(out)


def main() -> int:
    if not os.path.isdir(RES) or not os.listdir(RES):
        print("no results/ yet -- run `make chaos` first", file=sys.stderr)
        return 1

    obs = []
    parts = [p for p in (section_scenarios(obs), section_relay(obs)) if p]
    body = "\n\n".join(parts)

    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    if START not in text or END not in text:
        print(f"README is missing the {START} / {END} markers", file=sys.stderr)
        return 1

    new = text.split(START)[0] + START + "\n\n" + body + "\n\n" + END + \
        text.split(END)[1]

    if new == text:
        print("README already matches results/ -- nothing to do")
    else:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("rewrote the README results section from results/*.csv")

    print("\n--- OBSERVATIONS " + "-" * 52)
    for o in obs:
        print("  * " + o)
    print("\n  The tables are now yours. The PROSE is not:")
    print("  check 'What I got wrong' against these lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
