import os, json, time, requests
from collections import Counter
from datetime import datetime

KEY = os.environ["TWITTERAPI_KEY"]
URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

def parse(s):
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

def fetch(query, max_pages):
    cursor, tweets, pages = "", [], 0
    while pages < max_pages:
        for attempt in range(5):
            r = requests.get(URL, headers={"X-API-Key": KEY},
                             params={"query": query, "queryType": "Latest", "cursor": cursor},
                             timeout=30)
            if r.status_code == 429:
                wait = 6 * (attempt + 1)
                print(f"  rate limit (429), waiting {wait}s, attempt {attempt + 1}")
                time.sleep(wait)
                continue
            r.raise_for_status()
            break
        else:
            print("  giving up after repeated 429, keeping what we have")
            break
        data = r.json()
        tweets += data.get("tweets", [])
        pages += 1
        print(f"  page {pages}: {len(tweets)} tweets so far")
        if not data.get("has_next_page"):
            break
        cursor = data.get("next_cursor", "")
        time.sleep(6)
    return tweets, pages

for query, max_pages in [("#ChargeNow", 10), ('ChargeNow OR "Charge Now"', 5)]:
    print("\n==", query, "==")
    tweets, pages = fetch(query, max_pages)
    print("pages:", pages, "| tweets:", len(tweets))
    if not tweets:
        continue
    dates = sorted(parse(t["createdAt"]) for t in tweets)
    print("from", dates[0].date(), "to", dates[-1].date())
    print("retweets:", sum(1 for t in tweets if t.get("retweeted_tweet")))
    print("languages:", Counter(t.get("lang") for t in tweets).most_common(5))
    print("distinct authors:", len({t["author"]["id"] for t in tweets}))
    for t in tweets[:10]:
        print("-", t["text"][:140].replace("\n", " "))
    with open(f"test_{query[:8].replace('#', '')}.json", "w") as f:
        json.dump(tweets, f)