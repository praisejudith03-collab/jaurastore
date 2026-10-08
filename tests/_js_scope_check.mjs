// Detect calls to file-local helper functions that only exist inside a
// different function. Syntax checks cannot catch this class of browser error:
// an unbound `api()` call still parses, then fails only when that screen opens.
//
// Run normally over every shipped JS file, or pass paths for a diagnostic:
//   node tests/_js_scope_check.mjs
//   node tests/_js_scope_check.mjs /tmp/admin-before-fix.js
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
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

function sourceFiles() {
  if (process.argv.length > 2) {
    return process.argv.slice(2).map((name) => path.resolve(name));
  }
  const tracked = execFileSync("git", ["ls-files", "-z", "--", "*.js"], {
    cwd: root, encoding: "utf8",
  }).split("\0").filter(Boolean);
  return tracked
    .filter((name) => !name.startsWith("tests/") && !name.startsWith("tools/"))
    .map((name) => path.join(root, name));
}

function bindPattern(pattern, scope, addBinding) {
  if (!pattern) return;
  switch (pattern.type) {
    case "Identifier":
      addBinding(scope, pattern.name, pattern, false);
      break;
    case "RestElement":
      bindPattern(pattern.argument, scope, addBinding);
      break;
    case "AssignmentPattern":
      bindPattern(pattern.left, scope, addBinding);
      break;
    case "ArrayPattern":
      for (const item of pattern.elements) bindPattern(item, scope, addBinding);
      break;
    case "ObjectPattern":
      for (const property of pattern.properties) {
        if (property.type === "RestElement") bindPattern(property.argument, scope, addBinding);
        else bindPattern(property.value, scope, addBinding);
      }
      break;
    default:
      break;
  }
}

function inspectFile(filename) {
  const text = readFileSync(filename, "utf8");
  let ast;
  try {
    ast = parse(text, { ecmaVersion: "latest", sourceType: "script", locations: true, allowHashBang: true });
  } catch (scriptError) {
    try {
      ast = parse(text, { ecmaVersion: "latest", sourceType: "module", locations: true, allowHashBang: true });
    } catch (moduleError) {
      throw new Error(`${filename}: JavaScript parse failed: ${moduleError.message || scriptError.message}`);
    }
  }

  const makeScope = (parent, kind, node) => ({ parent, kind, node, bindings: new Map() });
  const helperDeclarations = new Map();
  const callSites = [];
  const addBinding = (scope, name, node, isHelper) => {
    if (!scope || !name) return;
    if (!scope.bindings.has(name)) scope.bindings.set(name, []);
    scope.bindings.get(name).push(node);
    if (isHelper) {
      if (!helperDeclarations.has(name)) helperDeclarations.set(name, []);
      helperDeclarations.get(name).push({ scope, node });
    }
  };
  const nearest = (scope, kinds) => {
    for (let current = scope; current; current = current.parent) {
      if (kinds.includes(current.kind)) return current;
    }
    return scope;
  };
  const isFunction = (node) => ["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(node.type);

  function visitFunction(node, outer) {
    if (node.type === "FunctionDeclaration" && node.id) {
      addBinding(outer, node.id.name, node.id, true);
    }
    const fn = makeScope(outer, "function", node);
    if (node.type === "FunctionExpression" && node.id) {
      addBinding(fn, node.id.name, node.id, false);
    }
    for (const param of node.params || []) bindPattern(param, fn, addBinding);
    // Visit defaults and nested parameter expressions after all parameter
    // names have been registered, so references resolve regardless of order.
    for (const param of node.params || []) visit(param, fn);
    visit(node.body, fn);
  }

  function visit(node, scope) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const child of node) visit(child, scope);
      return;
    }
    if (typeof node.type !== "string") return;

    if (node.type === "Program") {
      const program = makeScope(null, "program", node);
      for (const statement of node.body) visit(statement, program);
      return;
    }
    if (isFunction(node)) {
      visitFunction(node, scope);
      return;
    }
    if (node.type === "BlockStatement") {
      const block = makeScope(scope, "block", node);
      for (const statement of node.body) visit(statement, block);
      return;
    }
    if (node.type === "VariableDeclaration") {
      const target = node.kind === "var" ? nearest(scope, ["function", "program"]) : nearest(scope, ["block", "function", "program"]);
      for (const declaration of node.declarations) {
        bindPattern(declaration.id, target, addBinding);
        if (declaration.id?.type === "Identifier" && declaration.init && isFunction(declaration.init)) {
          addBinding(target, declaration.id.name, declaration.id, true);
        }
        if (declaration.init) visit(declaration.init, scope);
      }
      return;
    }
    if (node.type === "ClassDeclaration") {
      if (node.id) addBinding(scope, node.id.name, node.id, false);
      for (const [key, child] of Object.entries(node)) {
        if (key !== "id" && key !== "type" && key !== "start" && key !== "end" && key !== "loc") visit(child, scope);
      }
      return;
    }
    if (node.type === "CatchClause") {
      const caught = makeScope(scope, "block", node);
      bindPattern(node.param, caught, addBinding);
      visit(node.body, caught);
      return;
    }
    if (node.type === "ForStatement" || node.type === "ForInStatement" || node.type === "ForOfStatement") {
      const loop = makeScope(scope, "block", node);
      for (const [key, child] of Object.entries(node)) {
        if (key !== "type" && key !== "start" && key !== "end" && key !== "loc") visit(child, loop);
      }
      return;
    }
    if (node.type === "SwitchStatement") {
      const switched = makeScope(scope, "block", node);
      visit(node.discriminant, switched);
      for (const item of node.cases) visit(item, switched);
      return;
    }
    if (node.type === "CallExpression" && node.callee?.type === "Identifier") {
      callSites.push({ name: node.callee.name, node: node.callee, scope });
    }

    for (const [key, child] of Object.entries(node)) {
      if (key === "type" || key === "start" || key === "end" || key === "loc") continue;
      // Non-computed property names, labels, and keys are not references.
      if ((node.type === "MemberExpression" && key === "property" && !node.computed) ||
          ((node.type === "Property" || node.type === "MethodDefinition" || node.type === "PropertyDefinition") && key === "key" && !node.computed) ||
          ((node.type === "LabeledStatement" || node.type === "BreakStatement" || node.type === "ContinueStatement") && key === "label")) continue;
      visit(child, scope);
    }
  }

  visit(ast, null);
  const resolves = (scope, name) => {
    for (let current = scope; current; current = current.parent) {
      if (current.bindings.has(name)) return true;
    }
    return false;
  };
  const issues = callSites.filter((call) =>
    helperDeclarations.has(call.name) && !resolves(call.scope, call.name));
  return issues.map(({ name, node }) => `${filename}:${node.loc.start.line}:${node.loc.start.column + 1} calls ${name}(), but its helper is declared only in another lexical scope`);
}

let problems = [];
for (const filename of sourceFiles()) problems = problems.concat(inspectFile(filename));
if (problems.length) {
  console.error("JavaScript helper scope errors:");
  for (const problem of problems) console.error("  " + problem);
  process.exit(1);
}
console.log("JS HELPER SCOPE CHECK PASSED");
