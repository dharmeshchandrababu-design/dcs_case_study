{# Use the schema names from dbt_project.yml as they are (staging, core, reporting). #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
