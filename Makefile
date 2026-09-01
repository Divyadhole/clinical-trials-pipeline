.PHONY: up down logs daily backfill checks status test verify clean

up:            ## bring up postgres and run one load
	docker compose up --build

down:
	docker compose down

clean:         ## down and delete the postgres volume
	docker compose down -v

daily:
	python -m ctp daily

backfill:      ## make backfill START=2026-01-01 END=2026-03-31
	python -m ctp backfill --start $(START) --end $(END)

checks:
	python -m ctp checks

status:
	python -m ctp status

verify:        ## check the live API contract before trusting a zero-row load
	python -m ctp verify-api

test:
	pytest -q
