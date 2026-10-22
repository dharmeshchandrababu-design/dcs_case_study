"""Export posts with their AI label and keyword label to a CSV for review in Excel.

    python export_labels.py          # writes findings/labels_review.csv
"""
import duckdb

from common import ROOT, warehouse_path

con = duckdb.connect(str(warehouse_path()), read_only=True)
df = con.execute("""
    select
        p.post_id, p.created_at, p.lang, p.region_tag, p.text,
        ai.text_en,
        ai.relevant      as ai_relevant,
        ai.theme         as ai_theme,
        ai.subcategory   as ai_subcategory,
        ai.sentiment     as ai_sentiment,
        ai.feedback_type as ai_feedback_type,
        ai.severity      as ai_severity,
        ai.network_mentioned as ai_network,
        ai.label_confidence  as ai_confidence,
        coalesce(ai.model_version, 'no AI label') as ai_model,
        kw.theme         as keyword_theme,
        kw.sentiment     as keyword_sentiment
    from raw.posts p
    left join (select * from raw.labels where model_version not like 'keyword%'
               qualify row_number() over (partition by post_id order by labelled_at desc) = 1) ai
           on ai.post_id = p.post_id
    left join raw.labels kw on kw.post_id = p.post_id and kw.model_version = 'keyword-v1'
    where p.source_config = 'ev_charging'
    order by p.created_at desc
""").df()
out = ROOT / "findings" / "labels_review.csv"
out.parent.mkdir(exist_ok=True)
df.to_csv(out, index=False, encoding="utf-8-sig")   # utf-8-sig so Excel shows ä, ö, ü correctly
print(f"wrote {out} ({len(df)} posts, {int((df.ai_model != 'no AI label').sum())} with an AI label)")
