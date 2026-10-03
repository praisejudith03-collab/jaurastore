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


def test_upload_delete_fails_closed_when_live_product_references_cannot_be_read(
        monkeypatch, tmp_path):
    import catalog

    photo = tmp_path / "saved-photo.jpg"
    photo.write_bytes(b"the only saved copy")
    monkeypatch.setattr(Config, "UPLOAD_MODE", "local")
    monkeypatch.setattr(storage, "_key_from_url", lambda _url: "products/saved-photo.jpg")
    monkeypatch.setattr(storage, "resolve_local", lambda _key: str(photo))

    def unavailable(**_kwargs):
        raise RuntimeError("catalogue unavailable")

    monkeypatch.setattr(catalog, "merged", unavailable)
    assert storage.delete_upload("/uploads/products/saved-photo.jpg") is False
    assert photo.read_bytes() == b"the only saved copy"


def test_image_migration_refuses_other_buckets_before_client(monkeypatch):
    import migrate_images
    monkeypatch.setattr(migrate_images, '_client', lambda: pytest.fail('must not contact Supabase'))
    assert migrate_images.main(['--bucket', 'other']) == 2


# ------------------------------------------------- the media purge's reference
# Category covers are stored in the same public folders as product photos
# ("categories/..."), but they are NOT product rows. The purge used to scan
# products only, so a cover that only a category tile showed looked
# unreferenced and the only copy was deleted - the home page then fell back to
# the logo for good. These tests are the regression.
def _categories(monkeypatch, tmp_path, rows):
    """A real category table on disk, read through the same code production
    uses (catalog._read_categories_file -> CATEGORIES_PATH)."""
    import json

    path = tmp_path / "categories.json"
    path.write_text(json.dumps({"categories": rows}))
    monkeypatch.setenv("CATEGORIES_PATH", str(path))
    return path


def _stored_file(monkeypatch, tmp_path, name="cover.jpg"):
    """The one copy of an uploaded object, on the local backend."""
    file = tmp_path / name
    file.write_bytes(b"the only copy")
    monkeypatch.setattr(Config, "UPLOAD_MODE", "local")
    monkeypatch.setattr(storage, "resolve_local", lambda _key: str(file))
    return file


@pytest.mark.parametrize("stored", [
    "categories/bags-cover.jpg",                     # the bare bucket key
    "/uploads/categories/bags-cover.jpg",            # the local backend
    ORIGIN + "/storage/v1/object/public/uploads/categories/bags-cover.jpg?v=9",
])
def test_every_spelling_of_a_category_cover_keeps_its_file(monkeypatch, tmp_path, stored):
    monkeypatch.setattr(Config, "SUPABASE_URL", ORIGIN)
    cover = _stored_file(monkeypatch, tmp_path)
    _categories(monkeypatch, tmp_path, [{"id": "bags", "name": "Bags", "image": stored}])
    assert storage.delete_upload("/uploads/categories/bags-cover.jpg") is False
    assert cover.read_bytes() == b"the only copy"


@pytest.mark.parametrize("field", ["image", "image_url", "imageUrl"])
def test_the_cover_field_names_the_category_table_uses(monkeypatch, tmp_path, field):
    cover = _stored_file(monkeypatch, tmp_path)
    _categories(monkeypatch, tmp_path,
                [{"id": "bags", "name": "Bags", field: "categories/bags-cover.jpg"}])
    assert storage.delete_upload("/uploads/categories/bags-cover.jpg") is False
    assert cover.read_bytes() == b"the only copy"


def test_a_category_object_nobody_shows_is_still_purged(monkeypatch, tmp_path):
    """The fix must not turn the purge into a no-op: an object no row shows
    still goes, or the bucket fills with orphans."""
    gone = _stored_file(monkeypatch, tmp_path, "gone.jpg")
    _categories(monkeypatch, tmp_path,
                [{"id": "bags", "name": "Bags", "image": "categories/bags-cover.jpg"}])
    assert storage.delete_upload("/uploads/categories/gone.jpg") is True
    assert not gone.exists()


def test_an_unreadable_category_list_keeps_the_file(monkeypatch, tmp_path):
    """"I could not ask" is not "nobody shows it"."""
    cover = _stored_file(monkeypatch, tmp_path)
    monkeypatch.setattr(storage, "_live_category_rows", lambda: None)
    assert storage.delete_upload("/uploads/categories/bags-cover.jpg") is False
    assert cover.read_bytes() == b"the only copy"


def test_proofs_are_still_outside_the_purge_guard(monkeypatch, tmp_path):
    """Private receipts are never product photos or category covers, so the
    guard must not start protecting them from their own delete."""
    proof = _stored_file(monkeypatch, tmp_path, "receipt.png")
    _categories(monkeypatch, tmp_path, [{"id": "bags", "name": "Bags", "image": "categories/x.jpg"}])
    assert storage.delete_upload("/uploads/proofs/receipt.png") is True
    assert not proof.exists()


def test_a_foreign_project_url_is_not_our_object(monkeypatch, tmp_path):
    """The same path on somebody else's project must not keep a file here."""
    monkeypatch.setattr(Config, "SUPABASE_URL", ORIGIN)
    other = "https://other-project.supabase.co/storage/v1/object/public/uploads/categories/c.jpg"
    assert storage._key_from_url(other) == ""
    assert storage._reference_key(other) == ""
