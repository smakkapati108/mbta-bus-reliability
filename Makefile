PY := .venv/bin/python

.PHONY: setup collect gtfs refresh analyze train dashboard test install-launchd

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

collect:        ## poll vehicle positions every 60s until stopped
	$(PY) -m transit collect

gtfs:           ## download + load the static schedule if it changed
	$(PY) -m transit gtfs

refresh:        ## ingest new pings, dbt build, export marts
	$(PY) -m transit refresh

analyze:
	$(PY) -m transit analyze

train:
	$(PY) -m transit train

dashboard:
	.venv/bin/streamlit run dashboard/app.py

test:
	.venv/bin/pytest -q tests

install-launchd:
	./deploy/install_launchd.sh
