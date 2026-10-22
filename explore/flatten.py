import json, hashlib
import pandas as pd
from datetime import datetime

SALT = "change-me-before-real-use"   # used to anonymise author ids
FILES = {"hashtag": "test_ChargeN.json", "broad": "test_ChargeNo.json"}

def anon(x):
    return hashlib.sha256((SALT + str(x)).encode()).hexdigest()[:12]

def parse(s):
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

def hashtags(t):
    out = []
    for h in (t.get("entities") or {}).get("hashtags") or []:
        out.append((h.get("text") or h.get("tag") or "").lower())
    return ",".join(out)

rows = []
for source, path in FILES.items():
    with open(path) as f:
        for t in json.load(f):
            a = t.get("author") or {}
            rows.append({
                "tweet_id": t.get("id"),
                "created_at": parse(t["createdAt"]),
                "text": t.get("text"),
                "lang": t.get("lang"),
                "author_hash": anon(a.get("id")),
                "followers": a.get("followers"),
                "location": a.get("location"),
                "hashtags": hashtags(t),
                "is_retweet": t.get("retweeted_tweet") is not None,
                "retweets": t.get("retweetCount"),
                "likes": t.get("likeCount"),
                "replies": t.get("replyCount"),
                "quotes": t.get("quoteCount"),
                "views": t.get("viewCount"),
                "found_by": source,
            })

df = pd.DataFrame(rows).drop_duplicates("tweet_id")
df.to_csv("tweets_flat.csv", index=False)

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)
print("rows:", len(df), "| found by hashtag:", (df.found_by == "hashtag").sum())
print("\nfirst 10 posts:")
print(df[["created_at", "lang", "followers", "is_retweet", "text"]].head(10).to_string())
print("\nposts per day:")
print(df.groupby(df.created_at.dt.date).size().tail(15))
print("\nlanguages:")
print(df.lang.value_counts().head(5))
print("\ntop hashtags:")
print(df.hashtags.str.split(",").explode().value_counts().head(10))
print("\nretweet share:", round(df.is_retweet.mean(), 2))