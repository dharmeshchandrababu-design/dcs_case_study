# EV Charging Insights Pipeline (DCS case study)

Turns public posts about EV charging into insights DCS teams can act on.
The same pipeline answers the original brief (#ChargeNow within a time interval, required fields stored)
and, with a wider configuration, the EV charging analysis where the modelling and insights are built.

## Why this scope
#ChargeNow has only 181 posts since 2014 (2 in 2024, none in 2025 or 2026), mostly official or partner posts,
so the hashtag carries almost no customer voice. Following Youssef's suggestion, I widened the search to the
EV charging conversation (German and English, last 12 months, about 1,500 posts before relevance filtering)
and focused on modelling and analysis for the business teams.

## Architecture (ELT)
```
extract.py   twitterapi.io search for a time interval -> data/raw/<config>/extracted_date=.../run_<id>.json
load.py      required fields, hashed author id       -> DuckDB raw.posts
label.py     AI labels for unlabelled posts           -> DuckDB raw.labels
dbt build    staging -> intermediate -> star schema -> marts, with tests
```
`run_pipeline.sh` runs the steps in this order; in production the same four tasks would be an Airflow DAG.
DuckDB runs locally; the dbt models would run on Snowflake by switching the adapter.

### dbt models
| Layer | Model | Purpose |
|---|---|---|
| Staging | `stg_posts` (incremental), `stg_labels` | Cleaning only, one model per source |
| Seeds | `taxonomy`, `networks` | Business view: owner team, whether DCS can fix it, network role |
| Intermediate | `int_posts_enriched` | Joins posts, labels and networks; keeps relevant posts not clearly outside Europe |
| Core | `fct_posts`, `dim_theme`, `dim_network`, `dim_date` | Star schema, grain one post. No author dimension, for privacy |
| Reporting | `mart_problem_ranking` | Which problems are frequent, negative and fixable by DCS, with owner team |
| Reporting | `mart_network_scorecard` | Partner quality (CPOs), rival offers, CHARGE NOW share of voice |
| Reporting | `mart_voice_examples` | Drivers' own words, and serious posts split into DCS risk, partner risk, market watch |
| Reporting | `mart_monthly_trend` | Monthly development for themes DCS can act on |

Metric definitions are documented in `dbt/models/marts/_marts.yml` (`dbt docs generate` shows them).

## How to run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add your twitterapi.io key and a salt

# original brief: #ChargeNow in a time interval
python extract.py --config chargenow --from 2021-01-01 --to 2026-10-22
python load.py --config chargenow   # -> data/output/chargenow_posts.csv

# EV charging analysis
python extract.py --config ev_charging
python load.py --config ev_charging
python label.py --provider keyword  # baseline
python label.py                     # AI labels (config/labelling.yaml)
cd dbt && dbt build --profiles-dir .
```
Reprocess a date range: `python extract.py --config ev_charging --from 2026-03-01 --to 2026-04-01 --force`,
then `python load.py --config ev_charging` (newer versions replace older ones by post_id).
Replay from raw without calling the API (e.g. after a parser fix): `python load.py --config ev_charging --reload`.

### Time interval and raw layer
- The interval is on post creation time, in UTC: `--from` inclusive, `--to` exclusive (`YYYY-MM-DD`).
- Every run lands a new, never-changed file in `data/raw/<config>/extracted_date=YYYY-MM-DD/`, plus a
  `.meta.json` run record (interval, posts per search, status: complete, partial or stopped).
  Each post carries `_extracted_at`, `_search`, `_interval` and `_run_id`.
- Raw grain is one post per run, so the same post can appear in several files. `load.py` keeps one row per
  post in `raw.posts` (the latest version wins), and `raw.loaded_files` records which files were loaded.
- `extracted_date=2026-10-21/run_legacy_volume_test.json` is the overnight volume test that came before
  `extract.py` (scripts in `explore/`).

## Robustness (brief checklist)
| Requirement | How | Where |
|---|---|---|
| Empty results | Zero posts is logged as a valid run | `extract.py`, `load.py` |
| API changes | Raw JSON kept unchanged for reparsing; parsing tolerates missing fields and warns when a core field is missing | `load.py` |
| Source unavailability | Timeouts, retries with growing waits on 429 and server errors, checkpoint per time window, clean stop on missing key or credits | `extract.py` |
| Logging | Timestamped logs per step in `data/logs/` | all steps |
| Fault tolerance | Append-only raw files; a rerun of an interval resumes from its checkpoint; load reads only new files and keeps the latest version per post_id; `--reload` replays raw; dbt staging merges by post_id | `extract.py`, `load.py`, `stg_posts` |
| Scalability | Pagination, config-driven searches, incremental load, labelling and staging; at scale: date partitions, Snowflake, Airflow | design |

## AI labelling
`label.py` labels relevance, theme and subcategory (from `taxonomy.csv`), sentiment, feedback type,
severity and the network named. The categories are defined once in the seeds and shared by the prompt and dbt.
- Providers: `keyword` (rule-based baseline), `ollama` (free local model, no data leaves the laptop),
  `anthropic` (Claude, future option). Switching is one line in `config/labelling.yaml`.
- Answers are validated against the allowed values; an invalid answer is retried once, then falls back to the baseline.
- Only posts without a label from that labeller are sent, so reruns do not repeat AI calls.
- Accuracy: `python pilot.py export`, hand-label 50 posts, `python pilot.py compare` -> `findings/label_accuracy.md`.

## Privacy
Raw files stay local and are not committed. Only salted, hashed author ids enter DuckDB; usernames are never
loaded and @mentions in the text are masked. A dbt test fails if a username column reaches the marts.
Keys live in `.env`, which is git-ignored.

## Assumptions and limits
- Location is self-reported in profiles, so the Europe filter is approximate.
- X is a skewed sample of drivers: results show themes and direction, not market shares.
- Search volumes were capped per query, so compare shares within themes, not raw volumes across searches.
- One primary network per post; network roles are set manually in `networks.csv` (to be confirmed with DCS).
- A post counts as a DCS risk only if it is serious and about DCS.
- Tweet count = the author's total post count (`statusesCount`).
- Orchestration and monitoring are not built; the steps run in sequence from one script.

## Findings
See `findings/` (in progress).
