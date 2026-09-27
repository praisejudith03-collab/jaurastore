// Client-side image compression simulation (owner request 2026-09-27).
//
// Boots the REAL js/admin.js in a stubbed browser and drives its
// compressImageFile() helper with fake camera photos, so the rule the owner
// asked for is proven, not just read:
//
//   * a huge phone original (4032x3024, 6 MB) is scaled to 1200px on its
//     LONGEST side, re-encoded, and comes back a fraction of the size;
//   * WebP is used when the browser can encode it, JPEG when it cannot;
//   * the uploaded filename follows the new format (.webp / .jpg), so the
//     server stores the right content type;
//   * a picture is NEVER upscaled, a small one is left byte-identical, a
//     re-encode that does not shrink is thrown away, and GIF / SVG /
//     video / undecodable files pass through untouched;
//   * EXIF orientation is applied while decoding (imageOrientation:
//     "from-image"), so portrait phone photos are not stored sideways.
//
// Run directly:   node tests/_image_compression_sim.mjs   (exit 0 = all pass)
// Or via pytest:  python3 -m pytest tests/test_image_compression.py -q
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const adminSrc = readFileSync(path.join(root, "js", "admin.js"), "utf8");

let failures = 0;
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  ->  " + detail : ""));
  if (!ok) failures++;
}

// --------------------------------------------------------------- fake browser
class FakeBlob {
  constructor(size, type) { this.size = size; this.type = type; }
}
class FakeFile extends FakeBlob {
  constructor(name, type, size, pixels) {
    super(size, type);
    this.name = name;
    this.pixels = pixels || null;      // { width, height } for the decoder
  }
}

/** A canvas whose encoder produces a believable byte count: ~0.09 bytes per
 *  pixel for WebP and ~0.13 for JPEG at q≈0.8, scaled by the quality asked
 *  for. `encodeFactor` lets a test force a re-encode that does NOT shrink. */
function makeSandbox(opts = {}) {
  const calls = { canvases: [], encodes: [], bitmaps: [] };
  const webpSupported = opts.webpSupported !== false;
  const encodeFactor = opts.encodeFactor || 1;

  const makeCanvas = () => {
    const canvas = {
      width: 0, height: 0,
      getContext: () => ({
        fillStyle: "", imageSmoothingQuality: "",
        fillRect(...a) { canvas.filled = a; },
        drawImage(src, x, y, w, h) { canvas.drew = { src, x, y, w, h }; },
      }),
      toDataURL(type) {
        if (type === "image/webp" && !webpSupported) return "data:image/png;base64,00";
        return "data:" + (type || "image/png") + ";base64,00";
      },
      toBlob(cb, type, quality) {
        calls.encodes.push({ type, quality, width: canvas.width, height: canvas.height });
        const perPixel = (type === "image/webp" ? 0.09 : 0.13) * (quality || 0.8) * encodeFactor;
        cb(new FakeBlob(Math.max(1, Math.round(canvas.width * canvas.height * perPixel)), type));
      },
    };
    calls.canvases.push(canvas);
    return canvas;
  };

  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, Math, JSON, Date, Number, String, Array, Object, Boolean, Set, Map,
    RegExp, Error, Uint8Array, encodeURIComponent, decodeURIComponent,
    Blob: FakeBlob, File: FakeFile,
    URL: { createObjectURL: () => "blob:preview", revokeObjectURL() {} },
    createImageBitmap: (file, options) => {
      calls.bitmaps.push({ file, options });
      if (!file || !file.pixels) return Promise.reject(new Error("cannot decode"));
      return Promise.resolve({
        width: file.pixels.width, height: file.pixels.height,
        close() { this.closed = true; },
      });
    },
    document: {
      createElement: (tag) => (tag === "canvas" ? makeCanvas() : { style: {}, dataset: {} }),
      addEventListener() {}, removeEventListener() {},
      querySelector: () => null, querySelectorAll: () => [],
      body: { dataset: {}, classList: { add() {}, remove() {}, contains: () => false } },
      documentElement: { dataset: {} },
    },
    navigator: { onLine: true },
    location: { href: "https://jaurastore.com.ng/admin.html", search: "" },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    JA: { toast() {}, escape: (s) => String(s), ready: Promise.resolve() },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.FileReader = undefined;       // force the createImageBitmap path
  vm.createContext(sandbox);
  // js/admin.js ends with browser boot code that needs a real DOM; the
  // top-level function declarations are instantiated before any of it runs,
  // so the helpers are available even if that boot throws.
  try { vm.runInContext(adminSrc, sandbox, { filename: "js/admin.js" }); } catch (e) { /* boot only */ }
  sandbox.__calls = calls;
  return sandbox;
}

function helper(sandbox, name) {
  return vm.runInContext(`typeof ${name} === "function" ? ${name} : null`, sandbox);
}

// ------------------------------------------------------------------- the rules
{
  const sandbox = makeSandbox();
  const compress = helper(sandbox, "compressImageFile");
  check("compressImageFile is a shared top-level helper", typeof compress === "function");
  check("the 1200px ceiling is the shipped default",
    vm.runInContext("PHOTO_MAX_DIMENSION", sandbox) === 1200,
    String(vm.runInContext("PHOTO_MAX_DIMENSION", sandbox)));

  // ---- a 6 MB, 4032x3024 camera original -------------------------------
  const camera = new FakeFile("IMG_20260927_112233.jpg", "image/jpeg", 6 * 1024 * 1024,
    { width: 4032, height: 3024 });
  const out = await compress(camera);
  check("a huge phone photo is compressed", out.compressed === true);
  check("the longest side is scaled to 1200px",
    out.width === 1200 && out.height === 900, `${out.width}x${out.height}`);
  check("it is re-encoded to WebP where WebP is available",
    out.type === "image/webp", out.type);
  check("the upload filename follows the new format",
    out.filename === "IMG_20260927_112233.webp", out.filename);
  check("the bytes actually shrink (mobile data + storage)",
    out.size < out.originalSize / 20,
    `${out.originalSize} -> ${out.size} bytes`);
  check("the result is a different blob from the original file",
    out.blob !== camera && out.blob.size === out.size);
  const bitmap = sandbox.__calls.bitmaps[0];
  check("EXIF orientation is applied while decoding",
    bitmap && bitmap.options && bitmap.options.imageOrientation === "from-image",
    JSON.stringify(bitmap && bitmap.options));

  // ---- portrait photo: the LONG side is what gets capped ---------------
  const portrait = new FakeFile("portrait.jpg", "image/jpeg", 5 * 1024 * 1024,
    { width: 3024, height: 4032 });
  const tall = await compress(portrait);
  check("a portrait photo caps its height, not its width",
    tall.width === 900 && tall.height === 1200, `${tall.width}x${tall.height}`);

  // ---- never upscaled ---------------------------------------------------
  const small = new FakeFile("thumb.jpg", "image/jpeg", 40 * 1024, { width: 420, height: 320 });
  const kept = await compress(small);
  check("a small photo is left byte-identical (never upscaled)",
    kept.compressed === false && kept.blob === small, `${kept.width}x${kept.height}`);

  // ---- a big-but-small-dimensioned PNG is still re-encoded --------------
  const fatPng = new FakeFile("banner.png", "image/png", 2 * 1024 * 1024, { width: 900, height: 600 });
  const png = await compress(fatPng);
  check("a heavy but small-dimensioned picture is still squeezed",
    png.compressed === true && png.width === 900 && png.height === 600,
    `${png.width}x${png.height} ${png.size}`);

  // ---- formats that must pass through untouched -------------------------
  const gif = new FakeFile("loop.gif", "image/gif", 3 * 1024 * 1024, { width: 800, height: 800 });
  const svg = new FakeFile("logo.svg", "image/svg+xml", 20 * 1024, { width: 512, height: 512 });
  const video = new FakeFile("clip.mp4", "video/mp4", 12 * 1024 * 1024, null);
  const broken = new FakeFile("broken.jpg", "image/jpeg", 4 * 1024 * 1024, null); // decoder rejects
  for (const [label, file] of [["an animated GIF", gif], ["an SVG", svg],
                               ["a video", video], ["an undecodable file", broken]]) {
    const res = await compress(file);
    check(label + " is uploaded untouched",
      res.compressed === false && res.blob === file && res.filename === file.name);
  }
}

// ---- JPEG fallback where the canvas cannot encode WebP --------------------
{
  const sandbox = makeSandbox({ webpSupported: false });
  const compress = helper(sandbox, "compressImageFile");
  const camera = new FakeFile("photo.HEIC", "image/heic", 8 * 1024 * 1024,
    { width: 4000, height: 3000 });
  const out = await compress(camera);
  check("without WebP support the fallback is JPEG", out.type === "image/jpeg", out.type);
  check("the JPEG fallback renames the upload too", out.filename === "photo.jpg", out.filename);
  check("the JPEG fallback still scales to 1200px",
    out.width === 1200 && out.height === 900, `${out.width}x${out.height}`);
  check("a JPEG encode flattens transparency onto white",
    sandbox.__calls.canvases[sandbox.__calls.canvases.length - 1].filled !== undefined);
}

// ---- a re-encode that would not shrink is thrown away ---------------------
{
  const sandbox = makeSandbox({ encodeFactor: 40 });
  const compress = helper(sandbox, "compressImageFile");
  const already = new FakeFile("optimised.webp", "image/webp", 300 * 1024, { width: 1100, height: 900 });
  const out = await compress(already);
  check("a re-encode that does not shrink keeps the original bytes",
    out.compressed === false && out.blob === already, `${out.size} vs ${out.originalSize}`);
}

// ---- the legacy helpers still answer what their callers expect ------------
{
  const sandbox = makeSandbox();
  const fileToBlob = helper(sandbox, "fileToBlob");
  check("fileToBlob() is still exported for older call sites", typeof fileToBlob === "function");
  const camera = new FakeFile("x.jpg", "image/jpeg", 6 * 1024 * 1024, { width: 3000, height: 2000 });
  const blob = await fileToBlob(camera);
  check("fileToBlob() hands back the compressed bytes", blob && blob.size < camera.size,
    `${camera.size} -> ${blob && blob.size}`);
}

console.log(failures ? `\n${failures} image-compression check(s) FAILED`
                     : "\nall image-compression checks passed");
process.exit(failures ? 1 : 0);
