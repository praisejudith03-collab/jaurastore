"""No internal-tool wording in any HTML/shared bundle served to shoppers."""
from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHOPPER = [p for p in ROOT.glob("*.html") if p.name != "admin.html"]
SHOPPER += [p for p in (ROOT / "js").glob("*.js") if p.name != "admin.js"]
FORBIDDEN = re.compile(r"admin[\s_-]*portal|portail\s+(?:d['’])?admin(?:istrat\w*)?", re.I)


@pytest.mark.parametrize("path", SHOPPER, ids=lambda p: p.name)
def test_shopper_copy_does_not_name_internal_tools(path):
    # Scan strings AND comments: stricter than rendered-text-only, and catches
    # both translation dictionaries before a shopper happens to open a dialog.
    assert not FORBIDDEN.search(path.read_text()), str(path)
