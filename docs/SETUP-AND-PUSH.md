# outbox-lab — run it, then two pushes

Everything runs in your WSL Ubuntu. You already have Postgres from the database lab.

---

# Part 0 — Setup (about 3 minutes)

```bash
wsl
sudo service postgresql start          # WSL doesn't start it on boot

cd ~/GitHub
unzip ~/outbox-lab.zip                 # wherever you saved it
cd outbox-lab

pip install -r requirements.txt --break-system-packages
make db
```

`make db` creates the `outboxlab` database. If it says it already exists, that's fine.

**Docker is optional.** The chaos harness uses an in-process broker, so `make all` needs nothing but Postgres and Python. Docker is only for the five-container demo in Part 6.

---

# Part 1 — Run it and get YOUR numbers

Quick check first:

```bash
make test
```

13 tests. Some of them assert that the **naive version still breaks** — if fault injection stopped working, those fail. A green suite that proves nothing is the thing to guard against.

Then the real run:

```bash
make all
```

About a minute. It runs six failure scenarios against both versions, runs the relay comparison, draws two charts, and rewrites the README's tables from the results.

### What you should see

The naive version breaking in 4 of 6 scenarios, the guarded version in 0. If your naive version comes out clean somewhere mine broke, tell me — that means the injection didn't fire and the comparison isn't fair.

### If something fails

| Symptom | Fix |
|---|---|
| `could not connect to server` | `sudo service postgresql start` |
| `database "outboxlab" does not exist` | `make db` |
| `ModuleNotFoundError: psycopg` | `pip install -r requirements.txt --break-system-packages` |
| Tests skip with "no database reachable" | Postgres isn't running, or your DSN differs — set `OUTBOX_DSN` |

---

# Part 2 — Put your numbers in the README

**One command.** The tables are generated, not typed:

```bash
make report
```

It rewrites everything between `<!-- RESULTS:START -->` and `<!-- RESULTS:END -->` from `results/*.csv`, then prints an **OBSERVATIONS** block:

```
  * naive broke in 4/6 scenarios, guarded in 0
  * worst case the naive version lost 168 of 500 confirmations (34%)
  * worst case the naive version sent 990 duplicate emails on 500 orders
  * outbox created 255 duplicate publishes on 500 orders, all absorbed by dedupe
  * batched relay did 22x the work and still failed
```

Then two things by hand:

1. **The badge** on line 3 — swap `munevvercoskun` if your username differs.
2. **"What I got wrong"** — those three paragraphs are mine. Rewrite them in your own words using your OBSERVATIONS. Findings 1 and 2 are about the code, so they'll still be true for you; finding 3 is about the method and also stays. The numbers inside them need to be yours.

---

# Part 3 — Create the repo on GitHub

**+ → New repository**

- **Name:** `outbox-lab`
- **Description:**
  > A failure injector for queue-based systems. Kills the service, relay and worker at the moments that hurt, counts lost and duplicated work, and shows the transactional outbox and idempotent consumer eliminating both.
- **Public**
- **All three checkboxes off.** No README, no .gitignore, no licence.

Topics (the gear next to **About**): `distributed-systems`, `transactional-outbox`, `idempotency`, `rabbitmq`, `postgresql`, `fastapi`, `chaos-engineering`, `python`

---

# Part 4 — Push 1: the system

The theme: **a working system and a way to break it.**

```bash
cd ~/GitHub/outbox-lab
git init

git add .gitignore requirements.txt Makefile pytest.ini
git commit -m "Project skeleton: Makefile, dependencies, gitignore"

git add sql/schema.sql
git commit -m "Schema: orders, outbox, dedupe and dead-letter tables with a partial index"

git add src/db.py src/broker.py
git commit -m "Database access and an at-least-once broker, in memory and over RabbitMQ"

git add src/pipeline.py
git commit -m "Both versions side by side: naive publish-after-commit, and outbox plus idempotent consumer"

git add src/invariants.py
git commit -m "Invariants: every order gets exactly one confirmation, nothing stuck"

git add src/chaos.py src/relay_experiment.py
git commit -m "Failure injection: six scenarios, seeded and reproducible"

git add tests/
git commit -m "Tests: assert the naive version still breaks, and that claim and work share a transaction"

git branch -M main
git remote add origin https://github.com/munevvercoskun/outbox-lab.git
git push -u origin main
```

---

# Part 5 — Push 2: the results and the write-up

The theme: **the findings.**

```bash
git add src/plot.py
git commit -m "Charts: customers affected per scenario, and the relay comparison"

git add src/report.py
git commit -m "Report generator: rewrite the README tables from results/ so the two cannot drift"

git add results/
git commit -m "Measured results: six scenarios, 500 orders, both versions"

git add api/ src/run_relay.py src/run_worker.py Dockerfile docker-compose.yml
git commit -m "Five real processes, so the kills can be done by hand instead of injected"

git add README.md docs/
git commit -m "README: results, the relay bug I shipped first, and notes on method"

git add .github/
git commit -m "CI: run every scenario against a real Postgres on each push"

git status
```

`nothing to commit, working tree clean`, then:

```bash
git push
```

Check on GitHub: **the two charts render**, and the **Actions tab is green**.

---

# Part 6 — The five-container demo (optional, needs Docker)

Worth doing once, because it's the version you can demo live in an interview.

```bash
make up        # Postgres, RabbitMQ, API, relay, worker

# in another terminal
curl -X POST localhost:8000/orders -H 'content-type: application/json' \
     -d '{"customer_id": 1, "amount_cents": 1999}'

docker compose kill worker          # a real SIGKILL, mid-message
docker compose start worker
curl localhost:8000/invariants      # missing 0, duplicated 0
```

Then do it again with `MODE=naive make up` and watch the same kill lose or duplicate work.

RabbitMQ's web UI is at `localhost:15672`, guest/guest — useful for showing the queue depth while the worker is dead.

---

# Part 7 — Talking about it

**The résumé line:**

> Built a failure injector for queue-based systems that kills the producer, relay and consumer at each point where work can be lost or repeated. Measured 34% of confirmations lost and up to 990 duplicates across 500 orders in the naive implementation, and zero of either after applying the transactional outbox and idempotent consumption — with seeded, reproducible scenarios and tests that assert the failures still reproduce.

**When someone asks you to walk through it**, lead with the thing most people miss:

> "The headline is that the two patterns only work together. The outbox fixes losing events, and it *causes* duplicate events — a relay that publishes and dies before marking the row sent will publish it again. In my run it published 755 events for 500 orders. If the consumer weren't idempotent I'd have just swapped one bug for another."

**Then the mistake:**

> "My first relay claimed a batch of 50 and marked them all sent in one transaction. It looked fine with no failures. Under a 30% crash rate it never made progress at all — every crash rolled back the whole batch, so it re-published the same first rows forever. It did 22 times the work and still left every order without a confirmation. Both versions are in the repo because the broken one looks completely reasonable."

**Three questions to expect:**

1. *"Why not just use exactly-once delivery?"* — It doesn't exist. Anything advertising it is doing at-least-once plus deduplication, which is this, with the dedupe hidden. The question is only ever where the dedupe lives.
2. *"Isn't the outbox a lot of machinery?"* — One table, one index, one relay process. The alternative is a failure mode you find out about from a customer. I'd skip it if losing an event is genuinely fine, which is sometimes true.
3. *"What doesn't this cover?"* — Publisher confirms. The outbox protects the database-to-broker handoff; it doesn't protect against the broker accepting a message and losing it. The relay should wait for a confirm before marking the row sent. It's in "what I'd do next" because I didn't measure it.
