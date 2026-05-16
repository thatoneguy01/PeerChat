.PHONY: test test-e2e test-unit e2e-run e2e-docker-up e2e-docker-down

VENV := .venv/bin

test: test-unit test-e2e

test-unit:
	$(VENV)/python -m pytest tests -m "not e2e" -q

test-e2e:
	$(VENV)/python -m pytest tests/e2e -m e2e -v

e2e-run:
	$(VENV)/python -m e2e.cli run --peers 3

e2e-docker-up:
	docker compose -f docker-compose.e2e.yml up --build -d

e2e-docker-down:
	docker compose -f docker-compose.e2e.yml down
