{#
  Generic test: the combination of the listed columns must be unique (the grain of the model).
  Same behaviour as dbt_utils.unique_combination_of_columns.
#}
{% test unique_combination_of_columns(model, combination_of_columns) %}
select {{ combination_of_columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}
