.PHONY: install dev test lint format serve quickstart clean

install:
	pip install -e .

dev:
	pip install -e ".[dev,serve]"

test:
	pytest -q

lint:
	ruff check src tests

format:
	ruff check --fix src tests

serve:
	uvicorn shoppilot.serving.api:app --reload

quickstart:
	python examples/01_quickstart.py

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
