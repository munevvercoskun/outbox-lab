"""Tests that assert the lab is measuring what it claims.

The important ones are the negative tests. If the naive version ever passes a
scenario it should fail, the fault injection is not working and every other
number on this page is decoration.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import db                                    # noqa: E402
from broker import MemoryBroker              # noqa: E402
from chaos import SCENARIOS, run             # noqa: E402
from invariants import check                 # noqa: E402
from pipeline import (Crasher, create_order_guarded,  # noqa: E402
                      worker_drain_guarded)

ORDERS = 120
SEED = 7


@pytest.fixture(scope="module", autouse=True)
def database():
    try:
        db.reset_schema()
    except Exception as exc:                 # pragma: no cover
        pytest.skip(f"no database reachable: {exc}")


# --- the harness works at all --------------------------------------------

def test_no_failures_both_versions_are_perfect():
    """If this fails, something is broken that has nothing to do with chaos."""
    for mode in ("naive", "guarded"):
        r = run(mode, "none", ORDERS, SEED)
        assert r["orders"] == ORDERS
        assert r["missing"] == 0, mode
        assert r["duplicated"] == 0, mode
        assert r["ok"], mode


# --- the failures are real -----------------------------------------------

def test_naive_loses_events_when_the_service_dies_after_commit():
    r = run("naive", "crash_after_commit", ORDERS, SEED)
    assert r["missing"] > 0, (
        "the naive version did not lose a single event, so the crash is not "
        "being injected where it matters")


def test_naive_duplicates_work_when_the_worker_dies_before_acking():
    r = run("naive", "crash_before_ack", ORDERS, SEED)
    assert r["duplicated"] > 0, (
        "no duplicate work, so redelivery is not happening")


def test_naive_duplicates_on_duplicate_delivery():
    r = run("naive", "duplicate_delivery", ORDERS, SEED)
    assert r["duplicated"] == ORDERS


# --- the fix works --------------------------------------------------------

@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_guarded_holds_every_invariant_in_every_scenario(scenario):
    r = run("guarded", scenario, ORDERS, SEED)
    assert r["missing"] == 0, f"{scenario}: {r['missing']} orders got no email"
    assert r["duplicated"] == 0, f"{scenario}: {r['duplicated']} duplicate emails"
    assert r["orphans"] == 0
    assert r["outbox_unpublished"] == 0, (
        f"{scenario}: {r['outbox_unpublished']} events stuck in the outbox")


# --- the specific mechanisms ---------------------------------------------

def test_the_outbox_itself_creates_duplicate_publishes():
    """The point that is easy to miss: the fix makes duplicates MORE likely.

    A relay that dies after publishing and before marking will publish again.
    That is correct at-least-once behaviour, and it is exactly why the
    consumer has to be idempotent. If this ever stops being true, the dedupe
    table is no longer earning its place.
    """
    r = run("guarded", "relay_crash", ORDERS, SEED)
    assert r["published"] > r["orders"], (
        "the relay never republished anything, so this scenario proves nothing")
    assert r["duplicated"] == 0, "but the consumer absorbed every one of them"


def test_dedupe_insert_and_work_are_one_transaction():
    """Claim and work must commit together.

    If they were separate transactions, a crash between them would mark the
    event processed without doing the work -- losing it silently, which is
    worse than either failure this lab started with.
    """
    db.reset_schema()
    broker = MemoryBroker()
    quiet = Crasher()

    with db.connect() as conn:
        create_order_guarded(conn, broker, 1, 500, quiet)
        from pipeline import relay_drain
        relay_drain(conn, broker, quiet)

        # Crash between the claim and the ack on every single message.
        killer = Crasher(point="after_work_before_ack", rate=1.0, seed=1)
        for _ in range(5):
            worker_drain_guarded(conn, broker, killer)
            if broker.pending("order.created") == 0 and not broker.inflight:
                break

        res = check(conn)

    # Either the work happened and was recorded, or neither did. Never one.
    assert res.missing + res.emails == res.orders
    assert res.duplicated == 0


def test_invariants_can_actually_fail():
    """A check that never fails is not a check.

    Insert a duplicate email by hand and confirm the invariant notices.
    """
    db.reset_schema()
    with db.connect() as conn:
        conn.execute("INSERT INTO orders (order_id, customer_id, amount_cents) "
                     "VALUES (1, 1, 100)")
        conn.execute("INSERT INTO emails_sent (order_id) VALUES (1)")
        conn.execute("INSERT INTO emails_sent (order_id) VALUES (1)")
        conn.commit()
        res = check(conn)
    assert res.duplicated == 1
    assert not res.ok
