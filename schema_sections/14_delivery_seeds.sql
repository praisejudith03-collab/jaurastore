-- SECTION: delivery_seeds
-- Seed the zones the storefront has always shown. on conflict do nothing, so
-- re-running the schema never overwrites an admin's edited fares.
insert into delivery_zones (id, name, currency, fare_min, fare_max, kind, sort_order)
values
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
on conflict (id) do nothing;

