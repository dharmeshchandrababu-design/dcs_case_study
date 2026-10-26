"""Load step: messy input, empty files and reruns must not break the table."""
import json
from collections import Counter

import duckdb
import pytest

import load

CFG = {"name": "chargenow"}


def post(post_id, text="Laden mit #ChargeNow", extracted_at="2026-10-01T10:00:00+00:00", **extra):
    p = {"id": post_id, "createdAt": "Wed Oct 01 08:00:00 +0000 2026", "text": text,
         "lang": "de", "author": {"id": "a1", "followers": 10, "location": "Berlin"},
         "likeCount": 1, "_extracted_at": extracted_at, "_run_id": "r1", "_search": "ChargeNow hashtag"}
    p.update(extra)
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An empty warehouse and a raw folder in a temp directory."""
    monkeypatch.setattr(load, "RAW", tmp_path)
    con = duckdb.connect(str(tmp_path / "test.duckdb"))
    load.create_tables(con)
    yield con, tmp_path
    con.close()


def write_raw(folder, name, posts):
    f = folder / "chargenow" / "extracted_date=2026-10-01" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(posts))
    return f


def test_missing_fields_become_null_and_are_counted():
    missing = Counter()
    row = load.flatten({"id": "1"}, "salt", "chargenow", missing)
    assert row["post_id"] == "1"
    assert row["created_at"] is None and row["author_hash"] is None and row["text"] == ""
    assert missing["createdAt"] == 1 and missing["author.id"] == 1


def test_usernames_masked_and_author_hashed():
    row = load.flatten(post("1", text="Danke @ionity_de"), "salt", "chargenow", Counter())
    assert row["text"] == "Danke @user"
    assert row["author_hash"] != "a1" and len(row["author_hash"]) == 16


def test_empty_raw_file_is_a_valid_run(env):
    con, raw = env
    f = write_raw(raw, "run_1.json", [])
    load.load_files(con, CFG, "salt", [f])
    assert con.execute("select count(*) from raw.posts").fetchone()[0] == 0
    assert con.execute("select count(*) from raw.loaded_files").fetchone()[0] == 1


def test_loading_twice_adds_no_duplicates(env):
    con, raw = env
    f = write_raw(raw, "run_1.json", [post("1"), post("2")])
    load.load_files(con, CFG, "salt", [f])
    load.load_files(con, CFG, "salt", [f])
    assert con.execute("select count(*), count(distinct post_id) from raw.posts").fetchone() == (2, 2)


def test_newest_version_wins(env):
    con, raw = env
    old = write_raw(raw, "run_1.json", [post("1", likeCount=1, extracted_at="2026-10-01T10:00:00+00:00")])
    new = write_raw(raw, "run_2.json", [post("1", likeCount=9, extracted_at="2026-10-02T10:00:00+00:00")])
    load.load_files(con, CFG, "salt", [new])
    load.load_files(con, CFG, "salt", [old])          # an older file arriving late must not win
    assert con.execute("select count(*), max(like_count) from raw.posts").fetchone() == (1, 9)


def test_same_post_twice_in_one_batch(env):
    con, raw = env
    f = write_raw(raw, "run_1.json", [post("1", likeCount=1, extracted_at="2026-10-01T10:00:00+00:00"),
                                      post("1", likeCount=5, extracted_at="2026-10-01T11:00:00+00:00")])
    load.load_files(con, CFG, "salt", [f])
    assert con.execute("select count(*), max(like_count) from raw.posts").fetchone() == (1, 5)


@pytest.mark.parametrize("location, expected", [
    ("Berlin, Deutschland", "europe"), ("Texas, USA", "outside"), ("", "unknown"), (None, "unknown"),
    ("somewhere nice", "unknown")])
def test_region_tag(location, expected):
    assert load.region_tag(location) == expected
