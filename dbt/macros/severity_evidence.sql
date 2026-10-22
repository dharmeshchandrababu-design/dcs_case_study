{#
  The AI severity label is only kept when the post text also contains matching evidence
  (two independent signals must agree). Reason: the small local model marked many posts
  about high prices as "financial", although nobody was charged wrongly.
  Patterns cover English and German.
#}
{% macro severity_checked(severity_col, text_col) -%}
case
    when {{ severity_col }} = 'financial' and regexp_matches({{ text_col }},
        '(?i)charged twice|double charged|doppelt (abgebucht|abgerechnet|berechnet)|zu viel (berechnet|abgebucht)|overcharg|refund|erstattung|zurückerstatt|geblockt|reserviert|vorautoris|pre-?auth|abgebucht|nicht erstattet|falsch abgerechnet|wrongly (charged|billed)|betrug|scam|[0-9]+ ?€ bezahlt|paid [0-9]+')
        then 'financial'
    when {{ severity_col }} = 'legal' and regexp_matches({{ text_col }},
        '(?i)afir|eichrecht|illegal|rechtswidrig|preisangabe|price display|gericht|urteil|court|ruling|bußgeld|kartell|dsgvo|gdpr')
        then 'legal'
    when {{ severity_col }} = 'safety' and regexp_matches({{ text_col }},
        '(?i)\bfire\b|caught fire|in brand|brandgefahr|feuer|funken|spark|stromschlag|electric shock|gefährlich|dangerous|unsafe|melted|geschmolzen')
        then 'safety'
    else 'none'
end
{%- endmacro %}
