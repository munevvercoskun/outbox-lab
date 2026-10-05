ORDERS ?= 500
SEED   ?= 7
PY     ?= python3

export OUTBOX_DSN ?= host=localhost port=5432 dbname=outboxlab user=postgres password=postgres

.PHONY: all db chaos relay plot report test up down logs clean help

help:
	@echo "make db       create the database (once)"
	@echo "make chaos    run all six failure scenarios, both versions"
	@echo "make relay    the relay comparison experiment"
	@echo "make plot     draw the two charts"
	@echo "make report   rewrite the README tables from results/"
	@echo "make test     13 tests"
	@echo "make all      chaos + relay + plot + report"
	@echo ""
	@echo "make up       the real thing: 5 containers, Postgres + RabbitMQ"
	@echo "make down     stop them"
	@echo ""
	@echo "current: ORDERS=$(ORDERS) SEED=$(SEED)"

db:
	-psql -h localhost -U postgres -c "CREATE DATABASE outboxlab;"

chaos:
	cd src && $(PY) chaos.py --orders $(ORDERS) --seed $(SEED)

relay:
	cd src && ORDERS=$(ORDERS) SEED=$(SEED) $(PY) relay_experiment.py

plot:
	cd src && $(PY) plot.py

# Rewrites the README's results section from results/, so the two cannot
# drift apart. Prints what changed and what prose still needs your attention.
report:
	cd src && $(PY) report.py

test:
	$(PY) -m pytest

all: chaos relay plot report
	@echo ""
	@echo "done. The README's tables now hold YOUR numbers."
	@echo "Read the OBSERVATIONS above -- the written findings are still yours."

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f relay worker

clean:
	rm -f results/*.csv results/*.png results/*.json
