"""Break it on purpose, then count what broke.

Each scenario runs the whole system end to end -- create orders, publish,
consume -- with one failure injected at a chosen point, and then checks the
invariants. Both versions face exactly the same failures.

Everything is seeded, so a run is reproducible. A benchmark you cannot repeat
is an anecdote.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import db
from broker import MemoryBroker
from invariants import check
from pipeline import (Crasher, InjectedCrash, create_order_guarded,
                      create_order_naive, relay_drain, worker_drain_guarded,
                      worker_drain_naive)

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "results")

# name -> (injection point, rate, duplicate every message?, what it models)
SCENARIOS = {
    "none": (
        None, 0.0, False,
        "No failures at all. Both versions should be perfect here"),
    "crash_after_commit": (
        "after_commit_before_publish", 0.30, False,
        "The service dies between saving the order and publishing the event"),
    "crash_before_ack": (
        "after_work_before_ack", 0.30, False,
        "The worker dies after doing the work, before acknowledging"),
    "relay_crash": (
        "relay_after_publish_before_mark", 0.30, False,
        "The relay dies after publishing, before recording that it did"),
    "duplicate_delivery": (
        None, 0.0, True,
        "The broker delivers every message twice"),
    "everything": (
        "after_work_before_ack", 0.30, True,
        "Worker crashes AND duplicate deliveries, together"),
}


def run(mode: str, scenario: str, n_orders: int, seed: int) -> dict:
    point, rate, dupes, _desc = SCENARIOS[scenario]

    db.reset_schema()
    broker = MemoryBroker(duplicate_every_message=dupes)
    crasher = Crasher(point=point, rate=rate, seed=seed)

    with db.connect() as conn:
        # --- the order service -------------------------------------------
        for i in range(n_orders):
            try:
                if mode == "naive":
                    create_order_naive(conn, broker, 1000 + i, 1999, crasher)
                else:
                    create_order_guarded(conn, broker, 1000 + i, 1999, crasher)
            except InjectedCrash:
                # The process died mid-request. A supervisor restarts it; the
                # request itself is lost, which is fine -- the customer sees an
                # error and retries. What must NOT be lost is an order that was
                # already committed.
                conn.rollback()

        # --- the relay (guarded only) ------------------------------------
        if mode == "guarded":
            relay_drain(conn, broker, crasher)

        # --- the worker --------------------------------------------------
        # Loop because an injected crash requeues messages; a supervisor would
        # restart the worker and it would pick them up again.
        for _ in range(50):
            if mode == "naive":
                worker_drain_naive(conn, broker, crasher)
            else:
                worker_drain_guarded(conn, broker, crasher)
            if broker.pending("order.created") == 0 and not broker.inflight:
                break

        res = check(conn)

    out = res.as_dict()
    out.update(mode=mode, scenario=scenario, seed=seed,
               published=broker.published, delivered=broker.delivered,
               crashes=crasher.fired)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scenario", default="all")
    args = ap.parse_args()

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    rows = []

    print(f"\n{'scenario':<22} {'version':<9} {'orders':>7} {'emails':>7} "
          f"{'missing':>8} {'dupes':>7} {'stuck':>6}  verdict")
    print("-" * 82)

    for name in names:
        for mode in ("naive", "guarded"):
            r = run(mode, name, args.orders, args.seed)
            rows.append(r)
            verdict = "OK" if r["ok"] else "BROKEN"
            print(f"{name:<22} {mode:<9} {r['orders']:>7} {r['emails']:>7} "
                  f"{r['missing']:>8} {r['duplicated']:>7} "
                  f"{r['outbox_unpublished']:>6}  {verdict}")
        print()

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "chaos.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(RESULTS, "scenarios.json"), "w",
              encoding="utf-8") as fh:
        json.dump({k: {"point": v[0], "rate": v[1], "duplicates": v[2],
                       "describes": v[3]} for k, v in SCENARIOS.items()},
                  fh, indent=2)

    bad = [r for r in rows if not r["ok"]]
    print(f"wrote {path}")
    print(f"{len(rows) - len(bad)} of {len(rows)} runs held every invariant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
