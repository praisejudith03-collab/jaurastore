"""Generated SQL is an exact, ordered partition of the canonical schema."""
from pathlib import Path
import re
import pytest
from tools import split_schema

ROOT = Path(__file__).resolve().parents[1]


def test_sections_round_trip():
    source = (ROOT / 'supabase_schema.sql').read_text()
    generated = split_schema.sections(source)
    assert len(generated) == 17
    assert ''.join(generated.values()) == source
    assert split_schema.main(['--check']) == 0


def test_check_reports_drift_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(split_schema, 'ROOT', tmp_path)
    (tmp_path / 'supabase_schema.sql').write_text((ROOT / 'supabase_schema.sql').read_text())
    assert split_schema.main(['--check']) == 1
    assert not (tmp_path / 'schema_sections').exists()
    assert split_schema.main([]) == 0
    file = tmp_path / 'schema_sections/01_products.sql'
    file.write_text('drift')
    assert split_schema.main(['--check']) == 1
    assert file.read_text() == 'drift'


def test_check_rejects_extra_sql(tmp_path, monkeypatch):
    monkeypatch.setattr(split_schema, 'ROOT', tmp_path)
    (tmp_path / 'supabase_schema.sql').write_text((ROOT / 'supabase_schema.sql').read_text())
    split_schema.main([])
    (tmp_path / 'schema_sections/99_extra.sql').write_text('select 1;')
    assert split_schema.main(['--check']) == 1


def test_missing_boundary_fails():
    with pytest.raises(ValueError):
        split_schema.sections('select 1;')


def test_single_bucket_and_receipts_table():
    source = (ROOT / 'supabase_schema.sql').read_text()
    inserts = re.findall(r'insert into storage\.buckets.*?;', source, re.S | re.I)
    assert len(inserts) == 1
    assert "values ('uploads', 'uploads', true)" in inserts[0]
    assert 'create table if not exists receipts (' in source
    assert "bucket_id = 'receipts'" not in source


def test_generated_bytes_and_mobile_size():
    files = sorted((ROOT / 'schema_sections').glob('*.sql'))
    assert b''.join(p.read_bytes() for p in files) == (ROOT / 'supabase_schema.sql').read_bytes()
    assert all(p.stat().st_size < 6000 for p in files)


def test_crlf_bytes_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(split_schema, 'ROOT', tmp_path)
    source = b'-- preamble\r\n-- SECTION: first\r\nselect 1;\r\n-- SECTION: second\r\nselect 2;\r\n'
    (tmp_path / 'supabase_schema.sql').write_bytes(source)
    assert split_schema.main([]) == 0
    assert b''.join(p.read_bytes() for p in sorted((tmp_path / 'schema_sections').glob('*.sql'))) == source
    assert split_schema.main(['--check']) == 0


def test_required_tables_and_no_destructive_statements():
    source = (ROOT / 'supabase_schema.sql').read_text()
    code = re.sub(r'--[^\n]*', '', source).lower()
    assert not re.search(r'\b(drop|truncate|delete)\b', code)
    for table in ('products', 'receipts', 'orders', 'product_reviews', 'coupon_uses'):
        assert f'create table if not exists {table} (' in code
    review = code.split('create table if not exists product_reviews (', 1)[1].split(');', 1)[0]
    for field in ('rating', 'title', 'body', 'created_at', 'updated_at'):
        assert re.search(r'\b' + field + r'\b', review)
