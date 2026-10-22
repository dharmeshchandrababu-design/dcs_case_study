#!/usr/bin/env bash
# Runs the whole pipeline in order. This sequence is what an Airflow DAG would schedule.
#
#   ./run_pipeline.sh 2025-10-22 2026-10-22     extract both configs for this interval, then the rest
#   ./run_pipeline.sh                            no extract: reuse raw data already landed (no API credits)
#
# Interval: post creation time, UTC, FROM inclusive, TO exclusive.
set -euo pipefail
cd "$(dirname "$0")"
FROM="${1:-}"; TO="${2:-}"

if [ -n "$FROM" ] && [ -n "$TO" ]; then
  python extract.py --config chargenow   --from "$FROM" --to "$TO"
  python extract.py --config ev_charging --from "$FROM" --to "$TO"
fi
python load.py --config chargenow        # original brief: writes data/output/chargenow_posts.csv
python load.py --config ev_charging
python label.py --provider keyword       # baseline labels (always available, fallback)
python label.py                          # AI labels for posts without one, then English text for relevant posts
cd dbt && dbt build --profiles-dir .     # staging, intermediate, star schema, marts, tests
