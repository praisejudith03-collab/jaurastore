"""Tests for the .xlsx Google Sheets error guard (Task 6).

When a push targets an uploaded .xlsx file instead of a native Google Sheet,
the Google API returns an error; we must surface a clear UI hint:
"Please open your spreadsheet in Google Drive and click File → Save as
Google Sheets to enable syncing."
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google_sheets


def test_xlsx_format_error_is_detected():
    assert google_sheets._is_xlsx_format_error(
        "This operation is not supported for this document")
    assert google_sheets._is_xlsx_format_error(
        "The document is not a Google Sheet")
    assert google_sheets._is_xlsx_format_error(
        "Workbook cannot be edited: xlsx file")


def test_non_xlsx_error_passes_through():
    assert not google_sheets._is_xlsx_format_error("Permission denied")
    assert not google_sheets._is_xlsx_format_error("Invalid request")


def test_annotation_adds_save_as_google_sheets_hint():
    msg = "This operation is not supported for this document"
    annotated = google_sheets._annotate_error(msg)
    assert "Save as Google Sheets" in annotated
    assert google_sheets._XLSX_FIX_HINT.split(".")[0] in annotated


def test_annotation_leaves_other_errors_untouched():
    msg = "Permission denied"
    assert google_sheets._annotate_error(msg) == msg
