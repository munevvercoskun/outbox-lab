"""The two versions of the same system.

NAIVE is how almost everyone writes it the first time:

    commit the order
    publish the event          <-- a gap. Crash here and the event never exists.
    consumer: do the work, then acknowledge
                               <-- another gap. Crash here and the work repeats.

GUARDED closes both gaps, with two patterns that are useless on their own:

    one transaction: order row AND event row
    a relay publishes from the outbox and marks it sent
    consumer: claim the event id, do the work, same transaction, then ack

The important and slightly annoying part is that the outbox makes duplicates
MORE likely, not less. The relay can publish and die before marking the row
sent, so it publishes again next time. You have traded "sometimes lost" for
"sometimes twice", which is only an improvement if the consumer is idempotent.
That is why both patterns are here. Either alone is a half-fix.
"""
from __future__ import annotations

import json
import random

import psycopg

from broker import new_event_id

TOPIC = "order.created"
MAX_ATTEMPTS = 3


class InjectedCrash(RuntimeError):
    """Raised where a real process would have died."""


class Crasher:
    """Fault injection at named points.

    Crashes are raised, not SIGKILLed. That models losing the process at that
    exact instruction, which is the thing being tested, and it keeps the run
    deterministic for a given seed. It does NOT test what happens to a half-
    written disk page, and the README says so.
    """

    def __init__(self, point: str | None = None, rate: float = 0.0,
                 seed: int = 1) -> None:
        self.point = point
        self.rate = rate
        self.rng = random.Random(seed)
        self.fired = 0

    def maybe(self, point: str) -> None:
        if point == self.point and self.rng.random() < self.rate:
            self.fired += 1
            raise InjectedCrash(point)


# --- the order service ----------------------------------------------------

def create_order_naive(conn, broker, customer_id: int, amount: int,
                       crasher: Crasher) -> int:
    """Commit, then publish. Two systems, two writes, a gap in between."""
    with conn.transaction():
        order_id = conn.execute(
            "INSERT INTO orders (customer_id, amount_cents) VALUES (%s, %s) "
            "RETURNING order_id", (customer_id, amount)).fetchone()[0]

    # The transaction is committed. The order exists. The event does not.
    crasher.maybe("after_commit_before_publish")

    broker.publish(TOPIC, new_event_id(), {"order_id": order_id})
    return order_id


def create_order_guarded(conn, broker, customer_id: int, amount: int,
                         crasher: Crasher) -> int:
    """One transaction for both writes. Nothing publishes here at all."""
    with conn.transaction():
        order_id = conn.execute(
            "INSERT INTO orders (customer_id, amount_cents) VALUES (%s, %s) "
            "RETURNING order_id", (customer_id, amount)).fetchone()[0]
        conn.execute(
            "INSERT INTO outbox (event_id, topic, payload) VALUES (%s, %s, %s)",
            (new_event_id(), TOPIC, json.dumps({"order_id": order_id})))

    # Same crash point as the naive version. Here it costs nothing: the event
    # is already durable, and the relay will find it.
    crasher.maybe("after_commit_before_publish")
    return order_id


# --- the relay (guarded only) --------------------------------------------

def relay_once(conn, broker, crasher: Crasher, batch: int = 50) -> int:
    """Publish unsent outbox rows, ONE TRANSACTION PER ROW.

    The per-row transaction is the whole design, and I got it wrong the first
    time. My first version claimed a batch of 50, published them all, and
    marked them all sent in one transaction. Under a 30% crash rate a batch of
    50 essentially never completes, every crash rolled back every mark in that
    batch, and the relay re-published the same first few events forever while
    the rest of the outbox never moved. It made no progress at all.

    Committing per row means a crash loses at most one row's worth of progress.

    FOR UPDATE SKIP LOCKED is what lets several relays run at once: each one
    locks the row it took and the others step over it instead of queueing
    behind it. Without SKIP LOCKED, two relays are one relay with extra steps.
    """
    published = 0
    for _ in range(batch):
        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT event_id, topic, payload FROM outbox "
                    "WHERE published_at IS NULL "
                    "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                ).fetchone()
                if row is None:
                    return published

                event_id, topic, payload = row
                broker.publish(topic, event_id, payload)

                # Published, but not yet recorded as published. Dying here is
                # what produces a duplicate delivery -- the row is still
                # unpublished as far as the database knows, so the next round
                # sends it again. That is the outbox being at-least-once, and
                # it is why the consumer has to be idempotent.
                crasher.maybe("relay_after_publish_before_mark")

                conn.execute(
                    "UPDATE outbox SET published_at = now(), "
                    "attempts = attempts + 1 WHERE event_id = %s", (event_id,))
            published += 1
        except InjectedCrash:
            conn.rollback()
            raise
    return published


def relay_once_batched(conn, broker, crasher: Crasher, batch: int = 50) -> int:
    """The version I wrote first. Kept so the repo can measure how bad it is.

    One transaction for the whole batch: a crash anywhere in it throws away
    every mark, so the relay repeats work it already did and never advances.
    """
    published = 0
    with conn.transaction():
        rows = conn.execute(
            "SELECT event_id, topic, payload FROM outbox "
            "WHERE published_at IS NULL "
            "ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED",
            (batch,)).fetchall()
        for event_id, topic, payload in rows:
            broker.publish(topic, event_id, payload)
            published += 1
            crasher.maybe("relay_after_publish_before_mark")
            conn.execute(
                "UPDATE outbox SET published_at = now(), attempts = attempts + 1 "
                "WHERE event_id = %s", (event_id,))
    return published


def relay_drain(conn, broker, crasher: Crasher, max_rounds: int = 5000,
                batched: bool = False) -> int:
    """Run the relay until the outbox is empty, surviving injected crashes.

    Each crash is a process death; a supervisor restarts it and it carries on
    from whatever the database says is still unpublished.
    """
    once = relay_once_batched if batched else relay_once
    total = 0
    for _ in range(max_rounds):
        try:
            n = once(conn, broker, crasher)
        except InjectedCrash:
            conn.rollback()
            continue
        if n == 0:
            break
        total += n
    return total


# --- the worker -----------------------------------------------------------

def _send_email(conn, order_id: int) -> None:
    """The side effect. Exactly one of these per order is the whole point."""
    conn.execute("INSERT INTO emails_sent (order_id) VALUES (%s)", (order_id,))


def worker_drain_naive(conn, broker, crasher: Crasher,
                       max_messages: int = 100000) -> int:
    """Do the work, then acknowledge. No deduplication anywhere."""
    handled = 0
    for _ in range(max_messages):
        msg = broker.take(TOPIC)
        if msg is None:
            break
        try:
            with conn.transaction():
                _send_email(conn, msg.payload["order_id"])
            # Work done and committed. Not yet acknowledged.
            crasher.maybe("after_work_before_ack")
        except InjectedCrash:
            # The worker died. The broker takes the message back and gives it
            # to someone else, who does the work a second time.
            broker.consumer_died()
            continue
        broker.ack(msg.tag)
        handled += 1
    return handled


def worker_drain_guarded(conn, broker, crasher: Crasher,
                         max_messages: int = 100000) -> int:
    """Claim the event id and do the work in ONE transaction, then acknowledge.

    The single transaction is the part people get wrong. Claim in one
    transaction and work in another, and a crash between them marks the event
    done without doing it -- which loses work silently, the worst outcome of
    the three.
    """
    handled = 0
    for _ in range(max_messages):
        msg = broker.take(TOPIC)
        if msg is None:
            break
        try:
            with conn.transaction():
                try:
                    conn.execute(
                        "INSERT INTO processed_events (event_id) VALUES (%s)",
                        (msg.event_id,))
                except psycopg.errors.UniqueViolation:
                    # Seen it. Someone already did this work. Ack and move on.
                    raise _AlreadyDone from None
                _send_email(conn, msg.payload["order_id"])
            crasher.maybe("after_work_before_ack")
        except _AlreadyDone:
            broker.ack(msg.tag)
            handled += 1
            continue
        except InjectedCrash:
            broker.consumer_died()
            continue
        broker.ack(msg.tag)
        handled += 1
    return handled


class _AlreadyDone(Exception):
    """Internal: this event was processed before."""


# --- dead lettering -------------------------------------------------------

def dead_letter(conn, msg, error: str) -> None:
    """Park a message that has failed too many times.

    Without this, one message that can never succeed sits at the head of the
    queue being retried forever, and everything behind it waits.
    """
    with conn.transaction():
        conn.execute(
            "INSERT INTO dead_letters (event_id, topic, payload, error, attempts) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
            (msg.event_id, msg.topic, json.dumps(msg.payload), error,
             msg.deliveries))
