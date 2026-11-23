"""Admin Products desk: the Out of Stock quick filter and the Newest/Oldest sort.

Owner directive 2026-11-23: restocking on a phone must not mean scrolling the
whole catalogue, and a freshly added piece must be at the TOP of the list.

* "Out of Stock (N)" is one tap away, carries a catalogue-wide count badge, and
  collects BOTH single items at zero and products whose every variant has run
  out - the same rule the storefront's Buy button uses.
* Newest to Oldest is the default order; Oldest to Newest is one tap away.
* Both compose with the search box, the category select and the visibility
  tabs, and both survive the round trip through a product editor.

Run with:  python3 -m pytest tests/test_admin_products_stock_sort.py -q
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _admin():
    return open(os.path.join(ROOT, "js", "admin.js"), encoding="utf-8").read()


def _css():
    return open(os.path.join(ROOT, "css", "style.css"), encoding="utf-8").read()


_NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("js/admin.js", "utf8");
function grab(name) {
  const start = source.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  let depth = 0, at = source.indexOf("{", start);
  for (; at < source.length; at++) {
    if (source[at] === "{") depth++;
    else if (source[at] === "}" && --depth === 0) return source.slice(start, at + 1);
  }
  throw new Error("unterminated " + name);
}

// A piece added in the desk: its id IS its creation time (jau-<base36 ms>),
// which is the only stamp a browser-created row carries before the server
// answers with created_at.
const addedId = "jau-" + Date.parse("2026-11-22T10:00:00Z").toString(36);
const rows = [
  // created_at present (the migrated database)
  { id: "a-new", name: "Newest bag", category: "bags", online: true, stock: 3,
    created_at: "2026-11-20T10:00:00Z", updated_at: "2026-11-20T10:00:00Z" },
  { id: "b-old", name: "Oldest bag", category: "bags", online: true, stock: 3,
    created_at: "2026-01-05T10:00:00Z", updated_at: "2026-11-21T10:00:00Z" },
  { id: "c-zero", name: "Zero bag", category: "bags", online: true, stock: 0,
    created_at: "2026-06-01T10:00:00Z" },
  // every variant at zero while the cached total still reads 24
  { id: "d-variants", name: "Variant cream", category: "beauty", online: false,
    stock: 24, options: [{ title: "Colour", values: ["Red", "Blue"] }],
    optionStock: { Red: 0, Blue: 0 }, updated_at: "2026-02-02T10:00:00Z" },
  // one variant still sells: NOT out of stock
  { id: "e-partial", name: "Partial oil", category: "beauty", online: true,
    stock: 7, options: [{ title: "Size", values: ["S", "M"] }],
    optionStock: { S: 0, M: 7 }, created_at: "2026-03-03T10:00:00Z" },
  // no timestamp at all: the shipped catalogue's wix-NNN order
  { id: "wix-007", name: "Seed gift", category: "gifts", online: true, stock: 2 },
  // added here, no created_at yet (the save is still in flight)
  { id: addedId, name: "Just added", category: "gifts", online: true, stock: 2 },
];
const controls = {
  "prod-search": { value: "" },
  "prod-cat": { value: "" },
  "prod-sort": { value: "newest" },
};
const sandbox = {
  JA: { products: () => rows, categories: () => [{ id: "bags" }, { id: "beauty" }, { id: "gifts" }] },
  document: { getElementById: (id) => controls[id] || null },
  selectedProductIds: new Set(),
  prodPage: 3, prodSearchQ: "", prodCatSel: "", dashCat: "",
  prodView: "all", PRODUCT_VIEWS: ["all", "live", "hidden"],
  prodStock: "all", PRODUCT_STOCK_FILTERS: ["all", "out"],
  prodSort: "newest", PRODUCT_SORTS: ["newest", "oldest"],
  paintDesk: (tab) => { sandbox.lastPainted = tab; },
  renderProdGrid: () => {}, bindProdGridEvents: () => {},
  URLSearchParams,
};
vm.createContext(sandbox);
vm.runInContext([
  "productStockUnits", "productIsOutOfStock", "totalOutOfStock",
  "productAddedTime", "sortProductsByDate", "getFilteredProducts",
  "applyProductFilter", "productsStateFromUrl", "applyProductsState",
].map(grab).join("\n"), sandbox);
function ids() { return Array.from(vm.runInContext("getFilteredProducts().map(p => p.id)", sandbox)); }
function check(condition, message) { if (!condition) throw new Error(message); }
function same(list, expected, message) {
  if (JSON.stringify(list) !== JSON.stringify(expected)) {
    throw new Error(message + " -> got " + JSON.stringify(list)
      + " expected " + JSON.stringify(expected));
  }
}

// ---------------------------------------------------------------- stock state
check(vm.runInContext("productIsOutOfStock({ id: 'x', stock: 0 })", sandbox),
  "a single item at zero is out of stock");
check(!vm.runInContext("productIsOutOfStock({ id: 'x', stock: 1 })", sandbox),
  "a single item with stock is not");
check(vm.runInContext(
  "productIsOutOfStock({ id: 'x', stock: 24, options: [{ values: ['Red'] }], optionStock: { Red: 0 } })",
  sandbox), "a variant product whose every option is 0 is out of stock even with a stale total");
check(!vm.runInContext(
  "productIsOutOfStock({ id: 'x', stock: 24, options: [{ values: ['Red','Blue'] }], optionStock: { Red: 0, Blue: 2 } })",
  sandbox), "one stocked option keeps a variant product sellable");
check(!vm.runInContext(
  "productIsOutOfStock({ id: 'x', stock: 4, options: [], colors: [], optionStock: { Red: 0 } })",
  sandbox), "a leftover variant map must not outvote a simple product's own quantity");

// ------------------------------------------------------------ filter + counts
check(vm.runInContext("totalOutOfStock(JA.products())", sandbox) === 2,
  "the badge counts every sold-out piece catalogue-wide, hidden ones included");
same(ids(), [addedId, "a-new", "c-zero", "e-partial", "d-variants", "b-old", "wix-007"],
  "Newest to Oldest is the default and reads created_at before updated_at");

vm.runInContext("prodStock = 'out'", sandbox);
same(ids(), ["c-zero", "d-variants"],
  "the Out of Stock filter lists single-item and variant stockouts together");

// ...composes with the visibility tab, the category and the search box
vm.runInContext("prodView = 'hidden'", sandbox);
same(ids(), ["d-variants"], "sold out AND hidden");
vm.runInContext("prodView = 'live'", sandbox);
same(ids(), ["c-zero"], "sold out AND live");
vm.runInContext("prodView = 'all'; prodStock = 'all'; dashCat = 'bags'", sandbox);
same(ids(), ["a-new", "c-zero", "b-old"], "the bags category, newest first");
vm.runInContext("prodStock = 'out'", sandbox);
same(ids(), ["c-zero"], "sold out in bags");
vm.runInContext("dashCat = ''; prodStock = 'all'; prodSearchQ = 'cream'", sandbox);
same(ids(), ["d-variants"], "search still narrows the list");
vm.runInContext("prodSearchQ = ''", sandbox);

// ------------------------------------------------------------------- sorting
vm.runInContext("prodSort = 'oldest'", sandbox);
same(ids(), ["wix-007", "b-old", "d-variants", "e-partial", "c-zero", "a-new", addedId],
  "Oldest to Newest is the exact reverse walk of the same stamps");
// an edited row keeps ITS creation date: a save today must not teleport the
// oldest piece to the top of the list
check(vm.runInContext("productAddedTime({ id: 'b-old', created_at: '2026-01-05T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' })", sandbox)
  === Date.parse("2026-01-05T10:00:00Z"),
  "created_at wins over a later updated_at");
check(vm.runInContext("productAddedTime({ id: 'no-stamp' })", sandbox) === 0,
  "a row with no stamp and no legacy id sorts last, never NaN");

// The sort dropdown: reorders without losing the other filters.
vm.runInContext("prodSort = 'newest'; prodSearchQ = 'bag'; prodPage = 2; selectedProductIds.add('a-new')", sandbox);
controls["prod-sort"].value = "oldest";
vm.runInContext("applyProductFilter({ target: { id: 'prod-sort' } })", sandbox);
check(sandbox.prodSort === "oldest", "the dropdown sets the sort");
check(sandbox.prodPage === 1, "a new sort returns to page one");
check(sandbox.prodSearchQ === "bag", "a re-sort keeps the search box");
check(Array.from(sandbox.selectedProductIds).join() === "a-new",
  "ticked products stay ticked - they are still visible");
same(ids(), ["b-old", "c-zero", "a-new"], "the search results are re-ordered, not replaced");

// --------------------------------------------------- return-URL round trip
sandbox.applyProductsState(sandbox.productsStateFromUrl("/admin/products?stock=out&sort=oldest"));
check(sandbox.prodStock === "out" && sandbox.prodSort === "oldest",
  "the editor round trip restores the stock filter and the sort");
sandbox.applyProductsState(sandbox.productsStateFromUrl("/admin/products?category=bags"));
check(sandbox.prodStock === "all" && sandbox.prodSort === "newest",
  "a link without them falls back to everything, newest first");
console.log("ADMIN PRODUCTS STOCK+SORT CHECKS PASSED");
"""


def test_out_of_stock_grouping_and_date_sorting():
    result = subprocess.run(["node", "-e", _NODE_CHECK], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"admin products VM failed:\n{result.stdout}\n{result.stderr}"
    assert "ADMIN PRODUCTS STOCK+SORT CHECKS PASSED" in result.stdout


def test_the_desk_shows_an_out_of_stock_chip_with_a_count_badge():
    js = _admin()
    assert 'class="adx-oos-chip' in js
    assert 'data-prod-stock="out"' in js
    assert 'data-toggle-stock="1"' in js, "the chip has to toggle back off"
    assert 'id="prod-stock-out"' in js, "the count badge lives on the chip"
    # ...and the same number is stated in the counter line under the filters
    head = js[js.index('<p class="adx-count">'):]
    head = head[:head.index("</p>")]
    assert 'id="prod-count-out"' in head
    assert "Out of stock" in head
    # the badge is recomputed on every repaint, catalogue-wide
    grid = js[js.index("function renderProdGrid()"):]
    grid = grid[:grid.index("function updateProductBulkUI()")]
    assert 'setText("prod-stock-out", outCount);' in grid
    assert 'setText("prod-count-out", outCount);' in grid
    assert "const outCount = totalOutOfStock(all);" in grid


def test_the_out_of_stock_filter_lists_both_kinds_of_stockout():
    js = _admin()
    filtered = js[js.index("function getFilteredProducts()"):]
    filtered = filtered[:filtered.index("function renderProdGrid()")]
    assert 'if (stockFilter === "out" && !productIsOutOfStock(p)) return false;' in filtered
    helper = js[js.index("function productIsOutOfStock("):]
    helper = helper[:helper.index("function totalOutOfStock(")]
    # variant-level stockouts are recognised: every unit a row carries is <= 0
    assert "every((quantity) => quantity <= 0)" in helper
    units = js[js.index("function productStockUnits("):]
    units = units[:units.index("function productIsOutOfStock(")]
    assert "optionStock" in units and "hasVariants" in units


def test_the_sort_filter_offers_newest_first_by_default_and_oldest():
    js = _admin()
    assert 'id="prod-sort"' in js
    assert "Newest to Oldest" in js
    assert "Oldest to Newest" in js
    # default: newest selected, oldest not
    assert '<option value="newest"${prodSort === "oldest" ? "" : " selected"}>Newest to Oldest</option>' in js
    assert '<option value="oldest"${prodSort === "oldest" ? " selected" : ""}>Oldest to Newest</option>' in js
    # the ordering is applied in the same pass as every other filter
    filtered = js[js.index("function getFilteredProducts()"):]
    filtered = filtered[:filtered.index("function renderProdGrid()")]
    assert "return sortProductsByDate(filtered, sort);" in filtered
    sorter = js[js.index("function sortProductsByDate("):]
    sorter = sorter[:sorter.index("function getFilteredProducts(")]
    assert "newestFirst ? -1 : 1" in sorter
    assert "if (left === right) return 0;" in sorter, "ties keep the catalogue order"


def test_the_controls_are_wired_and_survive_the_editor():
    js = _admin()
    assert '$("#prod-sort")?.addEventListener("change", applyProductFilter);' in js
    assert 'document.querySelectorAll("[data-prod-stock]").forEach' in js
    # a sort change only re-sorts: it must not repaint the desk and lose the
    # search box, the tab or the ticked products
    handler = js[js.index("function applyProductFilter(e)"):]
    handler = handler[:handler.index("function esc(")]
    assert "const sortChanged = !!(e && e.target && e.target.id === \"prod-sort\");" in handler
    assert "if (sortChanged) {" in handler
    assert 'params.set("stock", prodStock);' in js or 'prodStock !== "all"' in js
    url_fn = js[js.index("function productsReturnUrl()"):]
    url_fn = url_fn[:url_fn.index("function readProductsReturn()")]
    assert 'params.set("sort", sort);' in url_fn
    assert 'params.set("stock", prodStock);' in url_fn
    state = js[js.index("function productsStateFromUrl(raw)"):]
    state = state[:state.index("function applyProductsState(")]
    assert 'state.sort = sort;' in state and 'state.stock = stock;' in state
    apply_state = js[js.index("function applyProductsState(state)"):]
    apply_state = apply_state[:apply_state.index("function restoreProductsReturn(")]
    assert 'prodStock = stockFilters.indexOf(String(s.stock || "")) > 0' in apply_state
    assert 'prodSort = sorts.indexOf(String(s.sort || "")) >= 0' in apply_state


def test_the_mobile_controls_are_reachable():
    css = _css()
    assert ".adx-oos-chip" in css
    assert ".adx-oos-chip.is-on" in css, "the active filter has to look active"
    assert ".adx-oos-chip.is-clear" in css, "a fully stocked shop still shows the counter"
    mobile = css[css.index("@media (max-width: 920px)"):]
    assert ".adx-oos-chip { flex: 1 1 100%;" in mobile, "thumb-sized full-width tap target"
    assert '.adx-filters select { flex: 1 1 calc(50% - 4px);' in mobile


# --------------------------------------------------------------------------
# The database side of the sort: created_at, owned by the database.
# --------------------------------------------------------------------------

def test_created_at_is_in_the_schema_and_the_boot_migration():
    import auto_migrate
    assert auto_migrate.PRODUCT_COLUMNS.get("created_at") == "timestamptz"
    assert "add column if not exists created_at" in auto_migrate.PRODUCT_SCHEMA_STATEMENT
    assert "alter column created_at set default now()" in auto_migrate.PRODUCT_SCHEMA_STATEMENT

    for path in ("supabase_schema.sql", "schema_sections/01_products.sql"):
        sql = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert re.search(r"^\s*created_at\s+timestamptz default now\(\)", sql, re.MULTILINE), \
            f"{path} must define created_at in the create-table block"
        assert "add column if not exists created_at" in sql, \
            f"{path} must be able to heal a table that predates created_at"


def test_the_migration_file_is_additive_and_backfills_nothing():
    sql = open(os.path.join(ROOT, "migrations", "20261123_products_created_at.sql"),
               encoding="utf-8").read()
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "add column if not exists created_at" in body
    assert "alter column created_at set default now()" in body
    # legacy rows keep NULL (the desk falls back to updated_at), so no UPDATE
    # touches the catalogue write-lock
    for bad in ("update ", "drop ", "delete ", "truncate "):
        assert bad not in body, f"the migration must stay additive ({bad!r})"


def test_a_product_save_can_never_rewrite_created_at():
    """The stamp belongs to the database: a save echoes the whole row back,
    and a stale browser copy must not be able to reset the date."""
    src = open(os.path.join(ROOT, "supabase_store.py"), encoding="utf-8").read()
    body = src[src.index("def _upsert_products_resilient("):]
    body = body[:body.index("def upsert_products(")]
    assert 'row.pop("created_at", None)' in body
    assert 'row.pop("createdAt", None)' in body
    # the patch path only ever writes columns named by the dirty fields
    patch = src[src.index("_PRODUCT_PATCH_COLUMNS = {"):]
    patch = patch[:patch.index("}\n") + 2]
    assert "created_at" not in patch
