import os, json, time, requests
from collections import Counter
from datetime import datetime, timezone

KEY = os.environ["TWITTERAPI_KEY"]
URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
START_YEAR = 2014

def get_page(query, cursor):
    for attempt in range(6):
        r = requests.get(URL, headers={"X-API-Key": KEY},
                         params={"query": query, "queryType": "Latest", "cursor": cursor},
                         timeout=60)
        if r.status_code == 429:
            time.sleep(6 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Still rate limited after retries")

def ts(year):
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())

now_year = datetime.now(timezone.utc).year
all_tweets = {}
per_year = Counter()

for year in range(START_YEAR, now_year + 1):
    query = f"#ChargeNow since_time:{ts(year)} until_time:{ts(year + 1)}"
    cursor, pages, n = "", 0, 0
    while pages < 100:
        data = get_page(query, cursor)
        pages += 1
        for t in data.get("tweets", []):
            if t["id"] not in all_tweets:
                all_tweets[t["id"]] = t
                n += 1
        if not data.get("has_next_page"):
            break
        cursor = data.get("next_cursor", "")
        time.sleep(6)
    per_year[year] = n
    print(f"{year}: {n} posts ({pages} pages)")
    time.sleep(6)

print("\nTOTAL unique #ChargeNow posts since", START_YEAR, ":", len(all_tweets))

with open("chargenow_only.json", "w") as f:
    json.dump({"tweets": list(all_tweets.values())}, f)
print("saved chargenow_only.json")