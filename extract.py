"""Extract: search posts on X through twitterapi.io for a time interval and land the raw JSON.

Usage:
    python extract.py --config chargenow --from 2021-01-01 --to 2026-10-22
    python extract.py --config ev_charging            # last window_days from the config

Time interval: on post creation time, UTC, --from inclusive and --to exclusive (YYYY-MM-DD).

Landing zone (append only, never changed after writing):
    data/raw/<config>/extracted_date=YYYY-MM-DD/run_<run_id>.json        posts as returned by the API,
                                                                          plus _extracted_at, _search, _interval
    data/raw/<config>/extracted_date=YYYY-MM-DD/run_<run_id>.meta.json   run record: interval, counts, status

Robustness:
- empty results are a valid outcome and are recorded in the run record
- timeouts on every call, retries with growing waits on rate limits and server errors
- a checkpoint per config and interval, so a rerun of the same interval resumes and skips finished searches
- missing credentials or credits (401/402/403) stop the run cleanly with status "stopped"
"""
import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from common import RAW, get_logger, load_config, load_env, read_json, write_json

URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
log = get_logger("extract")


class CreditsExhausted(Exception):
    pass


def get_page(key, query, cursor, max_attempts=6):
    for attempt in range(max_attempts):
        try:
            r = requests.get(URL, headers={"X-API-Key": key},
                             params={"query": query, "queryType": "Latest", "cursor": cursor},
                             timeout=60)
        except requests.RequestException as e:          # network down, timeout
            wait = 6 * (attempt + 1)
            log.warning("request failed (%s), retrying in %ss", e, wait)
            time.sleep(wait)
            continue
        if r.status_code == 429 or r.status_code >= 500:  # busy or server error
            wait = 6 * (attempt + 1)
            log.warning("HTTP %s, retrying in %ss", r.status_code, wait)
            time.sleep(wait)
            continue
        if r.status_code in (401, 402, 403):
            raise CreditsExhausted(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("source still unavailable after retries")


def build_query(cfg, s, since, until):
    terms = s["terms"]
    lang = s.get("lang")
    if cfg.get("ev_terms") and s["theme"] not in cfg.get("exempt_themes", []):
        terms = f"{terms} {cfg['ev_terms'][lang]}"
        if cfg.get("eu_terms", {}).get(lang):
            terms = f"{terms} {cfg['eu_terms'][lang]}"
    q = f"({terms})"
    if lang:
        q += f" lang:{lang}"
    if not cfg.get("include_retweets", True):
        q += " -filter:retweets"
    return q + f" since_time:{since} until_time:{until}"


def parse_window(args, cfg):
    if args.date_to:
        until = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
    else:
        until = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if args.date_from:
        since = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
    else:
        since = until - timedelta(days=cfg.get("window_days", 365))
    return int(since.timestamp()), int(until.timestamp())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--force", action="store_true",
                   help="re-extract every search even if this interval was already landed")
    args = p.parse_args()

    load_env()
    key = os.environ.get("TWITTERAPI_KEY")
    if not key:
        raise SystemExit("TWITTERAPI_KEY is not set (put it in .env)")
    cfg = load_config(args.config)
    since, until = parse_window(args, cfg)

    run_at = datetime.now(timezone.utc)
    run_id = run_at.strftime("%Y%m%dT%H%M%S%f")[:-3]   # unique per run (milliseconds)
    interval = {"from": datetime.fromtimestamp(since, timezone.utc).date().isoformat(),
                "to": datetime.fromtimestamp(until, timezone.utc).date().isoformat()}
    folder = RAW / cfg["name"] / f"extracted_date={run_at.date().isoformat()}"
    posts_file = folder / f"run_{run_id}.json"
    meta_file = folder / f"run_{run_id}.meta.json"
    # checkpoint per config and interval: which searches are already landed (in any earlier run)
    checkpoint = RAW / cfg["name"] / f"_checkpoint_{interval['from']}_{interval['to']}.json"
    progress = {"done": {}} if args.force else read_json(checkpoint, {"done": {}})
    posts, meta = [], {"run_id": run_id, "config": cfg["name"], "interval": interval,
                       "started_at": run_at.isoformat(), "status": "running", "searches": {}}
    log.info("run %s start config=%s interval=%s..%s (to exclusive)", run_id, cfg["name"],
             interval["from"], interval["to"])

    def save(status=None):
        if status:
            meta["status"] = status
        meta["posts"] = len(posts)
        meta["ended_at"] = datetime.now(timezone.utc).isoformat()
        write_json(posts_file, posts)
        write_json(meta_file, meta)
        write_json(checkpoint, progress)

    status = "complete"
    for i, s in enumerate(cfg["searches"]):
        key_name = f"{i:02d}|{s['theme']}|{s.get('lang')}"
        if key_name in progress["done"]:
            log.info("skip (already landed for this interval) %s", key_name)
            continue
        query = build_query(cfg, s, since, until)
        cursor, pages, found, more = "", 0, 0, False
        try:
            while pages < cfg.get("max_pages", 10):
                data = get_page(key, query, cursor)
                pages += 1
                tweets = data.get("tweets") or []
                now = datetime.now(timezone.utc).isoformat()
                for t in tweets:
                    found += 1
                    posts.append({**t, "_extracted_at": now, "_search": f"{s['theme']}|{s.get('lang')}",
                                  "_interval": interval, "_run_id": run_id})
                more = bool(data.get("has_next_page"))
                if not tweets or not more:
                    break
                cursor = data.get("next_cursor", "")
                time.sleep(cfg.get("sleep_seconds", 6))
        except CreditsExhausted as e:
            meta["searches"][key_name] = {"posts": found, "status": "stopped", "error": str(e)}
            log.error("%s: credentials or credits problem, stopping cleanly", e)
            status = "stopped"
            break
        except Exception as e:
            meta["searches"][key_name] = {"posts": found, "status": "failed", "error": str(e)}
            log.error("FAILED %s: %s (retried on next run of this interval)", key_name, e)
            status = "partial"
            save()
            continue
        if found == 0:
            log.info("empty result (valid) %s", key_name)
        meta["searches"][key_name] = {"posts": found, "capped": more, "pages": pages, "status": "complete"}
        progress["done"][key_name] = run_id
        save()
        log.info("done %s posts=%d%s pages=%d", key_name, found, "+" if more else "", pages)
        time.sleep(cfg.get("sleep_seconds", 6))

    save(status)
    log.info("run %s end status=%s searches_done=%d/%d posts_landed=%d file=%s", run_id, status,
             len(progress["done"]), len(cfg["searches"]), len(posts), posts_file)


if __name__ == "__main__":
    main()
