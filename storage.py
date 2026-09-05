"""Where uploads live: payment proofs, admin-uploaded product photos, product
videos, category assets and the homepage hero.

Three back ends, chosen with UPLOAD_MODE in .env:

  local (default) - written under data/uploads/ and served by the Flask app at
                    /uploads/...  Works with zero configuration. On a host with
                    an ephemeral filesystem (Render free tier) attach a
                    persistent disk at data/ or switch to s3 / supabase.

  s3             - any S3-compatible bucket (Cloudflare R2, AWS S3, MinIO).
                    Set S3_BUCKET / S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY
                    / S3_PUBLIC_BASE. No code change is needed to switch.

  supabase       - the project's Supabase Storage bucket (default name:
                    uploads, override with SUPABASE_BUCKET). Needs
                    SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, the same pair
                    the rest of the app uses. Public objects come back as
                    …/storage/v1/object/public/<bucket>/<key>. Payment proofs
                    and receipts (the `proofs` folder) come back as SIGNED
                    URLs instead, so they stay admin-only and are never
                    publicly guessable - see signed_url_for(), which refreshes
                    a signed URL when an admin opens the receipts view.
                    /uploads/... keeps serving files written to the local
                    disk (old files, and the fallback below).

When the configured Supabase bucket (or S3 bucket) is unreachable the write
falls back to the local disk, so a customer's proof of payment is never lost.

Every file is re-identified from its own bytes (magic number), never from the
filename a browser sends. That means a renamed .exe can never be stored and
later served as an image or a document, and an .html/.svg (which would execute
script in the site's origin) is refused outright.
"""
import os, io, hashlib, secrets, datetime, time
from config import Config
from security import clean

ROOT = os.path.dirname(os.path.abspath(__file__))

MAX_BYTES = 6 * 1024 * 1024              # 6 MB: product photos
MAX_RECEIPT_BYTES = 8 * 1024 * 1024      # 8 MB: payment receipts / PDFs / docs
MAX_VIDEO_BYTES = 40 * 1024 * 1024       # 40 MB: product + homepage hero video

# Folders whose contents are PRIVATE (customer payment evidence: receipts and
# payment proofs). In Supabase mode, objects stored under these folders are
# handed out as SIGNED URLs - short-lived links only the server can mint - so
# a receipt is admin-only and never publicly guessable. Everything else
# (products, categories, videos, misc = hero/banner/logo) is a public URL.
SENSITIVE_FOLDERS = ("proofs",)

# Supabase Storage caps signed-URL expiry at 7 days. Use the maximum: the URL
# handed out at upload time is what the database stores, and signed_url_for()
# refreshes it while an admin has the receipts view open.
SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60
# How long a refreshed signed URL is reused from cache before a new one is
# requested (keeps the admin's receipts table from minting a URL per row per
# page load).
SIGNED_URL_REFRESH_SECONDS = 60 * 60

IMAGE_EXT = ("jpg", "jpeg", "png", "webp", "gif", "avif", "heic")
DOC_EXT = ("pdf", "doc", "docx")
VIDEO_EXT = ("mp4", "webm", "mov")
ALLOWED_EXT = IMAGE_EXT + DOC_EXT + VIDEO_EXT

MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    "avif": "image/avif", "heic": "image/heic",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
}

# (extension, tuple of accepted magic-byte prefixes)
_SIGNATURES = (
    ("jpg", (b"\xff\xd8\xff",)),
    ("png", (b"\x89PNG\r\n\x1a\n",)),
    ("webp", (b"RIFF",)),
    ("gif", (b"GIF87a", b"GIF89a")),
    ("pdf", (b"%PDF-",)),
    # OLE2 compound file -> legacy Word .doc
    ("doc", (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)),
    # OOXML (zip) container -> .docx (magic bytes cannot distinguish docx from
    # xlsx/pptx; the zip container is what matters for safety).
    ("docx", (b"PK\x03\x04",)),
    # ISO base media (MP4 / MOV / AVIF / HEIC) handled by _iso_brand below.
    ("mp4", ()),
    ("webm", (b"\x1a\x45\xdf\xa3",)),
)


def _iso_brand(head: bytes) -> bytes:
    """The ISO-BMFF brand (the 4 bytes after 'ftyp'), or b'' when not BMFF."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return head[8:12]
    return b""


def _ext_from_bytes(head: bytes) -> str:
    """Identify the real file type from its first bytes, never its name.

    Returns one of ALLOWED_EXT, or '' when the bytes do not match anything on
    the allowlist (a renamed .exe, an .html/.svg, a truncated file, ...).
    """
    for ext, sigs in _SIGNATURES:
        for sig in sigs:
            if not sig:
                continue
            if head.startswith(sig):
                if ext == "webp" and head[8:12] != b"WEBP":
                    continue
                return ext
    brand = _iso_brand(head)
    if brand:
        # HEIC / HEIF / AVIF are ISO-BMFF image containers. Check them before
        # the generic video fallback so they are not mis-typed as mp4.
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            return "heic"
        if brand in (b"qt  ", b"qt  "[:4]):
            return "mov"
        # Most MP4 brands. Any unrecognised ftyp container plays as mp4.
        return "mp4"
    return ""


def _ext_from_name(name: str) -> str:
    tail = (name or "").rsplit(".", 1)[-1].lower()
    return tail if tail in ALLOWED_EXT else ""


def mime_for(ext: str) -> str:
    return MIME.get((ext or "").lower(), "application/octet-stream")


def kind_for(ext: str) -> str:
    """High level category for a stored extension: image / video / document."""
    ext = (ext or "").lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in DOC_EXT:
        return "document"
    return "unknown"


def is_inline_renderable(ext: str) -> bool:
    """Only images and videos can be shown inline in an <img>/<video>."""
    return kind_for(ext) in ("image", "video")


def is_document(ext: str) -> bool:
    return kind_for(ext) == "document"


def validate_upload(data: bytes, filename: str = "", allow_pdf: bool = False,
                    max_bytes: int = MAX_BYTES, allow_documents: bool = None,
                    kind: str = "media"):
    """Returns (ok, message, extension).

    ``kind`` selects what the slot is allowed to hold:
      * "media" (default) - images; PDF/DOC/DOCX only when allow_documents /
        allow_pdf is True (customer receipts, product assets).
      * "image"           - images only (no documents at all).
      * "video"           - videos only.
      * "asset"           - the broad allowlist (images, videos, documents) for
                            category / hero assets.

    The extension is always read from the file's own bytes.
    """
    if not data:
        return False, "The file was empty.", ""
    if len(data) > max_bytes:
        return False, (f"That file is {len(data) / 1048576:.1f} MB. "
                       f"The limit is {max_bytes // (1024 * 1024)} MB."), ""
    ext = _ext_from_bytes(data[:32])
    k = kind_for(ext)

    if not ext:
        return False, "Only JPG, PNG, WebP, GIF, AVIF, HEIC, PDF, DOC, DOCX, MP4, WebM or MOV files can be uploaded.", ""

    # The slot's own rules.
    if kind == "image" and k != "image":
        return False, "That is a %s. Upload a JPG, PNG, WebP or GIF image here." % (_type_label(ext)), ext
    if kind == "video" and k != "video":
        return False, "That is a %s. Upload an MP4 or WebM video here." % (_type_label(ext)), ext

    # Documents need to be explicitly allowed (receipts, category/hero assets).
    if k == "document":
        if not (allow_documents or allow_pdf):
            if kind == "image":
                return False, "That is a %s. Upload a JPG or PNG image here." % (_type_label(ext)), ext
            return False, "That is a %s. Upload an image here." % (_type_label(ext)), ext
        if ext == "pdf":
            if b"\x00" in data[:1024]:
                return False, "That PDF could not be read.", ""
            if data.rstrip()[-6:] != b"%%EOF" and b"%%EOF" not in data[-2048:]:
                return False, "That PDF looks incomplete. Save it again and retry.", ""
        elif ext in ("doc", "docx"):
            # a bare, truncated or empty office file is usually garbage
            if len(data) < 24:
                return False, "That document looks incomplete. Save it again and retry.", ""
        return True, "ok", ext

    if k == "image":
        # WebP already checked for the WEBP brand; ensure the RIFF header is
        # consistent enough to not be a false positive.
        if ext == "webp" and len(data) < 12:
            return False, "That WebP looks incomplete. Try another file.", ""
    return True, "ok", ext


def _type_label(ext: str) -> str:
    return {"pdf": "PDF", "doc": "Word document", "docx": "Word document",
            "mp4": "video", "webm": "video", "mov": "video",
            "gif": "GIF", "avif": "AVIF", "heic": "HEIC"}.get((ext or "").lower(), "file")


def validate_image(data: bytes, filename: str = ""):
    """Product photos: images only."""
    ok, msg, ext = validate_upload(data, filename, allow_pdf=False,
                                   max_bytes=MAX_BYTES, kind="image")
    return ok, msg, ext


def validate_asset(data: bytes, filename: str = "", max_bytes: int = MAX_BYTES):
    """Category / hero assets: the broad allowlist, documents included."""
    return validate_upload(data, filename, allow_documents=True,
                           max_bytes=max_bytes, kind="asset")


def validate_video(data: bytes, filename: str = ""):
    """Hero / product video: video formats only, capped at MAX_VIDEO_BYTES."""
    if not data:
        return False, "The file was empty.", ""
    if len(data) > MAX_VIDEO_BYTES:
        return False, (f"That video is {len(data) / 1048576:.1f} MB. "
                       f"The limit is {MAX_VIDEO_BYTES // (1024 * 1024)} MB — "
                       "export it smaller (720p is plenty for a hero)."), ""
    ext = _ext_from_bytes(data[:32])
    if kind_for(ext) != "video":
        return False, "Only MP4, WebM or MOV videos can be uploaded here.", ""
    return True, "ok", ext


def _folder_name(folder: str) -> str:
    """The safe first path segment for a folder (same rules as _object_name)."""
    return "".join(c for c in (folder or "misc").lower() if c.isalnum() or c in "-_")[:24] or "misc"


def _is_sensitive(folder: str) -> bool:
    """True when the folder holds private material (payment proofs/receipts)."""
    return _folder_name(folder) in SENSITIVE_FOLDERS


def _save(data: bytes, folder: str, ext: str, s3_content_type: str = "") -> tuple:
    """Shared write path (Supabase, S3 or local), returns (ok, message, url)."""
    digest = hashlib.sha256(data).hexdigest()
    key = _object_name(folder, ext, digest)
    content_type = s3_content_type or mime_for(ext)

    if Config.UPLOAD_MODE == "supabase":
        ok2, _msg2, url = _save_supabase(data, key, ext, content_type, folder)
        if ok2:
            # a sensitive folder keeps its short-lived signed URL; a public
            # asset is announced as /uploads/<key> so the product row never
            # points a shopper's browser at another host
            return True, "stored", (url if _is_sensitive(folder) else "/uploads/" + key)
        # never silently lose a customer's proof of payment: fall back to disk

    if Config.UPLOAD_MODE == "s3":
        ok2, _msg2, url = _save_s3(data, key, ext, content_type)
        if ok2:
            return True, "stored", url
        # never silently lose a customer's proof of payment: fall back to disk

    full = _local_path(key)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, full)
    except OSError as exc:  # pragma: no cover - filesystem failure
        return False, f"Could not save the upload ({exc.__class__.__name__}).", ""
    return True, "stored", "/uploads/" + key


def save_image(data: bytes, folder: str = "misc", filename: str = "", allow_pdf: bool = False,
               max_bytes: int = MAX_BYTES):
    """Stores an image (or, when allowed, a document) and returns
    (ok, message, url)."""
    ok, msg, ext = validate_upload(data, filename, allow_pdf=allow_pdf,
                                   max_bytes=max_bytes, kind="media")
    if not ok:
        return False, msg, ""
    return _save(data, folder, ext)


def save_asset(data: bytes, folder: str = "misc", filename: str = "", max_bytes: int = MAX_BYTES):
    """Stores a broad-allowlist asset (image / video / document) and returns
    (ok, message, url)."""
    ok, msg, ext = validate_asset(data, filename, max_bytes=max_bytes)
    if not ok:
        return False, msg, ""
    return _save(data, folder, ext)


def save_video(data: bytes, folder: str = "videos", filename: str = ""):
    """Stores a video and returns (ok, message, url)."""
    ok, msg, ext = validate_video(data, filename)
    if not ok:
        return False, msg, ""
    return _save(data, folder, ext)


def _object_name(folder: str, ext: str, digest: str) -> str:
    now = datetime.datetime.utcnow()
    return f"{_folder_name(folder)}/{now:%Y/%m}/{digest[:16]}-{secrets.token_hex(4)}.{ext}"


def _local_path(key: str) -> str:
    base = Config.UPLOAD_DIR if os.path.isabs(Config.UPLOAD_DIR) else os.path.join(ROOT, Config.UPLOAD_DIR)
    return os.path.join(base, key.replace("/", os.sep))


def _save_s3(data: bytes, key: str, ext: str, content_type: str = ""):
    """S3 / R2 upload. Requires boto3 plus credentials in .env."""
    if not (Config.S3_BUCKET and Config.S3_ACCESS_KEY and Config.S3_SECRET_KEY):
        return False, "s3 not configured", ""
    try:
        import boto3  # imported lazily so local installs stay dependency-free
        from botocore.config import Config as BotoConfig
    except ImportError:
        return False, "boto3 is not installed", ""

    try:
        kwargs = dict(
            region_name=Config.S3_REGION or "auto",
            aws_access_key_id=Config.S3_ACCESS_KEY,
            aws_secret_access_key=Config.S3_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
        if Config.S3_ENDPOINT:
            kwargs["endpoint_url"] = Config.S3_ENDPOINT
        client = boto3.client("s3", **kwargs)
        client.put_object(
            Bucket=Config.S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
            CacheControl="public, max-age=31536000",
        )
        base = (Config.S3_PUBLIC_BASE or "").rstrip("/")
        if not base:
            if Config.S3_ENDPOINT:
                base = f"{Config.S3_ENDPOINT.rstrip('/')}/{Config.S3_BUCKET}"
            else:
                base = f"https://{Config.S3_BUCKET}.s3.{Config.S3_REGION}.amazonaws.com"
        return True, "stored", f"{base}/{key}"
    except Exception as exc:  # pragma: no cover - network/credentials
        return False, f"s3 upload failed ({exc.__class__.__name__})", ""


def supabase_public_url(key: str) -> str:
    """The public URL for an object in the Supabase uploads bucket.

    <SUPABASE_URL>/storage/v1/object/public/<bucket>/<key> - the shape the
    browser loads for product / category / banner / logo images.
    """
    base = (Config.SUPABASE_URL or "").rstrip("/")
    if not base:
        return ""
    import supabase_store
    return f"{base}/storage/v1/object/public/{supabase_store._bucket()}/{key}"


def _save_supabase(data: bytes, key: str, ext: str, content_type: str,
                   folder: str = "") -> tuple:
    """Upload to Supabase Storage. Requires SUPABASE_URL + the service-role
    key (the same pair the rest of the app authenticates with).

    Public folders (products, categories, videos, misc) return a public URL
    so the image loads in the browser with zero server round-trips. Sensitive
    folders (proofs = payment receipts / proofs) return a SIGNED URL instead:
    the object is still in the public bucket, but the only link anyone ever
    sees is a short-lived one the server minted, so a receipt is admin-only
    and never publicly guessable.
    """
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        return False, "supabase not configured", ""
    try:
        import supabase_store
    except ImportError:
        return False, "supabase store unavailable", ""
    c = supabase_store.client()
    if c is None:
        return False, "supabase client unavailable", ""
    bucket = supabase_store._bucket()
    try:
        c.storage.from_(bucket).upload(
            key, data, {"content-type": content_type or "application/octet-stream"})
    except Exception as exc:
        return False, f"supabase upload failed ({exc.__class__.__name__})", ""
    if _is_sensitive(folder):
        try:
            res = c.storage.from_(bucket).create_signed_url(key, SIGNED_URL_TTL_SECONDS)
        except Exception:
            return False, "could not sign the receipt url", ""
        url = res.get("signedUrl") if isinstance(res, dict) else getattr(res, "signedUrl", "")
        if not url:
            return False, "could not sign the receipt url", ""
        return True, "stored", url
    return True, "stored", supabase_public_url(key)


# path -> (signed url, epoch when the cache entry expires)
_signed_url_cache = {}


def _signed_cache_get(path: str) -> str:
    item = _signed_url_cache.get(path)
    if item and item[1] > time.time():
        return item[0]
    return ""


def _signed_cache_put(path: str, url: str) -> None:
    if len(_signed_url_cache) > 1024:
        _signed_url_cache.clear()
    _signed_url_cache[path] = (url, time.time() + SIGNED_URL_REFRESH_SECONDS)


def signed_url_for(value: str) -> str:
    """A fresh signed URL for a proof / receipt stored in Supabase Storage.

    Signed URLs expire (Supabase caps them at 7 days), and the URL saved in
    the database at upload time is exactly such a URL. When an admin opens
    the receipts view we refresh those URLs here so a receipt stays viewable
    for as long as the file exists - not just for 7 days after upload.

    Only touches URLs we produced: a Supabase storage URL whose path starts
    with a sensitive folder (proofs/). Product photos, category assets,
    local /uploads/ links and foreign URLs are returned unchanged. Never
    raises - a Supabase hiccup must never break the receipts list.
    """
    raw = (value or "").strip()
    if not raw or "/storage/v1/object/" not in raw:
        return value or ""
    try:
        import supabase_store
        if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
            return value
        # only URLs from our own project - never mint a link for a foreign
        # host that happens to look like a Supabase storage URL
        if not raw.startswith(Config.SUPABASE_URL.rstrip("/") + "/storage/"):
            return value
        bucket = supabase_store._bucket()
        path = supabase_store._storage_path_from_url(raw, bucket)
        if not path:
            return value
        if path.lstrip("/").split("/", 1)[0].lower() not in SENSITIVE_FOLDERS:
            return value                      # public asset; nothing to sign
        cached = _signed_cache_get(path)
        if cached:
            return cached
        c = supabase_store.client()
        if c is None:
            return value
        res = c.storage.from_(bucket).create_signed_url(path, SIGNED_URL_TTL_SECONDS)
        url = res.get("signedUrl") if isinstance(res, dict) else getattr(res, "signedUrl", "")
        if not url:
            return value
        _signed_cache_put(path, url)
        return url
    except Exception:
        return value


def local_root() -> str:
    base = Config.UPLOAD_DIR if os.path.isabs(Config.UPLOAD_DIR) else os.path.join(ROOT, Config.UPLOAD_DIR)
    return os.path.abspath(base)


def resolve_local(key: str):
    """Absolute path for a key, or None if it escapes the upload root."""
    root = local_root()
    full = os.path.abspath(os.path.join(root, (key or "").lstrip("/")))
    if not (full == root or full.startswith(root + os.sep)):
        return None
    return full if os.path.isfile(full) else None


def public_url(value: str) -> str:
    """Only ever returns http(s), /uploads/... or a plain relative asset path."""
    raw = clean(value, 500)
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "/")):
        return raw
    return "/uploads/" + raw.lstrip("/")


def _key_from_url(value: str) -> str:
    """The storage key for a URL we produced, or "" when it is not ours.

    Accepts the shapes we hand out: "/uploads/<key>" from the local backend,
    "<S3_PUBLIC_BASE>/<key>" from the S3 backend, and Supabase public /
    signed URLs ("<…>/storage/v1/object/<public|sign>/<bucket>/<key>").
    """
    raw = clean(value, 500)
    if not raw:
        return ""
    if raw.startswith("/uploads/"):
        return raw[len("/uploads/"):].lstrip("/")
    if "/storage/v1/object/" in raw:
        # …/object/public/<bucket>/<key>  or  …/object/sign/<bucket>/<key>?token=…
        parts = raw.split("/storage/v1/object/", 1)[1].split("/", 2)
        if len(parts) == 3:
            return parts[2].split("?", 1)[0].lstrip("/")
        return ""
    for base in (Config.S3_PUBLIC_BASE, Config.S3_ENDPOINT):
        base = (base or "").rstrip("/")
        if base and raw.startswith(base + "/"):
            key = raw[len(base) + 1:]
            if Config.S3_BUCKET and key.startswith(Config.S3_BUCKET + "/"):
                key = key[len(Config.S3_BUCKET) + 1:]
            return key.lstrip("/")
    if "/uploads/" in raw:
        return raw.split("/uploads/", 1)[1].lstrip("/")
    return ""


def own_upload_path(value: str) -> str:
    """The same-origin /uploads/<key> for a URL that points at OUR storage.

    Product rows may only carry same-origin image links (see
    catalog.resolve_image and the external-host test), but the Supabase back
    end handed out absolute bucket URLs at upload time - and those were then
    blanked to the branded placeholder, so a photo uploaded from a phone never
    appeared on the shop. Rewriting our own bucket URL to /uploads/<key> at
    read time heals every stored row with no migration: this app's /uploads/
    route knows how to reach the object. A URL from any other host returns ""
    and stays stripped.
    """
    raw = clean(value, 500)
    if not raw:
        return ""
    if raw.startswith("/uploads/"):
        return raw
    key = _key_from_url(raw)
    if not key:
        return ""
    if "/storage/v1/object/" in raw:
        base = (Config.SUPABASE_URL or "").rstrip("/")
        if not base or not raw.startswith(base + "/"):
            return ""             # somebody else's Supabase project
    return "/uploads/" + key


def public_redirect_for(key: str) -> str:
    """A public bucket URL for an /uploads/<key> this server cannot serve.

    Only for non-sensitive folders: a payment proof must never be reachable
    through a public object link, so it stays a 404 unless the file is on disk.
    """
    if Config.UPLOAD_MODE != "supabase":
        return ""
    if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
        return ""
    if _folder_name((key or "").split("/", 1)[0]) in SENSITIVE_FOLDERS:
        return ""
    return supabase_public_url(key)


def _delete_s3(key: str) -> bool:
    if not (Config.S3_BUCKET and Config.S3_ACCESS_KEY and Config.S3_SECRET_KEY):
        return False
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        return False
    try:
        kwargs = dict(
            region_name=Config.S3_REGION or "auto",
            aws_access_key_id=Config.S3_ACCESS_KEY,
            aws_secret_access_key=Config.S3_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )
        if Config.S3_ENDPOINT:
            kwargs["endpoint_url"] = Config.S3_ENDPOINT
        boto3.client("s3", **kwargs).delete_object(Bucket=Config.S3_BUCKET, Key=key)
        return True
    except Exception:  # pragma: no cover - network/credentials
        return False


def delete_upload(value: str) -> bool:
    """Remove a file we stored earlier. True when something was deleted.

    Used when an admin deletes a payment receipt: the row AND the uploaded
    file have to go, so a deleted receipt is not still downloadable from its
    URL. Never raises - a missing file is simply "nothing to delete".
    """
    key = _key_from_url(value)
    if not key or ".." in key:
        return False
    removed = False
    if Config.UPLOAD_MODE == "supabase" or "/storage/v1/object/" in (value or ""):
        # the object lives in the Supabase bucket (or the URL says it does -
        # the mode can be switched back after uploads already went there)
        try:
            import supabase_store
            removed = bool(supabase_store._delete_storage_object_from_url(value))
        except Exception:
            removed = False
    if Config.UPLOAD_MODE == "s3":
        removed = _delete_s3(key)
    full = resolve_local(key)
    if full:
        try:
            os.remove(full)
            removed = True
        except OSError:
            pass
    return removed
