"""Load: flatten the landed raw JSON into the required fields and load it into DuckDB.

Usage:
    python load.py --config chargenow
    python load.py --config ev_charging
    python load.py --config ev_charging --reload   # reprocess all raw files (e.g. after a parser fix)

- reads only raw files not loaded before (tracked in raw.loaded_files)
- one row per post in raw.posts: the latest version wins (by _extracted_at), so updated
  engagement arrives and overlapping intervals or reruns never create duplicates

- author ids are salted and hashed; usernames are never loaded
- @mentions in the text are masked
- parsing is defensive: a missing field becomes NULL and is counted in a warning
- the chargenow run also writes data/output/chargenow_posts.csv with the brief's required fields
"""
import argparse
import hashlib
import os
import re
from collections import Counter
from datetime import datetime, timezone

import duckdb
import pandas as pd

from common import DATA, RAW, warehouse_path, get_logger, load_config, load_env, read_json

log = get_logger("load")

EUROPE = re.compile(r"\b(europe|eu|deutschland|germany|german|berlin|hamburg|münchen|munich|köln|cologne|frankfurt|"
                    r"stuttgart|düsseldorf|dortmund|essen|leipzig|dresden|hannover|nürnberg|bremen|bayern|bavaria|"
                    r"nrw|sachsen|hessen|österreich|austria|wien|vienna|schweiz|switzerland|suisse|zürich|zurich|"
                    r"france|paris|nederland|netherlands|amsterdam|belgi|brussels|danmark|denmark|sverige|sweden|"
                    r"norge|norway|oslo|finland|españa|spain|madrid|italia|italy|polska|poland|portugal|ireland|"
                    r"dublin|uk|united kingdom|england|london|scotland|wales|czech|praha|prague)\b|🇩🇪|🇪🇺|🇦🇹|🇨🇭|🇳🇱|🇬🇧|🇫🇷", re.I)
OUTSIDE = re.compile(r"\b(usa|u\.s\.a?|united states|america|california|texas|florida|new york|nyc|ny|ca|tx|fl|"
                     r"seattle|chicago|boston|canada|toronto|ontario|vancouver|india|mumbai|delhi|bangalore|"
                     r"bengaluru|pakistan|australia|sydney|melbourne|new zealand|nigeria|lagos|kenya|south africa|"
                     r"brasil|brazil|mexico|méxico|china|japan|tokyo|korea|singapore|philippines|indonesia|"
                     r"malaysia|thailand|dubai|uae)\b|🇺🇸|🇮🇳|🇨🇦|🇦🇺", re.I)


def region_tag(location):
    if not location or not location.strip():
        return "unknown"
    if EUROPE.search(location):
        return "europe"
    if OUTSIDE.search(location):
        return "outside"
    return "unknown"


def parse_time(s):
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


CORE_FIELDS = {"id", "createdAt", "text", "author.id"}   # a missing core field is a likely API change


def parse_extracted(s):
    if not s:
        return None
    return datetime.fromisoformat(s).astimezone(timezone.utc).replace(tzinfo=None)


def flatten(t, salt, config_name, missing):
    def get(obj, *path):
        for p in path:
            if not isinstance(obj, dict) or p not in obj:
                if ".".join(path) in CORE_FIELDS:
                    missing[".".join(path)] += 1
                return None
            obj = obj[p]
        return obj

    author_id = get(t, "author", "id")
    tags = get(t, "entities", "hashtags") or []
    return {
        "post_id": str(get(t, "id")),
        "source_config": config_name,
        "created_at": parse_time(get(t, "createdAt")),
        "text": re.sub(r"@\w+", "@user", get(t, "text") or ""),
        "lang": get(t, "lang"),
        "author_hash": hashlib.sha256(f"{salt}{author_id}".encode()).hexdigest()[:16] if author_id else None,
        "follower_count": get(t, "author", "followers"),
        "author_post_count": get(t, "author", "statusesCount"),
        "location_raw": get(t, "author", "location"),
        "region_tag": region_tag(get(t, "author", "location")),
        "hashtags": ",".join(h.get("text", "") for h in tags if isinstance(h, dict)),
        "is_retweet": t.get("retweeted_tweet") is not None,
        "is_reply": bool(t.get("isReply")),
        "like_count": t.get("likeCount"),
        "retweet_count": t.get("retweetCount"),
        "reply_count": t.get("replyCount"),
        "quote_count": t.get("quoteCount"),
        "view_count": t.get("viewCount"),
        "search_themes": "; ".join(t.get("_themes") or [t.get("_search", "")]),
        "extracted_at": parse_extracted(t.get("_extracted_at")),
        "run_id": t.get("_run_id"),
    }


def load_files(con, cfg, salt, new_files):
    """Load new raw files into raw.posts: one row per post, the latest version wins."""
    if not new_files:
        log.info("%s: no new raw files (valid, nothing to load)", cfg["name"])
        return
    missing = Counter()
    rows, n_raw = [], 0
    for f in new_files:
        raw = read_json(f, [])
        n_raw += len(raw)
        rows += [flatten(t, salt, cfg["name"], missing) for t in raw if t.get("id")]
    for field, n in missing.items():
        log.warning("field %s missing in %d of %d posts (possible API change)", field, n, n_raw)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inserted = updated = 0
    df = pd.DataFrame(rows)
    con.execute("begin")
    if not df.empty:
        # 1. inside this batch: one post can appear in several runs or searches.
        #    Keep the latest version, and remember every search that found it.
        themes = df.groupby("post_id")["search_themes"].apply(
            lambda x: "; ".join(sorted({p for v in x for p in v.split("; ") if p})))
        df = df.sort_values("extracted_at", na_position="first").drop_duplicates("post_id", keep="last")
        df["search_themes"] = df["post_id"].map(themes)
        df["loaded_at"] = now

        # 2. against the table: replace a stored post only with an equal or newer version
        con.register("incoming_ids", df[["post_id"]])
        old = con.execute("""select post_id, search_themes as themes_old, extracted_at as extracted_old
                             from raw.posts where post_id in (select post_id from incoming_ids)""").df()
        df = df.merge(old, on="post_id", how="left")
        is_new = ~df["post_id"].isin(old["post_id"])
        is_newer = df["extracted_old"].isna() | (df["extracted_at"] >= df["extracted_old"])
        df = df[is_new | is_newer].copy()
        inserted = int(is_new[df.index].sum())
        updated = len(df) - inserted
        df["search_themes"] = ["; ".join(sorted({p for v in (a, b) if isinstance(v, str)
                                                 for p in v.split("; ") if p}))
                               for a, b in zip(df["search_themes"], df["themes_old"])]
        df = df.drop(columns=["themes_old", "extracted_old"])

        con.register("incoming", df)
        con.execute("delete from raw.posts where post_id in (select post_id from incoming)")
        con.execute("insert into raw.posts by name select * from incoming")
    for f in new_files:
        con.execute("insert or replace into raw.loaded_files values (?, ?, ?, ?)",
                    [str(f.relative_to(RAW)), cfg["name"], len(read_json(f, [])), now])
    con.execute("commit")
    log.info("%s: %d new raw files, %d raw records, %d new posts, %d updated",
             cfg["name"], len(new_files), n_raw, inserted, updated)


def create_tables(con):
    """Create the raw tables if they do not exist; tables from an earlier version get the new columns."""
    con.execute("create schema if not exists raw")
    con.execute("""
        create table if not exists raw.posts (
            post_id varchar primary key, source_config varchar, created_at timestamp, text varchar,
            lang varchar, author_hash varchar, follower_count bigint, author_post_count bigint,
            location_raw varchar, region_tag varchar, hashtags varchar, is_retweet boolean,
            is_reply boolean, like_count bigint, retweet_count bigint, reply_count bigint,
            quote_count bigint, view_count bigint, search_themes varchar, extracted_at timestamp,
            run_id varchar, loaded_at timestamp)
    """)
    # tables created by an earlier version of this script get the new columns
    con.execute("alter table raw.posts add column if not exists extracted_at timestamp")
    con.execute("alter table raw.posts add column if not exists run_id varchar")
    con.execute("""create table if not exists raw.labels (
        post_id varchar, relevant boolean, theme varchar, subcategory varchar, sentiment varchar,
        feedback_type varchar, severity varchar, network_mentioned varchar, label_confidence double,
        model_version varchar, labelled_at timestamp, text_en varchar, updated_at timestamp,
        primary key (post_id, model_version))""")
    con.execute("alter table raw.labels add column if not exists text_en varchar")
    con.execute("alter table raw.labels add column if not exists updated_at timestamp")
    con.execute("""create table if not exists raw.loaded_files (
        file_path varchar primary key, source_config varchar, posts bigint, loaded_at timestamp)""")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--reload", action="store_true",
                   help="reprocess all raw files, not only new ones (e.g. after a parser fix)")
    args = p.parse_args()
    load_env()
    cfg = load_config(args.config)
    salt = os.environ.get("AUTHOR_HASH_SALT", "dcs-case-study")

    con = duckdb.connect(str(warehouse_path()))
    create_tables(con)

    files = sorted((RAW / cfg["name"]).glob("extracted_date=*/run_*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    done = set() if args.reload else {r[0] for r in con.execute(
        "select file_path from raw.loaded_files where source_config = ?", [cfg["name"]]).fetchall()}
    new_files = [f for f in files if str(f.relative_to(RAW)) not in done]
    load_files(con, cfg, salt, new_files)
    total = con.execute("select count(*) from raw.posts where source_config = ?", [cfg["name"]]).fetchone()[0]
    log.info("%s: raw.posts now holds %d posts", cfg["name"], total)

    if cfg["name"] == "chargenow":
        out = DATA / "output"
        out.mkdir(parents=True, exist_ok=True)
        required = ["post_id", "author_hash", "location_raw", "follower_count", "created_at",
                    "hashtags", "author_post_count", "is_retweet"]
        con.execute(f"copy (select {', '.join(required)} from raw.posts where source_config = 'chargenow' "
                    f"order by created_at) to '{out / 'chargenow_posts.csv'}' (header)")
        log.info("wrote %s", out / "chargenow_posts.csv")
    con.close()


if __name__ == "__main__":
    main()
