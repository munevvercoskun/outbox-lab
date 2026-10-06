"""A real HTTP service, so the patterns are not only a simulation.

`docker compose up` gives you this API, Postgres, RabbitMQ, a relay and a
worker as five separate processes. Then you can kill them by hand:

    docker compose kill worker      # mid-work, no acknowledgement
    docker compose kill relay       # mid-publish
    docker compose start worker relay
    curl localhost:8000/invariants  # and see that nothing was lost or doubled

The chaos harness injects crashes in-process, which is deterministic and
repeatable but is not a real process death. This is the real one, by hand.
"""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import db                                       # noqa: E402
from broker import RabbitBroker                 # noqa: E402
from invariants import check                    # noqa: E402
from pipeline import (Crasher, create_order_guarded,   # noqa: E402
                      create_order_naive)

AMQP = os.environ.get("AMQP_URL", "amqp://guest:guest@rabbitmq:5672/")
MODE = os.environ.get("MODE", "guarded")        # guarded | naive

app = FastAPI(title="outbox-lab", version="1.0")
_quiet = Crasher()                               # no injected failures here


class NewOrder(BaseModel):
    customer_id: int = Field(ge=1)
    amount_cents: int = Field(gt=0)


@app.get("/health")
def health():
    """Liveness. Deliberately touches nothing external.

    A liveness check that tests the database restarts every copy of your
    service during a database blip, which turns a 30-second problem into an
    outage. Dependencies belong in /ready.
    """
    return {"status": "ok", "mode": MODE}


@app.get("/ready")
def ready():
    """Readiness. This one is allowed to fail when a dependency is down."""
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready"}


@app.post("/orders", status_code=201)
def create_order(order: NewOrder):
    with db.connect() as conn:
        if MODE == "naive":
            broker = RabbitBroker(AMQP)
            try:
                order_id = create_order_naive(
                    conn, broker, order.customer_id, order.amount_cents, _quiet)
            finally:
                broker.conn.close()
        else:
            # Nothing is published here. The order and the event are written in
            # one transaction and the relay takes it from there, so the API
            # does not even need the broker to be reachable.
            order_id = create_order_guarded(
                conn, None, order.customer_id, order.amount_cents, _quiet)
    return {"order_id": order_id, "mode": MODE}


@app.get("/invariants")
def invariants():
    """What a customer would call correct, checked live."""
    with db.connect() as conn:
        return check(conn).as_dict()


@app.get("/stats")
def stats():
    with db.connect() as conn:
        return db.counts(conn)
