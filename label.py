"""Enrich: label each post that has no label yet, and translate relevant posts to English.
Everything is stored in raw.labels (one row per post and labeller, text_en included).

Usage:
    python label.py                      # provider from config/labelling.yaml
    python label.py --provider keyword   # rule-based baseline, no AI needed
    python label.py --limit 50           # label only 50 posts (pilot)
    python label.py --no-translate       # skip the translation pass

Two passes, both incremental:
1. label posts that have no label from this labeller yet
2. translate posts the AI marked as relevant and that are not in English, if text_en is still empty
   (only relevant posts, so no time is spent translating noise)

The categories come from dbt/seeds/taxonomy.csv and dbt/seeds/networks.csv,
so they are defined once and shared by the AI prompt and the dbt models.
The labeller is one replaceable function (label_batch); switching to Claude
means changing the provider in the config, nothing else.
"""
import argparse
import csv
import json
import os
import re
import time
from datetime import datetime, timezone

import duckdb
import requests

from common import ROOT, get_logger, load_env, warehouse_path

log = get_logger("label")

PROMPT_VERSION = "v3"   # bump when the prompt or categories change; stored in model_version

SENTIMENTS = {"positive", "neutral", "negative", "mixed"}
FEEDBACK_TYPES = {"complaint", "praise", "request", "question", "information"}
SEVERITIES = {"safety", "legal", "financial", "none"}


def read_seed(name):
    with open(ROOT / "dbt" / "seeds" / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


TAXONOMY = read_seed("taxonomy")
PAIRS = {(r["theme"], r["subcategory"]) for r in TAXONOMY}
NETWORKS = read_seed("networks")
NETWORK_PATTERNS = [(n["network"], re.compile(r"\b(" + n["aliases"] + r")\b", re.I)) for n in NETWORKS]


# ---------------------------------------------------------------- keyword baseline
EV = re.compile(r"charg|lade|laden|wallbox|kwh|\bevs?\b|e-auto|elektro|supercharger|ionity|fastned|enbw|"
                r"plugsurfing|charge ?point|tesla|ccs|typ(e)? ?2", re.I)
NOT_EV = re.compile(r"\b(iphone|android phone|smartphone|handy|laptop|airpods|power ?bank|phone charger)\b", re.I)
THEME_RULES = [
    ("billing_payment", "wrong_or_double_charge", r"charged twice|double charged|doppelt (abgebucht|abgerechnet)|zu viel berechnet|overcharg"),
    ("billing_payment", "invoice_or_refund", r"invoice|rechnung|refund|erstattung|gutschrift"),
    ("billing_payment", "payment_method", r"contactless|kontaktlos|credit card|kreditkarte|ec-karte|girocard|card payment|kartenzahlung|ad[- ]hoc"),
    ("price", "roaming_surcharge", r"roaming ?(fee|geb[üu]hr|aufschlag|surcharge)"),
    ("price", "blocking_fee", r"blocking fee|blockiergeb[üu]hr|idle fee|standgeb[üu]hr"),
    ("price", "price_transparency", r"hidden fee|versteckt|undurchsichtig|intransparent|price display|preisangabe|unclear price"),
    ("price", "price_level", r"per kwh|pro kwh|ct/kwh|\bcents?\b|expensive|teuer|wucher|abzocke|price|preis|ladekosten|ladepreis"),
    ("access_roaming", "card_or_app_not_accepted", r"(card|karte|rfid|app).{0,40}(not accepted|rejected|declined|abgelehnt|nicht akzeptiert|funktioniert nicht|doesn.t work)"),
    ("access_roaming", "session_wont_start", r"(won.t|wouldn.t|doesn.t|did not|didn.t) start|startet nicht|bricht ab|abgebrochen|session (failed|stopped)"),
    ("access_roaming", "network_not_covered", r"roaming|not supported|nicht unterst[üu]tzt"),
    ("app_digital", "app_bug_or_login", r"\bapp\b.{0,40}(crash|bug|login|log in|anmeld|absturz|useless|nutzlos|fehler)"),
    ("app_digital", "charger_status_accuracy", r"shows? (as )?(available|free)|status (wrong|falsch)|als frei angezeigt|map (wrong|falsch)"),
    ("app_digital", "route_planning", r"route|routen|road trip|langstrecke|trip plan|abrp|reiseplan"),
    ("reliability", "charger_broken", r"broken|out of order|not working|defekt|kaputt|au[ßs]er betrieb|geht nicht|offline|st[öo]rung"),
    ("reliability", "charger_occupied", r"occupied|queue|blocked by|besetzt|blockiert|zugeparkt|warteschlange|waiting"),
    ("reliability", "slow_charging", r"slow charg|langsam|only \d+ ?kw|nur \d+ ?kw|throttl|gedrosselt"),
    ("coverage", "too_few_chargers", r"not enough charg|too few|zu wenig(e)? lade|no charg(er|ing) (station|point)s? (near|in)|ladew[üu]ste"),
    ("home_fleet", "fleet_and_company_car", r"company car|fleet|dienstwagen|firmenwagen|flotte|reimburs"),
    ("home_fleet", "home_charging", r"wallbox|home charg|zu hause laden|zuhause laden|heimladen"),
    ("regulation", "regulation_and_compliance", r"afir|eichrecht|ladesäulenverordnung|regulation|verordnung|gesetz"),
]
THEME_RULES = [(t, s, re.compile(p, re.I)) for t, s, p in THEME_RULES]
NEG = re.compile(r"broken|not working|defekt|kaputt|terrible|awful|nightmare|annoying|frustrat|nervig|[äa]rger|"
                 r"expensive|teuer|rip.?off|abzocke|wucher|worst|useless|nutzlos|fail|problem|disaster|katastroph|"
                 r"sucks|schlecht|mies|hate|hasse|wtf|unacceptable|unzumutbar|frust", re.I)
POS = re.compile(r"great|love|easy|seamless|super|toll|klasse|perfekt|perfect|fantastic|awesome|excellent|"
                 r"works well|happy|zufrieden|smooth|reliable|zuverl[äa]ssig|amazing|recommend|empfehl", re.I)
SAFETY = re.compile(r"\bfire\b|caught fire|feuer\b|brandgefahr|in brand|\bsparks?\b|sparking|funken|electric shock|stromschlag|dangerous|gef[äa]hrlich|unsafe|melted|geschmolzen", re.I)
FINANCIAL = re.compile(r"charged twice|double charged|doppelt|refund|erstattung|overcharg|abgezockt|zu viel berechnet|scam|betrug", re.I)
LEGAL = re.compile(r"eichrecht|afir|illegal|rechtswidrig|preisangabe|price display|gdpr|dsgvo|abmahnung", re.I)
REQUEST = re.compile(r"\bwish\b|should|would be (nice|great)|please add|w[üu]nsche|sollte|w[äa]re (schön|toll|gut)|bitte", re.I)


def keyword_label(text):
    relevant = bool(EV.search(text)) and not NOT_EV.search(text)
    theme, sub = "other", "other"
    for t, s, rx in THEME_RULES:
        if rx.search(text):
            theme, sub = t, s
            break
    neg, pos = len(NEG.findall(text)), len(POS.findall(text))
    sentiment = "mixed" if neg and pos else "negative" if neg else "positive" if pos else "neutral"
    if theme == "other" and relevant and sentiment != "neutral":
        theme, sub = "experience", "overall_experience"
    if "?" in text:
        ftype = "question"
    elif REQUEST.search(text):
        ftype = "request"
    elif sentiment in ("negative", "mixed"):
        ftype = "complaint"
    elif sentiment == "positive":
        ftype = "praise"
    else:
        ftype = "information"
    severity = ("safety" if SAFETY.search(text) else "financial" if FINANCIAL.search(text)
                else "legal" if LEGAL.search(text) else "none")
    network = next((name for name, rx in NETWORK_PATTERNS if rx.search(text)), "none")
    return {"relevant": relevant, "theme": theme, "subcategory": sub, "sentiment": sentiment,
            "feedback_type": ftype, "severity": severity, "network_mentioned": network,
            "label_confidence": 0.4}


# ---------------------------------------------------------------- AI labellers
def build_prompt():
    cats = "\n".join(f"- {r['theme']} / {r['subcategory']}: {r['description']}" for r in TAXONOMY)
    nets = ", ".join(n["network"] for n in NETWORKS)
    return f"""You classify public social media posts about EV charging in Europe for an e-mobility
service provider. Posts are in English or German. For every post return one JSON object.

Fields and allowed values:
- relevant: true if the post talks about charging an electric vehicle in any way: public or home
  charging, charging prices or tariffs, charge cards or apps, roaming, charging networks or providers,
  broken or occupied chargers, payment at chargers, wallboxes, charging rules (AFIR, Eichrecht).
  This includes posts where charging is only one part of a wider discussion.
  false only if charging plays no role: EV sales or market news, car models, stocks, politics,
  or other topics.
- theme and subcategory: exactly one pair from this list (use other / other if nothing fits):
{cats}
- sentiment: positive, neutral, negative or mixed (the author's feeling about charging)
- feedback_type: complaint, praise, request, question or information
- severity: safety (danger to people), legal (possible rule breach such as missing price display
  or billing not compliant with Eichrecht or AFIR), financial (the customer was charged wrongly:
  charged twice, more than the agreed or displayed price, money blocked or not refunded), or none.
  High or unfair prices on their own are NOT financial severity: use none for them.
- network: the ONE main charging network or provider the post is about, one of [{nets}],
  another single name if not in the list, or none. Never return a list of several names.
- confidence: your confidence from 0 to 1

Answer only with JSON in this shape:
{{"labels": [{{"id": "...", "relevant": true, "theme": "...", "subcategory": "...",
"sentiment": "...", "feedback_type": "...", "severity": "...", "network": "...", "confidence": 0.8}}]}}

Examples:
"Ladesäule bei Ionity schon wieder defekt, dritte diese Woche" ->
relevant true, reliability / charger_broken, negative, complaint, none, Ionity
"Why do I pay 79 cents per kWh with roaming? Ridiculous" ->
relevant true, price / roaming_surcharge, negative, complaint, none, none
"Cable at the charger was melted and sparking, stay away" ->
relevant true, reliability / charger_broken, negative, complaint, safety, none
"Wish the app showed the price before I plug in" ->
relevant true, price / price_transparency, neutral, request, none, none
"In Ungarn wäre Roaming teuer, ich habe dort Ionity für 39ct genutzt" ->
relevant true, price / roaming_surcharge, neutral, information, none, Ionity
"EnBW für ADAC Mitglieder war super, bis die Kombi zurückgenommen wurde" ->
relevant true, price / price_level, mixed, complaint, none, EnBW
"5 Ladekarten und 3 Abos, warum kann ich nicht einfach mit EC-Karte zahlen?" ->
relevant true, access_roaming / too_many_apps_and_cards, negative, complaint, none, none
"An derselben Säule kostet es 39 Cent mit Vertrag und 79 Cent ad hoc" ->
relevant true, price / price_spread_same_charger, negative, complaint, none, none
"79 Cent pro kWh bei EnBW und dann wurden 150 Euro auf der Kreditkarte geblockt" ->
relevant true, billing_payment / payment_preauthorisation, negative, complaint, financial, EnBW
"Ionity is so expensive now, 85 cents per kWh" ->
relevant true, price / price_level, negative, complaint, none, Ionity
"Nur eine Ladesäule im ganzen Ort, ständig besetzt" ->
relevant true, coverage / too_few_chargers, negative, complaint, none, none
"France hit a record 35% EV market share in July" -> relevant false, other / other, neutral, information, none, none
"My phone charger broke again" -> relevant false, other / other, negative, complaint, none, none

Use exactly the field names above. Put the theme in "theme" and the subcategory in "subcategory".
Return one object per post, with the same "id" as given, for every post in the input."""


SYSTEM_PROMPT = build_prompt()


def parse_json(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def call_ollama(model, user_msg, system=None):
    r = requests.post("http://localhost:11434/api/chat", timeout=300, json={
        "model": model, "stream": False, "format": "json", "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system or SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]})
    r.raise_for_status()
    return r.json()["message"]["content"]


def call_anthropic(model, user_msg, system=None):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=300,
                      headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                               "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": 4000, "temperature": 0, "system": system or SYSTEM_PROMPT,
                            "messages": [{"role": "user", "content": user_msg}]})
    r.raise_for_status()
    return r.json()["content"][0]["text"]


SUBCAT_TO_THEME = {sub: theme for theme, sub in PAIRS}


def norm(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def validate(item):
    """Return (clean label dict, None) or (None, reason) if the answer breaks the rules.
    Small format differences are repaired: 'price / roaming_surcharge' in one field,
    capital letters, spaces instead of underscores, a missing theme when the subcategory is clear."""
    theme, sub = norm(item.get("theme")), norm(item.get("subcategory"))
    if "/" in theme:
        theme, _, maybe_sub = theme.partition("/")
        theme, sub = theme.strip("_"), sub or maybe_sub.strip("_")
    if "/" in sub:
        sub = sub.partition("/")[2].strip("_")
    if (theme, sub) not in PAIRS and sub in SUBCAT_TO_THEME:
        theme = SUBCAT_TO_THEME[sub]
    if (theme, sub) not in PAIRS:                       # e.g. other/none, or a theme with one subcategory
        subs = [s2 for t2, s2 in PAIRS if t2 == theme]
        if len(subs) == 1:
            sub = subs[0]
    if (theme, sub) not in PAIRS:
        return None, f"unknown theme/subcategory {theme}/{sub}"
    sentiment, ftype = norm(item.get("sentiment")), norm(item.get("feedback_type"))
    severity = norm(item.get("severity")) or "none"
    if sentiment not in SENTIMENTS:
        return None, f"bad sentiment {sentiment}"
    if ftype not in FEEDBACK_TYPES:
        return None, f"bad feedback_type {ftype}"
    if severity not in SEVERITIES:
        return None, f"bad severity {severity}"
    try:
        conf = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = 0.5
    rel = item.get("relevant")
    rel = rel if isinstance(rel, bool) else str(rel).strip().lower() in ("true", "yes", "1")
    network = str(item.get("network") or "none").strip() or "none"
    return {"relevant": rel, "theme": theme, "subcategory": sub, "sentiment": sentiment,
            "feedback_type": ftype, "severity": severity,
            "network_mentioned": "none" if network.lower() in ("none", "null", "n/a") else network,
            "label_confidence": conf}, None


def answer_items(answer):
    """Accept the shapes models tend to return: {"labels": [...]}, a plain list, or one object."""
    if isinstance(answer, list):
        return answer
    if isinstance(answer, dict):
        for key in ("labels", "posts", "results", "items"):
            if isinstance(answer.get(key), list):
                return answer[key]
        if "theme" in answer:
            return [answer]
    return []


def label_batch(provider, model, batch, debug=False):
    """The one replaceable function: list of (post_id, text) -> {post_id: (labels, model_version)}."""
    if provider == "keyword":
        return {pid: (keyword_label(text), "keyword-v1") for pid, text in batch}
    call = {"ollama": call_ollama, "anthropic": call_anthropic}[provider]
    user_msg = json.dumps([{"id": pid, "text": text[:1000]} for pid, text in batch], ensure_ascii=False)
    ids = {pid for pid, _ in batch}
    out = {}
    for attempt in range(2):                 # one retry if the answer is not usable
        try:
            raw_answer = call(model, user_msg)
            if debug:
                log.info("raw answer: %s", raw_answer[:2000])
            items = answer_items(parse_json(raw_answer))
            for pos, item in enumerate(items):
                pid = str(item.get("id", "")).strip()
                if pid not in ids and len(items) == len(batch):
                    pid = batch[pos][0]      # model dropped or changed the id: use the position
                clean, reason = validate(item)
                if clean and pid in ids:
                    out[pid] = (clean, f"{provider}:{model}:{PROMPT_VERSION}")
                elif reason:
                    log.warning("post %s: %s", pid, reason)
            if len(out) == len(batch):
                break
        except (ValueError, KeyError, requests.RequestException) as e:
            log.warning("batch answer unusable (%s), attempt %d", e, attempt + 1)
            time.sleep(3)
    missing = [(pid, text) for pid, text in batch if pid not in out]
    if missing and len(batch) > 1:           # the model skipped posts: ask again one post at a time
        log.info("%d posts missing from the batch answer, labelling them one by one", len(missing))
        for single in missing:
            out.update({k: v for k, v in label_batch(provider, model, [single], debug).items()
                        if v[1] != "keyword-fallback"})
    for pid, text in batch:                  # anything still missing falls back to the baseline
        if pid not in out:
            log.warning("post %s: no valid AI label, using keyword fallback", pid)
            out[pid] = (keyword_label(text), "keyword-fallback")
    return out


# ---------------------------------------------------------------- translation
TRANSLATE_PROMPT = ("Translate each social media post to natural English. Keep the meaning, tone, numbers, "
                    "prices and company names. Do not add explanations. Answer only with JSON: "
                    '{"translations": [{"id": "...", "text_en": "..."}]} with one object per post and the same id.')


def translate_batch(provider, model, batch):
    """list of (post_id, text) -> {post_id: English text}. Missing posts are simply left out."""
    user_msg = json.dumps([{"id": pid, "text": text[:1000]} for pid, text in batch], ensure_ascii=False)
    call = {"ollama": call_ollama, "anthropic": call_anthropic}[provider]
    ids = [pid for pid, _ in batch]
    for attempt in range(2):
        try:
            answer = parse_json(call(model, user_msg, TRANSLATE_PROMPT))
            items = answer.get("translations", []) if isinstance(answer, dict) else answer
            out = {str(i.get("id")): str(i.get("text_en", "")).strip() for i in items if i.get("text_en")}
            if not set(out) & set(ids) and len(items) == len(batch):   # ids lost: use the position
                out = {ids[n]: str(i.get("text_en", "")).strip() for n, i in enumerate(items)}
            return {k: v for k, v in out.items() if k in ids and v}
        except (ValueError, KeyError, AttributeError, requests.RequestException) as e:
            log.warning("translation answer unusable (%s), attempt %d", e, attempt + 1)
            time.sleep(3)
    return {}


def translation_pass(con, provider, model, source_config, size, limit=None, retranslate=False):
    # reuse a translation that already exists for the same post (e.g. from an earlier labeller).
    # With --retranslate this is skipped, so posts labelled by the new model get a fresh translation
    # from it. An interrupted run simply continues with the same command.
    if not retranslate:
        con.execute("""
        update raw.labels set text_en = t.text_en
        from (select post_id, max(text_en) as text_en from raw.labels where text_en is not null group by post_id) t
        where raw.labels.post_id = t.post_id and raw.labels.text_en is null
        """)
    todo = con.execute("""
        with ai as (
            select * from raw.labels where model_version not like 'keyword%'
            qualify row_number() over (partition by post_id order by labelled_at desc) = 1
        )
        select ai.post_id, ai.model_version, p.text
        from ai join raw.posts p using (post_id)
        where ai.relevant and ai.text_en is null and coalesce(p.lang, '') <> 'en' and p.source_config = ?
        order by ai.post_id
    """, [source_config]).fetchall()
    if limit:
        todo = todo[:limit]
    log.info("translating %d relevant non-English posts", len(todo))
    versions = {pid: mv for pid, mv, _ in todo}
    done = 0
    for i in range(0, len(todo), size):
        batch = [(pid, text) for pid, _, text in todo[i: i + size]]
        result = translate_batch(provider, model, batch)
        for single in [b for b in batch if b[0] not in result and len(batch) > 1]:
            result.update(translate_batch(provider, model, [single]))   # retry skipped posts one by one
        con.executemany("update raw.labels set text_en = ? where post_id = ? and model_version = ?",
                        [(t, pid, versions[pid]) for pid, t in result.items()])
        done += len(result)
        log.info("translation progress %d/%d", min(i + size, len(todo)), len(todo))
    log.info("translated %d posts (untranslated ones are retried on the next run)", done)


# ---------------------------------------------------------------- main
def main():
    import yaml
    p = argparse.ArgumentParser()
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--limit", type=int)
    p.add_argument("--debug", action="store_true", help="print the raw AI answer")
    p.add_argument("--no-translate", action="store_true", help="skip the translation pass")
    p.add_argument("--retranslate", action="store_true",
                   help="give posts labelled in this run a fresh translation instead of reusing the old one")
    p.add_argument("--only-relevant", action="store_true",
                   help="only label posts that the latest AI label marked as relevant (e.g. relabel with a bigger model)")
    args = p.parse_args()
    load_env()
    cfg = yaml.safe_load(open(ROOT / "config" / "labelling.yaml"))
    provider = args.provider or cfg["provider"]
    model = args.model or cfg.get("model")
    version = "keyword-v1" if provider == "keyword" else f"{provider}:{model}:{PROMPT_VERSION}"

    con = duckdb.connect(str(warehouse_path()))
    con.execute("""
        create table if not exists raw.labels (
            post_id varchar, relevant boolean, theme varchar, subcategory varchar, sentiment varchar,
            feedback_type varchar, severity varchar, network_mentioned varchar, label_confidence double,
            model_version varchar, labelled_at timestamp, text_en varchar,
            primary key (post_id, model_version))
    """)
    con.execute("alter table raw.labels add column if not exists text_en varchar")
    # only posts that have no label from this labeller yet (incremental, saves AI calls)
    todo = con.execute("""
        select p.post_id, p.text from raw.posts p
        where p.source_config = ?
          and not exists (select 1 from raw.labels l where l.post_id = p.post_id and l.model_version = ?)
          and (not ? or p.post_id in (
                select post_id from raw.labels where model_version not like 'keyword%'
                qualify row_number() over (partition by post_id order by labelled_at desc) = 1 and relevant))
        order by p.post_id
    """, [cfg["source_config"], version, args.only_relevant]).fetchall()
    if args.limit:
        todo = todo[: args.limit]
    log.info("labelling %d posts with %s", len(todo), version)

    size = 1 if provider == "keyword" else cfg.get("batch_size", 10)
    done = 0
    for i in range(0, len(todo), max(size, 1) if provider != "keyword" else len(todo) or 1):
        batch = todo[i: i + (size if provider != "keyword" else len(todo))]
        results = label_batch(provider, model, batch, debug=args.debug)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [(pid, l["relevant"], l["theme"], l["subcategory"], l["sentiment"], l["feedback_type"],
                 l["severity"], l["network_mentioned"], l["label_confidence"], mv, now)
                for pid, (l, mv) in results.items()]
        con.executemany("""insert or replace into raw.labels
            (post_id, relevant, theme, subcategory, sentiment, feedback_type, severity,
             network_mentioned, label_confidence, model_version, labelled_at)
            values (?,?,?,?,?,?,?,?,?,?,?)""", rows)
        done += len(rows)
        if provider != "keyword":
            log.info("progress %d/%d", done, len(todo))
    log.info("labelled %d posts", done)
    if provider != "keyword" and not args.no_translate:
        translation_pass(con, provider, model, cfg["source_config"], cfg.get("batch_size", 5), args.limit,
                         retranslate=args.retranslate)
    con.close()


if __name__ == "__main__":
    main()
