-- ---------------------------------------------------------------------------
-- The whole lab is four tables and one index.
--
-- The thing to notice is which tables exist *only* because the system is
-- distributed. `orders` and `emails_sent` are the business. `outbox` and
-- `processed_events` are the price of having a queue in the middle.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS dead_letters     CASCADE;
DROP TABLE IF EXISTS processed_events CASCADE;
DROP TABLE IF EXISTS emails_sent      CASCADE;
DROP TABLE IF EXISTS outbox           CASCADE;
DROP TABLE IF EXISTS orders           CASCADE;

-- The business state. Written by the order service.
CREATE TABLE orders (
    order_id     BIGSERIAL   PRIMARY KEY,
    customer_id  BIGINT      NOT NULL,
    amount_cents INT         NOT NULL CHECK (amount_cents > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The outbox. An event written in the SAME TRANSACTION as the order it
-- describes, so there is no moment where one exists without the other.
-- A separate relay process publishes these and marks them sent.
CREATE TABLE outbox (
    event_id     UUID        PRIMARY KEY,
    topic        TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    attempts     INT         NOT NULL DEFAULT 0
);

-- Partial index: the relay only ever asks for unpublished rows, and this keeps
-- that query cheap no matter how large the table grows. Once a row is
-- published it drops out of the index entirely.
CREATE INDEX idx_outbox_unpublished
    ON outbox (created_at)
    WHERE published_at IS NULL;

-- The side effect the worker performs. Exactly one of these per order is the
-- property every scenario in this lab is testing.
CREATE TABLE emails_sent (
    id       BIGSERIAL   PRIMARY KEY,
    order_id BIGINT      NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The deduplication table. The PRIMARY KEY is doing the real work: a second
-- delivery of the same event fails to insert, and the worker skips it.
-- This is the whole idempotent-consumer pattern. There is nothing else to it.
CREATE TABLE processed_events (
    event_id     UUID        PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Messages that failed too many times. They go here instead of blocking the
-- queue forever behind a message that will never succeed.
CREATE TABLE dead_letters (
    event_id  UUID        PRIMARY KEY,
    topic     TEXT        NOT NULL,
    payload   JSONB       NOT NULL,
    error     TEXT,
    attempts  INT         NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
