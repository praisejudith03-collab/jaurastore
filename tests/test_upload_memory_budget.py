"""Python upload-buffer pacing for Render's 512 MB memory limit."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")

import pytest  # noqa: E402
from flask import Flask, request  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402

import api  # noqa: E402


def _slot_is_available():
    available = api._UPLOAD_BUFFER_SLOT.acquire(blocking=False)
    if available:
        api._UPLOAD_BUFFER_SLOT.release()
    return available


def test_upload_reader_is_bounded_and_closes_its_spooled_stream():
    upload = FileStorage(stream=io.BytesIO(b"0123456789"), filename="large.bin")
    assert api._read_upload_buffer(upload, 4) == b"01234"
    assert upload.stream.closed


def test_upload_guard_serializes_multipart_and_closes_files_after_success():
    app = Flask(__name__)
    observed = {}

    @api._upload_memory_guard
    def handler():
        observed["inside"] = not request.files["file"].stream.closed
        return "saved"

    with app.test_request_context(
            "/upload", method="POST",
            data={"file": (io.BytesIO(b"payload"), "photo.jpg")},
            content_type="multipart/form-data"):
        upload = request.files["file"]
        assert handler() == "saved"
        assert observed["inside"] is True
        assert upload.stream.closed
    assert _slot_is_available(), "the upload semaphore must be released"


def test_upload_guard_closes_files_and_releases_slot_when_handler_raises():
    app = Flask(__name__)

    @api._upload_memory_guard
    def handler():
        raise RuntimeError("storage failed")

    with app.test_request_context(
            "/upload", method="POST",
            data={"file": (io.BytesIO(b"payload"), "photo.jpg")},
            content_type="multipart/form-data"):
        upload = request.files["file"]
        with pytest.raises(RuntimeError, match="storage failed"):
            handler()
        assert upload.stream.closed
    assert _slot_is_available(), "the upload semaphore must be released on errors"
