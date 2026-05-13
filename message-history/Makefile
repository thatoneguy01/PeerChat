.PHONY: test test-local test-edge clean

test:
	pytest tests

test-local:
	pytest tests/test_localstore.py

test-edge:
	pytest tests/test_localstore_edgecases.py

clean:
	rm -rf logs index .pytest_cache tests/__pycache__ storage/__pycache__