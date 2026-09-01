"""Where uploads live: payment proofs and admin-uploaded product photos.

Two back ends, chosen with UPLOAD_MODE in .env:

  local (default) - written under data/uploads/ and served by the Flask app at
                    /uploads/...  Works with zero configuration. On a host with
                    an ephemeral filesystem (Render free tier) attach a
                    persistent disk at data/ or switch to s3.

  s3             - any S3-compatible bucket (Cloudflare R2, AWS S3, MinIO).
                    Set S3_BUCKET / S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY
                    / S3_PUBLIC_BASE. No code change is needed to switch.

Every image is validated by magic bytes before it is written, so a renamed
.exe can never be stored and later served as an image.
"""
import os, io, hashlib, secrets, datetime
from config import Config
from security import clean

ROOT = os.path.dirname(os.path.abspath(__file__))

MAX_BYTES = 6 * 1024 * 1024              # 6 MB: product photos
MAX_RECEIPT_BYTES = 8 * 1024 * 1024      # 8 MB: payment receipts / PDFs
MAX_VIDEO_BYTES = 40 * 1024 * 1024       # 40 MB: homepage hero video
ALLOWED_EXT = ("jpg", "jpeg", "png", "webp", "pdf")
VIDEO_EXT = ("mp4", "webm")
MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "webp": "image/webp",
    "pdf": "application/pdf",
    "mp4": "video/mp4", "webm": "video/webm",
}

# (extension, tuple of accepted magic-byte prefixes)
_SIGNATURES = (
    ("jpg", (b"\xff\xd8\xff",)),
    ("png", (b"\x89PNG\r\n\x1a\n",)),
    ("webp", (b"RIFF",)),
    ("pdf", (b"%PDF-",)),
)


def _ext_from_bytes(head: bytes) -> str:
    """Identify the real file type from its first bytes."""
    for ext, sigs in _SIGNATURES:
        for sig in sigs:
            if head.startswith(sig):
                if ext == "webp" and head[8:12] != b"WEBP":
                    continue
                return ext
    return ""


def _ext_from_name(name: str) -> str:
    tail = (name or "").rsplit(".", 1)[-1].lower()
    return tail if tail in ALLOWED_EXT else ""


def mime_for(ext: str) -> str:
    return MIME.get((ext or "").lower(), "application/octet-stream")


def validate_upload(data: bytes, filename: str = "", allow_pdf: bool = False,
                    max_bytes: int = MAX_BYTES):
    """Returns (ok, message, extension).

    The extension is read from the file's own bytes, never from the name a
    browser sends, so a renamed .exe can never be stored.
    """
    if not data:
        return False, "The file was empty.", ""
    if len(data) > max_bytes:
        return False, (f"That file is {len(data) / 1048576:.1f} MB. "
                       f"The limit is {max_bytes // (1024 * 1024)} MB."), ""
    ext = _ext_from_bytes(data[:16])
    if not ext:
        return False, "Only JPG, PNG, WebP or PDF files can be uploaded.", ""
    if ext == "pdf" and not allow_pdf:
        return False, "That is a PDF. Upload a JPG or PNG image here.", ""
    if ext == "pdf":
        if b"\x00" in data[:1024]:
            return False, "That PDF could not be read.", ""
        if data.rstrip()[-6:] != b"%%EOF" and b"%%EOF" not in data[-2048:]:
            return False, "That PDF looks incomplete. Save it again and retry.", ""
    return True, "ok", ext


def validate_image(data: bytes, filename: str = ""):
    """Product photos: images only."""
    ok, msg, ext = validate_upload(data, filename, allow_pdf=False, max_bytes=MAX_BYTES)
    if ok:
        return True, "ok", ext
    if ext == "pdf":
        return False, "That is a PDF. Upload a JPG or PNG image here.", ""
    return False, msg, ext


def _video_ext_from_bytes(head: bytes) -> str:
    """Identify MP4 / WebM from the file's own bytes, never from its name."""
    if len(head) >= 12 and head[4:8] == b"ftyp":          # ISO base media (MP4/MOV)
        brand = head[8:12]
        if brand[:3] in (b"mp4", b"iso", b"avc", b"M4V", b"m4v", b"dash", b"MSN") or brand in (b"mmp4", b"3gp4", b"3gp5"):
            return "mp4"
        return "mp4"                                       # any ftyp container plays as mp4
    if head.startswith(b"\x1a\x45\xdf\xa3"):               # EBML → WebM/Matroska
        return "webm"
    return ""


def validate_video(data: bytes, filename: str = ""):
    """Hero video: MP4 or WebM only, capped at MAX_VIDEO_BYTES."""
    if not data:
        return False, "The file was empty.", ""
    if len(data) > MAX_VIDEO_BYTES:
        return False, (f"That video is {len(data) / 1048576:.1f} MB. "
                       f"The limit is {MAX_VIDEO_BYTES // (1024 * 1024)} MB — "
                       "export it smaller (720p is plenty for a hero)."), ""
    ext = _video_ext_from_bytes(data[:16])
    if not ext:
        return False, "Only MP4 or WebM videos can be uploaded here.", ""
    return True, "ok", ext


def save_video(data: bytes, folder: str = "videos", filename: str = ""):
    """Stores a hero video and returns (ok, message, url)."""
    ok, msg, ext = validate_video(data, filename)
    if not ok:
        return False, msg, ""
    digest = hashlib.sha256(data).hexdigest()
    key = _object_name(folder, ext, digest)
    if Config.UPLOAD_MODE == "s3":
        ok2, _msg2, url = _save_s3(data, key, ext)
        if ok2:
            return True, "stored", url
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


def _object_name(folder: str, ext: str, digest: str) -> str:
    now = datetime.datetime.utcnow()
    safe_folder = "".join(c for c in (folder or "misc").lower() if c.isalnum() or c in "-_")[:24] or "misc"
    return f"{safe_folder}/{now:%Y/%m}/{digest[:16]}-{secrets.token_hex(4)}.{ext}"


def _local_path(key: str) -> str:
    base = Config.UPLOAD_DIR if os.path.isabs(Config.UPLOAD_DIR) else os.path.join(ROOT, Config.UPLOAD_DIR)
    return os.path.join(base, key.replace("/", os.sep))


def save_image(data: bytes, folder: str = "misc", filename: str = "", allow_pdf: bool = False,
               max_bytes: int = MAX_BYTES):
    """Stores an upload and returns (ok, message, url)."""
    ok, msg, ext = validate_upload(data, filename, allow_pdf=allow_pdf, max_bytes=max_bytes)
    if not ok:
        return False, msg, ""
    digest = hashlib.sha256(data).hexdigest()
    key = _object_name(folder, ext, digest)

    if Config.UPLOAD_MODE == "s3":
        ok2, msg2, url = _save_s3(data, key, ext)
        if ok2:
            return True, "stored", url
        # never silently lose a customer's proof of payment: fall back to disk
        key = key  # noqa: F841  (reuse the same object name locally)

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


def _save_s3(data: bytes, key: str, ext: str):
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
            ContentType=f"image/{'jpeg' if ext == 'jpg' else ext}",
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
