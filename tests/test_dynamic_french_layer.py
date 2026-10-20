"""The dynamic French layer: no raw English, no "0 CFA" on a French view.

Three layers protect a French shopper (owner directive 2026-10-20):
  1. the shipped French copy - every catalogue name and description;
  2. js/i18n.js translateCopy(), the dynamic fallback for owner-written copy
     that arrives without a French translation;
  3. a price guard: a card never prints a zero amount as a price.

The JavaScript half runs the REAL js/i18n.js and js/store.js in a stub browser.

Run with: python3 -m pytest tests/test_dynamic_french_layer.py -q
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_every_catalogue_name_has_a_french_name():
    rows = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    missing = [r["id"] for r in rows if not str(r.get("nameFr") or "").strip()]
    assert not missing, f"products without a French name: {missing}"
    # A French name that is byte-identical to the English one is only allowed
    # for a brand/model name, never for a plain English noun phrase.
    # "Bracelet" is the same word in French, so it is not in this list.
    englishish = {"mug", "cream", "deodorant", "headphone", "bottle", "wipes",
                  "candle", "frame", "light", "cup", "clipper", "diffuser",
                  "fragrance", "earpod", "tripod"}
    offenders = []
    for r in rows:
        name = str(r.get("name") or "")
        french = str(r.get("nameFr") or "")
        if name.strip().lower() != french.strip().lower():
            continue
        words = set(w.strip(",.").lower() for w in name.split())
        if words & englishish:
            offenders.append((r["id"], name))
    assert not offenders, f"French name is still the English words: {offenders}"


def test_the_name_table_covers_the_translated_catalogue():
    table = json.load(open(os.path.join(ROOT, "data", "names_fr.json"),
                           encoding="utf-8"))
    rows = json.load(open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))
    expected = {r["id"]: r["nameFr"] for r in rows
                if str(r.get("nameFr") or "").strip().lower()
                != str(r.get("name") or "").strip().lower()}
    assert table == expected
    assert len(table) >= 230


def test_the_server_fills_french_name_and_description_for_a_bare_supabase_row():
    import catalog
    row = {"id": "wix-066", "name": "Ceramic mug", "description": ""}
    filled = catalog._with_french_description(row)
    assert filled["nameFr"] == "Mug en céramique"
    # An owner-written French name always wins.
    owned = {"id": "wix-066", "name": "Ceramic mug", "nameFr": "Ma tasse"}
    assert catalog._with_french_description(owned)["nameFr"] == "Ma tasse"
    # Unknown copy is never invented.
    unknown = {"id": "jau-unknown", "name": "Zebra widget", "description": ""}
    out = catalog._with_french_description(unknown)
    assert not str(out.get("nameFr") or "").strip()


def test_the_merged_catalogue_serves_french_names_for_every_shipped_row():
    """Every row that comes from the shipped catalogue answers in French.

    A synthetic row another test creates in the overrides file is not the
    shop's catalogue and is deliberately out of scope here - the name table
    can only translate copy it was written for.
    """
    import catalog
    seed_ids = {r["id"] for r in json.load(
        open(os.path.join(ROOT, "data", "seed.json"), encoding="utf-8"))}
    rows = catalog.merged(include_hidden=True)
    shipped = [r for r in rows if r.get("id") in seed_ids]
    assert shipped, "the shipped catalogue did not merge"
    missing = [r["id"] for r in shipped
               if str(r.get("name") or "").strip()
               and not str(r.get("nameFr") or "").strip()]
    assert not missing, f"merged() rows without nameFr: {missing}"


_NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
function el() {
  return { classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    setAttribute(){}, getAttribute(){ return ""; }, style: {}, appendChild(){},
    remove(){}, addEventListener(){}, querySelector(){ return null; },
    querySelectorAll(){ return []; }, textContent: "", innerHTML: "" };
}
const document = { documentElement: { lang: "" }, querySelectorAll: () => [],
  addEventListener(){}, body: el(), querySelector: () => null,
  createElement: () => el(), readyState: "complete" };
const store = () => ({ getItem: () => null, setItem(){}, removeItem(){} });
const window = { document, location: { search: "", href: "https://x/" },
  navigator: { language: "fr-FR", languages: ["fr-FR", "fr"] },
  localStorage: store(), sessionStorage: store(), addEventListener(){},
  matchMedia(){ return { matches: false, addEventListener(){} }; },
  setTimeout, clearTimeout, console };
window.window = window;
document.body = el();
document.body.dataset = {};
const ctx = vm.createContext({ window, document,
  navigator: window.navigator, localStorage: store(), sessionStorage: store(),
  console, setTimeout, clearTimeout, location: window.location, URLSearchParams,
  // Never settles: the harness has no network, and a rejected promise here
  // would surface as an unhandled rejection after the checks already passed.
  fetch: () => new Promise(() => {}),
  AbortSignal, AbortController, TextEncoder, TextDecoder, Blob,
  Promise, Date, JSON, Math,
  CustomEvent: class { constructor(t, o) { this.type = t; this.detail = (o||{}).detail; } },
  matchMedia: window.matchMedia });
vm.runInContext(fs.readFileSync("js/i18n.js", "utf8"), ctx);
const I18N = ctx.window.I18N;
function check(c, m) { if (!c) throw new Error(m); }

check(I18N.lang() === "fr", "a French device boots French, got " + I18N.lang());
const copy = I18N.translateCopy;
check(copy("100% human hair.&#010;10 inch.") === "cheveux 100 % humains. 10 pouces.",
  "hair copy: " + copy("100% human hair.&#010;10 inch."));
check(/faux ongles/.test(copy("24 PCs of press on nail with a glue")),
  "nails copy: " + copy("24 PCs of press on nail with a glue"));
check(copy("Zebra quantum widget") === "",
  "unknown copy must not be half-translated");
check(copy("") === "", "empty copy stays empty");

// Load the shared commerce layer on top of the same browser. The timers are
// stubbed: this harness is not a scheduler.
ctx.setInterval = () => 0;
ctx.setTimeout = (fn) => 0;
ctx.clearInterval = () => {};
vm.runInContext(fs.readFileSync("js/store.js", "utf8"), ctx);
const JA = vm.runInContext("JA", ctx) || ctx.window.JA;
check(!!JA, "store.js did not export JA");
const priced = { id: "p1", description: "Only English here", priceNgn: 0, priceCfa: 0 };
check(JA.displayDescription(priced) === "Only English here",
  "untranslatable copy is shown as written, never blank");
const frRow = { id: "p2", description: "English copy", descriptionFr: "Texte en français" };
check(JA.displayDescription(frRow) === "Texte en français", "owner French wins");
check(JA.priceOnRequest() === "Prix sur demande",
  "the zero-price label must be French, got " + JA.priceOnRequest());
const html = JA.priceHTML({ id: "p3", slug: "p3", name: "X", priceNgn: 0, priceCfa: 0 });
check(!/0\s*CFA/.test(html), "a card must never print 0 CFA: " + html);
check(/Prix sur demande/.test(html), "the card says the price is coming: " + html);
console.log("DYNAMIC FRENCH CHECKS PASSED");
"""


def test_the_french_fallback_and_zero_price_guard_run_in_a_real_dom():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed (frontend checks need Node >= 18)")
    proc = subprocess.run([node, "-e", _NODE_CHECK], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        "dynamic French checks failed:\n" + proc.stdout + proc.stderr)
    assert "DYNAMIC FRENCH CHECKS PASSED" in proc.stdout


def test_the_glossary_never_translates_a_product_name():
    """Names are translated by the catalogue/name table, never by the
    word-for-word glossary: a brand must survive intact."""
    js = _read("js", "store.js")
    block = js[js.index("function displayName(p)"):js.index("function inFrench()")]
    assert "translateCopy" not in block
    assert "p.nameFr" in block
