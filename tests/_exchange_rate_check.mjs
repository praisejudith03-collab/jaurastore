// Execute the real bindExchangeRate() from js/admin.js against a tiny fake
// form. This catches runtime ReferenceErrors that `node --check` cannot see.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const require = createRequire(path.join(process.env.JA_JSDOM_DIR || root, "package.json"));
let parse;
try {
  ({ parse } = require("acorn"));
} catch (_err) {
  console.error("acorn is required but not installed. Run: npm install --no-save --prefix /tmp/uidom jsdom acorn");
  process.exit(3);
}

const source = readFileSync(path.join(root, "js/admin.js"), "utf8");
const ast = parse(source, { ecmaVersion: "latest", sourceType: "script" });
let declaration = null;
function find(node) {
  if (!node || typeof node !== "object" || declaration) return;
  if (Array.isArray(node)) { for (const child of node) find(child); return; }
  if (node.type === "FunctionDeclaration" && node.id?.name === "bindExchangeRate") {
    declaration = node;
    return;
  }
  for (const [key, child] of Object.entries(node)) {
    if (!["type", "start", "end", "loc"].includes(key)) find(child);
  }
}
find(ast);
assert.ok(declaration, "could not find the shipped bindExchangeRate() function");

const calls = [];
const toasts = [];
const input = {
  value: "",
  addEventListener() {},
};
const form = {
  dataset: {},
  elements: { cfaRate: input },
  onsubmit: null,
};
const preview = { textContent: "" };
const errorBox = { hidden: true, textContent: "" };
const button = { disabled: false, textContent: "Save exchange rate" };
const elements = new Map([
  ["#fx-form", form],
  ["#fx-preview", preview],
  ["#fx-form-error", errorBox],
  ["#fx-form-save", button],
]);
const context = vm.createContext({
  window: {
    JA_NET: {
      async api(path, opts) {
        calls.push({ path, opts: opts || null });
        if (opts?.method === "POST") return { ok: true };
        return { settings: { cfaRate: 0.45 } };
      },
    },
  },
  $: (selector) => elements.get(selector) || null,
  JA: { toast: (message) => toasts.push(String(message)) },
});
vm.runInContext(`let settingsFxRate = null;\n${source.slice(declaration.start, declaration.end)}`, context, {
  filename: "js/admin.js#bindExchangeRate",
});

await vm.runInContext("bindExchangeRate()", context);
assert.equal(calls.length, 1, "initial load should request the saved admin setting");
assert.equal(calls[0].path, "api/admin/growth/settings");
assert.equal(input.value, 0.45, "saved rate should populate the input");
assert.equal(preview.textContent, "₦10,000 ≈ FCFA 4,500", "live preview should use the saved setting");

input.value = "0.46";
await form.onsubmit({ preventDefault() {} });
assert.equal(calls.length, 2, "saving should call the settings endpoint");
assert.equal(calls[1].opts.method, "POST");
assert.equal(calls[1].opts.json.cfaRate, 0.46);
assert.equal(errorBox.hidden, true, "successful save should not show an error");
assert.ok(toasts.some((message) => message.includes("0.46")), "successful save should show a success toast");
assert.equal(button.disabled, false, "save button should be re-enabled");

console.log("EXCHANGE RATE BIND CHECKS PASSED");
