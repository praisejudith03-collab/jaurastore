const $ = (s, r = document) => r.querySelector(s);

function fileToData(file, maxSize, quality) {
  return new Promise((resolve, reject) => {
    if (!file) { reject(new Error("No file")); return; }
    const r = new FileReader();
    r.onerror = reject;
    r.onload = () => {
      const img = new Image();
      img.onload = () => {
        const max = maxSize || 1200;
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", quality || 0.82));
      };
      img.onerror = () => resolve(r.result);
      img.src = r.result;
    };
    r.readAsDataURL(file);
  });
}
function fileToBlob(file, maxSize, quality) {
  return new Promise((resolve) => {
    if (!file) { resolve(null); return; }
    if (typeof createImageBitmap !== "function" && typeof FileReader !== "function") {
      resolve(file); return;
    }
    const done = (blob) => resolve(blob && blob.size && blob.size < file.size ? blob : file);
    const draw = (img, width, height) => {
      try {
        const max = maxSize || 1400;
        const scale = Math.min(1, max / Math.max(width, height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        if (canvas.toBlob) canvas.toBlob((b) => done(b), "image/jpeg", quality || 0.82);
        else done(null);
      } catch (e) { resolve(file); }
    };
    if (typeof createImageBitmap === "function") {
      createImageBitmap(file).then((bmp) => {
        draw(bmp, bmp.width, bmp.height);
        try { bmp.close(); } catch (e) {}
      }, () => resolve(file));
      return;
    }
    const r = new FileReader();
    r.onerror = () => resolve(file);
    r.onload = () => {
      const img = new Image();
      img.onload = () => draw(img, img.width, img.height);
      img.onerror = () => resolve(file);
      img.src = r.result;
    };
    r.readAsDataURL(file);
  });
}
function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}
let loginEmail = "";
let loginNeedsEmail = false;
function paintLogin(msg, needsEmail = loginNeedsEmail) {
  $("#admin-root").innerHTML = `
    <div class="adx-login">
      <div class="adx-login-card">
        <img class="adx-login-logo" src="images/brand/logo.jpg?v=131" alt="Jaura Store" />
        <h1 class="serif-title">Jaura Store</h1>
        <p class="adx-login-sub" data-no-i18n>Sign in to manage your store</p>
        ${msg ? `<p class="admin-err">${JA.escape(msg)}</p>` : ""}
        <form id="login-form" class="field adx-login-form">
          ${needsEmail ? `<label>Email</label>
          <input type="email" name="email" required autocomplete="username" value="${JA.escape(loginEmail)}" />` : ""}
          <label ${needsEmail ? 'style="margin-top:14px"' : ""} data-no-i18n>Password</label>
          <input type="password" name="password" required autocomplete="current-password" placeholder="Permanent admin password" />
          <button class="btn adx-login-btn" id="login-btn" data-no-i18n>Sign in</button>
        </form>
        <p class="admin-note" style="margin-top:16px">Admin access uses the permanent password configured by the store owner.</p>
      </div>
    </div>`;
  const form = $("#login-form");
  const btn = $("#login-btn");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    loginEmail = String(fd.get("email") || "").trim();
    btn.disabled = true;
    btn.textContent = "Signing in…";
    const res = await JA.loginAdmin(loginEmail, fd.get("password"));
    btn.disabled = false;
    btn.textContent = "Sign in";
    if (res.ok) { loginNeedsEmail = false; paintDesk(); }
    else if (/email/i.test(res.error || "")) {
      loginNeedsEmail = true;
      paintLogin(res.error || "Could not sign in.");
    }
    else paintLogin(res.error || "Could not sign in.");
  });
}
let editingId = null;
function productImages(p) {
  if (p && p.images && p.images.length) return p.images.slice(0, 20);
  if (p && p.image) return [p.image];
  return [];
}
function imgSrc(entry) {
  if (!entry) return "";
  if (typeof entry === "string") return entry;
  return entry.preview || entry.url || "";
}
const _VIDEO_EXT = /(\.mp4|\.webm|\.mov)(\?.*)?$/i;
const _DOC_EXT = /(\.pdf|\.doc|\.docx)(\?.*)?$/i;
function mediaKind(entry) {
  if (entry && entry.video) return "video";
  const s = imgSrc(entry) || (typeof entry === "string" ? entry : "");
  const u = String(s).split("?")[0].toLowerCase();
  if (_VIDEO_EXT.test(u)) return "video";
  if (_DOC_EXT.test(u)) return "doc";
  return "image";
}
function mediaTileHTML(src, i, poster) {
  const kind = mediaKind(src);
  const url = JA.asset(imgSrc(src) || (typeof src === "string" ? src : ""));
  const pending = src && src.pending;
  const pendingCls = pending ? " is-pending" : "";
  const main = i === 0 ? " is-main" : "";
  let body;
  if (kind === "video" && !pending) {
    const pos = poster ? `poster="${JA.asset(poster)}"` : "";
    body = `<video class="au-tile-video" src="${url}" ${pos} muted loop playsinline preload="metadata"></video>`;
  } else if (kind === "doc" && !pending) {
    const label = /\.docx?$/i.test(String(imgSrc(src) || "")) ? "DOC" : "PDF";
    body = `<a class="media-doc-chip" href="${url}" target="_blank" rel="noopener">${label}<span>View / Download</span></a>`;
  } else {
    body = `<img src="${url}" alt="" />`;
  }
  return `
    <div class="au-tile${main}${pendingCls}" data-img-i="${i}">
      ${body}
      ${i === 0 ? `<span>Main</span>` : `<span>${i + 1}</span>`}
      <button type="button" class="au-tile-x" data-del-img="${i}" aria-label="Remove">×</button>
    </div>`;
}
let _mediaPaint = 0;
function paintMedia(box) {
  const target = box || document.getElementById("media-box");
  if (!target) return;
  if (_mediaPaint) return;
  _mediaPaint = (typeof requestAnimationFrame === "function"
    ? requestAnimationFrame : (fn) => setTimeout(fn, 16))(() => {
    _mediaPaint = 0;
    const el = document.getElementById("media-box") || target;
    if (el) el.innerHTML = mediaStripHTML(window.__editImages || []);
  }) || 1;
}
const _PHOTO_LANES = 2;
let _photoBusy = 0;
const _photoWaiting = [];
function photoSlot(run) {
  return new Promise((resolve) => {
    const start = () => {
      _photoBusy += 1;
      Promise.resolve().then(run).then((v) => v, (e) => ({ error: (e && e.message) || "upload failed" }))
        .then((v) => {
          _photoBusy -= 1;
          const next = _photoWaiting.shift();
          if (next) next();
          resolve(v);
        });
    };
    if (_photoBusy < _PHOTO_LANES) start(); else _photoWaiting.push(start);
  });
}
function mediaStripHTML(imgs) {
  const firstImg = (imgs || []).find((s) => mediaKind(s) === "image");
  const poster = firstImg ? imgSrc(firstImg) : "";
  const tiles = (imgs || []).map((src, i) => mediaTileHTML(src, i, poster)).join("");
  const plus = (imgs || []).length < 20 ? `<label class="au-tile au-plus">+<input type="file" id="more-media" accept="image/*,video/*" multiple hidden /></label>` : "";
  return `<div class="au-media-row">${tiles}${plus}</div>
    <p class="admin-note">Drag & drop, or tap + to pick several at once. Photos up to 6 MB, videos up to 40 MB. Your photos & videos stay as they are — up to 20 items.</p>
    <button type="button" class="au-view-media" id="view-media">Photos: ${(imgs || []).length} of 20 — tap + to add, × to remove</button>`;
}
function editorOptions(p) {
  if (p && p.options && p.options.length) return p.options;
  if (p && p.colors && p.colors.length) return [{ title: "Colour", type: "COLOR", values: p.colors }];
  return [];
}
function optionRowHTML(o, i) {
  const vals = (o && o.values) || [];
  return `
    <div class="au-opt" data-opt-row>
      <div class="au-opt-top">
        <strong>${JA.escape((o && o.title) || "New option")}</strong>
        <button type="button" class="au-opt-del" data-del-opt>Remove</button>
      </div>
      <div class="au-chips">${vals.map((v) => `<em>${JA.escape(v)}</em>`).join("")}</div>
      <input name="opt-title-${i}" value="${JA.escape((o && o.title) || "")}" placeholder="Option name (Colour, Size, Type, Length, Scent…)" />
      <input name="opt-vals-${i}" value="${JA.escape(vals.join(", "))}" placeholder="Values, comma separated — e.g. Ash, Blue, Black" />
    </div>`;
}
function optionBlockHTML(opts) {
  const list = opts || [];
  if (!list.length) return `<p class="admin-note" data-opt-empty>No options yet. Add Colour, Size, Type, Length or Scent so shoppers can choose on the product page.</p>`;
  return list.map((o, i) => optionRowHTML(o, i)).join("");
}
function collectOptions(root) {
  const options = [];
  (root || document).querySelectorAll("[data-opt-row]").forEach((row) => {
    const title = (row.querySelector('input[name^="opt-title"]')?.value || "").trim();
    const vals = (row.querySelector('input[name^="opt-vals"]')?.value || "").split(",").map((s) => s.trim()).filter(Boolean);
    if (title && vals.length) {
      options.push({ title, type: /colou?r/i.test(title) ? "COLOR" : "DROP_DOWN", values: vals });
    }
  });
  return options;
}
function refreshOptionChips() {
  const box = document.getElementById("opt-box");
  document.querySelectorAll("[data-opt-row]").forEach((row) => {
    const title = (row.querySelector('input[name^="opt-title"]')?.value || "").trim() || "New option";
    const strong = row.querySelector(".au-opt-top strong");
    if (strong) strong.textContent = title;
    const vals = (row.querySelector('input[name^="opt-vals"]')?.value || "").split(",").map((s) => s.trim()).filter(Boolean);
    const chips = row.querySelector(".au-chips");
    if (chips) chips.innerHTML = vals.map((v) => `<em>${JA.escape(v)}</em>`).join("");
  });
  const count = document.getElementById("opt-count");
  if (count) count.textContent = `${document.querySelectorAll("[data-opt-row]").length}/20`;
  const existing = editingId && editingId !== "new" ? (JA.product(editingId) || {}) : {};
  const status = document.getElementById("stock-status")?.value;
  const qty = Number(document.getElementById("stock-qty")?.value);
  const stock = status === "out" ? 0 : (qty > 0 ? qty : 24);
  const typed = currentOptionStock();
  const optionStock = { ...(existing.optionStock || {}), ...typed };
  const fake = { ...existing, options: collectOptions(box || document), stock, optionStock };
  const varBox = document.getElementById("var-box");
  if (varBox) varBox.innerHTML = optionStockHTML(fake);
}
function addOptionRow(title, values) {
  const box = document.getElementById("opt-box");
  if (!box) return;
  box.querySelector("[data-opt-empty]")?.remove();
  const n = box.querySelectorAll("[data-opt-row]").length;
  if (n >= 20) { JA.toast("Maximum 20 options."); return; }
  const wrap = document.createElement("div");
  wrap.innerHTML = optionRowHTML({ title: title || "", values: values ? String(values).split(",").map((s) => s.trim()).filter(Boolean) : [] }, n);
  box.appendChild(wrap.firstElementChild);
  refreshOptionChips();
}
function bindMedia() {
  const box = document.getElementById("media-box");
  if (!box || box.dataset.bound === "1") return;
  box.dataset.bound = "1";
  ["dragenter", "dragover"].forEach((ev) => box.addEventListener(ev, (e) => {
    e.preventDefault(); box.classList.add("is-drop");
  }));
  ["dragleave", "drop"].forEach((ev) => box.addEventListener(ev, (e) => {
    e.preventDefault(); box.classList.remove("is-drop");
  }));
  box.addEventListener("drop", (e) => {
    const files = [...((e.dataTransfer && e.dataTransfer.files) || [])].filter((f) => {
      const t = String(f.type || "");
      const n = String(f.name || "");
      if (/^(image|video)\//.test(t)) return true;
      if (_VIDEO_EXT.test(n) || /\.(jpe?g|png|webp|gif|avif|heic)$/i.test(n)) return true;
      return !t;
    });
    if (!files.length) return;
    if (!window.__editImages) window.__editImages = [];
    for (const file of files) {
      if (window.__editImages.length >= 20) break;
      uploadProductImage(file, box);
    }
  });
  box.addEventListener("change", async (e) => {
    const input = e.target && e.target.matches && e.target.matches("input[type=file]") ? e.target : null;
    if (!input || !input.files || !input.files.length) return;
    if (!window.__editImages) window.__editImages = [];
    for (const file of [...input.files]) {
      if (window.__editImages.length >= 20) break;
      uploadProductImage(file, box);
    }
    input.value = "";
  });
  async function uploadProductImage(file, box) {
    const t = String(file.type || "");
    const n = String(file.name || "");
    const isVideo = /^video\//.test(t) || _VIDEO_EXT.test(n);
    const looksImage = /^image\//.test(t) || /\.(jpe?g|png|webp|gif|avif|heic)$/i.test(n);
    const endpoint = (isVideo || !looksImage) ? "api/admin/uploads/product" : "api/admin/uploads/image";
    const maxVideo = 40 * 1024 * 1024, maxPhoto = 6 * 1024 * 1024;
    if (isVideo && file.size > maxVideo) {
      JA.toast("That video is " + (file.size / 1048576).toFixed(1) + " MB. The limit is 40 MB.");
      return;
    }
    if (!isVideo && file.size > maxPhoto) {
      JA.toast("That photo is " + (file.size / 1048576).toFixed(1) + " MB. The limit is 6 MB.");
      return;
    }
    if (!window.JA_NET) {
      try { window.__editImages.push(await fileToData(file)); } catch (err) {}
      paintMedia(box); return;
    }
    const preview = URL.createObjectURL(file);
    const idx = window.__editImages.push({ pending: true, preview, video: isVideo }) - 1;
    paintMedia(box);
    let payload = file;
    if (!isVideo && looksImage) {
      try { payload = (await fileToBlob(file, 1400, 0.82)) || file; } catch (err) { payload = file; }
    }
    const res = await photoSlot(() => window.JA_NET.api(endpoint, {
      method: "POST", blob: payload, field: "file", filename: file.name || (isVideo ? "video.mp4" : "photo.jpg"),
      queue: true, timeout: isVideo ? 300000 : 45000, label: isVideo ? "Video" : "Photo",
    }));
    if (res && res.url) {
      window.__editImages[idx] = res.url;
      JA.toast(isVideo ? "Video uploaded." : "Photo uploaded.");
    } else if (res && res.queued) {
      window.__jaPendingPhoto = (window.__jaPendingPhoto || 0) + 1;
    } else {
      window.__editImages[idx] = { pending: true, preview, video: isVideo, failed: true };
      JA.toast((res && res.error) || "That file did not upload. It will retry by itself.");
    }
    paintMedia(box);
  }
  box.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del-img]");
    if (del) {
      e.preventDefault();
      const i = Number(del.getAttribute("data-del-img"));
      if (!window.__editImages) window.__editImages = [];
      window.__editImages.splice(i, 1);
      paintMedia(box); return;
    }
    if (e.target.closest("#view-media")) {
      // It used to answer with a toast and nothing else, so the owner could
      // not see which tiles the count referred to. Scroll them into view and
      // flash the row for a moment instead.
      const row = box.querySelector(".au-media-row");
      if (row) {
        if (typeof row.scrollIntoView === "function") {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        row.classList.remove("is-flash");
        void row.offsetWidth;                      // restart the animation
        row.classList.add("is-flash");
        setTimeout(() => row.classList.remove("is-flash"), 1400);
      }
      JA.toast((window.__editImages || []).length + " photo(s). Tap × to delete, + to add more (up to 20).");
    }
  });
}
function bindOptions() {
  const box = document.getElementById("opt-box");
  if (box && box.dataset.bound !== "1") {
    box.dataset.bound = "1";
    box.addEventListener("input", () => refreshOptionChips());
    box.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del-opt]");
      if (!del) return;
      e.preventDefault();
      del.closest("[data-opt-row]")?.remove();
      if (!box.querySelector("[data-opt-row]")) {
        box.innerHTML = `<p class="admin-note" data-opt-empty>No options yet. Add Colour, Size, Type, Length or Scent so shoppers can choose on the product page.</p>`;
      }
      refreshOptionChips();
    });
  }
  const varBox = document.getElementById("var-box");
  if (varBox && varBox.dataset.bound !== "1") {
    varBox.dataset.bound = "1";
    varBox.addEventListener("input", (e) => {
      if (e.target && e.target.matches && e.target.matches("[data-opt-stock]")) syncOptionStockTotals();
    });
  }
  document.getElementById("add-opt")?.addEventListener("click", () => addOptionRow("", ""));
  document.querySelectorAll("[data-preset]").forEach((b) => {
    b.onclick = () => addOptionRow(b.dataset.preset, "");
  });
  const status = document.getElementById("stock-status");
  const qty = document.getElementById("stock-qty");
  status?.addEventListener("change", () => {
    if (!qty) return;
    if (status.value === "out") {
      if (Number(qty.value) > 0) qty.dataset.prev = qty.value;
      qty.value = 0;
    } else if (!(Number(qty.value) > 0)) {
      qty.value = qty.dataset.prev || "24";
    }
    refreshOptionChips();
  });
  qty?.addEventListener("input", () => {
    if (status && Number(qty.value) > 0) status.value = "in";
    refreshOptionChips();
  });
}
function reviewsAdminHTML(id) {
  const list = (id && JA.reviews) ? JA.reviews(id) : (window.__editReviews || []);
  window.__editReviews = list.slice();
  if (!list.length) return `<p class="admin-note" id="rev-empty">No reviews yet.</p>`;
  return list.map((r) => `
    <article class="rev-note admin-rev">
      <p>${JA.starsHTML ? JA.starsHTML(r.rating != null ? r.rating : r.stars) : ""} <strong>${JA.escape(r.name || "")}</strong></p>
      ${r.title ? `<p class="rev-title"><strong>${JA.escape(r.title)}</strong></p>` : ""}
      <p>${JA.escape(r.body != null ? r.body : (r.note || ""))}</p>
      <button type="button" class="au-opt-del" data-del-rev="${JA.escape(r.created_at || r.at || "")}">Remove</button>
    </article>`).join("");
}
function bindReviewsAdmin(id) {
  const box = document.getElementById("rev-admin");
  const paint = () => { if (box) box.innerHTML = reviewsAdminHTML(id); };
  document.getElementById("rev-add")?.addEventListener("click", () => {
    const name = (document.getElementById("rev-name")?.value || "").trim();
    const note = (document.getElementById("rev-note")?.value || "").trim();
    const rating = Number(document.getElementById("rev-stars")?.value || 5);
    if (!note) { JA.toast("Type the customer note first."); return; }
    const pid = id && id !== "new" ? id : (document.querySelector("#prod-form [name=id]")?.value || "");
    if (pid && JA.addReview) JA.addReview(pid, { name, body: note, rating });
    else window.__editReviews = (window.__editReviews || []).concat([{ name: name || "Customer", body: note, rating, created_at: new Date().toISOString() }]);
    if (document.getElementById("rev-name")) document.getElementById("rev-name").value = "";
    if (document.getElementById("rev-note")) document.getElementById("rev-note").value = "";
    paint(); JA.toast("Review added.");
  });
  box?.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del-rev]");
    if (!del) return;
    const at = del.getAttribute("data-del-rev");
    const pid = id && id !== "new" ? id : "";
    if (pid && JA.removeReview) JA.removeReview(pid, at);
    window.__editReviews = (window.__editReviews || []).filter((r) => r.at !== at);
    paint();
  });
}
function currentOptionStock() {
  const map = {};
  document.querySelectorAll("[data-opt-stock]").forEach((inp) => {
    const v = inp.getAttribute("data-opt-stock");
    if (inp.value !== "") map[v] = Math.max(0, parseInt(inp.value, 10) || 0);
  });
  return map;
}
function optionStockHTML(p) {
  const opt = (p.options || [])[0];
  const vals = (opt && opt.values) || p.colors || [];
  if (!vals.length) {
    return `<h3>Stock per option</h3>
      <p class="admin-note">Add an option above (Colour, Size…) and a stock box appears here for each choice. Until then the single Quantity below is used.</p>`;
  }
  const os = p.optionStock || {};
  const toCfa = JA.toCfa || ((n) => Math.round(Number(n || 0) * 0.44));
  const price = `${Number(p.compareNgn) > Number(p.priceNgn) ? `<s>${JA.money(p.compareNgn, "NGN")}</s> ` : ""}${JA.money(p.priceNgn || 0, "NGN")} · ${JA.money(toCfa(p.priceNgn), "CFA")}`;
  const rows = vals.map((v) => {
    const qty = os[v] != null ? Number(os[v]) : "";
    const state = qty === "" ? "" : (qty > 0 ? "in" : "out");
    return `<div class="adx-var" data-var-row>
      <div class="adx-var-name"><strong>${JA.escape(v)}</strong><span>${price}</span></div>
      <label class="adx-var-qty">Stock
        <input type="number" min="0" inputmode="numeric" data-opt-stock="${JA.escape(v)}" value="${qty}" placeholder="0" />
      </label>
      <em class="adx-var-state ${state}" data-var-state>${qty === "" ? "—" : (qty > 0 ? "In stock" : "Sold out")}</em>
    </div>`;
  }).join("");
  const total = vals.reduce((n, v) => n + (Number(os[v]) > 0 ? Number(os[v]) : 0), 0);
  return `<h3>Stock per ${JA.escape((opt && opt.title) || "option")}</h3>
    <p class="admin-note">Type how many pieces you have of each ${JA.escape((opt && opt.title) || "option").toLowerCase()}. The total quantity below updates by itself; a choice with 0 shows as sold out.</p>
    <div class="adx-vars">${rows}</div>
    <p class="admin-note" id="opt-stock-total"><strong>Total: ${total}</strong> piece(s) across ${vals.length} ${JA.escape((opt && opt.title) || "option")} choice(s).</p>`;
}
function syncOptionStockTotals() {
  const inputs = [...document.querySelectorAll("[data-opt-stock]")];
  if (!inputs.length) return;
  let total = 0, touched = false;
  inputs.forEach((inp) => {
    const row = inp.closest("[data-var-row]");
    const state = row && row.querySelector("[data-var-state]");
    if (inp.value === "") {
      if (state) { state.textContent = "—"; state.className = "adx-var-state"; }
      return;
    }
    touched = true;
    const n = Math.max(0, parseInt(inp.value, 10) || 0);
    total += n;
    if (state) {
      state.textContent = n > 0 ? "In stock" : "Sold out";
      state.className = "adx-var-state " + (n > 0 ? "in" : "out");
    }
  });
  const totalEl = document.getElementById("opt-stock-total");
  if (totalEl) totalEl.innerHTML = `<strong>Total: ${total}</strong> piece(s). This becomes the product quantity when you save.`;
  if (!touched) return;
  const qty = document.getElementById("stock-qty");
  const status = document.getElementById("stock-status");
  if (qty) qty.value = total;
  if (status) status.value = total > 0 ? "in" : "out";
}
function bindCfaPreview() {
  const form = document.getElementById("prod-form");
  const el = document.getElementById("cfa-preview");
  if (!form || !el) return;
  const toCfa = JA.toCfa || ((n) => Math.round(Number(n || 0) * 0.44));
  const paint = () => {
    const n = Number(form.priceNgn && form.priceNgn.value) || 0;
    const c = Number(form.compareNgn && form.compareNgn.value) || 0;
    if (!(n > 0)) {
      el.textContent = "Enter the ₦ price. The website will show F CFA converted at 1 ₦ = 0.44.";
      return;
    }
    const now = toCfa(n);
    const was = c > 0 ? toCfa(c) : 0;
    const line = was > now ? "<s>" + JA.money(was, "CFA") + "</s> " + JA.money(now, "CFA") : JA.money(now, "CFA");
    el.innerHTML = "Website will show " + line + " · converted from ₦ at 1 ₦ = 0.44 F CFA.";
  };
  form.addEventListener("input", paint);
  paint();
}
function productForm(p = {}) {
  const allCats = (JA.categories ? JA.categories() : JA.CATEGORIES);
  // Preserve current filter category when adding new product
  const preCat = p.category || dashCat || prodCatSel || "";
  const cats = allCats.map((c) =>
    `<option value="${c.id}" ${preCat === c.id ? "selected" : (p.category === c.id ? "selected" : "")}>${JA.escape(c.name)}</option>`
  ).join("");
  window.__editImages = productImages(p);
  const opts = editorOptions(p);
  const inStock = p.id ? Number(p.stock) > 0 : true;
  return `<form id="prod-form" class="au-edit">
    <button type="button" class="au-back" id="cancel-edit">← Store Products</button>
    <h2>Product ${preCat ? `· ${JA.escape(allCats.find(c=>c.id===preCat)?.name||preCat)}` : ""}</h2>
    <div id="media-box">${mediaStripHTML(window.__editImages)}</div>
    <div class="field"><label>Product Name</label><input name="name" required maxlength="80" value="${JA.escape(p.name || "")}" /></div>
    <div class="field"><label>Product Name (French — shown when the site is in French)</label><input name="nameFr" maxlength="80" value="${JA.escape(p.nameFr || "")}" placeholder="Optional" /></div>
    <input type="hidden" name="id" value="${p.id || ""}" />
    <div class="au-2">
      <div class="field"><label>Price ₦</label><div class="au-price"><input name="priceNgn" type="number" min="0" required value="${p.priceNgn || ""}" /><i>₦</i></div></div>
      <div class="field"><label>Strikethrough ₦</label><div class="au-price"><input name="compareNgn" type="number" min="0" value="${p.compareNgn || ""}" /><i>₦</i></div></div>
    </div>
    <p class="admin-note" id="cfa-preview">CFA on the website is converted from Naira at 1 ₦ = 0.44 F CFA. You only enter ₦.</p>
    <div class="field"><label>Add a description</label><textarea name="description" rows="3">${JA.escape(p.description || "")}</textarea></div>
    <div class="field"><label>Description (French — shown when the site is in French)</label><textarea name="descriptionFr" rows="3" placeholder="Optional">${JA.escape(p.descriptionFr || "")}</textarea></div>
    <div class="field"><label>Ribbon</label>
      <select name="badge">
        <option value="">None</option>
        ${["sale", "new", "bestseller"].map((b) => `<option value="${b}" ${p.badge === b ? "selected" : ""}>${b}</option>`).join("")}
      </select>
    </div>
    <label class="au-tog"><span>Show in online store</span>
      <input type="checkbox" name="online" ${p.online === false ? "" : "checked"} />
    </label>
    <div class="field"><label>Category</label><select name="category">${cats}</select></div>
    <h3>Product options <small id="opt-count">${opts.length}/20</small></h3>
    <div id="opt-box">${optionBlockHTML(opts)}</div>
    <div class="au-opt-presets">
      <button type="button" data-preset="Colour">+ Colour</button>
      <button type="button" data-preset="Size">+ Size</button>
      <button type="button" data-preset="Type">+ Type</button>
      <button type="button" data-preset="Length">+ Length</button>
      <button type="button" data-preset="Scent">+ Scent</button>
    </div>
    <button type="button" class="au-link-btn" id="add-opt">+ Add Option</button>
    <div id="var-box">${optionStockHTML({ ...p, options: opts })}</div>
    <h3>Inventory</h3>
    <div class="au-2">
      <div class="field"><label>Availability</label>
        <select name="stockStatus" id="stock-status">
          <option value="in" ${inStock ? "selected" : ""}>In stock</option>
          <option value="out" ${inStock ? "" : "selected"}>Out of stock</option>
        </select>
      </div>
      <div class="field"><label>Quantity</label>
        <input name="stock" id="stock-qty" type="number" min="0" value="${p.id ? (p.stock ?? 0) : 24}" />
      </div>
    </div>
    <p class="admin-note">Choose <strong>Out of stock</strong> to stop sales. Choose <strong>In stock</strong> and set a quantity so customers can add it to cart.</p>
    <div class="field"><label>SKU</label><input name="sku" value="${JA.escape(p.sku || "")}" /></div>
    <div class="field"><label>Featured</label>
      <select name="featured"><option value="no">No</option><option value="yes" ${p.featured ? "selected" : ""}>Yes</option></select>
    </div>
    <h3>Customer reviews</h3>
    <p class="admin-note">Stars and notes show on the product page. Quantity stays in Admin only — shoppers never see the stock number.</p>
    <div id="rev-admin">${reviewsAdminHTML(p.id)}</div>
    <div class="au-2">
      <div class="field"><label>Customer name</label><input id="rev-name" maxlength="60" placeholder="e.g. Ada" /></div>
      <div class="field"><label>Stars</label>
        <select id="rev-stars"><option value="5">5</option><option value="4">4</option><option value="3">3</option><option value="2">2</option><option value="1">1</option></select>
      </div>
    </div>
    <div class="field"><label>Customer note</label><textarea id="rev-note" rows="2" maxlength="600" placeholder="Their comment"></textarea></div>
    <button type="button" class="au-link-btn" id="rev-add">+ Add review to this product</button>
    <button class="btn au-save" type="submit">${p.id ? "Save" : "Add a Product"}</button>
    ${p.id ? `<button type="button" class="au-del-prod" data-del="${JA.escape(p.id)}">Delete this product</button>` : ""}
  </form>`;
}
async function handleProductSubmit(e, existing) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const saveBtn = e.target.querySelector(".au-save");
  let rawImages = (window.__editImages || []).filter(Boolean);
  if (rawImages.some((s) => typeof s === "object") && window.JA_NET) {
    JA.toast("Finishing the photo upload…");
    const deadline = Date.now() + 8000;
    while (rawImages.some((s) => typeof s === "object") && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 250));
      rawImages = (window.__editImages || []).filter(Boolean);
    }
  }
  const stillUploading = rawImages.filter((s) => typeof s === "object");
  let images = rawImages.filter((s) => typeof s === "string" && s).slice(0, 20);
  if (!images.length && existing) {
    images = (existing.images && existing.images.length) ? existing.images.slice(0, 20) : (existing.image ? [existing.image] : []);
  }
  if (!images.length) {
    JA.toast(stillUploading.length ? "Your photo is still uploading. Wait a moment, then press Save again." : "Please add a photo from your gallery.");
    return;
  }
  const image = images[0] || "";
  if (!image) { JA.toast("Please upload a photo."); return; }
  const name = fd.get("name").trim();
  const id = fd.get("id") || ("jau-" + Date.now().toString(36));
  const num = (k) => { const v = fd.get(k); return v === "" || v == null ? null : Number(v); };
  const status = String(fd.get("stockStatus") || "in");
  let stock = num("stock");
  if (status === "out") stock = 0;
  else if (!(stock > 0)) stock = (existing && Number(existing.stock) > 0) ? Number(existing.stock) : 24;
  const options = collectOptions(e.target);
  const optionStock = {};
  let hasOptionStock = false;
  const firstVals = (options[0] && options[0].values) || [];
  e.target.querySelectorAll("[data-opt-stock]").forEach((inp) => {
    const v = inp.getAttribute("data-opt-stock");
    if (!firstVals.includes(v) || inp.value === "") return;
    optionStock[v] = Math.max(0, parseInt(inp.value, 10) || 0);
    hasOptionStock = true;
  });
  if (hasOptionStock) stock = Object.values(optionStock).reduce((n, q) => n + q, 0);
  const colorOpt = options.find((o) => /colou?r/i.test(o.title || ""));
  const priceNgn = num("priceNgn") || 0;
  const compareNgn = num("compareNgn");
  if (!(priceNgn > 0)) { JA.toast("Enter the ₦ price."); return; }
  const toCfa = JA.toCfa || ((n) => Math.round(Number(n || 0) * 0.44));
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving…"; }
  const savedCategory = String(fd.get("category") || "").trim();
  const res = await JA.upsertProduct({
      ...(existing || {}),
      id,
      sku: fd.get("sku") || existing?.sku || ("JAU-" + Date.now().toString(36).toUpperCase().slice(-6)),
      slug: existing?.slug || slugify(name) || id,
      name,
      category: savedCategory,
      priceNgn,
      compareNgn,
      priceCfa: toCfa(priceNgn),
      compareCfa: compareNgn > 0 ? toCfa(compareNgn) : null,
      image,
      images,
      description: fd.get("description"),
      stock,
      badge: fd.get("badge"),
      featured: fd.get("featured") === "yes",
      online: !!fd.get("online"),
      colors: colorOpt ? colorOpt.values : [],
      options,
      optionStock: hasOptionStock ? optionStock : (existing?.optionStock || {}),
      nameFr: String(fd.get("nameFr") || "").trim() || existing?.nameFr || "",
      descriptionFr: String(fd.get("descriptionFr") || "").trim() || existing?.descriptionFr || "",
  });
  if (window.__editReviews && JA.setReviews) JA.setReviews(id, window.__editReviews);
  if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = existing ? "Save" : "Add a Product"; }
  // Only a server-confirmed save leaves this editor. A queued retry or a
  // Supabase failure keeps the form open with the error, so the admin never
  // believes a product is live when PostgreSQL rejected it.
  if (res && res.ok === false) {
    JA.toast((res && res.error) || "Could not save the product. No changes are live.");
    return;
  }
  if (res && res.mirrored === false) {
    JA.toast("Saved on the server only — not yet on the cloud copy. Tap Retry now.");
  } else {
    JA.toast(status === "out" ? "Live now · Out of stock." : "Live on the store now · " + images.length + " photo(s).");
  }
  editingId = null;
  // KEEP same category after save — don't reset to all products
  if (savedCategory) {
    dashCat = savedCategory;
    prodCatSel = savedCategory;
    prodPage = 1;
  }
  paintDesk("products");
}

let prodPage = 1;
const PROD_PER_PAGE = 20;
let prodSearchQ = "";
let prodCatSel = "";
let orderPage = 1;
const ORDER_PAGE = 15;
let dashRange = 30;
let salesRange = "30";
let dashTimer = null;
let dashCat = "";

function getFilteredProducts() {
  const all = JA.products();
  const qEl = document.getElementById("prod-search");
  const cEl = document.getElementById("prod-cat");
  const q = String(prodSearchQ || qEl?.value || "").toLowerCase().trim();
  const catSel = String(prodCatSel || cEl?.value || "");
  const catFilter = dashCat || catSel;
  return all.filter((p) => {
    if (catFilter && p.category !== catFilter) return false;
    if (q) {
      const blob = (p.name + " " + (p.nameFr || "") + " " + (p.sku || "") + " " + p.category).toLowerCase();
      if (!blob.includes(q)) return false;
    }
    return true;
  });
}
function renderProdGrid() {
  const all = JA.products();
  const cats = JA.categories ? JA.categories() : JA.CATEGORIES;
  const catName = (id) => (cats.find((c) => c.id === id) || {}).name || id || "";
  const filtered = getFilteredProducts();
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PROD_PER_PAGE));
  if (prodPage > pages) prodPage = pages;
  if (prodPage < 1) prodPage = 1;
  const start = (prodPage - 1) * PROD_PER_PAGE;
  const slice = filtered.slice(start, start + PROD_PER_PAGE);
  const cards = slice.map((p) => {
    const ngnNow = Number(p.priceNgn) || 0;
    const ngnWas = Number(p.compareNgn) || 0;
    const ngn = ngnNow > 0 ? JA.money(ngnNow, "NGN") : "";
    const ngnStrike = ngnWas > ngnNow ? JA.money(ngnWas, "NGN") : "";
    const cfaNowN = ngnNow > 0 ? (JA.toCfa ? JA.toCfa(ngnNow) : Math.round(ngnNow * 0.44)) : (Number(p.priceCfa) || 0);
    const cfaNow = JA.money(cfaNowN, "CFA");
    const stockN = Number(p.stock) || 0;
    const pill = stockN <= 0 ? `<span class="adx-pill out">Out of stock</span>` : stockN <= 5 ? `<span class="adx-pill low">${stockN} left</span>` : `<span class="adx-pill in">${stockN} in stock</span>`;
    const rowq = JA.escape((p.name + " " + (p.nameFr || "") + " " + (p.sku || "") + " " + p.category).toLowerCase());
    return `<article class="adx-card" data-row="${rowq}" data-cat="${JA.escape(p.category || "")}" data-edit="${JA.escape(p.id)}" role="button" tabindex="0" aria-label="Edit ${JA.escape(p.name)}">
      <div class="adx-card-pic"><img src="${JA.asset(p.image)}" alt="" loading="lazy" />${p.badge ? `<span class="adx-ribbon">${JA.escape(p.badge)}</span>` : ""}${p.online === false ? `<span class="adx-hidden-tag">Hidden</span>` : ""}</div>
      <div class="adx-card-body"><strong>${JA.escape(p.name)}</strong><span class="adx-card-cat">${JA.escape(catName(p.category))}</span><span class="adx-card-price">${ngnStrike ? `<s>${ngnStrike}</s> ` : ""}${ngn || cfaNow}</span><span class="adx-card-cfa">${ngn ? cfaNow : ""}</span>${pill}</div>
      <button type="button" class="adx-card-del" data-del="${JA.escape(p.id)}" aria-label="Delete"><svg viewBox="0 0 24 24"><path d="M6 7h12M9 7V5h6v2m-8 0l1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
    </article>`;
  }).join("");
  const grid = document.getElementById("prod-grid");
  if (grid) grid.innerHTML = cards || `<p class="empty">No products match that search.</p>`;
  const count = document.getElementById("prod-count");
  if (count) count.textContent = total;
  const countAll = document.getElementById("prod-count-all");
  if (countAll) countAll.textContent = all.length;
  const none = document.getElementById("prod-none");
  if (none) none.hidden = total > 0;
  const pager = document.getElementById("prod-pager");
  if (pager) {
    if (pages <= 1) pager.innerHTML = "";
    else {
      const nums = [];
      for (let n = 1; n <= pages; n++) {
        if (n === 1 || n === pages || Math.abs(n - prodPage) <= 1) nums.push(n);
        else if (nums[nums.length - 1] !== "…") nums.push("…");
      }
      pager.innerHTML = `<div class="adx-pager"><button type="button" ${prodPage <= 1 ? "disabled" : ""} data-prod-goto="${prodPage - 1}">‹ Prev</button>${nums.map((n) => n === "…" ? `<span class="gap">…</span>` : `<button type="button" class="${n === prodPage ? "is-on" : ""}" data-prod-goto="${n}">${n}</button>`).join("")}<button type="button" ${prodPage >= pages ? "disabled" : ""} data-prod-goto="${prodPage + 1}">Next ›</button></div>`;
      pager.querySelectorAll("[data-prod-goto]").forEach((b) => {
        b.addEventListener("click", () => {
          const pg = Number(b.getAttribute("data-prod-goto"));
          if (!isNaN(pg)) { prodPage = pg; renderProdGrid(); bindProdGridEvents(); }
        });
      });
    }
  }
}
function bindProdGridEvents() {
  document.querySelectorAll("#prod-grid [data-edit]").forEach((b) => {
    const open = () => { editingId = b.dataset.edit; paintDesk("products"); window.scrollTo({ top: 0, behavior: "smooth" }); };
    b.onclick = open;
    b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
  });
  document.querySelectorAll("#prod-grid [data-del]").forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();
      if (confirm("Delete this product from the website? Customers will not see it.")) {
        const res = await JA.removeProduct(b.dataset.del);
        if (!res || res.ok === false) {
          JA.toast((res && res.error) || "Could not delete the product. No changes were made.");
          renderProdGrid(); bindProdGridEvents();   // the server list is still the truth
          return;
        }
        JA.toast("Deleted from the website.");
        editingId = null;
        renderProdGrid(); bindProdGridEvents();
      }
    };
  });
}
function productsTable() {
  const all = JA.products();
  const cats = JA.categories ? JA.categories() : JA.CATEGORIES;
  const catName = (id) => (cats.find((c) => c.id === id) || {}).name || id || "";
  const catOpts = cats.map((c) => `<option value="${JA.escape(c.id)}" ${ (dashCat === c.id || prodCatSel === c.id) ? "selected" : ""}>${JA.escape(c.name)}</option>`).join("");
  const backBtn = dashCat ? `<button type="button" class="btn btn-line" id="back-all-products">← All products</button>` : "";
  const filteredNote = dashCat ? ` · <strong>${JA.escape(catName(dashCat))}</strong>` : "";
  const qVal = JA.escape(prodSearchQ);
  return `<div class="adx-list-head">
      <button type="button" class="btn adx-add-btn" id="add-product">+ New Product</button>
      ${backBtn}
      <div class="adx-filters">
        <input id="prod-search" type="search" placeholder="Search products…" autocomplete="off" value="${qVal}" />
        <select id="prod-cat" aria-label="Filter by category"><option value="">All categories</option>${catOpts}</select>
      </div>
      <p class="adx-count"><span id="prod-count">${all.length}</span> of <span id="prod-count-all">${all.length}</span> products${filteredNote} · <button type="button" class="au-cats-link" data-tab="categories">Manage categories</button></p>
    </div>
    <div class="adx-grid" id="prod-grid"></div>
    <p class="empty" id="prod-none" hidden>No products match that search.</p>
    <div id="prod-pager"></div>`;
}
function applyProductFilter() {
  const qEl = document.getElementById("prod-search");
  const cEl = document.getElementById("prod-cat");
  prodSearchQ = String(qEl?.value || "").toLowerCase().trim();
  prodCatSel = String(cEl?.value || "");
  prodPage = 1;
  renderProdGrid(); bindProdGridEvents();
}

function esc(v) { return JA.escape(String(v == null ? "" : v)); }
function analyticsPanel() {
  return `
    <div class="an-top">
      <h3 class="admin-h" style="margin:0">Store insights</h3>
      <div class="an-range">
        ${[["1", "Today"], ["7", "7 days"], ["30", "30 days"], ["90", "90 days"]].map(([v, label]) => `<button type="button" class="an-rng${String(dashRange) === v ? " is-on" : ""}" data-range="${v}">${label}</button>`).join("")}
        <label class="an-rng an-rng-custom" title="Pick the number of days">Custom
          <input type="number" id="an-custom" min="1" max="400" value="${dashRange}" hidden />
        </label>
        <button type="button" class="an-rng" id="an-refresh">Refresh</button>
      </div>
    </div>
    <p class="admin-note">Counted on the server, so the numbers follow your store — not one phone or browser. Refreshes by itself: live feed every 10 seconds, everything else every 30.</p>
    <h3 class="admin-h">Visits</h3>
    <div class="adx-periods" id="an-periods"><div class="adx-period"><span>Today</span><b>…</b></div><div class="adx-period"><span>This week</span><b>…</b></div><div class="adx-period"><span>This month</span><b>…</b></div></div>
    <div class="stats" id="an-kpis"><div class="stat"><span class="kicker">Loading</span><b>…</b></div></div>
    <h3 class="admin-h">Live on the store right now <span class="adx-live-dot" aria-hidden="true"></span></h3>
    <div id="an-live-box" class="live-box">Loading live visitors…</div>
    <div id="an-feed" class="adx-feed"><p class="empty">Loading the live activity feed…</p></div>
    <h3 class="admin-h">Sales over time</h3><div class="an-chart" id="an-sales"><p class="empty">Loading…</p></div>
    <h3 class="admin-h">Visitors &amp; page views</h3><div class="an-chart" id="an-chart"><p class="empty">Loading…</p></div>
    <div class="adx-2col"><div><h3 class="admin-h">Top viewed products</h3><div id="an-products" class="empty">Loading…</div></div><div><h3 class="admin-h">Top selling products</h3><div id="an-sellers" class="empty">Loading…</div></div></div>
    <h3 class="admin-h">Most visited pages</h3><div id="an-pages" class="empty">Loading…</div>
    <h3 class="admin-h">Conversion</h3><div class="stats" id="an-conv"></div><div id="an-revenue"></div>
    <h3 class="admin-h">Visitor locations</h3><div id="an-loc" class="empty">Loading…</div>
    <h3 class="admin-h">Latest orders</h3><div id="an-orders" class="empty">Loading…</div>`;
}
function dayLabel(day) { const d = new Date(day + "T00:00:00Z"); return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function trafficChart(series) {
  const max = Math.max(1, ...series.map((d) => Math.max(d.views, d.visitors)));
  const step = Math.max(1, Math.ceil((series || []).length / 10));
  return `<div class="an-scroll"><div class="an-bars">${(series || []).map((d, i) => {
    const showLab = i % step === 0;
    return `<div class="an-col" title="${esc(d.day)} · ${d.views} view(s) · ${d.visitors} visitor(s)"><div class="an-bar-wrap"><div class="an-bar an-bar-views" style="height:${Math.round((d.views / max) * 120)}px"></div><div class="an-bar an-bar-visitors" style="height:${Math.round((d.visitors / max) * 120)}px"></div></div>${showLab ? `<span>${esc(dayLabel(d.day))}</span>` : ""}${showLab ? `<em>${d.views}</em>` : ""}</div>`;
  }).join("")}</div></div><p class="admin-note"><span class="an-key an-key-views"></span> Page views &nbsp; <span class="an-key an-key-visitors"></span> Unique visitors</p>`;
}
function tableHTML(headers, rows) { return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`; }
function salesChart(series) {
  if (!series || !series.length) return `<p class="empty">No sales in this period yet.</p>`;
  const max = Math.max(1, ...series.map((d) => d.revenue));
  const total = series.reduce((n, d) => n + (d.revenue || 0), 0);
  const orders = series.reduce((n, d) => n + (d.orders || 0), 0);
  const step = Math.max(1, Math.ceil(series.length / 10));
  return `<div class="an-scroll"><div class="an-bars">${series.map((d, i) => {
    const showLab = i % step === 0;
    return `<div class="an-col" title="${esc(d.day)} · ${d.orders} order(s) · ${esc(JA.money(d.revenue, "NGN"))}"><div class="an-bar-wrap"><div class="an-bar an-bar-sales" style="height:${Math.round((d.revenue / max) * 120)}px"></div></div>${showLab ? `<span>${esc(dayLabel(d.day))}</span>` : ""}${showLab ? `<em>${d.orders || ""}</em>` : ""}</div>`;
  }).join("")}</div></div><p class="admin-note">${orders} order(s) · ${esc(JA.money(total, "NGN"))} in this period.</p>`;
}
function timeAgo(iso) {
  if (!iso) return "";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  if (!t) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " day(s) ago";
}
function activityLine(a) {
  const what = a.productName || a.productId || "";
  const where = [a.city, a.country].filter(Boolean).join(", ");
  let icon = "👀", text = `Viewing <strong>${esc(what || a.page || "the store")}</strong>`;
  if (a.type === "cart") { icon = "🛒"; text = `Added <strong>${esc(what)}</strong> to their cart`; }
  else if (a.type === "checkout_start") { icon = "💳"; text = `Started checkout`; }
  else if (a.type === "purchase") { icon = "🎉"; text = `Placed an order${a.value ? " · <strong>" + esc(JA.money(a.value, a.currency || "NGN")) + "</strong>" : ""}`; }
  return `<li class="adx-feed-row is-${esc(a.type)}"><i>${icon}</i><div>${text}${where ? ` <span class="adx-feed-geo">· ${esc(where)}</span>` : ""}</div><em>${esc(timeAgo(a.at))}</em></li>`;
}
function renderLive(visitors, activity) {
  const liveBox = $("#an-live-box");
  if (liveBox) {
    liveBox.innerHTML = (visitors || []).length ? visitors.map((v) => `<div class="live-row"><i></i><span>${esc([v.city, v.country].filter(Boolean).join(", ") || "Visitor")}</span><em>on ${esc(v.page || v.path || "the store")}</em><small>${esc(timeAgo(v.at))}</small></div>`).join("") : `<p class="empty">Nobody is browsing right now.</p>`;
  }
  const feed = $("#an-feed");
  if (feed) {
    feed.innerHTML = (activity || []).length ? `<ul class="adx-feed-list">${activity.slice(0, 25).map(activityLine).join("")}</ul>` : `<p class="empty">No activity in the last hour.</p>`;
  }
  const pill = $("#live-pill");
  if (pill && visitors) pill.textContent = visitors.length;
}
async function fillLiveFeed() {
  try {
    const res = await fetch("api/admin/live", { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) return;
    const d = await res.json();
    renderLive(d.visitors || [], d.activity || []);
  } catch (e) {}
}
async function fillAnalytics() {
  const data = await JA.adminAnalytics(dashRange);
  if (!data) { const box = $("#panel-analytics"); if (box) box.innerHTML = `<p class="empty">Could not load analytics.</p>`; return; }
  if (!$("#an-kpis")) return;
  const t = data.totals || {}; const c = data.conversion || {}; const pr = data.periods || {};
  const periodCard = (label, p) => { p = p || {}; return `<div class="adx-period"><span>${label}</span><b>${p.visits || 0}</b><small>${p.pageViews || 0} page views · ${p.visitors || 0} visitor(s)</small><small class="adx-period-rev">${p.orders || 0} order(s)${p.revenue ? " · " + esc(JA.money(p.revenue, "NGN")) : ""}</small></div>`; };
  $("#an-periods").innerHTML = periodCard("Today", pr.today) + periodCard("This week", pr.week) + periodCard("This month", pr.month);
  $("#an-kpis").innerHTML = [["Live now", t.liveNow || 0, "on the site"], ["Unique visitors", t.uniqueVisitors || 0, `${t.newVisitors || 0} new`], ["Visits", t.visits || 0, "sessions"], ["Page views", t.pageViews || 0, ""], ["Orders", c.orders || 0, `${c.units || 0} items`], ["Revenue", (c.revenueByCurrency || []).map((r) => JA.money(r.value, r.currency)).join(" · ") || "—", `last ${dashRange} days`]].map(([k, v, s]) => `<div class="stat"><span class="kicker">${k}</span><b>${v}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");
  renderLive(data.live || [], data.activity || []);
  $("#an-sales").innerHTML = salesChart(data.sales || []);
  $("#an-chart").innerHTML = trafficChart(data.series || []);
  const pages = data.topPages || [];
  $("#an-pages").innerHTML = pages.length ? tableHTML(["Page", "Views", "Visitors"], pages.map((p) => `<tr><td>${esc(p.path)}</td><td>${p.views}</td><td>${p.visitors}</td></tr>`).join("")) : `<p class="empty">No page views yet.</p>`;
  const prods = data.topProducts || [];
  $("#an-products").innerHTML = prods.length ? tableHTML(["Product", "Views", "In carts"], prods.slice().sort((a, b) => (b.views || 0) - (a.views || 0)).slice(0, 8).map((p) => `<tr><td>${esc(p.name || p.productId)}</td><td>${p.views || 0}</td><td>${p.carts || 0}</td></tr>`).join("")) : `<p class="empty">No product activity yet.</p>`;
  const sellers = prods.filter((p) => (p.purchases || 0) > 0 || (p.carts || 0) > 0).sort((a, b) => (b.purchases || 0) - (a.purchases || 0) || (b.carts || 0) - (a.carts || 0)).slice(0, 8);
  $("#an-sellers").innerHTML = sellers.length ? tableHTML(["Product", "Sold", "In carts"], sellers.map((p) => `<tr><td>${esc(p.name || p.productId)}</td><td>${p.purchases || 0}</td><td>${p.carts || 0}</td></tr>`).join("")) : `<p class="empty">No sales recorded yet.</p>`;
  $("#an-conv").innerHTML = [["Total sales", (c.revenueByCurrency || []).map((r) => JA.money(r.value, r.currency)).join(" · ") || "—", `${c.orders || 0} orders`], ["Average order value", c.averageOrderValue ? JA.money(c.averageOrderValue, (c.revenueByCurrency || [{}])[0].currency) : "—", ""], ["Checkout attempts", c.checkoutAttempts || 0, `${c.checkoutCompletionRate || 0}% completed`], ["Added to cart", c.cartAdds || 0, `${c.productViews || 0} product views`], ["Visit → order", `${c.visitToOrderRate || 0}%`, "conversion rate"]].map(([k, v, s]) => `<div class="stat"><span class="kicker">${k}</span><b>${v}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");
  const status = c.statusBreakdown || [];
  $("#an-revenue").innerHTML = status.length ? `<p class="admin-note">${status.map((s) => `${esc(s.status)}: ${s.n}`).join(" · ")}</p>` : "";
  const locs = data.locations || [];
  $("#an-loc").innerHTML = locs.length ? tableHTML(["Location", "Visitors"], locs.map((l) => `<tr><td>${esc([l.city, l.country].filter(Boolean).join(", "))}</td><td>${l.visitors}</td></tr>`).join("")) : `<p class="empty">No locations recorded yet.</p>`;
  const orders = data.recentOrders || [];
  $("#an-orders").innerHTML = orders.length ? tableHTML(["Order", "Customer", "Total", "Status"], orders.map((o) => `<tr><td>${esc(o.id)}</td><td>${esc(o.customer_name || "")}</td><td>${esc(JA.money(o.total, o.currency))}</td><td><span class="status-pill ${esc(o.status || "pending")}">${esc(orderStatusLabel(o.status))}</span></td></tr>`).join("")) : `<p class="empty">No orders yet.</p>`;
  document.querySelectorAll("[data-range]").forEach((b) => { b.onclick = () => { dashRange = Number(b.dataset.range); paintDesk("analytics"); }; });
  const custom = $("#an-custom"); const customLabel = document.querySelector(".an-rng-custom");
  if (customLabel) customLabel.addEventListener("click", () => { if (custom) { custom.hidden = false; custom.focus(); custom.select(); } });
  if (custom) custom.addEventListener("change", () => { const v = Math.max(1, Math.min(400, Number(custom.value) || 30)); dashRange = v; custom.value = v; paintDesk("analytics"); });
  const ref = $("#an-refresh"); if (ref) ref.onclick = () => { paintDesk("analytics"); JA.toast("Refreshed."); };
}
let liveTimer = null;
function startDashTimer() {
  clearInterval(dashTimer);
  dashTimer = setInterval(() => { if (document.body.dataset.page !== "admin") return; const on = document.querySelector("#panel-analytics.is-on"); if (on && !document.hidden) fillAnalytics(); }, 30000);
  clearInterval(liveTimer);
  liveTimer = setInterval(() => { if (document.body.dataset.page !== "admin") return; const on = document.querySelector("#panel-analytics.is-on"); if (on && !document.hidden && $("#an-feed")) fillLiveFeed(); }, 10000);
}
let serverOrders = [];
function orderStatusLabel(s) { return s === "confirmed" ? "Confirmed" : s === "declined" ? "Declined" : s === "past" ? "Past" : "Pending"; }
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-receipt-open]");
  if (btn) openReceiptModal(btn.getAttribute("data-receipt-open"), btn.getAttribute("data-receipt-label") || "Payment receipt", btn.getAttribute("data-receipt-name") || "receipt");
});
function fileTypeOf(url) { const u = String(url || "").split("?")[0].toLowerCase(); if (/\.pdf$/.test(u)) return "pdf"; if (/\.(png|jpe?g|webp|gif|avif|heic)$/.test(u)) return "image"; return "other"; }
function closeReceiptModal() { const mod = document.getElementById("receipt-modal"); if (mod) mod.classList.remove("is-open"); }
function openReceiptModal(url, label, name) {
  const kind = fileTypeOf(url); const title = (label || "Receipt"); const fname = esc(name || String(url).split("/").pop() || "receipt");
  let mod = document.getElementById("receipt-modal");
  if (!mod) {
    mod = document.createElement("div"); mod.id = "receipt-modal"; mod.className = "receipt-modal";
    mod.innerHTML = `<div class="receipt-modal-card"><button type="button" class="receipt-modal-close" aria-label="Close">×</button><div class="receipt-modal-head"></div><div class="receipt-modal-body"></div><div class="receipt-modal-actions"></div></div>`;
    document.body.appendChild(mod);
    mod.addEventListener("click", (e) => { if (e.target === mod || e.target.closest(".receipt-modal-close")) closeReceiptModal(); });
  }
  mod.classList.add("is-open");
  const head = mod.querySelector(".receipt-modal-head"); const body = mod.querySelector(".receipt-modal-body"); const actions = mod.querySelector(".receipt-modal-actions");
  head.textContent = title;
  actions.innerHTML = `<a class="btn btn-line" href="${esc(url)}" target="_blank" rel="noopener">Open full size</a><a class="btn btn-line" href="${esc(url)}" download="${fname}">Download ${kind === "pdf" ? "PDF" : "file"}</a>`;
  if (kind === "image") { body.innerHTML = `<img class="proof-preview" src="${esc(url)}" alt="${esc(title)}" />`; return; }
  body.innerHTML = `<p class="empty">Opening the receipt…</p>`;
  fetch(url, { credentials: "same-origin" }).then((r) => { if (!r.ok) throw new Error("bad"); return r.blob(); }).then((blob) => {
    const objectUrl = URL.createObjectURL(blob);
    body.innerHTML = `<iframe class="proof-frame receipt-modal-frame" src="${objectUrl}" title="${esc(title)}"></iframe>`;
  }).catch(() => { body.innerHTML = `<p class="empty">This file could not be opened here. Use the download link below.</p>`; });
}
function receiptViewer(url, label, name) {
  if (!url) return `<p class="empty">No receipt attached.</p>`;
  const kind = fileTypeOf(url); const safe = esc(url); const title = esc(label || "Receipt"); const fname = esc(name || String(url).split("/").pop() || "receipt");
  const body = kind === "image" ? `<img class="proof-preview" src="${safe}" alt="${title}" loading="lazy" />` : kind === "pdf" ? `<button type="button" class="btn btn-line" data-receipt-open="${safe}" data-receipt-label="${title}" data-receipt-name="${fname}">View the PDF here</button>` : `<button type="button" class="btn btn-line" data-receipt-open="${safe}" data-receipt-label="${title}" data-receipt-name="${fname}">View this file</button>`;
  return `<p class="proof-label">${title} — the original file, opened right here</p><div class="proof-frame-wrap">${body}</div><p class="proof-actions"><a class="btn btn-line" href="${safe}" target="_blank" rel="noopener">Open full size</a><a class="btn btn-line" href="${safe}" download="${fname}">Download ${esc(kind === "pdf" ? "PDF" : "file")}</a></p>`;
}
function orderActionsHTML(o) {
  const s = o.status || "pending";
  const del = `<button type="button" class="btn btn-line btn-danger" data-del-order="${esc(o.id)}">Delete</button>`;
  if (s === "pending") return `<button class="btn" data-confirm="${esc(o.id)}">Confirm payment</button><button class="btn btn-line" data-decline="${esc(o.id)}">Decline</button>${del}`;
  if (s === "confirmed") return `<button class="btn btn-line" data-reopen="${esc(o.id)}">Back to pending</button>${del}`;
  return `<button class="btn btn-line" data-reopen="${esc(o.id)}">Reopen (back to pending)</button>${del}`;
}
function orderCardHTML(o) {
  const c = o.customer || {}; const shot = o.proofUrl || (JA.getProof && JA.getProof(o.id, o.proof)) || ""; const when = o.at ? new Date(o.at).toLocaleString() : ""; const s = o.status || "pending"; const nItems = (o.items || []).reduce((n, i) => n + (Number(i.qty) || 0), 0);
  return `<details class="adx-order" data-order="${esc(o.id)}"><summary class="adx-order-row"><span class="adx-order-id">${esc(o.id)}</span><span class="adx-order-who"><strong>${esc(c.name || "Customer")}</strong><small>${esc(when)} · ${nItems} item(s)</small></span><span class="adx-order-total">${esc(JA.money(o.total, o.currency))}</span><span class="status-pill ${esc(s)}">${esc(orderStatusLabel(s))}</span></summary><div class="adx-order-body"><div class="order-card-top"><div><p><strong>${esc(c.name || "Customer")}</strong></p><p>${esc(c.email || "")}</p><p>${esc(c.phone || "")} · ${esc([c.city, c.zone].filter(Boolean).join(" / "))}</p><p>${esc([c.address, c.country].filter(Boolean).join(", "))}</p>${c.note ? `<p class="order-note"><em>Note:</em> ${esc(c.note)}</p>` : ""}<p>${esc(when)}</p></div><div><p style="margin-top:8px"><strong>${esc(JA.money(o.total, o.currency))}</strong> · ${o.currency === "NGN" ? "Naira" : "CFA"}</p><p class="admin-note">Pay by ${esc(o.payment || o.currency || "")}</p></div></div><ul class="order-items">${(o.items || []).map((i) => `<li>${i.qty}× ${esc(i.name)}${i.color ? " · " + esc(i.color) : ""}</li>`).join("")}</ul>${shot ? receiptViewer(shot, `Payment receipt for ${o.id}`, `${o.id}-receipt`) : `<p class="empty">No receipt attached.</p>`}<div class="order-actions">${orderActionsHTML(o)}</div></div></details>`;
}
let orderFilter = "all";
function ordersPanel() {
  return `<div class="adx-order-filters" id="order-filters">${["all", "pending", "past", "confirmed", "declined"].map((s) => `<button type="button" class="an-rng${orderFilter === s ? " is-on" : ""}" data-ofilter="${s}">${s === "all" ? "All" : orderStatusLabel(s)}</button>`).join("")}<a class="au-link-btn" href="api/admin/orders.csv" style="margin-left:auto">Download CSV</a></div><p class="admin-note">Tap an order to see everything — customer details, items, the payment receipt and the action buttons. Every checkout is kept forever.</p><div id="orders-box"><p class="empty">Loading orders…</p></div><div id="orders-pager"></div><h3 class="admin-h">Receipts customers uploaded</h3><div id="proofs-box"><p class="empty">Loading receipts…</p></div>`;
}
const PROOF_PAGE = 20;
let proofsShown = PROOF_PAGE;
async function fillProofs() {
  const box = $("#proofs-box"); if (!box) return; proofsShown = Math.max(proofsShown, PROOF_PAGE); let rows = [];
  try { const res = await fetch("api/admin/payment-proofs", { credentials: "same-origin", cache: "no-store" }); if (res.ok) rows = ((await res.json()) || {}).proofs || []; } catch (e) { rows = []; }
  if (!rows.length) { box.innerHTML = `<p class="empty">No receipts uploaded yet.</p>`; return; }
  const shown = rows.slice(0, proofsShown);
  box.innerHTML = `<div class="table-wrap"><table class="proofs-table"><thead><tr><th>Sent</th><th>Order</th><th>Customer</th><th>Contact</th><th>Method</th><th>Receipt</th><th>Manage</th></tr></thead><tbody>${shown.map((p) => `<tr><td data-label="Sent"><span class="cell-nowrap">${esc((p.at || "").replace("T", " ").slice(0, 16))}</span></td><td data-label="Order"><span class="cell-nowrap">${esc(p.order_id || "")}</span></td><td data-label="Customer">${esc(p.name || "")}<br /><small>${esc(p.items || "")}</small></td><td data-label="Contact"><span class="cell-nowrap">${esc(p.phone || "")}</span><br /><small>${esc(p.email || "")}</small></td><td data-label="Method">${esc(p.method || "")}</td><td data-label="Receipt">${p.file_url ? (fileTypeOf(p.file_url) === "image" ? `<a href="${esc(p.file_url)}" target="_blank" rel="noopener"><img class="proof-thumb" src="${esc(p.file_url)}" alt="Receipt" loading="lazy" /></a>` : `<button type="button" class="btn btn-line" data-receipt-open="${esc(p.file_url)}" data-receipt-label="Receipt for ${esc(p.order_id || "")}" data-receipt-name="${esc(p.file_name || "receipt")}">View ${esc((p.file_name || "").split(".").pop().toUpperCase())}</button>`) + `<br /><a class="btn btn-line" href="${esc(p.file_url)}" download="${esc(p.file_name || "receipt")}">Download</a><small>${Math.max(1, Math.round((p.file_size || 0) / 1024))} KB</small>` : "—"}</td><td data-label="Manage"><button type="button" class="btn btn-line proof-del" data-del-proof="${esc(String(p.id))}">Delete</button></td></tr>`).join("")}</tbody></table></div><p class="admin-note">The original file is stored in the configured receipt storage and is available here.</p>` + (rows.length > proofsShown ? `<p class="admin-more"><button type="button" class="btn btn-line" id="proofs-more">Show ${Math.min(PROOF_PAGE, rows.length - proofsShown)} more of ${rows.length}</button></p>` : `<p class="admin-note">Showing all ${rows.length} receipts.</p>`);
  const more = $("#proofs-more"); if (more) more.onclick = () => { proofsShown += PROOF_PAGE; fillProofs(); };
  box.querySelectorAll("[data-del-proof]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.delProof;
      if (!confirm("Delete this payment receipt permanently? The uploaded file is removed too.")) return;
      b.disabled = true; let res = null;
      try { res = await window.JA_NET.api("api/admin/payment-proofs/" + encodeURIComponent(id), { method: "DELETE" }); } catch (e) { res = null; }
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not delete that receipt."); b.disabled = false; return; }
      JA.toast("Receipt deleted."); fillProofs();
    };
  });
}
function renderOrderPage() {
  const box = $("#orders-box"); if (!box) return;
  const allFiltered = orderFilter === "all" ? serverOrders : orderFilter === "past" ? serverOrders.filter((o) => (o.status || "pending") !== "pending") : serverOrders.filter((o) => (o.status || "pending") === orderFilter);
  const total = allFiltered.length;
  const pages = Math.max(1, Math.ceil(total / ORDER_PAGE));
  if (orderPage > pages) orderPage = pages;
  if (orderPage < 1) orderPage = 1;
  const start = (orderPage - 1) * ORDER_PAGE;
  const slice = allFiltered.slice(start, start + ORDER_PAGE);
  if (!total) {
    box.innerHTML = `<p class="empty">${orderFilter === "all" ? "No orders yet." : "No " + orderStatusLabel(orderFilter).toLowerCase() + " orders."}</p>`;
    const pg = document.getElementById("orders-pager"); if (pg) pg.innerHTML = "";
    return;
  }
  box.innerHTML = slice.map(orderCardHTML).join("");
  const pager = document.getElementById("orders-pager");
  if (pager) {
    if (pages <= 1) pager.innerHTML = `<p class="admin-note">Showing all ${total} order(s).</p>`;
    else {
      const nums = [];
      for (let n = 1; n <= pages; n++) { if (n === 1 || n === pages || Math.abs(n - orderPage) <= 1) nums.push(n); else if (nums[nums.length - 1] !== "…") nums.push("…"); }
      pager.innerHTML = `<div class="adx-pager"><button type="button" ${orderPage <= 1 ? "disabled" : ""} data-order-goto="${orderPage - 1}">‹ Prev</button>${nums.map((n) => n === "…" ? `<span class="gap">…</span>` : `<button type="button" class="${n === orderPage ? "is-on" : ""}" data-order-goto="${n}">${n}</button>`).join("")}<button type="button" ${orderPage >= pages ? "disabled" : ""} data-order-goto="${orderPage + 1}">Next ›</button></div><p class="admin-note">Page ${orderPage} of ${pages} · ${total} order(s) total.</p>`;
      pager.querySelectorAll("[data-order-goto]").forEach((b) => {
        b.addEventListener("click", () => { const pg = Number(b.getAttribute("data-order-goto")); if (!isNaN(pg)) { orderPage = pg; renderOrderPage(); } });
      });
    }
  }
  bindOrderButtons();
}
async function fillOrders() {
  const box = $("#orders-box"); if (!box) return;
  serverOrders = await JA.adminOrders({ limit: 500 });
  orderPage = 1;
  document.querySelectorAll("[data-ofilter]").forEach((b) => {
    b.onclick = () => {
      orderFilter = b.dataset.ofilter;
      orderPage = 1;
      document.querySelectorAll("[data-ofilter]").forEach((x) => x.classList.toggle("is-on", x === b));
      renderOrderPage();
    };
  });
  renderOrderPage();
}
function bindOrderButtons() {
  const box = $("#orders-box"); if (!box) return;
  box.querySelectorAll("[data-confirm]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.confirm; b.disabled = true;
      const res = await JA.setOrderStatus(id, "confirmed");
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not update the order."); b.disabled = false; return; }
      const o = serverOrders.find((x) => x.id === id);
      JA.toast("Confirmed · " + id); fillOrders();
    };
  });
  box.querySelectorAll("[data-decline]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const res = await JA.setOrderStatus(b.dataset.decline, "declined");
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not update the order."); b.disabled = false; return; }
      JA.toast("Declined · " + b.dataset.decline); fillOrders();
    };
  });
  box.querySelectorAll("[data-reopen]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const res = await JA.setOrderStatus(b.dataset.reopen, "pending");
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not update the order."); b.disabled = false; return; }
      JA.toast("Reopened · " + b.dataset.reopen); fillOrders();
    };
  });
  box.querySelectorAll("[data-del-order]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.delOrder;
      if (!confirm("Delete order " + id + " permanently? Its payment receipt is removed too.")) return;
      b.disabled = true;
      const res = await JA.deleteOrder(id);
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not delete the order."); b.disabled = false; return; }
      JA.toast("Deleted " + id + "."); serverOrders = serverOrders.filter((o) => o.id !== id); renderOrderPage(); fillProofs();
    };
  });
}
function accountPanel() {
  return `<div class="admin-card"><h3 class="admin-h">Your account</h3><p class="admin-note">Signed in as <strong id="acct-email">…</strong>. You sign in with <strong>ADMIN_MASTER_PASSWORD</strong> — that is your main admin password. <strong>ADMIN_BOOTSTRAP_PASSWORD</strong> is the backup one, and it still works if the master password is ever unset. Both are managed in Render (Environment → Environment Variables), not in this portal: change one there and the new password works at your next sign-in.</p></div><details class="adx-advanced" style="margin-top:22px"><summary class="admin-h">Advanced settings</summary><div class="admin-card"><h3 class="admin-h">Connection &amp; sync</h3><p class="admin-note" id="sync-note">Checking for unsaved changes…</p><div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn btn-line" id="retry-sync">Retry now</button><button class="btn btn-line" id="reload-cat">Reload catalogue</button><button class="btn" id="sync-github" hidden>Sync to GitHub</button></div><div id="sync-status" class="admin-note" style="margin-top:12px"></div><p class="admin-note" style="margin-top:12px">Everything you save goes straight to the live store. If your Wi-Fi drops, the change waits on this device and sends itself as soon as you are back online.</p></div></details>`;
}
function bindAccount() {
  const email = $("#acct-email");
  if (email) JA.adminSession(true).then((e) => { email.textContent = e || "unknown"; });
  const note = $("#sync-note");
  const paintSync = () => { if (!note) return; const n = JA.syncPending ? JA.syncPending() : 0; note.textContent = n ? `${n} change(s) are waiting for a connection.` : "Everything you save goes straight to the live store."; };
  paintSync(); if (window.JA_NET) window.JA_NET.onStatus(paintSync);
  // Retry Now: flush the outbox AND re-POST stranded KEYS.custom
  // (jaura_custom_products) so a save that never left this phone still ships.
  $("#retry-sync")?.addEventListener("click", async () => {
    JA.toast("Retrying…");
    if (window.JA_NET) window.JA_NET.flush();
    if (JA.retryStrandedProducts) await JA.retryStrandedProducts();
    paintSync();
  });
  $("#reload-cat")?.addEventListener("click", async () => { if (JA.reloadCatalog) { JA.toast("Reloading…"); await JA.reloadCatalog(); paintDesk("products"); } });
  const statusBox = $("#sync-status");
  async function refreshSyncStatus() {
    if (!statusBox) return;
    try {
      const res = await fetch("api/admin/sync/status", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) {
        statusBox.textContent = "Could not read the sync status.";
        const ghBtn = $("#sync-github");
        if (ghBtn) ghBtn.hidden = true;
        return;
      }
      const d = await res.json();
      const health = d.supabaseHealth || "";
      const label = health === "ok" ? "OK"
        : health === "unreachable" ? "UNREACHABLE"
        : "not configured";
      // Only offer "Sync to GitHub" when a token is actually configured —
      // the button used to sit there permanently and always fail. Hidden on
      // error/timeout too (d.gitToken must be positively true).
      const ghBtn = $("#sync-github");
      if (ghBtn) ghBtn.hidden = !d.gitToken;
      const bits = [];
      bits.push(health === "ok"
        ? "Your store database is connected."
        : health === "unreachable"
          ? "Your store database cannot be reached right now."
          : "No store database is configured.");
      if (d.gitToken) {
        bits.push(d.onWrite
          ? "A backup copy of every change is also saved to GitHub automatically."
          : "GitHub backup is available — use the Sync button.");
      }
      statusBox.innerHTML = bits.map((b) => JA.escape(b)).join("<br>");
    } catch (e) {
      // Hide the button on a status-call failure too: we cannot confirm a token.
      const ghBtn = $("#sync-github");
      if (ghBtn) ghBtn.hidden = true;
      statusBox.textContent = "Sync status unavailable.";
    }
  }
  refreshSyncStatus();
  $("#sync-github")?.addEventListener("click", async () => {
    const b = $("#sync-github"); if (b) { b.disabled = true; b.textContent = "Syncing…"; }
    try {
      const res = await fetch("api/admin/sync/repo", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "X-CSRF-Token": JA.csrf() || "" }, });
      const d = await res.json();
      // This button is a CODE backup only — shop data (orders, products,
      // settings) lives in Supabase and is never touched by a GitHub sync.
      if (!res.ok || d.ok === false) {
        const msg = ((d && d.error) || "Code backup failed.") +
          " This only backs up the site code — your shop data is unaffected.";
        JA.toast(msg);
        if (statusBox) statusBox.textContent = msg;
        return;
      }
      JA.toast(d.pushed ? "Synced, committed and pushed to GitHub." : "Committed to the repo." + (d.note ? " " + d.note : ""));
      if (statusBox) statusBox.textContent = [d.committed ? "Committed to the repository." : "Nothing to sync.", d.pushed ? "Pushed to " + (d.branch || "main") + "." : (d.note || "Not pushed — no GitHub token configured.")].filter(Boolean).join(" ");
      refreshSyncStatus();
    } catch (e) {
      const msg = "Code backup failed. This only backs up the site code — your shop data is unaffected.";
      JA.toast(msg);
      if (statusBox) statusBox.textContent = msg;
    }
    finally { if (b) { b.disabled = false; b.textContent = "Sync to GitHub"; } }
  });
}
function marketingPanel() {
  return `<div class="admin-card" id="mk-settings-card"><h3 class="admin-h">Referral settings</h3><p class="admin-note">Loading…</p></div><div class="admin-card" id="mk-coupons-card"><h3 class="admin-h">Coupons</h3><p class="admin-note">Loading…</p></div><div class="admin-card" id="mk-referrals-card"><h3 class="admin-h">Referral codes</h3><p class="admin-note">Loading…</p></div><div class="admin-card" id="mk-backup-card"><h3 class="admin-h">Backups</h3><p class="admin-note">Product data is backed up to GitHub automatically every night at midnight. Customer orders stay on the server. You can also run a backup right now.</p><button type="button" class="btn" id="mk-backup-now">Back up now</button><p class="admin-note" id="mk-backup-out" hidden></p></div>`;
}
async function fillMarketing() {
  const api = (path, opts) => window.JA_NET.api(path, opts);
  const num = (v) => esc(String(v == null ? "" : v));
  try {
    const d = await api("api/admin/growth/settings"); const s = d.settings || {}; const card = $("#mk-settings-card");
    if (card) {
      card.innerHTML = `<h3 class="admin-h">Referral settings</h3><form id="mk-set-form" class="admin-form"><label class="mk-toggle"><input type="checkbox" name="referralEnabled" ${s.referralEnabled ? "checked" : ""} /> Referral programme ON — qualifying orders get a shareable code</label><div class="admin-grid"><label>Minimum spend for a code (₦)<input name="minSpendNgn" type="number" min="0" value="${num(s.minSpendNgn)}" /></label><label>NGN → CFA rate (1 ₦ = ? F CFA)<input name="cfaRate" type="number" min="0.01" max="100" step="0.0001" value="${num(s.cfaRate)}" /></label><label>Friend's discount % (code used at checkout)<input name="buyerPercent" type="number" min="1" max="50" value="${num(s.buyerPercent)}" /></label><label>Referrer reward coupon % (max 10)<input name="referrerPercent" type="number" min="1" max="10" value="${num(s.referrerPercent)}" /></label><label>Orders needed for the reward<input name="milestone" type="number" min="1" max="100" value="${num(s.milestone)}" /></label></div><p class="admin-note" id="mk-cfa-note"></p></label><button class="btn" type="submit">Save settings</button></form>`;
      const cfaNote = () => { const f = $("#mk-set-form"); const note = $("#mk-cfa-note"); if (!f || !note) return; const spend = Number(f.minSpendNgn.value) || 0; const rate = Number(f.cfaRate.value) || 0; note.textContent = rate > 0 ? `CFA shoppers qualify from ${Math.round(spend * rate).toLocaleString()} F CFA (₦${spend.toLocaleString()} × ${rate}).` : ""; };
      cfaNote(); ["minSpendNgn", "cfaRate"].forEach((n) => { const el = $("#mk-set-form") && $("#mk-set-form")[n]; if (el) el.addEventListener("input", cfaNote); });
      $("#mk-set-form").onsubmit = async (e) => {
        e.preventDefault(); const fd = new FormData(e.target);
        const patch = { referralEnabled: e.target.referralEnabled.checked, minSpendNgn: Number(fd.get("minSpendNgn")), cfaRate: Number(fd.get("cfaRate")), buyerPercent: Number(fd.get("buyerPercent")), referrerPercent: Number(fd.get("referrerPercent")), milestone: Number(fd.get("milestone")) , };
        try { await api("api/admin/growth/settings", { method: "POST", json: patch }); JA.toast("Marketing settings saved."); fillMarketing(); } catch (err) { JA.toast(err.message || "Could not save."); }
      };
    }
  } catch (e) {}
  try {
    const d = await api("api/admin/coupons"); const card = $("#mk-coupons-card");
    if (card) {
      const rows = (d.coupons || []).map((c) => `<tr><td><strong>${esc(c.code)}</strong>${c.kind === "reward" ? ' <small>(auto reward)</small>' : ""}</td><td>${num(c.percent)}%</td><td>${num(c.uses)}${c.max_uses ? " / " + num(c.max_uses) : ""}</td><td>${c.expires_at ? esc(c.expires_at) : "—"}</td><td>${c.active ? "Active" : "Off"}</td><td class="mk-row-actions"><button type="button" class="btn btn-line" data-mk-cp-toggle="${esc(c.code)}" data-on="${c.active ? 1 : 0}">${c.active ? "Turn off" : "Turn on"}</button><button type="button" class="btn btn-line" data-mk-cp-del="${esc(c.code)}">Delete</button></td></tr>`).join("");
      card.innerHTML = `<h3 class="admin-h">Coupons</h3><form id="mk-cp-form" class="mk-inline-form"><input name="code" placeholder="Code (blank = auto)" maxlength="24" /><input name="percent" type="number" placeholder="%" min="1" max="90" required style="width:80px" /><input name="maxUses" type="number" placeholder="Max uses" min="1" style="width:110px" /><input name="expiresAt" type="date" title="Expiry date (optional)" /><input name="note" placeholder="Note (optional)" maxlength="200" /><button class="btn" type="submit">Create coupon</button></form>${rows ? `<table class="mk-table"><thead><tr><th>Code</th><th>%</th><th>Uses</th><th>Expires</th><th>State</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="empty">No coupons yet.</p>`}`;
      $("#mk-cp-form").onsubmit = async (e) => {
        e.preventDefault(); const fd = new FormData(e.target); const body = { code: fd.get("code"), percent: Number(fd.get("percent")), note: fd.get("note") }; if (fd.get("maxUses")) body.maxUses = Number(fd.get("maxUses")); if (fd.get("expiresAt")) body.expiresAt = fd.get("expiresAt") + " 23:59:59";
        try { const res = await api("api/admin/coupons", { method: "POST", json: body }); JA.toast("Coupon created: " + res.code); fillMarketing(); } catch (err) { JA.toast(err.message || "Could not create the coupon."); }
      };
      card.querySelectorAll("[data-mk-cp-toggle]").forEach((b) => { b.onclick = async () => { try { await api("api/admin/coupons/" + encodeURIComponent(b.dataset.mkCpToggle), { method: "PATCH", json: { active: b.dataset.on !== "1" } }); fillMarketing(); } catch (err) { JA.toast(err.message || "Could not update."); } }; });
      card.querySelectorAll("[data-mk-cp-del]").forEach((b) => { b.onclick = async () => { if (!confirm("Delete coupon " + b.dataset.mkCpDel + "?")) return; try { await api("api/admin/coupons/" + encodeURIComponent(b.dataset.mkCpDel), { method: "DELETE" }); fillMarketing(); } catch (err) { JA.toast(err.message || "Could not delete."); } }; });
    }
  } catch (e) {}
  try {
    const d = await api("api/admin/referrals"); const card = $("#mk-referrals-card");
    if (card) {
      const rows = (d.referrals || []).map((r) => `<tr><td><strong>${esc(r.code)}</strong></td><td>${esc(r.name || "")}<br /><small>${esc(r.email)}</small></td><td>${num(r.uses)}</td><td>${r.reward_issued ? "Rewarded — " + esc(r.reward_coupon || "") : "Not yet"}</td><td><small>${esc(r.created_at || "")}</small></td></tr>`).join("");
      card.innerHTML = `<h3 class="admin-h">Referral codes</h3><p class="admin-note">Codes are minted automatically for qualifying orders. When a code reaches the milestone, the referrer's reward coupon is issued and shown here.</p>${rows ? `<table class="mk-table"><thead><tr><th>Code</th><th>Customer</th><th>Uses</th><th>Reward</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="empty">No referral codes yet.</p>`}`;
    }
  } catch (e) {}
  const bk = $("#mk-backup-now");
  if (bk && !bk.dataset.bound) {
    bk.dataset.bound = "1";
    bk.onclick = async () => {
      bk.disabled = true; bk.textContent = "Backing up…"; const out = $("#mk-backup-out");
      try { const res = await api("api/admin/backup", { method: "POST", json: {} }); if (out) { out.hidden = false; out.textContent = res.ok ? "Backup completed." : (res.error || res.note || "Backup finished with warnings."); } } catch (err) { if (out) { out.hidden = false; out.textContent = err.message || "Backup failed."; } }
      bk.disabled = false; bk.textContent = "Back up now";
    };
  }
}
function salesPanel() {
  const opts = [["7", "7 days"], ["30", "30 days"], ["90", "90 days"], ["all", "All"]];
  return `
    <div class="an-top">
      <h3 class="admin-h" style="margin:0">Confirmed sales</h3>
      <div class="an-range">
        ${opts.map(([v, label]) => `<button type="button" class="an-rng${String(salesRange) === v ? " is-on" : ""}" data-sales-range="${v}">${label}</button>`).join("")}
        <a class="an-rng" id="sales-csv" href="api/admin/sales.csv?days=${encodeURIComponent(salesRange)}">Export CSV</a>
      </div>
    </div>
    <p class="admin-note">Only confirmed orders count as sales. Pending orders are listed separately below.</p>
    <div class="stats" id="sales-kpis"><div class="stat"><span class="kicker">Loading</span><b>…</b></div></div>
    <p class="admin-note" id="sales-pending">Loading…</p>
    <h3 class="admin-h">Top products</h3><div id="sales-top" class="empty">Loading…</div>`;
}
async function fillSales() {
  const box = $("#sales-kpis");
  if (!box) return;
  let d = null;
  try {
    const res = await fetch("api/admin/sales?days=" + encodeURIComponent(salesRange), { credentials: "same-origin", cache: "no-store" });
    if (res.ok) d = await res.json();
  } catch (e) { d = null; }
  if (!d || d.ok === false) {
    box.innerHTML = `<p class="empty">Could not load sales.</p>`;
    return;
  }
  const rev = (d.revenueByCurrency || []).map((r) => JA.money(r.value, r.currency)).join(" · ") || "—";
  const avg = (d.averageByCurrency || []).length > 1
    ? (d.averageByCurrency || []).map((r) => JA.money(r.average, r.currency)).join(" · ")
    : ((d.averageByCurrency || [])[0] ? JA.money((d.averageByCurrency || [])[0].average, (d.averageByCurrency || [])[0].currency) : (d.averageOrderValue ? JA.money(d.averageOrderValue, ((d.revenueByCurrency || [])[0] || {}).currency || "NGN") : "—"));
  const rangeLab = String(salesRange) === "all" ? "all time" : `last ${salesRange} days`;
  box.innerHTML = [
    ["Confirmed revenue", rev, rangeLab],
    ["Orders", d.orders || 0, "confirmed"],
    ["Average order value", avg, "confirmed"],
    ["Units", d.units || 0, "items sold"],
  ].map(([k, v, s]) => `<div class="stat"><span class="kicker">${esc(k)}</span><b>${esc(String(v))}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");
  const pend = $("#sales-pending");
  if (pend) {
    const n = d.pendingCount != null ? d.pendingCount : (d.pendingOrders || 0);
    pend.textContent = `${n} order${Number(n) === 1 ? "" : "s"} awaiting confirmation — not counted in sales.`;
  }
  const top = $("#sales-top");
  if (top) {
    const rows = d.topProducts || [];
    top.innerHTML = rows.length
      ? tableHTML(["Product", "Units", "Orders"], rows.map((p) => `<tr><td>${esc(p.name || p.id)}</td><td>${p.units || 0}</td><td>${p.orders || 0}</td></tr>`).join(""))
      : `<p class="empty">No confirmed sales in this period yet.</p>`;
  }
  document.querySelectorAll("[data-sales-range]").forEach((b) => {
    b.onclick = () => {
      salesRange = b.dataset.salesRange;
      document.querySelectorAll("[data-sales-range]").forEach((x) => x.classList.toggle("is-on", x === b));
      const link = $("#sales-csv");
      if (link) link.href = "api/admin/sales.csv?days=" + encodeURIComponent(salesRange);
      fillSales();
    };
  });
}
const TAB_TITLES = { analytics: "Dashboard", products: "Products", orders: "Orders", sales: "Sales", marketing: "Marketing", categories: "Categories", delivery: "Delivery", settings: "Settings", account: "Account", };
const ADX_ICONS = {
  analytics: `<svg viewBox="0 0 24 24"><path d="M4 19V9h3v10H4zm6.5 0V5h3v14h-3zm6.5 0v-7h3v7h-3z"/></svg>`,
  products: `<svg viewBox="0 0 24 24"><path d="M4 8l8-4 8 4v9l-8 4-8-4V8zm8 4l8-4M12 12v9M12 12L4 8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  orders: `<svg viewBox="0 0 24 24"><path d="M5 4h14v16l-2.3-1.5L14.4 20l-2.4-1.5L9.6 20l-2.3-1.5L5 20V4zm3 5h8M8 12.5h8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  sales: `<svg viewBox="0 0 24 24"><path d="M4 20V10m5.5 10V4m5.5 16v-8m5 8V7" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  marketing: `<svg viewBox="0 0 24 24"><path d="M3 11l12-5v12L3 13v-2zm12-1.5L20 6v12l-5-3.5M7 14v5h3v-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  categories: `<svg viewBox="0 0 24 24"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  delivery: `<svg viewBox="0 0 24 24"><path d="M3 7h11v9H3zM14 10h4l3 3v3h-7z" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="7" cy="18" r="1.8" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="17.5" cy="18" r="1.8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1m0-12.8l-2.1 2.1M7.7 16.3l-2.1 2.1" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  account: `<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.5 20c1.4-3.6 4.2-5.4 7.5-5.4s6.1 1.8 7.5 5.4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
};
function paintDesk(tab = "analytics") {
  const pending = serverOrders.filter((o) => (o.status || "pending") === "pending").length;
  const navBtn = (id, badge) => `<button type="button" data-tab="${id}" class="adx-nav-btn ${tab === id ? "is-on" : ""}">${ADX_ICONS[id]}<span>${TAB_TITLES[id]}</span>${badge ? `<em class="adx-badge">${badge}</em>` : ""}</button>`;
  $("#admin-root").innerHTML = `
    <div class="adx">
      <aside class="adx-side">
        <div class="adx-brand"><img src="images/brand/logo.jpg?v=131" alt="" /><div><strong>Jaura Store</strong><span>Store manager</span></div></div>
        <nav class="adx-nav">${navBtn("analytics")}${navBtn("products")}${navBtn("orders", pending || "")}${navBtn("sales")}${navBtn("marketing")}${navBtn("categories")}${navBtn("delivery")}${navBtn("settings")}${navBtn("account")}</nav>
        <div class="adx-side-foot"><a class="adx-nav-btn" href="index.html"><svg viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8M9 5H5v14h14v-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><span>View store</span></a><button type="button" class="adx-nav-btn" id="logout"><svg viewBox="0 0 24 24"><path d="M9 5H5v14h4M13 8l4 4-4 4M17 12H8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><span>Sign out</span></button></div>
      </aside>
      <main class="adx-main">
        <header class="adx-head"><h1>${TAB_TITLES[tab] || "Dashboard"}</h1><div class="adx-head-actions"><a class="btn btn-line" href="index.html">View store</a><button type="button" class="btn btn-line" id="logout-m">Sign out</button></div></header>
        <section class="panel ${tab === "analytics" ? "is-on" : ""}" id="panel-analytics">${tab === "analytics" ? analyticsPanel() : ""}</section>
        <section class="panel ${tab === "products" ? "is-on" : ""}" id="panel-products">${tab !== "products" ? "" : editingId === "new" ? `<div id="form-slot">${productForm()}</div>` : (editingId ? `<div id="form-slot">${productForm(JA.product(editingId) || {})}</div>` : productsTable())}</section>
        <section class="panel ${tab === "orders" ? "is-on" : ""}" id="panel-orders">${tab === "orders" ? ordersPanel() : ""}</section>
        <section class="panel ${tab === "sales" ? "is-on" : ""}" id="panel-sales">${tab === "sales" ? salesPanel() : ""}</section>
        <section class="panel ${tab === "marketing" ? "is-on" : ""}" id="panel-marketing">${tab === "marketing" ? marketingPanel() : ""}</section>
        <section class="panel ${tab === "categories" ? "is-on" : ""}" id="panel-categories">${tab === "categories" ? categoryManager() : ""}</section>
        <section class="panel ${tab === "delivery" ? "is-on" : ""}" id="panel-delivery">${tab === "delivery" ? deliveryDesk() : ""}</section>
        <section class="panel ${tab === "settings" ? "is-on" : ""}" id="panel-settings">${tab === "settings" ? settingsForm() : ""}</section>
        <section class="panel ${tab === "account" ? "is-on" : ""}" id="panel-account">${tab === "account" ? accountPanel() : ""}</section>
      </main>
    </div>
    <nav class="admin-app-nav">
      <button type="button" data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">${ADX_ICONS.analytics}<span>Dashboard</span></button>
      <button type="button" data-tab="products" class="${tab === "products" ? "is-on" : ""}">${ADX_ICONS.products}<span>Products</span></button>
      <button type="button" data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">${ADX_ICONS.orders}<span>Orders</span>${pending ? `<em class="adx-badge">${pending}</em>` : ""}</button>
      <button type="button" data-tab="sales" class="${tab === "sales" ? "is-on" : ""}">${ADX_ICONS.sales}<span>Sales</span></button>
      <button type="button" data-tab="marketing" class="${tab === "marketing" ? "is-on" : ""}">${ADX_ICONS.marketing}<span>Marketing</span></button>
      <button type="button" data-tab="categories" class="${tab === "categories" ? "is-on" : ""}">${ADX_ICONS.categories}<span>Categories</span></button>
      <button type="button" data-tab="delivery" class="${tab === "delivery" ? "is-on" : ""}">${ADX_ICONS.delivery}<span>Delivery</span></button>
      <button type="button" data-tab="settings" class="${tab === "settings" ? "is-on" : ""}">${ADX_ICONS.settings}<span>Settings</span></button>
      <button type="button" data-tab="account" class="${tab === "account" ? "is-on" : ""}">${ADX_ICONS.account}<span>Account</span></button>
    </nav>`;
  const signOut = async () => { await JA.logoutAdmin(); paintLogin("Signed out."); };
  $("#logout").onclick = signOut;
  const logoutM = $("#logout-m"); if (logoutM) logoutM.onclick = signOut;
  document.querySelectorAll("[data-tab]").forEach((b) => { b.onclick = () => paintDesk(b.dataset.tab); });
  if (tab === "analytics") { fillAnalytics(); startDashTimer(); }
  if (tab === "orders") { fillOrders(); fillProofs(); }
  if (tab === "sales") fillSales();
  if (tab === "marketing") fillMarketing();
  if (tab === "account") bindAccount();
  if (tab === "settings") {
    bindHeroVideo(); bindBanner(); bindSiteBranding(); bindShippingNote();
  }
  if (tab === "delivery") {
    bindDeliveryPage();
    // Zones render from dzCache, so bind first and repaint the table body
    // once the server list arrives - no full repaint, which would drop the
    // admin out of a half-filled zone form.
    bindDeliveryZones();
    loadDeliveryZones().then(() => paintZoneTable()).catch(() => {});
  }

  const form = $("#prod-form");
  const existing = editingId && editingId !== "new" ? JA.product(editingId) : null;
  if (form) {
    form.addEventListener("submit", (e) => handleProductSubmit(e, existing));
    form.dataset.submitBound = "1";
    // "Delete this product" inside the editor: clear the Tombstone FIRST so
    // a failed delete is never reported as done.
    $(".au-del-prod", form)?.addEventListener("click", async () => {
      const pid = String(editingId || "");
      if (!pid || pid === "new" || !confirm("Delete this product from the website? Customers will not see it.")) return;
      const res = await JA.removeProduct(pid);
      if (!res || res.ok === false) {
        JA.toast((res && res.error) || "Could not delete the product. No changes were made.");
        return;
      }
      JA.toast("Deleted from the website.");
      editingId = null;
      paintDesk("products");
    });
  }
  $("#cancel-edit")?.addEventListener("click", () => { editingId = null; paintDesk("products"); });
  $("#add-product")?.addEventListener("click", () => { editingId = "new"; paintDesk("products"); });
  bindMedia(); bindOptions(); bindCategories(); bindCfaPreview(); bindReviewsAdmin(existing ? existing.id : "");

  if (tab === "products" && !editingId) {
    renderProdGrid(); bindProdGridEvents();
  }

  $("#prod-search")?.addEventListener("input", applyProductFilter);
  $("#prod-cat")?.addEventListener("change", applyProductFilter);
  if (dashCat && tab === "products" && !editingId) {
    const sel = document.getElementById("prod-cat");
    if (sel) sel.value = dashCat;
  }
  $("#back-all-products")?.addEventListener("click", () => { dashCat = ""; prodCatSel = ""; prodSearchQ = ""; prodPage = 1; paintDesk("products"); });

// The site_settings columns that carry checkout payment details. Kept in one
// list so the form, the POST payload and the server-confirmed repaint can
// never drift apart.
const PAYMENT_FIELDS = [
  "naira_payment_bank", "naira_payment_name", "naira_payment_account",
  "naira_payment_instructions",
  "cfa_payment_provider", "cfa_payment_name", "cfa_payment_account",
  "cfa_payment_instructions",
  "togo_payment_provider", "togo_payment_name", "togo_payment_account",
  "togo_payment_instructions",
];

  $("#set-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const btn = $("#set-form-save");
    const errBox = $("#set-form-error");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
    if (errBox) { errBox.hidden = true; errBox.textContent = ""; }
    // EXACT canonical fields; the server validates referral 0-100 and writes
    // the whole row into Supabase site_settings (id=1) atomically.
    const payload = {
      bank_name: String(fd.get("bank_name") || "").trim(),
      account_number: String(fd.get("account_number") || "").trim(),
      account_name: String(fd.get("account_name") || "").trim(),
      referral_commission_percentage: String(fd.get("referral_commission_percentage") || "0").trim(),
      // hero_banner_title / hero_banner_subtitle / contact_email /
      // contact_phone / site_logo_url are RETIRED from the Settings form
      // (nothing on the storefront ever read them). The submit handler
      // deliberately does not send them, so a stale stored value is
      // preserved rather than wiped. fillSiteForm still carries the
      // columns for the same reason.
      bannerFrom: String(fd.get("bannerFrom") || "").trim(),
      bannerTo: String(fd.get("bannerTo") || "").trim(),
      // Canonical column name. The server also still accepts the legacy
      // shippingNote alias, but the Admin form sends the real column.
      shipping_note: String(fd.get("shipping_note") || "").trim(),
    };
    // Checkout payment details: same canonical columns, no client-side
    // fallback. An empty field is saved empty and the storefront hides it.
    PAYMENT_FIELDS.forEach((k) => { payload[k] = String(fd.get(k) || "").trim(); });
    // include logo/banner if already uploaded (legacy aliases still map to
    // the same Supabase columns)
    const logoUrl = e.target.dataset.logoUrl || "";
    const shopBannerUrl = e.target.dataset.shopBannerUrl || "";
    if (logoUrl) payload.logoUrl = logoUrl;
    if (shopBannerUrl) payload.shopBannerUrl = shopBannerUrl;
    let saved = null;
    try { saved = await saveSiteConfig(payload); } catch (err) { saved = null; }
    if (btn) { btn.disabled = false; btn.textContent = "Save settings"; }
    if (!saved || saved.ok === false) {
      const msg = (saved && saved.error) || "Could not save the settings. No changes were made.";
      if (errBox) { errBox.textContent = msg; errBox.hidden = false; }
      JA.toast(msg);
      return;
    }
    // server-confirmed: repaint the form from the saved row
    const site = (saved && saved.site) || {};
    if (site) { try { fillSiteForm(site); } catch (err) {} }
    JA.toast("Settings saved — live on the site now.");
    JA.mountChrome();
  });
}

// Paint the exact site_settings fields back into the settings form.
function fillSiteForm(site) {
  if (!site) return;
  const set = {
    bank_name: site.bank_name, account_number: site.account_number,
    account_name: site.account_name,
    referral_commission_percentage: site.referral_commission_percentage,
    hero_banner_title: site.hero_banner_title,
    hero_banner_subtitle: site.hero_banner_subtitle,
    contact_email: site.contact_email, contact_phone: site.contact_phone,
    site_logo_url: site.site_logo_url,
    shipping_note: site.shipping_note != null ? site.shipping_note : site.shippingNote,
    bannerFrom: site.banner_from != null ? site.banner_from : site.bannerFrom,
    bannerTo: site.banner_to != null ? site.banner_to : site.bannerTo,
  };
  // Repaint the payment columns from the saved server row too, so what the
  // admin sees after Save is what Supabase actually stored.
  PAYMENT_FIELDS.forEach((k) => { set[k] = site[k]; });
  const form = $("#set-form");
  if (!form) return;
  Object.keys(set).forEach((name) => {
    const el = form.elements && form.elements[name];
    if (el && set[name] !== undefined && set[name] !== null) el.value = String(set[name]);
  });
}

function categoryManager() {
  const cats = JA.categories();
  const rows = cats.map((c, i) => {
    const n = JA.products().filter((p) => p.category === c.id).length;
    return `<article class="au-cat-card" data-cat-i="${i}" data-cat-id="${JA.escape(c.id)}">
      <div class="au-cat-pic">${_catAssetHTML(c.image)}<label class="au-cat-up">Change asset<input type="file" accept="image/*,.pdf,.doc,.docx,application/pdf" data-cat-img="${i}" hidden /></label></div>
      <div class="au-cat-fields">
        <input name="cat-id-${i}" type="hidden" value="${JA.escape(c.id)}" />
        <label>Name (English)</label><input name="cat-name-${i}" value="${JA.escape(c.name || "")}" />
        <label>Name (French)</label><input name="cat-fr-${i}" value="${JA.escape(c.nameFr || "")}" />
        <label class="au-tog"><span>Show on website</span><input type="checkbox" name="cat-on-${i}" ${c.hidden ? "" : "checked"} /></label>
        <p class="admin-note">${n} product${n === 1 ? "" : "s"} · <button type="button" class="au-link-btn" data-view-cat="${JA.escape(c.id)}">View products in this category</button></p>
        <button type="button" class="au-opt-del" data-cat-del="${JA.escape(c.id)}">Delete category</button>
      </div>
    </article>`;
  }).join("");
  return `<div class="au-cats-admin">
    <h2>Categories</h2>
    <p class="admin-note">Add or delete categories here. They show on the shop and in filters instantly. Tap a card to see its products. When you add a product inside a category, it stays in that category after save.</p>
    <div id="cat-list">${rows}</div>
    <div class="au-cat-new">
      <h3>Add a category</h3>
      <div class="au-2"><div class="field"><label>Name</label><input id="new-cat-name" placeholder="e.g. Jewellery" /></div><div class="field"><label>French name</label><input id="new-cat-fr" placeholder="ex. Bijoux" /></div></div>
      <button type="button" class="au-link-btn" id="add-cat">+ Add category</button>
    </div>
    <button type="button" class="btn au-save" id="save-cats">Save categories</button>
  </div>`;
}
function _catAssetHTML(image) {
  const u = String(image || "");
  if (/\.(pdf|doc|docx)(\?.*)?$/i.test(u.split("?")[0])) {
    const label = /\.docx?$/i.test(u) ? "DOC" : "PDF";
    return `<a class="media-doc-chip cat-doc-chip" href="${JA.asset(u)}" target="_blank" rel="noopener">${label}<span>View / Download</span></a>`;
  }
  return `<img src="${JA.asset(u)}" alt="" />`;
}
function collectCats() {
  const out = [];
  document.querySelectorAll("[data-cat-i]").forEach((row) => {
    const i = row.getAttribute("data-cat-i");
    const id = (row.querySelector(`[name="cat-id-${i}"]`)?.value || "").trim();
    const name = (row.querySelector(`[name="cat-name-${i}"]`)?.value || "").trim();
    if (!id || !name) return;
    // The uploaded Storage URL (dataset.catUrl) wins: it is the complete
    // HTTPS URL the server must store; the DOM img is only the preview.
    const uploadUrl = row.querySelector("[data-cat-img]")?.dataset.catUrl || "";
    const asset = uploadUrl || row.querySelector(".au-cat-pic img")?.getAttribute("src") || row.querySelector(".au-cat-pic a.media-doc-chip")?.getAttribute("href") || "";
    out.push({ id, name, nameFr: (row.querySelector(`[name="cat-fr-${i}"]`)?.value || "").trim(), image: asset, hidden: !row.querySelector(`[name="cat-on-${i}"]`)?.checked, });
  });
  return out;
}
function bindCategories() {
  const list = document.getElementById("cat-list");
  if (!list) return;
  const persist = async (msg) => {
    const cats = collectCats();
    const res = await JA.saveCategories(cats);
    if (!res || res.ok === false) {
      JA.toast((res && res.error) || "Could not save categories. No changes are live.");
      return false;
    }
    JA.toast(msg || "Categories saved. They show on the shop now.");
    return true;
  };
  list.addEventListener("change", async (e) => {
    const input = e.target.closest("[data-cat-img]");
    if (!input || !input.files || !input.files[0]) return;
    const f = input.files[0];
    const isDoc = /\.(pdf|doc|docx)$/i.test(f.name || "") || /pdf|word|msword|document/.test(f.type || "");
    if (isDoc && f.size > 8 * 1024 * 1024) { JA.toast("That asset is " + (f.size / 1048576).toFixed(1) + " MB. The limit is 8 MB."); input.value = ""; return; }
    const card = input.closest("[data-cat-i]"); const pic = card?.querySelector(".au-cat-pic");
    try {
      if (window.JA_NET) {
        const res = await window.JA_NET.api("api/admin/uploads/category", { method: "POST", blob: f, field: "file", filename: f.name || "category.jpg", timeout: 300000, label: "Category asset", });
        if (res && res.url) {
          // complete HTTPS Storage URL: repaint the preview, then persist
          // the real URL into categories.image_url through the server
          input.dataset.catUrl = res.url;
          if (pic) {
            const upLabel = pic.querySelector(".au-cat-up");
            pic.innerHTML = _catAssetHTML(res.url);
            if (upLabel) pic.appendChild(upLabel);
            else pic.innerHTML += `<label class="au-cat-up">Change asset<input type="file" accept="image/*,.pdf,.doc,.docx,application/pdf" data-cat-img="${card.getAttribute("data-cat-i")}" hidden /></label>`;
          }
          await persist("Asset saved — banner will use this image on shop page.");
          return;
        }
        // never claim success when the upload was only queued or failed:
        // the old asset stays, the admin can retry
        JA.toast((res && res.error) || "Could not upload that asset. No changes are live.");
        return;
      }
      // no live server (test/dev static hosting): keep the local preview
      const data = await fileToData(f);
      if (pic) { const upLabel = pic.querySelector(".au-cat-up"); pic.innerHTML = `<img src="${data}" alt="" />`; if (upLabel) pic.appendChild(upLabel); }
      persist("Photo saved.");
    } catch (err) { JA.toast((err && err.message) || "Could not read that asset."); }
    finally { input.value = ""; }
  });
  list.addEventListener("click", async (e) => {
    const viewBtn = e.target.closest("[data-view-cat]");
    if (viewBtn) { dashCat = viewBtn.getAttribute("data-view-cat"); prodCatSel = dashCat; prodPage = 1; editingId = null; paintDesk("products"); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    const card = e.target.closest("[data-cat-id]");
    if (card && !e.target.closest("input,label,button,a,select")) {
      dashCat = card.getAttribute("data-cat-id"); prodCatSel = dashCat; prodPage = 1; editingId = null; paintDesk("products"); window.scrollTo({ top: 0, behavior: "smooth" }); return;
    }
    const del = e.target.closest("[data-cat-del]");
    if (!del) return;
    const id = del.getAttribute("data-cat-del");
    const n = JA.products().filter((p) => p.category === id).length;
    const ask = n ? ("Move " + n + " product(s) into Beauty & skincare and delete this category?")
                  : "Delete this category?";
    if (!confirm(ask)) return;
    if (JA.deleteCategory) {
      const res = await JA.deleteCategory(id, "beauty");
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not delete the category. No changes are live."); return; }
    }
    JA.toast("Category deleted."); paintDesk("categories");
  });
  document.getElementById("add-cat")?.addEventListener("click", async () => {
    const name = (document.getElementById("new-cat-name")?.value || "").trim();
    const nameFr = (document.getElementById("new-cat-fr")?.value || "").trim();
    if (!name) { JA.toast("Type a category name."); return; }
    const id = slugify(name) || ("cat-" + Date.now().toString(36));
    if (collectCats().some((c) => c.id === id) || JA.categories().some((c) => c.id === id)) { JA.toast("That category already exists."); return; }
    const next = collectCats().concat([{ id, name, nameFr, image: "images/brand/logo.jpg?v=131", hidden: false }]);
    const res = await JA.saveCategories(next);
    if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not add the category. No changes are live."); return; }
    JA.toast("Category added — now you can add products in " + name + ". It shows on website instantly.");
    paintDesk("categories");
  });
  document.getElementById("save-cats")?.addEventListener("click", async () => {
    const ok = await persist();
    if (ok) paintDesk("categories");
  });
}

function settingsForm() {
  // server row first (if already fetched), localStorage only as an offline
  // paint convenience - the live Supabase row is the source of truth
  const s = { ...JA.settings(), ...(JA.getSiteConfig ? (JA.getSiteConfig() || {}) : {}) };
  return `
  <div class="admin-card adx-hero-card">
    <h3 class="admin-h">Homepage hero video</h3>
    <p class="admin-note">Upload a video (MP4/WebM/MOV, up to 40 MB) and it plays silently on a loop at the top of the homepage. You can also attach a PDF or document (up to 8 MB). A photo sets the hero poster.</p>
    <div id="hero-video-now"><p class="empty">Checking the current hero…</p></div>
    <div class="adx-hero-actions"><label class="btn adx-upload-btn">Upload video / document<input type="file" id="hero-video-file" accept="image/*,video/*,.pdf,.doc,.docx,application/pdf" hidden /></label><button type="button" class="btn btn-line" id="hero-video-remove" hidden>Remove hero asset</button></div>
    <p class="admin-note" id="hero-video-msg"></p>
  </div>
  <div class="admin-card" style="margin-top:22px">
    <h3 class="admin-h">Store branding — logo & shop banner</h3>
    <p class="admin-note">Upload your main store logo and the Shop page cursive banner image. They update sitewide instantly.</p>
    <div class="au-2">
      <div class="field"><label>Main store logo (J Aura logo)</label><div id="logo-now"><p class="admin-note">Checking current logo…</p></div><label class="btn adx-upload-btn" style="margin-top:8px">Upload / Change logo<input type="file" id="logo-file" accept="image/*" hidden /></label><button type="button" class="btn btn-line" id="logo-remove" hidden style="margin-top:8px">Remove custom logo</button></div>
      <div class="field"><label>Shop Banner Cursive Image (wordmark-bg)</label><div id="shop-banner-now"><p class="admin-note">Checking current shop banner…</p></div><label class="btn adx-upload-btn" style="margin-top:8px">Upload / Change shop banner<input type="file" id="shop-banner-file" accept="image/*" hidden /></label><button type="button" class="btn btn-line" id="shop-banner-remove" hidden style="margin-top:8px">Remove custom banner</button></div>
    </div>
    <p class="admin-note" id="branding-msg"></p>
  </div>
  <form id="banner-form" class="form-grid admin-card" style="margin-top:22px">
    <h3 class="admin-h full">Moving banner text</h3>
    <p class="admin-note full">The moving line under the header on every page. Write your own message here — it replaces the default delivery-window banner for every visitor. The <strong>bold highlight</strong> shows in gold at the end of the line. Empty text brings the default banner back.</p>
    <div class="field full"><label>Banner text</label><input name="convBanner" id="conv-banner" maxlength="300" placeholder="e.g. Back-to-school sale: 10% off every bag" /></div>
    <div class="field full"><label>Bold highlight (optional)</label><input name="convBold" id="conv-bold" maxlength="300" placeholder="e.g. ends Sunday" /></div>
    <div class="field full"><button class="btn">Save banner</button></div>
  </form>
  <form id="set-form" class="form-grid admin-card" style="margin-top:22px">
    <h3 class="admin-h full">Site settings — live from Supabase</h3>
    <p class="admin-note full">These fields are stored in the Supabase <code>site_settings</code> row (id=1) and shown on the site immediately after saving.</p>
    <div class="field"><label>Bank name</label><input name="bank_name" maxlength="120" value="${JA.escape(s.bank_name || "")}" /></div>
    <div class="field"><label>Account number</label><input name="account_number" maxlength="60" value="${JA.escape(s.account_number || "")}" /></div>
    <div class="field"><label>Account name</label><input name="account_name" maxlength="120" value="${JA.escape(s.account_name || "")}" /></div>
    <div class="field"><label>Referral commission % (0–100)</label><input name="referral_commission_percentage" id="referral-pct" type="number" min="0" max="100" step="0.01" value="${Number(s.referral_commission_percentage ?? 0)}" /><p class="admin-note">The % an order's referral code pays out. Saved straight into site_settings.</p></div>
    <h3 class="admin-h full">Checkout payment details</h3>
    <p class="admin-note full">Shown to the customer at checkout. These live in Supabase and the storefront carries <strong>no hardcoded fallback</strong> — an empty field hides that line, it never invents an account number. Changing an account here is live immediately, with no redeploy.</p>
    <div class="field"><label>Naira — bank</label><input name="naira_payment_bank" maxlength="120" value="${JA.escape(s.naira_payment_bank || "")}" /></div>
    <div class="field"><label>Naira — account name</label><input name="naira_payment_name" maxlength="120" value="${JA.escape(s.naira_payment_name || "")}" /></div>
    <div class="field"><label>Naira — account number</label><input name="naira_payment_account" maxlength="60" value="${JA.escape(s.naira_payment_account || "")}" /></div>
    <div class="field"><label>Naira — instructions</label><input name="naira_payment_instructions" maxlength="300" value="${JA.escape(s.naira_payment_instructions || "")}" /></div>
    <div class="field"><label>CFA (Benin) — provider</label><input name="cfa_payment_provider" maxlength="120" value="${JA.escape(s.cfa_payment_provider || "")}" placeholder="e.g. MTN MoMo Benin" /></div>
    <div class="field"><label>CFA (Benin) — account name</label><input name="cfa_payment_name" maxlength="120" value="${JA.escape(s.cfa_payment_name || "")}" /></div>
    <div class="field"><label>CFA (Benin) — account number</label><input name="cfa_payment_account" maxlength="60" value="${JA.escape(s.cfa_payment_account || "")}" /></div>
    <div class="field"><label>CFA (Benin) — instructions</label><input name="cfa_payment_instructions" maxlength="300" value="${JA.escape(s.cfa_payment_instructions || "")}" /></div>
    <div class="field"><label>Togo — provider</label><input name="togo_payment_provider" maxlength="120" value="${JA.escape(s.togo_payment_provider || "")}" placeholder="e.g. Moov Money Togo" /></div>
    <div class="field"><label>Togo — account name</label><input name="togo_payment_name" maxlength="120" value="${JA.escape(s.togo_payment_name || "")}" /></div>
    <div class="field"><label>Togo — account number</label><input name="togo_payment_account" maxlength="60" value="${JA.escape(s.togo_payment_account || "")}" /></div>
    <div class="field"><label>Togo — instructions</label><input name="togo_payment_instructions" maxlength="300" value="${JA.escape(s.togo_payment_instructions || "")}" /></div>
    <h3 class="admin-h full">Benin delivery window</h3>
    <p class="admin-note full">These dates appear on the moving banner under the header. Shoppers in Benin are told they will receive their order between these two days.</p>
    <div class="field"><label>Delivery window starts</label><input type="date" name="bannerFrom" id="banner-from" value="2026-09-15" /></div>
    <div class="field"><label>Delivery window ends</label><input type="date" name="bannerTo" id="banner-to" value="2026-09-25" /></div>
    <div class="field full"><label>Delivery fee / shipping note (shown at checkout)</label><textarea name="shipping_note" id="shipping-note" rows="3" maxlength="800" placeholder="e.g. Delivery fee: Lagos ₦2000-₦5000, Cotonou 1000-3000 CFA. Pickup in Cotonou is free for lighter products.">${JA.escape(s.shipping_note != null ? s.shipping_note : (s.shippingNote || ""))}</textarea><p class="admin-note">This note appears dynamically at checkout under the order totals. Leave empty to hide.</p></div>
    <div class="field full"><p class="admin-err" id="set-form-error" hidden></p><button class="btn" id="set-form-save">Save settings</button></div>
  </form>`;
}

/* ------------------------------------------------------------------ *
 * Admin -> Delivery. Two things the owner could never change without a
 * developer live here: the Delivery PAGE customers read (delivery.html
 * was frozen in the repo) and the delivery ZONES checkout offers.
 * ------------------------------------------------------------------ */

// Whatever the store is serving right now; the form is painted from this and
// re-painted from the server's answer after every save.
let dpCache = null;

const DP_FALLBACK = {
  title: "Delivery Locations",
  lead: "Curated coverage across West Africa",
  blocks: [
    { heading: "Nigeria", locations: [{ name: "Lagos Mainland", detail: "" }] },
    { heading: "Benin Republic", locations: [{ name: "Cotonou", detail: "" }] },
    { heading: "Togo", locations: [{ name: "Lom\u00e9", detail: "" }] },
  ],
};

function dpCurrent() {
  if (dpCache && Array.isArray(dpCache.blocks) && dpCache.blocks.length) return dpCache;
  const site = (JA.getSiteConfig && JA.getSiteConfig()) || {};
  const page = site.delivery_page;
  if (page && Array.isArray(page.blocks) && page.blocks.length) return page;
  return DP_FALLBACK;
}

function dpLocationRow(loc, bi, li) {
  return `
    <div class="dp-loc" data-dp-loc="${bi}:${li}">
      <input class="dp-loc-name" data-dp-name="${bi}:${li}" maxlength="80" placeholder="Town or area" value="${JA.escape((loc && loc.name) || "")}" />
      <input class="dp-loc-detail" data-dp-detail="${bi}:${li}" maxlength="300" placeholder="Districts covered (optional)" value="${JA.escape((loc && loc.detail) || "")}" />
      <button type="button" class="au-link-btn au-danger" data-dp-loc-del="${bi}:${li}">Remove</button>
    </div>`;
}

function dpBlockHTML(b, bi) {
  const locs = (Array.isArray(b.locations) ? b.locations : []);
  return `
    <section class="admin-block dp-block" data-dp-block="${bi}">
      <div class="field full">
        <label>Section heading</label>
        <input data-dp-heading="${bi}" maxlength="80" placeholder="e.g. Nigeria" value="${JA.escape(b.heading || "")}" />
      </div>
      <div class="dp-locs">${locs.map((l, li) => dpLocationRow(l, bi, li)).join("")}</div>
      <div class="field full">
        <button type="button" class="btn btn-line" data-dp-loc-add="${bi}">Add a location</button>
        <button type="button" class="au-link-btn au-danger" data-dp-block-del="${bi}">Remove this section</button>
      </div>
    </section>`;
}

function deliveryPagePanel() {
  const p = dpCurrent();
  const blocks = (Array.isArray(p.blocks) ? p.blocks : []);
  return `
  <div class="admin-card" id="delivery-page">
    <h3 class="admin-h">The Delivery page customers read</h3>
    <p class="admin-note">This is the page at <strong>delivery.html</strong>. Add a town here and it appears on the live site straight away \u2014 no developer needed. Leave it untouched and the page keeps showing what is already there.</p>
    <p class="admin-err" id="dp-error" hidden></p>
    <div class="field full"><label>Page title</label><input id="dp-title" maxlength="120" value="${JA.escape(p.title || "")}" /></div>
    <div class="field full"><label>Intro line</label><input id="dp-lead" maxlength="240" value="${JA.escape(p.lead || "")}" /></div>
    <div id="dp-blocks">${blocks.map((b, i) => dpBlockHTML(b, i)).join("")}</div>
    <div class="field full" style="margin-top:14px">
      <button type="button" class="btn btn-line" id="dp-add-block">Add a section</button>
      <button type="button" class="btn" id="dp-save">Save the delivery page</button>
    </div>
  </div>`;
}

/** Read the whole form back into a page object. */
function dpReadForm() {
  const title = ($("#dp-title") || {}).value || "";
  const lead = ($("#dp-lead") || {}).value || "";
  const blocks = [];
  document.querySelectorAll("[data-dp-block]").forEach((el) => {
    const bi = el.getAttribute("data-dp-block");
    const heading = (el.querySelector(`[data-dp-heading="${bi}"]`) || {}).value || "";
    const locations = [];
    el.querySelectorAll("[data-dp-loc]").forEach((row) => {
      const key = row.getAttribute("data-dp-loc");
      const name = (row.querySelector(`[data-dp-name="${key}"]`) || {}).value || "";
      const detail = (row.querySelector(`[data-dp-detail="${key}"]`) || {}).value || "";
      if (String(name).trim()) locations.push({ name: String(name).trim(), detail: String(detail).trim() });
    });
    if (String(heading).trim() || locations.length) {
      blocks.push({ heading: String(heading).trim(), locations });
    }
  });
  return { title: String(title).trim(), lead: String(lead).trim(), blocks };
}

function dpError(msg) {
  const el = $("#dp-error");
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
}

function bindDeliveryPage() {
  const box = $("#delivery-page");
  if (!box || box.dataset.bound === "1") return;
  box.dataset.bound = "1";

  // Every structural edit works on the CURRENT form contents, so a half-typed
  // town is never lost when the owner adds another row.
  const repaint = (page) => { dpCache = page; paintDesk("delivery"); };

  box.addEventListener("click", (e) => {
    const addLoc = e.target.closest("[data-dp-loc-add]");
    if (addLoc) {
      const page = dpReadForm();
      const bi = Number(addLoc.getAttribute("data-dp-loc-add"));
      if (!page.blocks[bi]) page.blocks[bi] = { heading: "", locations: [] };
      page.blocks[bi].locations.push({ name: "", detail: "" });
      repaint(page);
      return;
    }
    const delLoc = e.target.closest("[data-dp-loc-del]");
    if (delLoc) {
      const page = dpReadForm();
      const [bi, li] = delLoc.getAttribute("data-dp-loc-del").split(":").map(Number);
      if (page.blocks[bi] && page.blocks[bi].locations) page.blocks[bi].locations.splice(li, 1);
      repaint(page);
      return;
    }
    const delBlock = e.target.closest("[data-dp-block-del]");
    if (delBlock) {
      const page = dpReadForm();
      page.blocks.splice(Number(delBlock.getAttribute("data-dp-block-del")), 1);
      repaint(page);
      return;
    }
    if (e.target.closest("#dp-add-block")) {
      const page = dpReadForm();
      page.blocks.push({ heading: "", locations: [{ name: "", detail: "" }] });
      repaint(page);
    }
  });

  $("#dp-save")?.addEventListener("click", async () => {
    dpError("");
    const page = dpReadForm();
    if (!page.blocks.some((b) => (b.locations || []).length)) {
      dpError("Add at least one location before saving.");
      return;
    }
    const btn = $("#dp-save");
    if (btn) btn.disabled = true;
    const res = window.JA_NET
      ? await window.JA_NET.api("api/admin/delivery-page", { method: "POST", json: { page } })
      : null;
    if (btn) btn.disabled = false;
    if (!res || !res.ok) {
      dpError((res && res.error) || "Could not save the delivery page. Nothing changed.");
      return;
    }
    // Repaint from what the server actually stored, not from the form.
    dpCache = res.page || page;
    JA.toast("Delivery page saved \u2014 it is live on the store now.");
    paintDesk("delivery");
  });
}

function deliveryDesk() {
  return deliveryPagePanel() + deliveryZonesPanel();
}

/* ------------------------------------------------------------------ *
 * Delivery zones and fares. These are the authoritative list the
 * storefront renders at checkout (GET /api/site -> delivery_zones), and
 * the server rejects any zone it does not recognise. The fare is a RANGE
 * because transport varies with weight; the exact figure is agreed with
 * the customer after payment.
 * ------------------------------------------------------------------ */
function zoneKindLabel(k) {
  return k === "pickup" ? "Pickup (free)" : k === "quote" ? "Quote on WhatsApp" : "Delivery range";
}
function zoneFareLabel(z) {
  if (z.kind === "pickup") return "Free collection";
  if (z.kind === "quote") return "Agreed per order";
  const sym = z.currency === "NGN" ? "\u20A6" : "";
  const suf = z.currency === "CFA" ? " CFA" : "";
  return sym + Number(z.fare_min || 0).toLocaleString("en-US") + suf + " \u2013 "
       + sym + Number(z.fare_max || 0).toLocaleString("en-US") + suf;
}
function zoneRowsHTML() {
  const list = (dzCache && dzCache.length) ? dzCache : [];
  return list.map((z) => `
    <tr>
      <td>${JA.escape(z.name)}</td>
      <td>${JA.escape(z.currency)}</td>
      <td>${zoneFareLabel(z)}</td>
      <td>${zoneKindLabel(z.kind)}</td>
      <td>${z.active ? "Live" : "Hidden"}</td>
      <td class="au-row-actions">
        <button type="button" class="au-link-btn" data-zone-edit="${JA.escape(z.id)}">Edit</button>
        <button type="button" class="au-link-btn au-danger" data-zone-del="${JA.escape(z.id)}">Delete</button>
      </td>
    </tr>`).join("");
}
function paintZoneTable() {
  const body = document.querySelector("#zone-table tbody");
  if (!body) return;
  body.innerHTML = zoneRowsHTML()
    || `<tr><td colspan="6">No zones yet. Add the first one below.</td></tr>`;
}
function deliveryZonesPanel() {
  return `
  <section class="admin-block" id="delivery-zones">
    <h3 class="admin-h">Delivery zones and fares</h3>
    <p class="admin-note">Checkout offers exactly these zones and the server refuses any other value, so editing here changes the storefront immediately. A fare is a range: transport varies with weight and the final figure is confirmed with the customer after payment.</p>
    <p class="admin-err" id="zone-error" hidden></p>
    <div class="au-table-wrap"><table class="au-table" id="zone-table">
      <thead><tr><th>Zone</th><th>Currency</th><th>Fare</th><th>Type</th><th>Status</th><th></th></tr></thead>
      <tbody>${zoneRowsHTML() || `<tr><td colspan="6">No zones yet. Add the first one below.</td></tr>`}</tbody>
    </table></div>
    <form id="zone-form" autocomplete="off">
      <input type="hidden" name="zone_id" value="" />
      <div class="field"><label>Zone name *</label><input name="zone_name" maxlength="80" required placeholder="e.g. Lagos Mainland" /></div>
      <div class="field"><label>Currency</label><select name="zone_currency"><option value="CFA">CFA (XOF)</option><option value="NGN">Naira (NGN)</option></select></div>
      <div class="field"><label>Type</label><select name="zone_kind">
        <option value="delivery">Delivery range</option>
        <option value="pickup">Pickup (free)</option>
        <option value="quote">Quote on WhatsApp</option>
      </select></div>
      <div class="field"><label>Fare from</label><input name="zone_fare_min" type="number" min="0" step="1" value="0" /></div>
      <div class="field"><label>Fare to</label><input name="zone_fare_max" type="number" min="0" step="1" value="0" /></div>
      <div class="field"><label>Display order</label><input name="zone_sort" type="number" step="1" value="0" /></div>
      <div class="field"><label>Status</label><select name="zone_active"><option value="1">Live</option><option value="0">Hidden</option></select></div>
      <div class="field full"><button type="submit" class="btn" id="zone-save">Save zone</button>
        <button type="button" class="btn btn-line" id="zone-cancel" hidden>Cancel edit</button></div>
    </form>
  </section>`;
}

let dzCache = [];
// Once a zone save (or delete) has confirmed, paintDesk("delivery") must NOT
// let a late/failed zones GET wipe the table back to the pre-save list — that
// is exactly what made a successful zone edit look like it never saved.
let dzAuthoritative = false;

async function loadDeliveryZones() {
  const res = window.JA_NET ? await window.JA_NET.api("api/admin/delivery-zones") : null;
  if (res && res.ok && Array.isArray(res.zones)) {
    dzCache = res.zones;
    return dzCache;
  }
  // A failed or empty refetch must never blank a cache the owner just wrote.
  if (dzAuthoritative) return dzCache;
  dzCache = [];
  return dzCache;
}

function zoneError(msg) {
  const el = $("#zone-error");
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
}

// Puts the zone editor back to "add a new zone". Shared by Cancel edit, by a
// successful save and by deleting the zone currently being edited, so the
// button can never be left reading "Update zone" over an empty form - that
// stale label is what made a saved zone look like it had not been saved.
function resetZoneForm(form) {
  const f = form || $("#zone-form");
  if (!f) return;
  f.reset();
  if (f.zone_id) f.zone_id.value = "";
  const cancel = $("#zone-cancel");
  if (cancel) cancel.hidden = true;
  const btn = $("#zone-save");
  if (btn) {
    btn.disabled = false;
    btn.textContent = "Save zone";
  }
  zoneError("");
}

function bindDeliveryZones() {
  const form = $("#zone-form");
  if (!form || form.dataset.bound === "1") return;
  form.dataset.bound = "1";
  const block = $("#delivery-zones");
  if (!block) return;

  block.addEventListener("click", async (e) => {
    const del = e.target.closest("[data-zone-del]");
    if (del) {
      const id = del.getAttribute("data-zone-del");
      if (!window.confirm("Delete this delivery zone? Existing orders keep their saved snapshot.")) return;
      const origLabel = del.textContent;
      del.disabled = true;
      del.textContent = "Deleting…";
      let res = null;
      try {
        res = window.JA_NET
          ? await window.JA_NET.api("api/admin/delivery-zones/" + encodeURIComponent(id), { method: "DELETE" })
          : null;
      } catch (err) {
        res = { ok: false, error: (err && err.message) || "Network error while deleting the zone." };
      }
      // The row is about to be re-rendered, so un-stick the button first.
      del.disabled = false;
      del.textContent = origLabel || "Delete";
      if (!res || !res.ok) {
        const msg = (res && res.error) ? String(res.error) : "Could not delete the zone.";
        zoneError(msg);
        JA.toast(msg);
        return;
      }
      dzCache = res.zones || [];
      dzAuthoritative = true;
      // Repaint the table body only. A full desk repaint would rebuild the
      // form above it and throw away whatever the owner was typing.
      paintZoneTable();
      if ((form.zone_id.value || "").trim() === String(id || "").trim()) resetZoneForm(form);
      JA.toast("Zone deleted — live now.");
      return;
    }
    const edit = e.target.closest("[data-zone-edit]");
    if (edit) {
      const zid = edit.getAttribute("data-zone-edit");
      const z = dzCache.find((x) => String(x.id) === String(zid));
      if (!z) {
        // The row came from a stale cache - refetch, repaint, then the admin
        // can click Edit again on the row the server actually has.
        loadDeliveryZones().then(() => paintZoneTable()).catch(() => {});
        return;
      }
      form.zone_id.value = z.id;
      form.zone_name.value = z.name || "";
      form.zone_currency.value = z.currency || "CFA";
      form.zone_kind.value = z.kind || "delivery";
      form.zone_fare_min.value = z.fare_min;
      form.zone_fare_max.value = z.fare_max;
      form.zone_sort.value = z.sort_order;
      form.zone_active.value = z.active ? "1" : "0";
      const cancel = $("#zone-cancel");
      if (cancel) cancel.hidden = false;
      const btn = $("#zone-save");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Update zone";
      }
      zoneError("");
      form.zone_name.focus();
      try {
        if (typeof form.scrollIntoView === "function") form.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) { /* scrolling is cosmetic only */ }
    }
  });

  const cancel = $("#zone-cancel");
  if (cancel) cancel.addEventListener("click", () => resetZoneForm(form));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    zoneError("");
    const btn = $("#zone-save");
    const isUpdate = !!(form.zone_id.value || "").trim();
    const name = (form.zone_name.value || "").trim();
    if (!name) {
      const msg = "Give the zone a name, e.g. Lagos Mainland.";
      zoneError(msg);
      JA.toast(msg);
      form.zone_name.focus();
      return;
    }
    const payload = {
      id: form.zone_id.value || "",
      name: name,
      currency: form.zone_currency.value,
      kind: form.zone_kind.value,
      fare_min: Number(form.zone_fare_min.value || 0),
      fare_max: Number(form.zone_fare_max.value || 0),
      sort_order: Number(form.zone_sort.value || 0),
      active: form.zone_active.value === "1",
    };
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Saving…";
    }
    let res = null;
    try {
      res = window.JA_NET
        ? await window.JA_NET.api("api/admin/delivery-zones", { method: "POST", json: payload })
        : null;
    } catch (err) {
      res = { ok: false, error: (err && err.message) || "Network error while saving the zone." };
    }
    // ALWAYS put the button back, on failure too. Left reading "Saving…" or
    // "Update zone" it is a stale control: it looks busy, or it looks like the
    // edit never landed.
    if (btn) {
      btn.disabled = false;
      btn.textContent = isUpdate ? "Update zone" : "Save zone";
    }
    if (!res || !res.ok) {
      const msg = (res && res.error) ? String(res.error) : "Could not save the zone. Nothing changed.";
      zoneError(msg);
      JA.toast(msg);
      return;
    }
    // The server's list is the truth. Repaint the table from it immediately,
    // before anything else can repaint, so the new fare is on screen at once.
    dzCache = res.zones || [];
    dzAuthoritative = true;
    paintZoneTable();
    const z = res.zone || (dzCache.find((x) => String(x.id) === String(payload.id || "").toLowerCase()) || null);
    if (z) {
      let fareTxt = "";
      if (z.kind === "pickup") fareTxt = "free pickup";
      else if (z.kind === "quote") fareTxt = "quote";
      else fareTxt = `${Number(z.fare_min || 0).toLocaleString()}–${Number(z.fare_max || 0).toLocaleString()} ${z.currency || payload.currency || ""}`.trim();
      JA.toast(`${z.name}: ${fareTxt} saved — live now.`);
    } else {
      JA.toast("Zone saved — live now.");
    }
    // Clear the editor WITHOUT paintDesk(): rebuilding the whole delivery desk
    // is what used to swallow the repaint and leave a stale Update button.
    resetZoneForm(form);
  });
}
async function saveSiteConfig(patch) {
  return window.JA_NET ? window.JA_NET.api("api/admin/site", { method: "POST", json: patch }) : Promise.resolve(null);
}
function bindBanner() {
  const form = $("#banner-form"); if (!form) return;
  fetch("api/site", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((d) => {
    const site = (d && d.site) || {}; const conv = $("#conv-banner"); const bold = $("#conv-bold");
    if (conv) conv.value = site.convBanner || ""; if (bold) bold.value = site.convBold || "";
  }).catch(() => {});
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); const fd = new FormData(form);
    const conv = String(fd.get("convBanner") || "").trim(); const bold = String(fd.get("convBold") || "").trim();
    const saved = await saveSiteConfig({ convBanner: conv, convBold: bold });
    if (saved && saved.ok !== false) {
      // Repaint from the SERVER's answer (not the form), and re-fill both
      // inputs so what the admin sees is what Supabase stored. applySiteConfig
      // also fires ja:site so every open page (and the admin chrome) picks it up.
      const site = (saved && saved.site) || { convBanner: conv, convBold: bold };
      const liveConv = ("convBanner" in site) ? (site.convBanner || "") : conv;
      const liveBold = ("convBold" in site) ? (site.convBold || "") : bold;
      const convEl = $("#conv-banner"); const boldEl = $("#conv-bold");
      if (convEl) convEl.value = liveConv;
      if (boldEl) boldEl.value = liveBold;
      try {
        if (JA.applySiteConfig) JA.applySiteConfig(site);
        else if (JA.setBanner) JA.setBanner(liveConv, liveBold);
      } catch (err) {}
      JA.toast(liveConv ? "Banner saved — it moves under the header on every page now." : "Banner cleared — default is back.");
    }
    else JA.toast((saved && saved.error) || "Could not save the banner.");
  });
}
function paintHeroVideoNow(site) {
  const box = $("#hero-video-now"); const rm = $("#hero-video-remove"); if (!box) return; site = site || {};
  const video = site.heroVideo || ""; const doc = site.heroDoc || ""; const poster = site.heroPoster || "";
  if (video) { box.innerHTML = `<video class="adx-hero-preview" src="${JA.escape(video)}" ${poster ? `poster="${JA.escape(JA.asset(poster))}"` : ""} muted loop playsinline controls preload="metadata"></video><p class="admin-note">This video is live on the homepage right now.</p>`; if (rm) rm.hidden = false; }
  else if (doc) { box.innerHTML = `<a class="media-doc-chip adx-hero-doc" href="${JA.escape(JA.asset(doc))}" target="_blank" rel="noopener">${/\.docx?$/i.test(doc) ? "DOC" : "PDF"}<span>View / Download</span></a><p class="admin-note">This document is linked from the homepage hero.</p>`; if (rm) rm.hidden = false; }
  else if (poster) { box.innerHTML = `<img class="adx-hero-preview" src="${JA.escape(JA.asset(poster))}" alt="Hero poster" /><p class="admin-note">This photo is the homepage hero poster.</p>`; if (rm) rm.hidden = false; }
  else { box.innerHTML = `<p class="empty">No hero asset uploaded — default static hero shows.</p>`; if (rm) rm.hidden = true; }
}
function bindHeroVideo() {
  const file = $("#hero-video-file"); const msg = $("#hero-video-msg"); if (!file) return;
  fetch("api/site", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    const site = (d && d.site) || {};
    // server (Supabase) values are the source of truth for every field
    paintHeroVideoNow(site);
    try { fillSiteForm(site); } catch (e) {}
    const from = $("#banner-from"); const to = $("#banner-to"); if (from && site.bannerFrom) from.value = site.bannerFrom; if (to && site.bannerTo) to.value = site.bannerTo;
    const ship = $("#shipping-note");
    if (ship) ship.value = site.shipping_note != null ? site.shipping_note : (site.shippingNote || "");
    // also fill logo/banner preview
    paintBrandingNow(site);
  }).catch(() => paintHeroVideoNow({}));
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0]; if (!f) return;
    if (!window.JA_NET) { JA.toast("Hero upload needs the live server."); return; }
    const isVideo = /^video\//.test(f.type || "") || _VIDEO_EXT.test(String(f.name || ""));
    const cap = isVideo ? 40 * 1024 * 1024 : 8 * 1024 * 1024;
    if (f.size > cap) { if (msg) msg.textContent = "That file is " + (f.size / 1048576).toFixed(1) + " MB. Limit is " + (cap / (1024 * 1024)) + " MB."; JA.toast("Too big."); file.value = ""; return; }
    if (msg) msg.textContent = "Uploading " + (f.name || "asset") + "… keep this tab open.";
    const res = await window.JA_NET.api("api/admin/uploads/hero", { method: "POST", blob: f, field: "file", filename: f.name || "hero.mp4", timeout: 180000, });
    file.value = "";
    if (!res || !res.url) { if (msg) msg.textContent = (res && res.error) || "Upload failed."; JA.toast((res && res.error) || "Could not upload."); return; }
    const patch = res.kind === "video" ? { heroVideo: res.url, heroDoc: "", heroPoster: "" } : res.kind === "document" ? { heroVideo: "", heroDoc: res.url, heroPoster: "" } : { heroVideo: "", heroPoster: res.url, heroDoc: "" };
    const saved = await saveSiteConfig(patch);
    if (saved && saved.ok !== false) { if (msg) msg.textContent = res.kind === "video" ? "Done — homepage hero now plays your video." : res.kind === "document" ? "Done — homepage hero links to your document." : "Done — homepage hero uses your photo."; JA.toast("Hero asset is on the homepage."); paintHeroVideoNow(patch); }
    else { if (msg) msg.textContent = (saved && saved.error) || "Uploaded, but saving failed."; }
  });
  $("#hero-video-remove")?.addEventListener("click", async () => {
    if (!confirm("Remove the hero asset? Homepage goes back to default.")) return;
    const saved = await saveSiteConfig({ heroVideo: "", heroDoc: "", heroPoster: "" });
    if (saved && saved.ok !== false) { JA.toast("Hero removed."); paintHeroVideoNow({}); if (msg) msg.textContent = ""; } else JA.toast((saved && saved.error) || "Could not remove.");
  });
}
function paintBrandingNow(site) {
  site = site || {};
  const logoBox = $("#logo-now"); const logoRm = $("#logo-remove");
  const bannerBox = $("#shop-banner-now"); const bannerRm = $("#shop-banner-remove");
  const form = $("#set-form");
  // canonical site_settings column wins; the legacy alias is only a fallback
  const logoUrl = site.site_logo_url || site.logoUrl || "";
  const shopBannerUrl = site.shop_banner_url || site.shopBannerUrl || "";
  if (logoBox) {
    if (logoUrl) {
      logoBox.innerHTML = `<img class="adx-logo-preview" src="${JA.escape(JA.asset(logoUrl))}" alt="Logo" /><p class="admin-note">Live logo now.</p>`;
      if (logoRm) logoRm.hidden = false;
      if (form) form.dataset.logoUrl = logoUrl;
      if (form && form.elements && form.elements.site_logo_url) form.elements.site_logo_url.value = logoUrl;
    } else {
      logoBox.innerHTML = `<p class="empty">No custom logo — default logo.jpg shows. Upload to change sitewide.</p>`;
      if (logoRm) logoRm.hidden = true;
      if (form) form.dataset.logoUrl = "";
    }
  }
  if (bannerBox) {
    if (shopBannerUrl) {
      bannerBox.innerHTML = `<img class="adx-logo-preview" style="width:200px" src="${JA.escape(JA.asset(shopBannerUrl))}" alt="Shop banner" /><p class="admin-note">Live shop banner now.</p>`;
      if (bannerRm) bannerRm.hidden = false;
      if (form) form.dataset.shopBannerUrl = shopBannerUrl;
    } else {
      bannerBox.innerHTML = `<p class="empty">No custom shop banner — wordmark-bg.jpg shows. Upload to change sitewide.</p>`;
      if (bannerRm) bannerRm.hidden = true;
      if (form) form.dataset.shopBannerUrl = "";
    }
  }
}
function bindSiteBranding() {
  const logoFile = $("#logo-file"); const bannerFile = $("#shop-banner-file"); const msg = $("#branding-msg");
  const form = $("#set-form");
  if (!logoFile && !bannerFile) return;
  if (logoFile) {
    logoFile.addEventListener("change", async () => {
      const f = logoFile.files && logoFile.files[0]; if (!f) return;
      if (!window.JA_NET) { JA.toast("Logo upload needs live server."); return; }
      if (f.size > 6 * 1024 * 1024) { JA.toast("Logo too big — 6 MB max."); logoFile.value = ""; return; }
      if (msg) msg.textContent = "Uploading logo…";
      try {
        const res = await window.JA_NET.api("api/admin/uploads/image", { method: "POST", blob: f, field: "file", filename: f.name || "logo.jpg", timeout: 60000 });
        if (res && res.url) {
          const saved = await saveSiteConfig({ logoUrl: res.url });
          if (saved && saved.ok !== false) {
            JA.toast("Logo saved — live sitewide now.");
            if (msg) msg.textContent = "Logo live now.";
            if (form) form.dataset.logoUrl = res.url;
            // repaint from the server-confirmed row (canonical site_logo_url)
            paintBrandingNow(saved.site || { logoUrl: res.url });
          } else {
            const m = (saved && saved.error) || "Uploaded, but the logo could not be saved. No changes were made.";
            if (msg) msg.textContent = m; JA.toast(m);
          }
        } else JA.toast((res && res.error) || "Logo upload failed.");
      } catch (e) { JA.toast("Logo upload failed."); }
      logoFile.value = "";
    });
  }
  if (bannerFile) {
    bannerFile.addEventListener("change", async () => {
      const f = bannerFile.files && bannerFile.files[0]; if (!f) return;
      if (!window.JA_NET) { JA.toast("Banner upload needs live server."); return; }
      if (f.size > 6 * 1024 * 1024) { JA.toast("Banner too big — 6 MB max."); bannerFile.value = ""; return; }
      if (msg) msg.textContent = "Uploading shop banner…";
      try {
        const res = await window.JA_NET.api("api/admin/uploads/image", { method: "POST", blob: f, field: "file", filename: f.name || "banner.jpg", timeout: 60000 });
        if (res && res.url) {
          const saved = await saveSiteConfig({ shopBannerUrl: res.url });
          if (saved && saved.ok !== false) {
            JA.toast("Shop banner saved — live sitewide now.");
            if (msg) msg.textContent = "Shop banner live now.";
            if (form) form.dataset.shopBannerUrl = res.url;
            // repaint from the server-confirmed row (canonical shop_banner_url)
            paintBrandingNow(saved.site || { shopBannerUrl: res.url });
          } else {
            const m = (saved && saved.error) || "Uploaded, but the banner could not be saved. No changes were made.";
            if (msg) msg.textContent = m; JA.toast(m);
          }
        } else JA.toast((res && res.error) || "Banner upload failed.");
      } catch (e) { JA.toast("Banner upload failed."); }
      bannerFile.value = "";
    });
  }
  $("#logo-remove")?.addEventListener("click", async () => {
    if (!confirm("Remove custom logo? Default returns.")) return;
    const saved = await saveSiteConfig({ logoUrl: "" });
    if (saved && saved.ok !== false) { JA.toast("Logo removed."); paintBrandingNow(saved.site || {}); if (form) form.dataset.logoUrl = ""; if (form && form.elements && form.elements.site_logo_url) form.elements.site_logo_url.value = ""; }
    else JA.toast((saved && saved.error) || "Could not remove the logo. No changes were made.");
  });
  $("#shop-banner-remove")?.addEventListener("click", async () => {
    if (!confirm("Remove custom shop banner? Default returns.")) return;
    const saved = await saveSiteConfig({ shopBannerUrl: "" });
    if (saved && saved.ok !== false) { JA.toast("Shop banner removed."); paintBrandingNow(saved.site || {}); if (form) form.dataset.shopBannerUrl = ""; }
    else JA.toast((saved && saved.error) || "Could not remove the banner. No changes were made.");
  });
}
function bindShippingNote() {
  // Load existing shipping note into textarea
  fetch("api/site", { cache: "no-store" }).then((r) => r.ok ? r.json() : null).then((d) => {
    const site = (d && d.site) || {};
    const el = $("#shipping-note");
    if (el) el.value = site.shipping_note != null ? site.shipping_note : (site.shippingNote || "");
    paintBrandingNow(site);
  }).catch(()=>{});
}

async function bootAdmin() {
  await JA.ready;
  JA.mountChrome();
  await (JA.loadServerCategories ? JA.loadServerCategories() : Promise.resolve());
  const ok = await JA.isAdmin();
  if (ok) paintDesk(); else paintLogin();
}
document.addEventListener("DOMContentLoaded", bootAdmin);
