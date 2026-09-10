"""Category API persistence through fresh apps and legacy Supabase columns."""
import json
import threading
import pytest
import api
import app as appmod
import db
from config import Config
import supabase_store as sb
from test_categories_resilient import _LegacyCatTable, _FakeResult
from _pw import PW


class DurableClient:
    def __init__(self):
        self.rows = {}
        self.metadata = {}

    def table(self, name):
        if name == "categories":
            parent = self
            class Table(_LegacyCatTable):
                def select(self, *a):
                    self.reading = True
                    return self
                def order(self, *a):
                    return self
                def range(self, start, end):
                    self.bounds = (start, end)
                    return self
                def execute(self):
                    if getattr(self, 'reading', False):
                        rows = sorted(parent.rows.values(), key=lambda r: r['id'])
                        a, b = self.bounds
                        return _FakeResult(rows[a:b+1])
                    return super().execute()
            return Table(self.rows, missing=("name_fr", "image_url", "hidden", "updated_at"))
        assert name == "growth_settings"
        parent = self
        class Metadata:
            def upsert(self, rows):
                self.rows = rows
                return self
            def select(self, *a):
                return self
            def eq(self, column, value):
                self.key = value
                return self
            def limit(self, *a):
                return self
            def execute(self):
                if hasattr(self, 'rows'):
                    for row in self.rows:
                        parent.metadata[row['key']] = row['value']
                    return _FakeResult(self.rows)
                value = parent.metadata.get(self.key)
                return _FakeResult([{'value': value}] if value else [])
        return Metadata()


@pytest.fixture()
def durable(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(db, "_local", threading.local())
    monkeypatch.setenv("ADMIN_MASTER_PASSWORD", PW)
    monkeypatch.setattr(api, "CATEGORIES_FILE", str(tmp_path / "ephemeral.json"))
    remote = DurableClient()
    monkeypatch.setattr(sb, "client", lambda: remote)
    monkeypatch.setattr(sb, "enabled", lambda: True)
    monkeypatch.setattr(sb, "_CATS_SHAPE", {"fill": {}, "drop": [], "values": {}})
    return remote


def test_many_categories_french_and_order_survive_redeploy(durable, monkeypatch):
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as admin:
        login = admin.post('/api/admin/login', json={'email':'jaurastore@gmail.com', 'password':PW})
        assert login.status_code == 200
        headers = {'X-CSRF-Token':login.json['csrf']}
        monkeypatch.setattr(Config, 'ENV', 'production')
        cats = [{'id':f'cat-{i}', 'name':f'Category {i}', 'nameFr':f'Catégorie {i}', 'order':i+2} for i in range(1100)]
        cats += [{'id':'household', 'name':'Household & Kitchen', 'nameFr':'Maison & cuisine', 'order':0},
                 {'id':'perfume', 'name':'Perfume', 'nameFr':'Parfum', 'order':1, 'image':'images/brand/logo.jpg'}]
        saved = admin.put('/api/admin/categories', headers=headers, json={'categories':cats})
        assert saved.status_code == 200, saved.json
        assert saved.json['count'] == 1102
        assert len(durable.rows) == 1102
        # Prove this exercises the legacy schema: French names were dropped
        # from the table but are durably retained in growth_settings.
        assert 'name_fr' not in durable.rows['perfume']
        assert 'Parfum' in durable.metadata[sb.CATEGORIES_KEY]
    monkeypatch.setattr(sb, '_CATS_SHAPE', {'fill':{}, 'drop':[], 'values':{}})
    # No local file exists after redeploy; fresh app/client reads only remote data.
    fresh = appmod.create_app()
    with fresh.test_client() as shopper:
        rows = shopper.get('/api/categories').json['categories']
        assert len(rows) == 1102
        assert [c['id'] for c in rows[:2]] == ['household', 'perfume']
        assert rows[1]['nameFr'] == 'Parfum'
        assert rows[1]['image'] == 'images/brand/logo.jpg'
    # Reordering is persisted, not a hard-coded household override.
    cats[-1]['order'] = 0
    cats[-2]['order'] = 1
    api._save_categories(cats)
    with appmod.create_app().test_client() as shopper:
        assert shopper.get('/api/categories').json['categories'][0]['id'] == 'perfume'


def test_metadata_failure_is_not_acknowledged_as_a_saved_order(durable, monkeypatch):
    monkeypatch.setattr(Config, 'ENV', 'production')
    monkeypatch.setattr(sb, 'save_categories', lambda cats: False)
    with pytest.raises(RuntimeError):
        api._save_categories([{'id':'perfume','name':'Perfume','nameFr':'Parfum','order':0}])


def test_owner_adds_category_and_shoppers_see_it_immediately(durable, monkeypatch):
    """The owner's story: add a brand-new category (English + French name) in
    Admin -> Categories, and the public website shows it on the next request -
    with no redeploy, no cache and no second save."""
    monkeypatch.setattr(Config, 'ENV', 'production')
    app = appmod.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as admin:
        login = admin.post('/api/admin/login', json={'email':'jaurastore@gmail.com', 'password':PW})
        assert login.status_code == 200
        headers = {'X-CSRF-Token':login.json['csrf']}
        current = admin.get('/api/categories').json['categories']
        assert not any(c['id'] == 'jewellery' for c in current)
        # The admin panel's "Add a category" flow saves the WHOLE table with
        # the new row appended (js/store.js saveCategories -> PUT below).
        nxt = [{'id':c['id'], 'name':c['name'], 'nameFr':c.get('nameFr',''),
                'image':c.get('image',''), 'hidden':bool(c.get('hidden')),
                'order':i} for i, c in enumerate(current)]
        nxt.append({'id':'jewellery', 'name':'Jewellery', 'nameFr':'Bijoux'})
        saved = admin.put('/api/admin/categories', headers=headers, json={'categories':nxt})
        assert saved.status_code == 200, saved.json
    # A shopper on another device sees it at once - fresh app, no local state.
    monkeypatch.setattr(sb, '_CATS_SHAPE', {'fill':{}, 'drop':[], 'values':{}})
    with appmod.create_app().test_client() as shopper:
        rows = shopper.get('/api/categories').json['categories']
        row = next(c for c in rows if c['id'] == 'jewellery')
        assert row['name'] == 'Jewellery'
        assert row['nameFr'] == 'Bijoux'


def test_initial_household_first_without_overriding_owner_order():
    rows = [{'id':'beauty','name':'Beauty'}, {'id':'household','name':'Household items'}]
    initial = api._ordered_categories(rows)
    assert initial[0]['id'] == 'household'
    assert initial[0]['name'] == 'Household & Kitchen'
    rows[0]['order'] = 0
    rows[1]['order'] = 1
    assert api._ordered_categories(rows)[0]['id'] == 'beauty'
