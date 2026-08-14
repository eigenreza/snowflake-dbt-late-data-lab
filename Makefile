# End-to-end steps for the Late Data Lab.
#
# Everything runs on DuckDB by default and needs no credentials.
# Set TARGET=snowflake to run the dbt steps against Snowflake.
#
# On Windows without make, run the commands of each target from PowerShell
# with .venv\Scripts\python.exe and .venv\Scripts\dbt.exe.

ifeq ($(OS),Windows_NT)
PY  := .venv/Scripts/python.exe
DBT := .venv/Scripts/dbt.exe
else
PY  := .venv/bin/python
DBT := .venv/bin/dbt
endif

TARGET   ?= dev
DBT_DIR  := transform
DBT_RUN  := $(DBT) --project-dir $(DBT_DIR) --target $(TARGET)

.PHONY: help setup generate extract advance fx load dbt-build dbt-docs reconcile experiment export demo test clean all

help:
	@echo "setup       create the virtual environment and install the packages"
	@echo "generate    build the synthetic on-prem core bank in onprem/core_bank.duckdb"
	@echo "extract     run the watermark extract job for the initial day, or DAY=YYYY-MM-DD"
	@echo "advance     move the core system to the next replay day"
	@echo "fx          refresh the Norges Bank FX cache in data/ (falls back to the cache offline)"
	@echo "load        load landed legacy files and FX rates into RAW"
	@echo "dbt-build   dbt deps, seed, snapshot, run and test (TARGET=dev|snowflake)"
	@echo "dbt-docs    generate dbt docs"
	@echo "reconcile   compare the old nightly report with its dbt rewrite"
	@echo "experiment  replay the load cycles for every strategy and score them"
	@echo "export      write marts and experiment results for the demo to demo/data"
	@echo "demo        start the Streamlit demo"
	@echo "test        run the pytest suite"
	@echo "clean       remove local databases, landed files and dbt artifacts"
	@echo "all         generate, extract, fx, load, dbt-build, reconcile, experiment, export"

setup:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

generate:
	$(PY) -m generator.build_core_bank

extract:
	$(PY) -m extract.extract_core $(if $(DAY),--day $(DAY),--day 2024-11-30)

advance:
	$(PY) -m generator.advance_day --next

fx:
	$(PY) -m extract.extract_fx

load:
	$(PY) -m load.load_$(if $(filter snowflake,$(TARGET)),snowflake,duckdb)

dbt-build:
	$(DBT_RUN) deps
	$(DBT_RUN) build

dbt-docs:
	$(DBT_RUN) docs generate

reconcile:
	$(PY) -m reconcile.reconcile --target $(TARGET)

experiment:
	$(PY) -m experiment.run --target $(TARGET)

export:
	$(PY) -m experiment.export_demo_data

demo:
	$(PY) -m streamlit run demo/app.py

test:
	$(PY) -m pytest

clean:
	$(PY) -c "import shutil, pathlib; [p.unlink() for p in pathlib.Path('.').glob('**/*.duckdb*') if '.venv' not in p.parts]; shutil.rmtree('landing', ignore_errors=True); shutil.rmtree('transform/target', ignore_errors=True); shutil.rmtree('transform/logs', ignore_errors=True)"

all: generate extract fx load dbt-build reconcile experiment export
