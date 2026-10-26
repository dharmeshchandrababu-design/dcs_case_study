{#
  Generic test: a SQL expression must be true for every row, e.g. "authors <= posts".
  Same behaviour as dbt_utils.expression_is_true.
#}
{% test expression_is_true(model, expression) %}
select *
from {{ model }}
where not ({{ expression }})
{% endtest %}
