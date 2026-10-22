"""Accuracy check for the AI labels: compare them with hand labels on a sample.

    python pilot.py export      # writes findings/pilot_hand_labels.csv (30 AI-relevant + 20 AI-not-relevant posts)
    python pilot.py compare     # after you filled the sheet: compares your labels with the AI labels

The sheet shows the post (and an English translation), never the AI's answers, so the check is fair.
"""
import argparse

import duckdb
import pandas as pd
import yaml

from common import ROOT, warehouse_path

SHEET = ROOT / "findings" / "pilot_hand_labels.csv"
FIELDS = ["relevant", "theme", "sentiment", "feedback_type", "severity"]

LATEST_AI = """
    select * from raw.labels where model_version not like 'keyword%'
    qualify row_number() over (partition by post_id order by labelled_at desc) = 1
"""


def export(n_rel, n_irr):
    con = duckdb.connect(str(warehouse_path()))
    df = con.execute(f"""
        with ai as ({LATEST_AI}),
        picked as (
            (select post_id from ai where relevant order by hash(post_id) limit {n_rel})
            union all
            (select post_id from ai where not relevant order by hash(post_id) limit {n_irr})
        )
        select p.post_id, p.lang, p.text, ai.text_en
        from picked join raw.posts p using (post_id) join ai using (post_id)
        order by hash(p.post_id)
    """).df()
    # translate the German posts that have no English text yet, so every row is readable
    missing = df[(df.lang != "en") & df.text_en.isna()]
    if len(missing):
        from label import translate_batch
        cfg = yaml.safe_load(open(ROOT / "config" / "labelling.yaml"))
        rows = list(zip(missing.post_id, missing.text))
        for i in range(0, len(rows), 5):
            out = translate_batch(cfg["provider"], cfg["model"], rows[i:i + 5])
            df.loc[df.post_id.isin(out), "text_en"] = df.post_id.map(out)
    df["text_en"] = df["text_en"].fillna(df["text"])
    for f in FIELDS:
        df[f] = ""
    SHEET.parent.mkdir(exist_ok=True)
    df.to_csv(SHEET, index=False, encoding="utf-8-sig")
    print(f"wrote {SHEET} ({len(df)} posts). Fill in: {', '.join(FIELDS)}")


def compare(sheet):
    hand = pd.read_csv(sheet, dtype=str, encoding="utf-8-sig").fillna("")
    con = duckdb.connect(str(warehouse_path()), read_only=True)
    ai = con.execute(f"""
        with ai as ({LATEST_AI})
        select ai.post_id, lower(cast(ai.relevant as varchar)) as relevant, ai.theme, ai.sentiment,
               ai.feedback_type, coalesce(f.severity, 'none') as severity, ai.model_version
        from ai left join core.fct_posts f using (post_id)
    """).df()
    m = hand.merge(ai, on="post_id", suffixes=("_hand", "_ai"))
    lines = [f"# Label accuracy (reference sheet: {sheet.name}, n = {len(m)} posts, "
             f"labeller: {', '.join(m.model_version.unique())})", ""]
    for f in FIELDS:
        rows = m[m[f + "_hand"].str.strip() != ""]
        if f != "relevant":   # theme, sentiment etc. only make sense for posts you marked relevant
            rows = rows[rows["relevant_hand"].str.strip().str.lower().isin(["true", "yes", "1"])]
        if rows.empty:
            continue
        agree = (rows[f + "_hand"].str.strip().str.lower() == rows[f + "_ai"].fillna("").str.lower()).sum()
        lines.append(f"- {f}: {agree} of {len(rows)} match ({agree / len(rows):.0%})")
    out = ROOT / "findings" / f"label_accuracy_{sheet.stem}.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["export", "compare"])
    p.add_argument("--relevant", type=int, default=30)
    p.add_argument("--not-relevant", type=int, default=20)
    p.add_argument("--sheet", default=str(SHEET), help="reference sheet to compare against")
    a = p.parse_args()
    export(a.relevant, a.not_relevant) if a.action == "export" else compare(ROOT / a.sheet if not a.sheet.startswith("/") else __import__("pathlib").Path(a.sheet))
