-- SECTION: delivery_seeds
-- Seed missing zones only; never overwrite Admin-edited rows. Section 11
-- supplies the current columns without changing legacy data. Some existing
-- tables ALSO require zone_name: populate it with the seed name on INSERT,
-- but do not add it on fresh tables or backfill/rename existing names.
do $$
declare
  has_zone_name boolean;
begin
  -- Inspect the same relation resolved by the INSERT (including search_path).
  select exists (
    select 1 from pg_catalog.pg_attribute
    where attrelid = 'delivery_zones'::regclass
      and attname = 'zone_name' and attnum > 0 and not attisdropped
  ) into has_zone_name;

  -- Dynamic SQL never references the absent legacy column on a fresh table.
  -- Both format arguments are fixed SQL fragments, not user-supplied values.
  -- No conflict target: also skip unique name/zone_name collisions where an
  -- Admin has retained a default name under a different ID.
  execute format($seed$
insert into delivery_zones (id, name, currency, fare_min, fare_max, kind, sort_order%s)
select id, name, currency, fare_min, fare_max, kind, sort_order%s
from (values
  ('lagos-mainland', 'Lagos Mainland', 'NGN', 2000, 5000, 'delivery', 1),
  ('lagos-island',   'Lagos Island',   'NGN', 3500, 6000, 'delivery', 2),
  ('ng-other',       'Other Nigeria',  'NGN',    0,    0, 'quote',    3),
  ('cotonou',        'Cotonou',        'CFA', 1000, 3000, 'delivery', 4),
  ('calavi',         'Calavi',         'CFA', 1500, 3500, 'delivery', 5),
  ('porto-novo',     'Porto-Novo',     'CFA', 1500, 3500, 'delivery', 6),
  ('bj-other',       'Other Benin',    'CFA',    0,    0, 'quote',    7),
  ('lome',           'Lomé',           'CFA', 2500, 3500, 'delivery', 8),
  ('tg-other',       'Other Togo',     'CFA',    0,    0, 'quote',    9),
  ('pickup-cotonou', 'Pickup in Cotonou is free for lighter products',
                     'CFA',    0,    0, 'pickup',   10)
) as seeds(id, name, currency, fare_min, fare_max, kind, sort_order)
on conflict do nothing;
$seed$,
    case when has_zone_name then ', zone_name' else '' end,
    case when has_zone_name then ', name' else '' end
  );
end $$;

