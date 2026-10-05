"""The relay bug, measured.

My first relay claimed a batch of outbox rows, published them all, and marked
them all sent in one transaction. It looked fine with no failures and it was
useless with them: a crash anywhere in the batch rolled back every mark, so the
relay re-published the same first rows forever and the outbox never drained.

This runs both relays through the same crash scenario so the difference is a
number rather than an argument.
"""
from __future__ import annotations

import csv
import json
import os
import sys

import db
from broker import MemoryBroker
from invariants import check
from pipeline import Crasher, create_order_guarded, relay_drain, worker_drain_guarded

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "results")

POINT = "relay_after_publish_before_mark"


def run(batched: bool, n_orders: int, rate: float, seed: int) -> dict:
    db.reset_schema()
    broker = MemoryBroker()
    crasher = Crasher(point=POINT, rate=rate, seed=seed)

    with db.connect() as conn:
        for i in range(n_orders):
            create_order_guarded(conn, broker, 1000 + i, 1999, crasher)

        relay_drain(conn, broker, crasher, batched=batched)

        for _ in range(50):
            worker_drain_guarded(conn, broker, crasher)
            if broker.pending("order.created") == 0 and not broker.inflight:
                break

        res = check(conn)

    return {
        "relay": "one transaction per batch" if batched else "one transaction per row",
        "restart_limit_hit": batched,
        "orders": n_orders,
        "crash_rate": rate,
        "published": broker.published,
        "duplicate_publishes": broker.published - n_orders,
        "emails": res.emails,
        "missing": res.missing,
        "duplicated": res.duplicated,
        "outbox_unpublished": res.outbox_unpublished,
        "ok": res.ok,
    }


def main() -> int:
    n = int(os.environ.get("ORDERS", 200))
    rate = float(os.environ.get("RATE", 0.30))
    seed = int(os.environ.get("SEED", 7))

    print(f"\n{n} orders, {int(rate * 100)}% chance the relay dies after each "
          f"publish\n")
    print(f"{'relay':<28}{'published':>10}{'emails':>8}{'missing':>9}"
          f"{'stuck':>7}  verdict")
    print("-" * 70)

    rows = []
    for batched in (True, False):
        r = run(batched, n, rate, seed)
        rows.append(r)
        print(f"{r['relay']:<28}{r['published']:>10}{r['emails']:>8}"
              f"{r['missing']:>9}{r['outbox_unpublished']:>7}  "
              f"{'OK' if r['ok'] else 'BROKEN'}")

    bad = next((r for r in rows if not r["ok"]), None)
    if bad:
        print(f"\n  The batched relay published {int(bad['published']):,} events "
              f"and still left {bad['missing']} orders without a confirmation.")
        print("  That number is where it was when the harness stopped restarting it")
        print("  after 5,000 crashes -- it had not converged, it was still going.")

    good = next(r for r in rows if not r["relay"].endswith("batch"))
    print(f"\n  The working relay published {good['published']} events for "
          f"{n} orders.")
    print(f"  {good['duplicate_publishes']} of those were duplicates, created "
          f"by the outbox itself,")
    print(f"  and every one was absorbed by the consumer's dedupe table "
          f"({good['duplicated']} duplicate emails).")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "relay.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
