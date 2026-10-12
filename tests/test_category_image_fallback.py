"""Category covers stay anchored permanently — admin fallback protection.

Spec section 1: category image URLs live in the database (categories.image_url
via Supabase), and a transient image failure in the admin desk (deploy, cache
purge, offline tab) must repaint a placeholder WITHOUT ever writing that
placeholder back over the stored URL. Reading the repainted <img src> back
into "Save categories" was the way a category cover used to disappear for
good; these tests pin the guards.
"""
import os
import re
import subprocess
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin_js():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def test_category_asset_images_render_with_a_fallback():
    src = _admin_js()
    start = src.index("function _catAssetHTML(image)")
    fn = src[start:src.index("\nfunction ", start + 1)]
    # The shop's own onerror handler + the stored-url anchor + lazy loading.
    assert 'onerror="fallbackImg(event)"' in fn
    assert "data-cat-original" in fn
    assert 'loading="lazy"' in fn


def test_category_card_anchors_the_stored_url():
    src = _admin_js()
    start = src.index("function categoryManager()")
    fn = src[start:src.index("\nfunction ", start + 1)]
    assert 'data-cat-stored=' in fn


def test_collect_cats_never_saves_a_placeholder_over_the_stored_url():
    src = _admin_js()
    start = src.index("function collectCats()")
    fn = src[start:src.index("\nfunction ", start + 1)]
    # Preference order: fresh upload first, then the STORED anchor, and a
    # placeholder-looking live src is explicitly refused.
    assert "dataset.catUrl" in fn
    assert "data-cat-stored" in fn
    assert "PLACEHOLDER_RE" in fn
    # Resolution order inside the asset expression: fresh upload first, then
    # the placeholder-checked live src, then the stored anchor.
    expr_start = fn.index("const asset =")
    expr = fn[expr_start:fn.index(";", expr_start)]
    assert expr.index("uploadUrl") < expr.index("isPlaceholder(liveSrc)") < expr.index("stored")
    # The placeholder regex from the source is itself the behaviour contract.
    match = re.search(r"PLACEHOLDER_RE = /(.+)/i", fn)
    assert match, "the placeholder regex must stay a literal for extraction"
    pattern = re.compile(match.group(1), re.I)

    def is_placeholder(u):
        return not u or pattern.search(u)

    assert is_placeholder("images/products/_placeholder.jpg")
    assert is_placeholder("images/products/_placeholder.jpg?v=9")
    assert is_placeholder("images/brand/logo.jpg?v=202")
    assert is_placeholder("")
    assert not is_placeholder("https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/categories/2026/10/abc.jpg")
    assert not is_placeholder("images/products/mouth-spray.jpg")
    assert not is_placeholder("/uploads/categories/2026/10/x.webp")


def test_collect_cats_real_dom_run_keeps_the_stored_cover():
    """The REAL collectCats() over a jsdom-free fake DOM: a broken cover whose
    <img> was repainted to the placeholder still saves the stored URL."""
    script = textwrap.dedent(r"""
        import { readFileSync } from "node:fs";
        import vm from "node:vm";
        const src = readFileSync("js/admin.js", "utf8");
        const start = src.indexOf("function collectCats()");
        const code = src.slice(start, src.indexOf("\nfunction ", start + 1));
        const STORED = "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/categories/2026/10/cover.jpg";
        const row = {
          getAttribute(name) {
            if (name === "data-cat-i") return "0";
            if (name === "data-cat-stored") return STORED;
            return null;
          },
          querySelector(sel) {
            if (sel === '[name="cat-id-0"]') return { value: "bags" };
            if (sel === '[name="cat-name-0"]') return { value: "Bags" };
            if (sel === '[name="cat-fr-0"]') return { value: "Sacs" };
            if (sel === "[data-cat-img]") return null;
            if (sel === '.au-cat-pic img') return { getAttribute: () => "images/products/_placeholder.jpg" };
            if (sel === '.au-cat-pic a.media-doc-chip') return null;
            return null;
          },
          querySelectorAll: () => [],
        };
        const sandbox = {
          console,
          document: { querySelectorAll: (sel) => sel === "[data-cat-i]" ? [row] : [] },
        };
        sandbox.window = sandbox; sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox, { filename: "collectCats.js" });
        const out = vm.runInContext("collectCats()", sandbox);
        if (out.length !== 1) { console.error(JSON.stringify(out)); process.exit(1); }
        if (out[0].image !== STORED) {
          console.error("saved image is not the stored URL: " + out[0].image);
          process.exit(1);
        }
        console.log("OK");
    """)
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                          text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_fresh_upload_still_wins_over_the_stored_anchor():
    script = textwrap.dedent(r"""
        import { readFileSync } from "node:fs";
        import vm from "node:vm";
        const src = readFileSync("js/admin.js", "utf8");
        const start = src.indexOf("function collectCats()");
        const code = src.slice(start, src.indexOf("\nfunction ", start + 1));
        const FRESH = "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/categories/2026/10/fresh.jpg";
        const STORED = "https://rvkweyipqgsggcnimhxf.supabase.co/storage/v1/object/public/categories/2026/09/old.jpg";
        const row = {
          getAttribute(name) {
            if (name === "data-cat-i") return "0";
            if (name === "data-cat-stored") return STORED;
            return null;
          },
          querySelector(sel) {
            if (sel === '[name="cat-id-0"]') return { value: "bags" };
            if (sel === '[name="cat-name-0"]') return { value: "Bags" };
            if (sel === '[name="cat-fr-0"]') return { value: "" };
            if (sel === "[data-cat-img]") return { dataset: { catUrl: FRESH } };
            return null;
          },
          querySelectorAll: () => [],
        };
        const sandbox = {
          console,
          document: { querySelectorAll: (sel) => sel === "[data-cat-i]" ? [row] : [] },
        };
        sandbox.window = sandbox; sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox, { filename: "collectCats.js" });
        const out = vm.runInContext("collectCats()", sandbox);
        if (out[0].image !== FRESH) {
          console.error("fresh upload lost: " + out[0].image); process.exit(1);
        }
        console.log("OK");
    """)
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                          text=True, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_categories_are_read_from_the_database_not_temp_files():
    """The persistence contract behind spec section 1: production reads the
    Supabase `categories` table (image_url column), never a local temp file,
    and the write path saves image_url into that same table."""
    api_src = open(os.path.join(ROOT, "api.py"), encoding="utf-8").read()
    assert "load_categories_table" in api_src
    assert "save_categories_table" in api_src
    store_src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    start = store_src.index("def save_categories_table(categories):")
    fn = store_src[start:store_src.index("\ndef ", start + 1)]
    assert '"image_url"' in fn
    sql = open(os.path.join(ROOT, "schema_sections", "08_categories.sql"),
               encoding="utf-8").read()
    assert "image_url text" in sql
    # Production has NO silent local fallback: an unreachable table raises.
    uncached = open(os.path.join(ROOT, "api.py"), encoding="utf-8").read()
    start = uncached.index("def _categories_data_uncached()")
    fn = uncached[start:uncached.index("\ndef ", start + 1)]
    assert "raise RuntimeError(\"Supabase categories unavailable\")" in fn
