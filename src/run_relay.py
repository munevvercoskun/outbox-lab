"""The outbox relay, as a long-running process (Docker Compose).

It does one thing forever: find unpublished outbox rows, publish them, mark
them sent -- one transaction per row, so a crash loses at most one row's
worth of progress.

Kill it with `docker compose kill relay` while it works. Nothing is lost,
because the events are in the database; some will be published twice, which
is what the consumer's dedupe table is for.
"""
from __future__ import annotations

import os
import signal
import sys
import time

import db
from broker import RabbitBroker
from pipeline import Crasher, relay_once

AMQP = os.environ.get("AMQP_URL", "amqp://guest:guest@rabbitmq:5672/")
IDLE_SLEEP = float(os.environ.get("RELAY_SLEEP", "0.25"))

running = True


def stop(signum, frame):
    """Graceful shutdown. Finish the row in flight, then exit."""
    global running
    running = False
    print("relay: SIGTERM, draining", flush=True)


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    broker = RabbitBroker(AMQP)
    quiet = Crasher()
    print("relay: started", flush=True)

    with db.connect() as conn:
        while running:
            try:
                n = relay_once(conn, broker, quiet, batch=100)
            except Exception as exc:                  # noqa: BLE001
                print(f"relay: {exc}", file=sys.stderr, flush=True)
                conn.rollback()
                time.sleep(1.0)
                continue
            if n:
                print(f"relay: published {n}", flush=True)
            else:
                time.sleep(IDLE_SLEEP)

    print("relay: stopped cleanly", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
