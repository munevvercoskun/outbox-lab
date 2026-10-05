"""What must be true when the dust settles.

These are properties of the business, not of the code. They are the same
whichever version ran, which is what makes the comparison fair: both versions
are judged against what a customer would consider correct.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Result:
    orders: int
    emails: int
    missing: int          # orders with no email -- a customer got nothing
    duplicated: int       # extra emails beyond one per order
    orphans: int          # emails for orders that do not exist
    outbox_unpublished: int
    dead_letters: int

    @property
    def ok(self) -> bool:
        return (self.missing == 0 and self.duplicated == 0
                and self.orphans == 0 and self.outbox_unpublished == 0)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["ok"] = self.ok
        return d


def check(conn) -> Result:
    orders = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
    emails = conn.execute("SELECT count(*) FROM emails_sent").fetchone()[0]

    # Orders that never got their email. The customer paid and heard nothing.
    missing = conn.execute("""
        SELECT count(*) FROM orders o
        WHERE NOT EXISTS (SELECT 1 FROM emails_sent e
                          WHERE e.order_id = o.order_id)
    """).fetchone()[0]

    # Every email beyond the first for an order. The customer got spammed, and
    # if this were a charge instead of an email it would be real money.
    duplicated = conn.execute("""
        SELECT COALESCE(SUM(n - 1), 0) FROM (
            SELECT count(*) AS n FROM emails_sent GROUP BY order_id
        ) t WHERE n > 1
    """).fetchone()[0]

    orphans = conn.execute("""
        SELECT count(*) FROM emails_sent e
        WHERE NOT EXISTS (SELECT 1 FROM orders o
                          WHERE o.order_id = e.order_id)
    """).fetchone()[0]

    unpublished = conn.execute(
        "SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()[0]
    dead = conn.execute("SELECT count(*) FROM dead_letters").fetchone()[0]

    return Result(orders, emails, missing, int(duplicated), orphans,
                  unpublished, dead)
