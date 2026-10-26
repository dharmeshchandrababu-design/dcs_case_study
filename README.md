# EV Charging Insights Pipeline (DCS case study)

A small ELT pipeline that searches public posts on X for a time interval, stores the required fields,
labels each post with an AI model, and models the result in dbt so DCS teams get insights they can act on.

- **Original brief:** `#ChargeNow` within a time interval, required fields stored. Covered by the `chargenow` config.
- **Extension (agreed with Youssef):** the wider EV charging conversation, with sentiment and business value.
  #ChargeNow has only 181 posts since 2014, none in 2025 or 2026, and mostly official or partner posts,
  so the analysis is built on 1,597 EV charging posts from the last 12 months (German and English).
- **Findings for the business teams:** see the findings deck.

## Results in one view

| | |
|---|---|
| Posts extracted (EV charging, 22 Oct 2025 to 22 Oct 2026) | 1,597 |
| Relevant posts about EV charging, not clearly outside Europe | **297** from 207 authors (209 German, 88 English) |
| Sentiment of relevant posts | 191 negative or mixed, 65 neutral, 41 positive |
| Top problems (posts, negative share) | price level 74 (80%), too many apps and cards 25 (96%), broken chargers 21 (86%), too few chargers 18 (94%), occupied chargers 17 (88%) |
| Networks drivers name | Ionity 37, EnBW 28, Tesla 17, Fastned 11. **CHARGE NOW: 0** |
| Billing errors (evidence-checked) | 2 posts: drivers complain about price levels, not wrong bills |
| Signal in the data | German searches: 28% of posts relevant. English searches: 11% |

Headline: drivers' biggest frustrations (too many apps and cards, confusing prices, roaming surcharges,
charging abroad) are what CHARGE NOW solves, but nobody names it. DCS's court win against EWE Go
(Munich, 6 Oct 2026) addresses the loudest complaint, unfair roaming prices.

## The brief, point by point

| Requirement | Where |
|---|---|
| Search posts mentioning #ChargeNow within a time interval | `extract.py --config chargenow --from ... --to ...` |
| Store in a format you choose | Raw JSON landing zone + DuckDB tables + `data/output/chargenow_posts.csv` |
| Anonymised user id | Salted SHA-256 hash of the author id (`author_hash`) |
| Location, follower count, datetime, hashtags | `location_raw`, `follower_count`, `created_at`, `hashtags` |
| Tweet count | `author_post_count` (the author's total posts, `statusesCount`) |
| Is retweet | `is_retweet` (computed from `retweeted_tweet`; retweets kept for #ChargeNow, excluded in the EV searches) |
| Empty results, API changes, source unavailability, logging, fault tolerance, scalability | See Robustness below |

## Architecture (ELT)

```
X (twitterapi.io)
   │  extract.py   search + time interval from config/*.yaml
   ▼
data/raw/<config>/extracted_date=YYYY-MM-DD/run_<id>.json   (+ .meta.json run record)   append only
   │  load.py      required fields, hashed author id, masked @mentions, region tag
   ▼
DuckDB raw.posts  (one row per post, latest version wins)        raw.loaded_files
   │  label.py     AI labels for posts without a label + English translation of relevant posts
   ▼
DuckDB raw.labels (one row per post and model version)
   │  dbt build
   ▼
staging ─► intermediate ─► core (star schema) ─► reporting (marts)
```

`run_pipeline.sh FROM TO` runs the four steps in order. In production the same four tasks would be an
Airflow DAG. DuckDB runs locally; the dbt models would run on Snowflake by switching the adapter.

### dbt models

| Layer | Model | Purpose |
|---|---|---|
| Sources | `raw.posts`, `raw.labels` | Written by the Python steps |
| Staging | `stg_posts`, `stg_labels` (both incremental, unique key `post_id`) | Cleaning only, one model per source. `stg_labels` picks one label per post (AI over keyword, newest wins) |
| Seeds | `taxonomy`, `networks` | Business knowledge: owner team, whether DCS can fix it, network role (dcs, cpo, emsp_competitor, both) |
| Intermediate | `int_posts_enriched` | Joins posts, labels and networks; keeps relevant posts not clearly outside Europe; severity evidence check |
| Core | `fct_posts`, `dim_theme`, `dim_network`, `dim_date` | Star schema, grain one post. No author dimension, for privacy |
| Reporting | `mart_problem_ranking` | Which problems are frequent, negative and fixable by DCS, with owner team and priority score |
| Reporting | `mart_network_scorecard` | Per network: posts, negative and positive share, top complaint, share of voice (CHARGE NOW listed at 0) |
| Reporting | `mart_network_feedback` | What each network is praised and criticised for |
| Reporting | `mart_voice_examples` | Drivers' own words (English and original), serious posts as DCS risk, partner risk or market watch |
| Reporting | `mart_monthly_trend` | Monthly development for themes DCS can act on (small numbers, flagged) |
| Reporting | `mart_search_funnel` | Relevance rate per search: where the signal is |

Every model and column is described in one YAML file per layer (`_sources.yml`, `_staging.yml`, `_intermediate.yml`,
`_core.yml`, `_reporting.yml`, `_seeds.yml`); `dbt docs generate` shows them with the lineage.

**Tests (276 dbt tests and a freshness check).** Unique and not-null keys, accepted values, value ranges, column
comparisons, unique combinations, fact-to-dimension relationships, labels must exist in the taxonomy, no future
posts, share of voice sums to 1, and no username or raw author id column may reach the core or reporting schemas.
Range, comparison and combination tests are small local generic tests in `dbt/tests/generic/` (the same logic as
dbt_utils, without a package download). Source freshness on `raw.posts` warns after 2 days without new data and
errors after 7: `dbt source freshness`.

**Python unit tests (`tests/`, pytest).** A post with missing fields, an empty raw file, loading the same file twice
(no duplicates), an older file arriving late (the newest version wins), username masking, the label validator
repairing answers such as `price / roaming_surcharge`, the time interval, retries on 429/5xx and the clean stop on
missing credits. Run with `pytest`; no API key, model or warehouse needed.

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # twitterapi.io key and a hash salt

# original brief: #ChargeNow in a time interval
python extract.py --config chargenow --from 2014-01-01 --to 2026-10-23
python load.py --config chargenow     # -> data/output/chargenow_posts.csv

# EV charging analysis
python extract.py --config ev_charging --from 2025-10-22 --to 2026-10-22
python load.py --config ev_charging
python label.py --provider keyword    # rule-based baseline for every post
python label.py                       # AI labels (config/labelling.yaml, qwen2.5:7b) + translations
python label.py --model qwen2.5:14b --only-relevant   # relabel the relevant posts with the larger model
cd dbt && dbt build --profiles-dir . && dbt source freshness --profiles-dir . && cd ..

# tests for the Python steps
pytest

# or everything in one go
./run_pipeline.sh 2025-10-22 2026-10-22
```

Time interval: on post creation time, UTC, `--from` inclusive, `--to` exclusive.
Reprocess a range: run `extract.py` for it again (with `--force`), then `load.py`; newer versions replace older
ones by `post_id`. Replay raw files without calling the API: `load.py --reload`.

AI labelling needs [Ollama](https://ollama.com) with `qwen2.5:7b` (and `qwen2.5:14b` for the relevant posts),
or `provider: anthropic` with an API key. The keyword baseline runs without any model.

## Incremental design and daily operation

**Why incremental.** For a company, storing data is cheap, but processing it costs money every time:
warehouse compute in Snowflake, and tokens when an AI model such as Claude labels posts. A full rebuild
reprocesses all history every day, so its cost grows every day. An incremental pipeline only processes what is
new or changed, so the daily cost depends on the day's new posts, not on the size of the history.

**Two modes, same code.**
- **Backfill (once):** this case ran one historical window per config: #ChargeNow from 2014, EV charging for the
  last 12 months (22 Oct 2025 to 22 Oct 2026).
- **Daily run (production):** each morning, extract only the last 2 to 3 days, for example
  `./run_pipeline.sh 2026-10-24 2026-10-27`. The overlap catches posts that appear late in search results;
  posts fetched twice are merged by `post_id`, so nothing is duplicated.

**What each step processes in a daily run**

| Step | Processes | How |
|---|---|---|
| `extract.py` | Posts created in the last 2 to 3 days | `--from` / `--to` window |
| `load.py` | Only raw files not loaded before; newest version per post | `raw.loaded_files`, merge by `post_id` |
| `label.py` | Only posts without a label from the current model (the biggest saving: AI calls and tokens) | skips labelled posts; reuses existing translations |
| `stg_posts` | Only posts loaded since the last run | incremental on `loaded_at`, unique key `post_id` |
| `stg_labels` | Only posts whose labels changed since the last run (new label or translation) | incremental on `updated_at`, re-ranks those posts, unique key `post_id` |
| intermediate, core, marts | Rebuilt each run | small today; at DCS scale `fct_posts` would also become incremental, the marts stay full rebuilds because they are small summaries |

**What is kept.** Incremental means less processing, not less data. Raw files and all label versions are kept as
an audit trail and for replay (`load.py --reload`, `dbt build --full-refresh`). In production a retention rule would
move old raw files to cheaper archive storage.

**Trade-off.** With a 2 to 3 day window, likes and views stop updating after a few days. That is fine here (the
analysis is about what people say, not reach); if engagement mattered, a weekly re-fetch of the last 30 days would
refresh it.

## Robustness

| Requirement | How it is handled |
|---|---|
| Empty results | A search with zero posts is recorded as a valid run, logged, and the run continues |
| API changes | Raw responses are stored unchanged for reparsing; parsing tolerates missing fields and warns when a core field (id, createdAt, text, author.id) is missing; the source sits behind one function |
| Source unavailability | Timeouts on every call; retries with growing waits on 429 and server errors; checkpoint per config and interval so a rerun resumes; missing key or credits (401/402/403) stop the run cleanly with status "stopped" |
| Logging | Timestamped logs per step in `data/logs/`, run records (`.meta.json`) with posts per search and status |
| Fault tolerance, reprocessing | Append-only raw files; load reads only new files (`raw.loaded_files`) and keeps the latest version per post; `--reload` replays raw; labelling only processes posts without a label; both dbt staging models merge by `post_id` |
| Scalability | Pagination, config-driven searches, incremental load, labelling and staging. At scale: cloud landing zone partitioned by date, Snowflake, Airflow, labelling in the warehouse |

## AI labelling and accuracy

Each relevant post gets: relevance, theme and subcategory (from `taxonomy.csv`), sentiment, feedback type,
severity, the network named, and an English translation (relevant non-English posts only).
Categories are defined once in the seeds and shared by the prompt and dbt. Every label row records the
model and prompt version (`model_version`); old labels are kept as history.

How the labels evolved:
1. Prompt v1 (qwen2.5:7b): many batches failed, relevance too strict (208 relevant).
2. v2: clearer relevance definition, batches of 5, retry one by one (430 relevant).
3. v3: severity redefined (high prices are not "financial"), one network per post, 5 new subcategories.
4. The small model still over-flagged financial risk, so dbt keeps the AI severity only when the text also
   contains matching evidence (macro `severity_checked`): financial 2, legal 23.
5. Relevant posts relabelled with qwen2.5:14b, the version used in the marts (297 relevant in scope).

Accuracy against 28 to 30 reference labels (Claude, reviewed by me), indicative only:

| Label | qwen2.5:7b v3 | qwen2.5:14b v3 |
|---|---|---|
| relevant | 93% | 87% (stricter) |
| theme | 64% | 68% |
| sentiment | 64% | 82% |
| feedback type | 57% | 71% |
| severity (raw AI) | 68% | 82% |

Relevance and sentiment are solid; themes are direction only, so the key findings were checked by reading
the posts. `python pilot.py export` / `compare` reproduces the check.

**Relevance funnel.** 1,597 posts extracted → 421 marked relevant by `qwen2.5:7b` → rechecked by `qwen2.5:14b`:
304 still relevant → 297 after removing 7 authors clearly outside Europe (134 with a European location, 163 unknown).

**Why the two models disagree on about 120 posts.** Reading a sample of them shows four patterns:
- *Ambiguous names:* EnBW is also an energy company, so posts about nuclear or wind power ("EnBW says it is too
  expensive") match the competitor search.
- *EV debate, not charging experience:* political or "EV myth" arguments that mention a charger in passing.
- *Replies without context:* "Wo ist Ionity teuer?" only makes sense with the post it answers, which is not fetched.
- *Advertising:* wallbox and installer posts.

The 7b model says "relevant" too easily. The 14b model is stricter but sometimes too strict: it also rejected some
real experiences (e.g. a driver reporting broken chargers). On the 28 reference posts, relevance was 93% (7b) vs 87%
(14b), so relevance is the least certain step and the 297 is a careful count, not an exact one.

**How to make it better:**
- Sharper searches: exclusions for known noise (e.g. EnBW with nuclear or wind terms), see `config/`.
- Fetch the parent post for replies so the model sees the context.
- A clearer relevance rule in the prompt with a few labelled examples (yes and no).
- A larger hand-labelled sample (150 to 200 posts) and precision and recall for relevance, not just accuracy.
- Posts where two models disagree go to a short human review list instead of being decided by one model.
- In production, a stronger model such as Claude for the relevance decision, keeping a small model for cheap pre-filtering.

## Privacy
- Raw files and the database stay local and are not committed (`.gitignore`).
- Only salted, hashed author ids enter DuckDB; usernames are never loaded; @mentions in the text are masked.
- A dbt test fails if a username or raw author id column reaches the core or reporting tables.
- Keys live in `.env`. Open question for production: limits on sending public posts to an external AI model.

## Assumptions and limits
- X is a skewed, enthusiast-heavy sample: results show themes and direction, not market shares.
- Location is self-reported in profiles, so the Europe filter is approximate.
- Before building the pipeline, search volumes were tested with small throwaway scripts (not part of the repository).
- Search volumes were capped per query (about 200 posts): compare shares within themes, not raw volumes across searches.
- One primary network per post; network roles are set manually in `networks.csv` (to confirm with DCS).
- A post counts as a DCS risk only if it is serious and about DCS.
- Modelling: staging, one intermediate model, a small star schema and marts.
- Orchestration and monitoring are not built, by design: the brief covers them in the follow-up discussion, and an
  Airflow setup on a laptop would add infrastructure without adding insight. `run_pipeline.sh` runs the four steps in
  the same order an Airflow DAG would (extract, load, label, dbt build). In production each step becomes a task,
  scheduled daily with a 2 to 3 day window, with retries and alerts on failed or "stopped" runs and on unexpected
  empty results.

## What I would do next
- Run daily: Airflow DAG with the four tasks, alert on failed or stopped runs and on unexpected empty results.
- Move the landing zone to cloud storage, the warehouse to Snowflake, and labelling to Claude (or warehouse AI functions).
- Add the OEM charging brands (BMW Charging, Mercedes me Charge) to the searches; exclude known noise.
- Apply the same labelling to internal sources such as support tickets, and join with session data to validate the findings.

## Repository
```
config/            search and labelling settings
extract.py load.py label.py pilot.py export_labels.py common.py
run_pipeline.sh    the four steps in order
dbt/               models, seeds, macros, tests
tests/             pytest unit tests for extract, load and label
findings/          label accuracy: reference labels and results
data/output/       chargenow_posts.csv (the brief's required fields)
```
