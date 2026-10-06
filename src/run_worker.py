"""The consumer, as a long-running process (Docker Compose).

MODE=guarded  claims the event id and does the work in one transaction,
              then acknowledges. Safe to run twice.
MODE=naive    does the work and acknowledges. Not safe to run twice.

Kill it with `docker compose kill worker` mid-message and compare.
"""
from __future__ import annotations

import os
import signal
import time

import db
from broker import RabbitBroker
from pipeline import TOPIC, Crasher, worker_drain_guarded, worker_drain_naive

AMQP = os.environ.get("AMQP_URL", "amqp://guest:guest@rabbitmq:5672/")
MODE = os.environ.get("MODE", "guarded")
IDLE_SLEEP = float(os.environ.get("WORKER_SLEEP", "0.25"))

running = True


def stop(signum, frame):
    global running
    running = False
    print("worker: SIGTERM, finishing current message", flush=True)


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    broker = RabbitBroker(AMQP)
    quiet = Crasher()
    drain = worker_drain_naive if MODE == "naive" else worker_drain_guarded
    print(f"worker: started in {MODE} mode", flush=True)

    with db.connect() as conn:
        while running:
            n = drain(conn, broker, quiet, max_messages=50)
            if n:
                print(f"worker: handled {n}", flush=True)
            else:
                time.sleep(IDLE_SLEEP)

    print("worker: stopped cleanly", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
