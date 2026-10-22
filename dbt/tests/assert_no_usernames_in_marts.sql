-- Privacy check: no column that could hold a username or a raw author id may reach core or reporting tables.
select table_schema, table_name, column_name
from information_schema.columns
where table_schema in ('core', 'reporting')
  and lower(column_name) in ('username', 'user_name', 'screen_name', 'author_id', 'author_name', 'name')
