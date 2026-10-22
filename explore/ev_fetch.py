import os, json, re, time, logging, requests
from datetime import datetime, timezone, timedelta

KEY = os.environ["TWITTERAPI_KEY"]
URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

MAX_PAGES = 10          # about 20 posts per page, so up to ~200 posts per search
SLEEP = 6               # seconds between calls (matches the free-tier rate limit)
WINDOW_DAYS = 365       # last 12 months

RAW_FILE = "ev_charging_raw.json"
PROGRESS_FILE = "ev_progress.json"
LOG_FILE = "ev_fetch.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("ev_fetch")

# English results are kept European by content
EU = '(Europe OR European OR Germany OR Berlin OR Ionity OR Plugsurfing OR EnBW OR Fastned OR Allego OR euros OR "Type 2")'
# These searches already contain EV- and Europe-specific terms
EU_EXEMPT = {"8 Competitors", "9 Regulation"}
# Every other search must also contain an EV term, so phones and laptops are excluded
EV_EN = '(EV OR EVs OR "electric car" OR "electric vehicle" OR "charge point" OR "charging station" OR wallbox)'
EV_DE = '(Elektroauto OR E-Auto OR Elektrofahrzeug OR Ladesäule OR Ladestation OR Ladepunkt OR Wallbox)'

# (theme, language, search terms)
QUERIES = [
    # ---------- English ----------
    ("1 Access/roaming", "en", '(RFID OR "charging card" OR "charge card") (rejected OR declined OR "not accepted" OR "doesn\'t work")'),
    ("1 Access/roaming", "en", '(roaming OR "charging app") (problem OR issue OR frustrating OR nightmare)'),
    ("2 Price", "en", '("per kWh" OR "roaming fee" OR surcharge OR "hidden fee" OR "too expensive" OR price)'),
    ("3 Billing", "en", '(invoice OR billed OR "charged twice" OR "double charged" OR refund)'),
    ("4 App", "en", '("charging app" OR "charge app") (crash OR "can\'t log" OR slow OR bug OR useless OR "doesn\'t show")'),
    ("5 Reliability", "en", '(broken OR "out of order" OR "not working" OR offline)'),
    ("5 Reliability", "en", '(occupied OR queue OR "blocked by" OR waiting)'),
    ("6 Alternatives", "en", '(contactless OR "credit card" OR "card payment")'),
    ("8 Competitors", "en", '(Ionity OR Plugsurfing OR "Shell Recharge" OR EnBW OR Fastned OR Allego) (problem OR broken OR expensive OR great OR love)'),
    ("7 Fleet", "en", '("company car" OR fleet) (charging) (reimburse OR expenses OR card OR billing)'),
    ("9 Regulation", "en", '(AFIR OR Eichrecht) (charging OR charger)'),
    ("Praise", "en", '("great charging experience" OR "charging worked" OR "easy to charge" OR seamless)'),
    ("Discovery", "en", '("EV charging" OR "public charging") (wish OR "why can\'t" OR frustrating OR annoying)'),
    ("Discovery trip", "en", '("road trip" OR "long distance") (charging) (plan OR stops OR problem)'),
    ("Discovery home", "en", '("home charger" OR "home charging") (cost OR install OR bill OR billing OR problem)'),
    # ---------- German ----------
    ("1 Access/roaming", "de", '(Ladekarte OR Ladekarten OR RFID) ("funktioniert nicht" OR abgelehnt OR "nicht akzeptiert")'),
    ("1 Access/roaming", "de", '("Lade-App" OR Ladeapp OR Roaming) (Problem OR nervig OR Fehler OR unmöglich)'),
    ("2 Price", "de", '(Ladepreis OR Ladepreise OR "pro kWh" OR Ladekosten OR Roaminggebühr) (teuer OR Wucher OR undurchsichtig)'),
    ("3 Billing", "de", '(Laden OR Ladevorgang) (Rechnung OR abgerechnet OR "doppelt abgerechnet" OR Erstattung)'),
    ("5 Reliability", "de", '(defekt OR kaputt OR "außer Betrieb" OR "geht nicht" OR blockiert OR besetzt)'),
    ("6 Alternatives", "de", '(Kartenzahlung OR EC-Karte OR Kreditkarte OR kontaktlos)'),
    ("8 Competitors", "de", '(Ionity OR Plugsurfing OR "Shell Recharge" OR EnBW OR Fastned OR Allego OR Elli) (Problem OR teuer OR super OR Ärger)'),
    ("7 Fleet", "de", '(Dienstwagen OR Firmenwagen OR Flotte) (Laden OR Ladekarte) (Abrechnung OR Erstattung)'),
    ("9 Regulation", "de", '(Eichrecht OR AFIR OR Ladesäulenverordnung) (Laden OR Ladesäule)'),
    ("Discovery", "de", '(Laden OR Ladesäule) (nervig OR Frust OR "wünsche mir" OR "warum kann man nicht")'),
    ("Discovery trip", "de", '(Urlaub OR Langstrecke OR Reise) (Ladestopp OR Laden) (Problem OR planen)'),
    ("Discovery home", "de", '(Kosten OR Abrechnung OR Installation OR Problem)'),
]


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def get_page(query, cursor):
    for attempt in range(6):
        r = requests.get(
            URL,
            headers={"X-API-Key": KEY},
            params={"query": query, "queryType": "Latest", "cursor": cursor},
            timeout=60,
        )
        if r.status_code == 429:
            wait = 6 * (attempt + 1)
            log.warning("429 rate limited, waiting %ss", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Still rate limited after retries")


def clean(text):
    text = re.sub(r"@\w+", "@user", text or "")
    return " ".join(text.split())


# The time window is fixed on the first run, so reruns use exactly the same range
progress = load_json(PROGRESS_FILE, None)
if progress is None:
    now = datetime.now(timezone.utc)
    progress = {
        "since": int((now - timedelta(days=WINDOW_DAYS)).timestamp()),
        "until": int(now.timestamp()),
        "done": {},
    }
    save_json(PROGRESS_FILE, progress)

store = {t["id"]: t for t in load_json(RAW_FILE, [])}
log.info("Start. Window %s to %s. Posts already stored: %d. Searches done: %d of %d",
         datetime.fromtimestamp(progress["since"], timezone.utc).date(),
         datetime.fromtimestamp(progress["until"], timezone.utc).date(),
         len(store), len(progress["done"]), len(QUERIES))

for i, (theme, lang, terms) in enumerate(QUERIES):
    key = f"{i:02d}|{theme}|{lang}"
    if key in progress["done"]:
        log.info("Skip (already done): %s", key)
        continue

    if theme not in EU_EXEMPT:
        terms = f"{terms} {EV_EN if lang == 'en' else EV_DE}"
        if lang == "en":
            terms = f"{terms} {EU}"
    full = (f"({terms}) lang:{lang} -filter:retweets "
            f"since_time:{progress['since']} until_time:{progress['until']}")

    cursor, pages, found, more, samples = "", 0, 0, False, []
    try:
        while pages < MAX_PAGES:
            data = get_page(full, cursor)
            pages += 1
            tweets = data.get("tweets", [])
            for t in tweets:
                found += 1
                if len(samples) < 2:
                    samples.append(clean(t.get("text"))[:120])
                rec = store.setdefault(t["id"], {**t, "_themes": []})
                tag = f"{theme}|{lang}"
                if tag not in rec["_themes"]:
                    rec["_themes"].append(tag)
            more = bool(data.get("has_next_page"))
            if not tweets or not more:
                break
            cursor = data.get("next_cursor", "")
            time.sleep(SLEEP)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        save_json(RAW_FILE, list(store.values()))
        if code in (401, 402, 403):
            log.error("HTTP %s: credits are probably used up. Stopping.", code)
            break
        log.error("FAILED %s: %s (will retry on next run)", key, e)
        continue
    except Exception as e:
        log.error("FAILED %s: %s (will retry on next run)", key, e)
        save_json(RAW_FILE, list(store.values()))
        continue

    progress["done"][key] = {"posts": found, "capped": more, "pages": pages}
    save_json(RAW_FILE, list(store.values()))
    save_json(PROGRESS_FILE, progress)
    log.info("[%s] posts: %d%s (%d pages)", key, found, "+" if more else "", pages)
    for s in samples:
        print("   -", s)
    time.sleep(SLEEP)

print("\n===== SUMMARY =====")
for key, v in progress["done"].items():
    print(f"{key:<40} {v['posts']}{'+' if v['capped'] else ''}")
print("\nSearches done:", len(progress["done"]), "of", len(QUERIES))
print("Unique posts stored:", len(store))
print("Files:", RAW_FILE, PROGRESS_FILE, LOG_FILE)