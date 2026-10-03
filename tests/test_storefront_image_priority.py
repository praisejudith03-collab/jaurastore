"""Which storefront photos a phone downloads first.

The category row is the first thing under the hero on a phone. It used to
render every tile lazy AND fetchpriority="low", so the tiles the shopper was
looking at waited behind photos much further down the page - and the tiles
came back as grey boxes.

``tests/_storefront_image_priority_dom_sim.mjs`` boots the real js/store.js
and js/app.js in Node's VM, renders the real homepage row, and checks the real
markup (visible tiles eager + high, the rest lazy and NOT low). This file runs
it, and pins the one thing a future edit must not reintroduce.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "tests" / "_storefront_image_priority_dom_sim.mjs"
EAGER_TILES = 4


def test_homepage_category_tiles_load_the_visible_images_first():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    proc = subprocess.run([node, str(SIM)], cwd=ROOT, capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, (
        "storefront image priority simulation failed:\n" + proc.stdout + proc.stderr)
    assert "all storefront image priority checks passed" in proc.stdout


def test_no_shipped_script_marks_a_photo_low_priority_again():
    """fetchpriority="low" on off-screen photos also held back the ones a
    thumb-scroll reaches next. loading="lazy" is the whole mechanism now."""
    for name in ("js/store.js", "js/app.js", "js/admin.js"):
        text = (ROOT / name).read_text()
        assert 'fetchpriority="low"' not in text, name


def test_the_eager_window_is_the_visible_row_not_the_whole_page():
    app = (ROOT / "js" / "app.js").read_text()
    assert f"const EAGER_TILES = {EAGER_TILES};" in app
    assert "eager: index < EAGER_TILES" in app
