"""Uploads-only storage contract and origin validation regressions."""
import ast
from pathlib import Path
import re
import pytest
import storage
import supabase_store as store
from config import Config

ORIGIN = 'https://test.supabase.co'


@pytest.mark.parametrize('suffix', [
    'https://evil.example/storage/v1/object/public/uploads/proofs/a.png',
    'https://test.supabase.co.evil.example/storage/v1/object/sign/uploads/proofs/a.png',
    'https://test.supabase.co@evil.example/storage/v1/object/public/uploads/proofs/a.png',
    'http://test.supabase.co/storage/v1/object/public/uploads/proofs/a.png',
    'https://test.supabase.co:999/storage/v1/object/public/uploads/proofs/a.png',
    '//evil.example/storage/v1/object/public/uploads/proofs/a.png',
    ORIGIN + '/storage/v1/object/public/receipts/proofs/a.png',
    ORIGIN + '/storage/v1/object/public/uploads/proofs/%2e%2e/a.png',
])
def test_foreign_or_unsafe_path_rejected(monkeypatch, suffix):
    monkeypatch.setattr(Config, 'SUPABASE_URL', ORIGIN)
    assert store._storage_path_from_url(suffix) == ''
    monkeypatch.setattr(store, 'client', lambda: pytest.fail('must not access storage'))
    assert store._delete_storage_object_from_url(suffix) is False


@pytest.mark.parametrize('kind', ['public', 'sign'])
def test_own_origin_paths(monkeypatch, kind):
    monkeypatch.setattr(Config, 'SUPABASE_URL', ORIGIN)
    url = f'{ORIGIN}/storage/v1/object/{kind}/uploads/proofs/a.png?token=abc'
    assert store._bucket_from_url(url) == 'uploads'
    assert store._storage_path_from_url(url) == 'proofs/a.png'
    assert store._storage_path_from_url(url, 'receipts') == ''


@pytest.mark.parametrize('url', [ORIGIN + '/storage/v1/object/sign/uploads/proofs/a.png', '/uploads/proofs/a.png'])
def test_missing_project_fails_closed(monkeypatch, url):
    monkeypatch.setattr(Config, 'SUPABASE_URL', '')
    assert store._bucket_from_url(url) == ''
    assert store._storage_path_from_url(url) == ''


def test_bucket_overrides_ignored(monkeypatch):
    monkeypatch.setenv('SUPABASE_PRIVATE_BUCKET', 'receipts')
    monkeypatch.setenv('SUPABASE_BUCKET', 'other')
    assert store._bucket() == 'uploads'


def test_proof_token_has_128_random_bits(monkeypatch):
    calls = []
    def token(n):
        calls.append(n)
        return 'a' * (2 * n)
    monkeypatch.setattr(storage.secrets, 'token_hex', token)
    path = storage._object_name('proofs', 'png', 'b' * 64)
    assert calls == [16]
    assert re.fullmatch(r'proofs/\d{4}/\d{2}/b{16}-a{32}\.png', path)


def test_no_production_private_bucket_setting():
    root = Path(__file__).resolve().parents[1]
    for file in root.glob('*.py'):
        tree = ast.parse(file.read_text())
        assert not any(isinstance(node, ast.Constant) and node.value == 'SUPABASE_PRIVATE_BUCKET'
                       for node in ast.walk(tree)), file.name


def test_obsolete_receipt_migration_is_disabled():
    import migrate_supabase
    with pytest.raises(SystemExit, match='disabled'):
        migrate_supabase._migrate_receipts(None)


@pytest.mark.parametrize('url', [
    'https://evil.example/storage/v1/object/public/uploads/proofs/a.png',
    'https://evil.example/uploads/proofs/a.png',
    'https://[invalid/storage/v1/object/public/uploads/proofs/a.png',
])
def test_foreign_url_never_deletes_local_file(monkeypatch, url):
    monkeypatch.setattr(Config, 'SUPABASE_URL', ORIGIN)
    monkeypatch.setattr(storage, 'resolve_local', lambda key: pytest.fail('must not resolve local file'))
    assert storage.delete_upload(url) is False


def test_no_project_key_parser_fails_closed(monkeypatch):
    monkeypatch.setattr(Config, 'SUPABASE_URL', '')
    assert storage._key_from_url(ORIGIN + '/storage/v1/object/public/uploads/proofs/a.png') == ''


def test_image_migration_refuses_other_buckets_before_client(monkeypatch):
    import migrate_images
    monkeypatch.setattr(migrate_images, '_client', lambda: pytest.fail('must not contact Supabase'))
    assert migrate_images.main(['--bucket', 'other']) == 2
