"""The product editor's SKU fields, note prompt and CFA proof.

Three things the owner asked for, checked as behaviour rather than as text:

  * a SKU at BOTH levels - one for the product, one per variant/option - with
    the typed value winning, the saved value second and a generated code last,
    so re-saving never renumbers a product;
  * a custom-note prompt that can actually be READ before it is saved (the
    textarea, the live "the customer sees ..." preview and the 160-character
    counter), not a one-line box;
  * a CFA figure with its arithmetic shown, and that figure must agree with
    the amount the SERVER will store (``currency.to_cfa``) - the browser and
    the shop cannot round differently or the card price is a lie.

Run in Node's VM (no browser, no network), the same way
``tests/test_admin_editor_cleanup.py`` drives the editor.
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Prices whose CFA figure is printed by the editor and compared, below, with
# the server's own conversion.
CFA_PRICES = (1800, 3750, 999, 50, 1, 12_000)

_NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("js/admin.js", "utf8");
function section(startMark, endMark) {
  const start = source.indexOf(startMark);
  const end = source.indexOf(endMark, start);
  if (start < 0 || end < 0) throw new Error("missing editor section: " + startMark);
  return source.slice(start, end);
}
const editorHelpers = section("function editorOptions(p) {", "function bindMedia()")
  + "\n" + section("function currentOptionStock() {", "function bindCfaPreview()")
  + "\n" + section("const EDIT_FIELD_MAP = {", "function productForm(p = {})")
  + "\n" + section("function productForm(p = {}) {", "async function handleProductSubmit(e, existing)");
const escape = (value) => String(value ?? "").replace(/[&<>\"']/g, ch =>
  ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;"}[ch]));
const inputs = {
  "[data-opt-sku]": [
    { getAttribute: () => "Colour: Red", value: " SKU-RED " },
    { getAttribute: () => "Colour: Blue", value: "   " },
    { getAttribute: () => "Colour: Green", value: "" },
  ],
  "[data-opt-supplier]": [],
  "[data-opt-stock]": [],
};
const sandbox = {
  JA: { categories: () => [{ id: "bags", name: "Bags" }], CATEGORIES: [], escape,
        money: (n, cur) => `${cur} ${n}` },
  window: {},
  document: {
    querySelectorAll: (sel) => inputs[sel] || [],
    getElementById: () => null,
  },
  dashCat: "bags", prodCatSel: "", editingId: "p1",
  productImages: p => p.images || (p.image ? [p.image] : []),
  mediaStripHTML: () => "<div>media</div>",
};
vm.createContext(sandbox);
vm.runInContext(editorHelpers, sandbox);

const fail = (msg) => { throw new Error(msg); };

// ------------------------------------------------------------ SKU, product
const product = {
  id: "p1", name: "Test bag", category: "bags", priceNgn: 1800,
  sku: "JAU-TEST-1", description: "d", stock: 3,
  options: [{ title: "Colour", values: ["Red", "Blue"] }],
  optionStock: { Red: 3, Blue: 0 },
  optionSku: { "Colour: Red": "SKU-RED" },
  customNotePrompt: "preferred colour",
};
const html = sandbox.productForm(product);
if (!html.includes('name="sku"') || !html.includes('value="JAU-TEST-1"'))
  fail("the product SKU field must render the saved code");
if (!html.includes('data-opt-sku="Colour: Red"') || !html.includes('value="SKU-RED"'))
  fail("the per-variant SKU must render the saved code");
if (!html.includes('data-opt-sku="Colour: Blue"'))
  fail("every active variant needs its own SKU field");
if ((html.match(/data-opt-sku=/g) || []).length !== 2)
  fail("only the active variants may render a SKU row");

// typed -> upper-cased and trimmed; saved -> kept; blank -> generated, and
// re-saving an existing product must NOT renumber it.
if (sandbox.productSku(" sku-9 ", { sku: "JAU-OLD" }) !== "SKU-9") fail("typed SKU must win");
if (sandbox.productSku("", { sku: "JAU-OLD" }) !== "JAU-OLD") fail("saved SKU must survive a blank field");
if (sandbox.productSku("   ", { sku: "JAU-OLD" }) !== "JAU-OLD") fail("whitespace is not a SKU");
const generated = sandbox.productSku("", {});
if (!/^JAU-[A-Z0-9]{4,6}$/.test(generated)) fail("a new product needs a generated SKU: " + generated);
if (sandbox.productSku("x".repeat(80), {}).length !== 40) fail("SKU must be capped at 40 characters");
if (sandbox.productSku("", { sku: "JAU-OLD" }) === generated) fail("the generator must not override a saved code");

// ------------------------------------------------------- SKU, per variant
const collected = sandbox.currentOptionSku();
if (JSON.stringify(collected) !== JSON.stringify({ "Colour: Red": "SKU-RED" }))
  fail("blank variant SKUs must not be saved: " + JSON.stringify(collected));
if (sandbox.optionSkuHTML({ options: [] }) !== "")
  fail("a product without options has no variant SKU block");
const twoVariants = sandbox.optionSkuHTML({
  options: [{ title: "Colour", values: ["Red", "Blue"] }],
  optionSku: { "Colour: Red": "SKU-RED" },
});
if (!twoVariants.includes('data-opt-sku="Colour: Blue"') || !twoVariants.includes('placeholder="SKU (optional)"'))
  fail("the variant SKU block must offer an empty field per variant");

// the editor reports a SKU edit so the server merges only what was touched
const typed = { tagName: "INPUT", getAttribute: () => "sku", closest: () => null };
sandbox.trackEditedField(typed);
if (!sandbox.window.__editDirty.has("sku")) fail("editing the SKU must be reported as an edit");
const variant = { tagName: "INPUT", getAttribute: () => null,
  closest: (attr) => (attr === "[data-opt-sku]" ? {} : null) };
sandbox.trackEditedField(variant);
if (!sandbox.window.__editDirty.has("optionSku")) fail("editing a variant SKU must be reported");
// const declarations live in the context's lexical scope, not on the sandbox
// object, so they are read by evaluating inside the same context.
const fieldMap = vm.runInContext("EDIT_FIELD_MAP", sandbox);
if (JSON.stringify(fieldMap.sku) !== JSON.stringify(["sku"]))
  fail("EDIT_FIELD_MAP must carry the product SKU");
const prefixes = vm.runInContext("EDIT_DATA_PREFIXES", sandbox);
if (!prefixes.some(([attr, fields]) => attr === "data-opt-sku" && fields.includes("optionSku")))
  fail("a variant SKU edit must be reported as optionSku");

// ------------------------------------------------------------ note prompt
if (!html.includes('<textarea name="customNotePrompt"') || !html.includes('rows="3"')
    || !html.includes('maxlength="160"'))
  fail("the note prompt must be a readable, capped textarea");
if (html.includes('<input name="customNotePrompt"'))
  fail("the one-line note prompt is gone; a single-line box cannot be read back");
if (!html.includes('id="note-preview"') || !html.includes('The customer sees: &quot;preferred colour&quot;'))
  fail("the prompt must be previewed the way the customer reads it");
if (!html.includes('id="note-count"') || !html.includes("16/160"))
  fail("the prompt needs its character count");
if (!sandbox.notePromptPreview("  size  ").includes('The customer sees: "size"'))
  fail("the preview must trim the prompt");
if (!/not asked anything/.test(sandbox.notePromptPreview("")))
  fail("an empty prompt must say so plainly");

// --------------------------------------------------------------- CFA proof
const placeholder = sandbox.cfaProof(0);
if (placeholder !== "CFA price will be calculated from the Naira price.")
  fail("a price of zero has no CFA figure yet: " + placeholder);
const proof = sandbox.cfaProof(1800);
if (!proof.includes("rounded up to the nearest 50") || !proof.includes("0.44"))
  fail("the CFA line must show its arithmetic: " + proof);
if (!proof.includes("CFA 800") && !proof.includes("800"))
  fail("1 800 NGN is 800 F CFA: " + proof);
if (!html.includes("rounded up to the nearest 50"))
  fail("the editor must show the CFA proof before anything is typed");

const cfa = {};
for (const price of JSON.parse(process.env.CFA_PRICES)) {
  const text = sandbox.cfaProof(price);
  const match = text.match(/CFA price: [A-Za-z ]*([0-9]+)/);
  if (!match) fail("no CFA figure in: " + text);
  cfa[String(price)] = Number(match[1]);
}
console.log(JSON.stringify({ ok: true, proof: cfa }));
"""


def _run_editor():
    result = subprocess.run(
        ["node", "-e", _NODE_CHECK], cwd=ROOT, capture_output=True, text=True,
        timeout=60, env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                         "CFA_PRICES": json.dumps(list(CFA_PRICES))})
    assert result.returncode == 0, f"editor VM failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


def test_editor_renders_sku_at_both_levels_with_a_readable_note_prompt_and_cfa_proof():
    payload = _run_editor()
    assert payload["ok"] is True


def test_the_editors_cfa_proof_matches_what_the_server_will_store():
    """The number printed in the editor and the number in the row must be the
    same, so this compares the editor's own arithmetic with currency.to_cfa."""
    from currency import to_cfa

    proof = _run_editor()["proof"]
    for price in CFA_PRICES:
        assert proof[str(price)] == to_cfa(price), (
            f"{price} NGN: editor says {proof[str(price)]}, server stores {to_cfa(price)}")


def test_the_save_uses_the_typed_sku_and_sends_the_variant_skus():
    source = (ROOT / "js" / "admin.js").read_text()
    assert 'productSku(fd.get("sku"), existing)' in source, (
        "the save must read the SKU field that is now rendered")
    assert "const optionSku = keepActiveOptionMap(currentOptionSku(), options);" in source
    assert re.search(r"\n      optionSku,\n      optionSkus: optionSku,", source), (
        "the save must send the per-variant SKUs under both names the server reads")
    assert 'sku: existing?.sku || ("JAU-" + Date.now()' not in source, (
        "the old silent generator overwrote the owner's own SKU")
