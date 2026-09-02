const $ = (s, r = document) => r.querySelector(s);

function fileToData(file, maxSize, quality) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error("No file"));
      return;
    }
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

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

let loginEmail = "";
let loginNeedsEmail = false;

function paintLogin(msg, needsEmail = loginNeedsEmail) {
  $("#admin-root").innerHTML = `
    <div class="adx-login">
      <div class="adx-login-card">
        <img class="adx-login-logo" src="images/brand/logo.jpg" alt="Jaura Store" />
        <h1 class="serif-title">Jaura Store</h1>
        <p class="adx-login-sub" data-no-i18n>Sign in to manage your store</p>
        ${msg ? `<p class="admin-err">${JA.escape(msg)}</p>` : ""}
        <form id="login-form" class="field adx-login-form">
          ${needsEmail ? `<label>Email</label>
          <input type="email" name="email" required autocomplete="username" value="${JA.escape(loginEmail)}" />` : ""}
          <label ${needsEmail ? 'style="margin-top:14px"' : ""} data-no-i18n>Password</label>
          <input type="password" name="password" required autocomplete="current-password" placeholder="Your admin password" />
          <button class="btn adx-login-btn" id="login-btn" data-no-i18n>Sign in</button>
        </form>
        <button type="button" class="wix-link-btn" id="forgot-btn" style="margin-top:16px">Forgot password? Reset it by email</button>
        <div id="otp-slot"></div>
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

  $("#forgot-btn").addEventListener("click", () => paintOtpRequest());
}

function paintOtpRequest(msg) {
  const slot = $("#otp-slot");
  if (!slot) return;
  slot.innerHTML = `
    <div class="otp-box">
      <h3>Reset your password</h3>
      <p class="admin-note">Enter the admin email so we know which account, then we send a 6-digit code to that email. Enter it below with a new password.</p>
      ${msg ? `<p class="admin-err">${JA.escape(msg)}</p>` : ""}
      <form id="otp-req" class="field">
        <label>Admin email</label>
        <input type="email" name="email" required value="${JA.escape(loginEmail)}" />
        <button class="btn" style="margin-top:14px" id="otp-send">Send code</button>
      </form>
      <div id="otp-step2"></div>
    </div>`;
  $("#otp-req").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = String(new FormData(e.target).get("email") || "").trim();
    loginEmail = email;
    const b = $("#otp-send");
    b.disabled = true; b.textContent = "Sending…";
    const r = await JA.requestOtp(email);
    b.disabled = false; b.textContent = "Send code";
    if (!r.ok) { paintOtpRequest(r.error || "Could not send the code."); return; }
    $("#otp-step2").innerHTML = `
      <form id="otp-do" class="field">
        <label>6-digit code</label>
        <input name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" required autocomplete="one-time-code" />
        <label style="margin-top:12px">New password</label>
        <input type="password" name="newPassword" required autocomplete="new-password" />
        <p class="admin-note">At least 10 characters, with upper case, lower case and a number.</p>
        <button class="btn" style="margin-top:12px">Set new password</button>
      </form>
      <p class="admin-note">${JA.escape(r.message || "")}</p>`;
    $("#otp-do").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const v = await JA.verifyOtp(email, String(fd.get("code") || "").trim());
      if (!v.ok) { JA.toast(v.error || "That code did not work."); return; }
      const rp = await JA.resetPassword(String(fd.get("newPassword") || ""));
      if (!rp.ok) { JA.toast(rp.error || "Could not set the password."); return; }
      JA.toast("Password updated. Sign in with it now.");
      paintLogin("Password updated — sign in with your new password.");
    });
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
    body = `<video class="wix-tile-video" src="${url}" ${pos} muted loop playsinline preload="metadata"></video>`;
  } else if (kind === "doc" && !pending) {
    const label = /\.docx?$/i.test(String(imgSrc(src) || "")) ? "DOC" : "PDF";
    body = `<a class="media-doc-chip" href="${url}" target="_blank" rel="noopener">${label}<span>View / Download</span></a>`;
  } else {
    body = `<img src="${url}" alt="" />`;
  }
  return `
    <div class="wix-tile${main}${pendingCls}" data-img-i="${i}">
      ${body}
      ${i === 0 ? `<span>Main</span>` : `<span>${i + 1}</span>`}
      <button type="button" class="wix-tile-x" data-del-img="${i}" aria-label="Remove">×</button>
    </div>`;
}
function mediaStripHTML(imgs) {
  const firstImg = (imgs || []).find((s) => mediaKind(s) === "image");
  const poster = firstImg ? imgSrc(firstImg) : "";
  const tiles = (imgs || []).map((src, i) => mediaTileHTML(src, i, poster)).join("");
  const plus = (imgs || []).length < 20 ? `<label class="wix-tile wix-plus">+<input type="file" id="more-media" accept="image/*,video/*" multiple hidden /></label>` : "";
  return `<div class="wix-media-row">${tiles}${plus}</div>
    <p class="admin-note">Drag &amp; drop, or tap + to pick several at once. Photos up to 6 MB, videos up to 40 MB. Your photos &amp; videos stay as they are — up to 20 items.</p>
    <button type="button" class="wix-view-media" id="view-media">View All Media (${(imgs || []).length}/20) ›</button>`;
}
function editorOptions(p) {
  if (p && p.options && p.options.length) return p.options;
  if (p && p.colors && p.colors.length) return [{ title: "Colour", type: "COLOR", values: p.colors }];
  return [];
}
function optionRowHTML(o, i) {
  const vals = (o && o.values) || [];
  return `
    <div class="wix-opt" data-opt-row>
      <div class="wix-opt-top">
        <strong>${JA.escape((o && o.title) || "New option")}</strong>
        <button type="button" class="wix-opt-del" data-del-opt>Remove</button>
      </div>
      <div class="wix-chips">${vals.map((v) => `<em>${JA.escape(v)}</em>`).join("")}</div>
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
    const strong = row.querySelector(".wix-opt-top strong");
    if (strong) strong.textContent = title;
    const vals = (row.querySelector('input[name^="opt-vals"]')?.value || "").split(",").map((s) => s.trim()).filter(Boolean);
    const chips = row.querySelector(".wix-chips");
    if (chips) chips.innerHTML = vals.map((v) => `<em>${JA.escape(v)}</em>`).join("");
  });
  const count = document.getElementById("opt-count");
  if (count) count.textContent = `${document.querySelectorAll("[data-opt-row]").length}/6`;
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
  if (n >= 6) { JA.toast("Maximum 6 options."); return; }
  const wrap = document.createElement("div");
  wrap.innerHTML = optionRowHTML({ title: title || "", values: values ? String(values).split(",").map((s) => s.trim()).filter(Boolean) : [] }, n);
  box.appendChild(wrap.firstElementChild);
  refreshOptionChips();
}
function bindMedia() {
  const box = document.getElementById("media-box");
  if (!box || box.dataset.bound === "1") return;
  box.dataset.bound = "1";
  // Drag & drop photos straight onto the media strip (Wix-style).
  ["dragenter", "dragover"].forEach((ev) => box.addEventListener(ev, (e) => {
    e.preventDefault();
    box.classList.add("is-drop");
  }));
  ["dragleave", "drop"].forEach((ev) => box.addEventListener(ev, (e) => {
    e.preventDefault();
    box.classList.remove("is-drop");
  }));
  box.addEventListener("drop", (e) => {
    const files = [...((e.dataTransfer && e.dataTransfer.files) || [])].filter((f) => {
      const t = String(f.type || "");
      const n = String(f.name || "");
      if (/^(image|video)\//.test(t)) return true;
      if (_VIDEO_EXT.test(n) || /\.(jpe?g|png|webp|gif|avif|heic)$/i.test(n)) return true;
      // Some phones/cameras hand over a file with an empty MIME type.
      // Keep it so magic-bytes on /api/admin/uploads/product can classify it.
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
    // Empty/unknown MIME goes to /product so the server can sniff magic bytes
    // (the image-only route rejects video).
    const endpoint = (isVideo || !looksImage) ? "api/admin/uploads/product" : "api/admin/uploads/image";
    // Say the limit up front, not after a failed upload.
    const maxVideo = 40 * 1024 * 1024, maxPhoto = 6 * 1024 * 1024;
    if (isVideo && file.size > maxVideo) {
      JA.toast("That video is " + (file.size / 1048576).toFixed(1) + " MB. The limit is 40 MB — export it smaller (720p is plenty).");
      return;
    }
    if (!isVideo && file.size > maxPhoto) {
      JA.toast("That photo is " + (file.size / 1048576).toFixed(1) + " MB. The limit is 6 MB — pick a smaller photo.");
      return;
    }
    if (!window.JA_NET) {                       // static hosting fallback
      try { window.__editImages.push(await fileToData(file)); } catch (err) {}
      box.innerHTML = mediaStripHTML(window.__editImages);
      return;
    }
    const preview = URL.createObjectURL(file);
    const idx = window.__editImages.push({ pending: true, preview, video: isVideo }) - 1;
    box.innerHTML = mediaStripHTML(window.__editImages);
    const res = await window.JA_NET.api(endpoint, {
      method: "POST",
      blob: file,
      field: "file",
      filename: file.name || (isVideo ? "video.mp4" : "photo.jpg"),
      queue: true,
      timeout: 300000,
      label: isVideo ? "Video" : "Photo",
    });
    if (res && res.url) {
      window.__editImages[idx] = res.url;
      JA.toast(isVideo ? "Video uploaded." : "Photo uploaded.");
    } else if (res && res.queued) {
      // it will finish by itself; keep the placeholder so nothing looks lost
      window.__jaPendingPhoto = (window.__jaPendingPhoto || 0) + 1;
    } else {
      window.__editImages[idx] = { pending: true, preview, video: isVideo, failed: true };
      JA.toast((res && res.error) || "That file did not upload. It will retry by itself.");
    }
    if (document.getElementById("media-box")) {
      document.getElementById("media-box").innerHTML = mediaStripHTML(window.__editImages);
    }
  }
  box.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del-img]");
    if (del) {
      e.preventDefault();
      const i = Number(del.getAttribute("data-del-img"));
      if (!window.__editImages) window.__editImages = [];
      window.__editImages.splice(i, 1);
      box.innerHTML = mediaStripHTML(window.__editImages);
      return;
    }
    if (e.target.closest("#view-media")) {
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
      <p>${JA.starsHTML ? JA.starsHTML(r.stars) : ""} <strong>${JA.escape(r.name || "")}</strong></p>
      <p>${JA.escape(r.note || "")}</p>
      <button type="button" class="wix-opt-del" data-del-rev="${JA.escape(r.at || "")}">Remove</button>
    </article>`).join("");
}
function bindReviewsAdmin(id) {
  const box = document.getElementById("rev-admin");
  const paint = () => {
    if (box) box.innerHTML = reviewsAdminHTML(id);
  };
  document.getElementById("rev-add")?.addEventListener("click", () => {
    const name = (document.getElementById("rev-name")?.value || "").trim();
    const note = (document.getElementById("rev-note")?.value || "").trim();
    const stars = Number(document.getElementById("rev-stars")?.value || 5);
    if (!note) { JA.toast("Type the customer note first."); return; }
    const pid = id && id !== "new" ? id : (document.querySelector("#prod-form [name=id]")?.value || "");
    if (pid && JA.addReview) {
      JA.addReview(pid, { name, note, stars });
    } else {
      window.__editReviews = (window.__editReviews || []).concat([{ name: name || "Customer", note, stars, at: new Date().toISOString() }]);
    }
    if (document.getElementById("rev-name")) document.getElementById("rev-name").value = "";
    if (document.getElementById("rev-note")) document.getElementById("rev-note").value = "";
    paint();
    JA.toast("Review added.");
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
  /* Read whatever is typed in the per-option stock boxes right now. */
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
      <p class="admin-note">Add an option above (Colour, Size…) and a stock box appears here for each choice — exactly like Wix. Until then the single Quantity below is used.</p>`;
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
    const line = was > now
      ? "<s>" + JA.money(was, "CFA") + "</s> " + JA.money(now, "CFA")
      : JA.money(now, "CFA");
    el.innerHTML = "Website will show " + line + " · converted from ₦ at 1 ₦ = 0.44 F CFA.";
  };
  form.addEventListener("input", paint);
  paint();
}
function productForm(p = {}) {
  const cats = (JA.categories ? JA.categories() : JA.CATEGORIES).map((c) =>
    `<option value="${c.id}" ${p.category === c.id ? "selected" : ""}>${JA.escape(c.name)}</option>`
  ).join("");
  window.__editImages = productImages(p);
  const opts = editorOptions(p);
  const inStock = p.id ? Number(p.stock) > 0 : true;   // a brand-new product starts in stock
  return `<form id="prod-form" class="wix-edit">
    <button type="button" class="wix-back" id="cancel-edit">← Store Products</button>
    <h2>Product</h2>
    <div id="media-box">${mediaStripHTML(window.__editImages)}</div>
    <div class="field"><label>Product Name</label><input name="name" required maxlength="80" value="${JA.escape(p.name || "")}" /></div>
    <div class="field"><label>Product Name (French — shown when the site is in French)</label><input name="nameFr" maxlength="80" value="${JA.escape(p.nameFr || "")}" placeholder="Optional" /></div>
    <input type="hidden" name="id" value="${p.id || ""}" />
    <div class="wix-2">
      <div class="field"><label>Price ₦</label><div class="wix-price"><input name="priceNgn" type="number" min="0" required value="${p.priceNgn || ""}" /><i>₦</i></div></div>
      <div class="field"><label>Strikethrough ₦</label><div class="wix-price"><input name="compareNgn" type="number" min="0" value="${p.compareNgn || ""}" /><i>₦</i></div></div>
    </div>
    <p class="admin-note" id="cfa-preview">CFA on the website is converted from Naira at 1 ₦ = 0.44 F CFA. You only enter ₦.</p>
    <div class="field"><label>Add a description</label><textarea name="description" rows="3">${JA.escape(p.description || "")}</textarea></div>
    <div class="field"><label>Ribbon</label>
      <select name="badge">
        <option value="">None</option>
        ${["sale", "new", "bestseller"].map((b) => `<option value="${b}" ${p.badge === b ? "selected" : ""}>${b}</option>`).join("")}
      </select>
    </div>
    <label class="wix-tog"><span>Show in online store</span>
      <input type="checkbox" name="online" ${p.online === false ? "" : "checked"} />
    </label>
    <div class="field"><label>Category</label><select name="category">${cats}</select></div>
    <h3>Product options <small id="opt-count">${opts.length}/6</small></h3>
    <div id="opt-box">${optionBlockHTML(opts)}</div>
    <div class="wix-opt-presets">
      <button type="button" data-preset="Colour">+ Colour</button>
      <button type="button" data-preset="Size">+ Size</button>
      <button type="button" data-preset="Type">+ Type</button>
      <button type="button" data-preset="Length">+ Length</button>
      <button type="button" data-preset="Scent">+ Scent</button>
    </div>
    <button type="button" class="wix-link-btn" id="add-opt">+ Add Option</button>
    <div id="var-box">${optionStockHTML({ ...p, options: opts })}</div>
    <h3>Inventory</h3>
    <div class="wix-2">
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
    <div class="wix-2">
      <div class="field"><label>Customer name</label><input id="rev-name" maxlength="60" placeholder="e.g. Ada" /></div>
      <div class="field"><label>Stars</label>
        <select id="rev-stars"><option value="5">5</option><option value="4">4</option><option value="3">3</option><option value="2">2</option><option value="1">1</option></select>
      </div>
    </div>
    <div class="field"><label>Customer note</label><textarea id="rev-note" rows="2" maxlength="600" placeholder="Their comment"></textarea></div>
    <button type="button" class="wix-link-btn" id="rev-add">+ Add review to this product</button>
    <button class="btn wix-save" type="submit">${p.id ? "Save" : "Add a Product"}</button>
    ${p.id ? `<button type="button" class="wix-del-prod" data-del="${JA.escape(p.id)}">Delete this product</button>` : ""}
  </form>`;
}

async function handleProductSubmit(e, existing) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const saveBtn = e.target.querySelector(".wix-save");
  let rawImages = (window.__editImages || []).filter(Boolean);

  // Photos upload on their own. Wait for them instead of refusing the save:
  // a slow connection used to mean the admin pressed Save and lost the
  // product. Give the outbox a fair chance to drain, then carry on.
  if (rawImages.some((s) => typeof s === "object") && window.JA_NET) {
    JA.toast("Finishing the photo upload…");
    const deadline = Date.now() + 300000;
    while (rawImages.some((s) => typeof s === "object") && Date.now() < deadline) {
      await window.JA_NET.flush().catch(() => {});
      await new Promise((r) => setTimeout(r, 700));
      rawImages = (window.__editImages || []).filter(Boolean);
    }
  }
  const stillUploading = rawImages.filter((s) => typeof s === "object");
  let images = rawImages.filter((s) => typeof s === "string" && s).slice(0, 20);
  if (!images.length && existing) {
    images = (existing.images && existing.images.length) ? existing.images.slice(0, 20) : (existing.image ? [existing.image] : []);
  }
  if (!images.length) {
    JA.toast(stillUploading.length
      ? "Your photo is still uploading. Wait a moment, then press Save again."
      : "Please add a photo from your gallery. We never replace it with AI.");
    return;
  }
  const image = images[0] || "";
  if (!image) {
    JA.toast("Please upload a photo from your gallery. We never replace it with AI.");
    return;
  }
  const name = fd.get("name").trim();
  const id = fd.get("id") || ("jau-" + Date.now().toString(36));
  const num = (k) => {
    const v = fd.get(k);
    return v === "" || v == null ? null : Number(v);
  };
  const status = String(fd.get("stockStatus") || "in");
  let stock = num("stock");
  if (status === "out") stock = 0;
  else if (!(stock > 0)) stock = (existing && Number(existing.stock) > 0) ? Number(existing.stock) : 24;
  const options = collectOptions(e.target);
  // Per-option stock (Wix-style): if any per-choice quantity was typed, the
  // sum of those quantities IS the product quantity.
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
  if (!(priceNgn > 0)) {
    JA.toast("Enter the ₦ price. CFA is converted on the website.");
    return;
  }
  const toCfa = JA.toCfa || ((n) => Math.round(Number(n || 0) * 0.44));
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving…"; }
  const res = await JA.upsertProduct({
      ...(existing || {}),
      id,
      sku: fd.get("sku") || existing?.sku || ("JAU-" + Date.now().toString(36).toUpperCase().slice(-6)),
      slug: existing?.slug || slugify(name) || id,
      name,
      category: fd.get("category"),
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
  });
  if (window.__editReviews && JA.setReviews) JA.setReviews(id, window.__editReviews);
  if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = existing ? "Save" : "Add a Product"; }
  if (res && res.ok === false && res.error) {
    JA.toast("Saved on this device — it will sync when you are back online.");
  } else {
    JA.toast((res && res.queued) ? "Saved on this device — uploading…"
      : (status === "out" ? "Live now · Out of stock." : "Live on the store now · " + images.length + " photo(s)."));
  }
  editingId = null;
  paintDesk("products");
}

function productsTable() {
  const all = JA.products();
  const cats = JA.categories ? JA.categories() : JA.CATEGORIES;
  const catName = (id) => (cats.find((c) => c.id === id) || {}).name || id || "";
  const cards = all.map((p) => {
    const ngnNow = Number(p.priceNgn) || 0;
    const ngnWas = Number(p.compareNgn) || 0;
    const ngn = ngnNow > 0 ? JA.money(ngnNow, "NGN") : "";
    const ngnStrike = ngnWas > ngnNow ? JA.money(ngnWas, "NGN") : "";
    const cfaNowN = ngnNow > 0 ? (JA.toCfa ? JA.toCfa(ngnNow) : Math.round(ngnNow * 0.44)) : (Number(p.priceCfa) || 0);
    const cfaNow = JA.money(cfaNowN, "CFA");
    const stockN = Number(p.stock) || 0;
    const pill = stockN <= 0 ? `<span class="adx-pill out">Out of stock</span>`
      : stockN <= 5 ? `<span class="adx-pill low">${stockN} left</span>`
      : `<span class="adx-pill in">${stockN} in stock</span>`;
    const rowq = JA.escape((p.name + " " + (p.nameFr || "") + " " + (p.sku || "") + " " + p.category).toLowerCase());
    return `<article class="adx-card" data-row="${rowq}" data-cat="${JA.escape(p.category || "")}" data-edit="${JA.escape(p.id)}" role="button" tabindex="0" aria-label="Edit ${JA.escape(p.name)}">
      <div class="adx-card-pic">
        <img src="${JA.asset(p.image)}" alt="" loading="lazy" />
        ${p.badge ? `<span class="adx-ribbon">${JA.escape(p.badge)}</span>` : ""}
        ${p.online === false ? `<span class="adx-hidden-tag">Hidden</span>` : ""}
      </div>
      <div class="adx-card-body">
        <strong>${JA.escape(p.name)}</strong>
        <span class="adx-card-cat">${JA.escape(catName(p.category))}</span>
        <span class="adx-card-price">${ngnStrike ? `<s>${ngnStrike}</s> ` : ""}${ngn || cfaNow}</span>
        <span class="adx-card-cfa">${ngn ? cfaNow : ""}</span>
        ${pill}
      </div>
      <button type="button" class="adx-card-del" data-del="${JA.escape(p.id)}" aria-label="Delete ${JA.escape(p.name)}" title="Delete">
        <svg viewBox="0 0 24 24"><path d="M6 7h12M9 7V5h6v2m-8 0l1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      </button>
    </article>`;
  }).join("");
  const catOpts = cats.map((c) => `<option value="${JA.escape(c.id)}">${JA.escape(c.name)}</option>`).join("");
  const backBtn = dashCat
    ? `<button type="button" class="btn btn-line" id="back-all-products">← All products</button>`
    : "";
  const filteredNote = dashCat ? ` · <strong>${JA.escape(catName(dashCat))}</strong>` : "";
  return `<div class="adx-list-head">
      <button type="button" class="btn adx-add-btn" id="add-product">+ New Product</button>
      ${backBtn}
      <div class="adx-filters">
        <input id="prod-search" type="search" placeholder="Search products…" autocomplete="off" />
        <select id="prod-cat" aria-label="Filter by category">
          <option value="">All categories</option>${catOpts}
        </select>
      </div>
      <p class="adx-count"><span id="prod-count">${all.length}</span> of ${all.length} products${filteredNote} · <button type="button" class="wix-cats-link" data-tab="categories">Manage categories</button></p>
    </div>
    <div class="adx-grid" id="prod-grid">${cards}</div>
    <p class="empty" id="prod-none" hidden>No products match that search.</p>`;
}

function applyProductFilter() {
  const q = String(document.getElementById("prod-search")?.value || "").toLowerCase().trim();
  const cat = String(document.getElementById("prod-cat")?.value || "");
  let shown = 0;
  document.querySelectorAll("#prod-grid .adx-card").forEach((row) => {
    const okQ = !q || (row.getAttribute("data-row") || "").includes(q);
    const okC = !cat || row.getAttribute("data-cat") === cat;
    const on = okQ && okC;
    row.style.display = on ? "" : "none";
    if (on) shown += 1;
  });
  const count = document.getElementById("prod-count");
  if (count) count.textContent = shown;
  const none = document.getElementById("prod-none");
  if (none) none.hidden = shown > 0;
}

let dashRange = 30;
let dashTimer = null;
let dashCat = "";   // when set, the products tab is filtered to one category

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
    <div class="adx-periods" id="an-periods">
      <div class="adx-period"><span>Today</span><b>…</b></div>
      <div class="adx-period"><span>This week</span><b>…</b></div>
      <div class="adx-period"><span>This month</span><b>…</b></div>
    </div>
    <div class="stats" id="an-kpis"><div class="stat"><span class="kicker">Loading</span><b>…</b></div></div>
    <h3 class="admin-h">Live on the store right now <span class="adx-live-dot" aria-hidden="true"></span></h3>
    <div id="an-live-box" class="live-box">Loading live visitors…</div>
    <div id="an-feed" class="adx-feed"><p class="empty">Loading the live activity feed…</p></div>
    <h3 class="admin-h">Sales over time</h3>
    <div class="an-chart" id="an-sales"><p class="empty">Loading…</p></div>
    <h3 class="admin-h">Visitors &amp; page views</h3>
    <div class="an-chart" id="an-chart"><p class="empty">Loading…</p></div>
    <div class="adx-2col">
      <div><h3 class="admin-h">Top viewed products</h3><div id="an-products" class="empty">Loading…</div></div>
      <div><h3 class="admin-h">Top selling products</h3><div id="an-sellers" class="empty">Loading…</div></div>
    </div>
    <h3 class="admin-h">Most visited pages</h3>
    <div id="an-pages" class="empty">Loading…</div>
    <h3 class="admin-h">Conversion</h3>
    <div class="stats" id="an-conv"></div>
    <div id="an-revenue"></div>
    <h3 class="admin-h">Visitor locations</h3>
    <div id="an-loc" class="empty">Loading…</div>
    <h3 class="admin-h">Latest orders</h3>
    <div id="an-orders" class="empty">Loading…</div>`;
}

function dayLabel(day) {
  const d = new Date(day + "T00:00:00Z");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function trafficChart(series) {
  const max = Math.max(1, ...series.map((d) => Math.max(d.views, d.visitors)));
  // Thin the labels so a wide range never collides.
  const step = Math.max(1, Math.ceil((series || []).length / 10));
  return `<div class="an-scroll"><div class="an-bars">${(series || []).map((d, i) => {
    const showLab = i % step === 0;
    return `
      <div class="an-col" title="${esc(d.day)} · ${d.views} view(s) · ${d.visitors} visitor(s)">
        <div class="an-bar-wrap">
          <div class="an-bar an-bar-views" style="height:${Math.round((d.views / max) * 120)}px" title="${d.views} page views"></div>
          <div class="an-bar an-bar-visitors" style="height:${Math.round((d.visitors / max) * 120)}px" title="${d.visitors} visitors"></div>
        </div>
        ${showLab ? `<span>${esc(dayLabel(d.day))}</span>` : ""}
        ${showLab ? `<em>${d.views}</em>` : ""}
      </div>`;
  }).join("")}</div></div>
    <p class="admin-note"><span class="an-key an-key-views"></span> Page views &nbsp; <span class="an-key an-key-visitors"></span> Unique visitors</p>`;
}

function tableHTML(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function salesChart(series) {
  if (!series || !series.length) return `<p class="empty">No sales in this period yet.</p>`;
  const max = Math.max(1, ...series.map((d) => d.revenue));
  const total = series.reduce((n, d) => n + (d.revenue || 0), 0);
  const orders = series.reduce((n, d) => n + (d.orders || 0), 0);
  const step = Math.max(1, Math.ceil(series.length / 10));
  return `<div class="an-scroll"><div class="an-bars">${series.map((d, i) => {
    const showLab = i % step === 0;
    return `
      <div class="an-col" title="${esc(d.day)} · ${d.orders} order(s) · ${esc(JA.money(d.revenue, "NGN"))}">
        <div class="an-bar-wrap">
          <div class="an-bar an-bar-sales" style="height:${Math.round((d.revenue / max) * 120)}px"></div>
        </div>
        ${showLab ? `<span>${esc(dayLabel(d.day))}</span>` : ""}
        ${showLab ? `<em>${d.orders || ""}</em>` : ""}
      </div>`;
  }).join("")}</div></div>
    <p class="admin-note">${orders} order(s) · ${esc(JA.money(total, "NGN"))} in this period. The number under each bar is that day's orders.</p>`;
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
  else if (a.type === "purchase") {
    icon = "🎉";
    text = `Placed an order${a.value ? " · <strong>" + esc(JA.money(a.value, a.currency || "NGN")) + "</strong>" : ""}`;
  }
  return `<li class="adx-feed-row is-${esc(a.type)}">
    <i>${icon}</i>
    <div>${text}${where ? ` <span class="adx-feed-geo">· ${esc(where)}</span>` : ""}</div>
    <em>${esc(timeAgo(a.at))}</em>
  </li>`;
}

function renderLive(visitors, activity) {
  const liveBox = $("#an-live-box");
  if (liveBox) {
    liveBox.innerHTML = (visitors || []).length
      ? visitors.map((v) => `<div class="live-row"><i></i><span>${esc([v.city, v.country].filter(Boolean).join(", ") || "Visitor")}</span><em>on ${esc(v.page || v.path || "the store")}</em><small>${esc(timeAgo(v.at))}</small></div>`).join("")
      : `<p class="empty">Nobody is browsing right now.</p>`;
  }
  const feed = $("#an-feed");
  if (feed) {
    feed.innerHTML = (activity || []).length
      ? `<ul class="adx-feed-list">${activity.slice(0, 25).map(activityLine).join("")}</ul>`
      : `<p class="empty">No activity in the last hour. It appears here the moment someone opens a product, adds to cart or orders.</p>`;
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
  } catch (e) { /* offline — the 30s full refresh will retry */ }
}

async function fillAnalytics() {
  const data = await JA.adminAnalytics(dashRange);
  if (!data) {
    const box = $("#panel-analytics");
    if (box) box.innerHTML = `<p class="empty">Could not load analytics. Check your connection and press Refresh.</p>`;
    return;
  }
  if (!$("#an-kpis")) return;   // the admin switched tabs while we were loading

  const t = data.totals || {};
  const c = data.conversion || {};
  const pr = data.periods || {};

  const periodCard = (label, p) => {
    p = p || {};
    return `<div class="adx-period">
      <span>${label}</span>
      <b>${p.visits || 0}</b>
      <small>${p.pageViews || 0} page views · ${p.visitors || 0} visitor(s)</small>
      <small class="adx-period-rev">${p.orders || 0} order(s)${p.revenue ? " · " + esc(JA.money(p.revenue, "NGN")) : ""}</small>
    </div>`;
  };
  $("#an-periods").innerHTML =
    periodCard("Today", pr.today) + periodCard("This week", pr.week) + periodCard("This month", pr.month);

  $("#an-kpis").innerHTML = [
    ["Live now", t.liveNow || 0, "on the site"],
    ["Unique visitors", t.uniqueVisitors || 0, `${t.newVisitors || 0} new`],
    ["Visits", t.visits || 0, "sessions"],
    ["Page views", t.pageViews || 0, ""],
    ["Orders", c.orders || 0, `${c.units || 0} items`],
    ["Revenue", (c.revenueByCurrency || []).map((r) => JA.money(r.value, r.currency)).join(" · ") || "—", `last ${dashRange} days`],
  ].map(([k, v, s]) => `<div class="stat"><span class="kicker">${k}</span><b>${v}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");

  renderLive(data.live || [], data.activity || []);

  $("#an-sales").innerHTML = salesChart(data.sales || []);
  $("#an-chart").innerHTML = trafficChart(data.series || []);

  const pages = data.topPages || [];
  $("#an-pages").innerHTML = pages.length
    ? tableHTML(["Page", "Views", "Visitors"],
      pages.map((p) => `<tr><td>${esc(p.path)}</td><td>${p.views}</td><td>${p.visitors}</td></tr>`).join(""))
    : `<p class="empty">No page views yet.</p>`;

  const prods = data.topProducts || [];
  $("#an-products").innerHTML = prods.length
    ? tableHTML(["Product", "Views", "In carts"],
      prods.slice().sort((a, b) => (b.views || 0) - (a.views || 0)).slice(0, 8)
        .map((p) => `<tr><td>${esc(p.name || p.productId)}</td><td>${p.views || 0}</td><td>${p.carts || 0}</td></tr>`).join(""))
    : `<p class="empty">No product activity yet. Open the shop and tap a product to start the count.</p>`;

  const sellers = prods.filter((p) => (p.purchases || 0) > 0 || (p.carts || 0) > 0)
    .sort((a, b) => (b.purchases || 0) - (a.purchases || 0) || (b.carts || 0) - (a.carts || 0)).slice(0, 8);
  $("#an-sellers").innerHTML = sellers.length
    ? tableHTML(["Product", "Sold", "In carts"],
      sellers.map((p) => `<tr><td>${esc(p.name || p.productId)}</td><td>${p.purchases || 0}</td><td>${p.carts || 0}</td></tr>`).join(""))
    : `<p class="empty">No sales recorded yet.</p>`;

  $("#an-conv").innerHTML = [
    ["Total sales", (c.revenueByCurrency || []).map((r) => JA.money(r.value, r.currency)).join(" · ") || "—", `${c.orders || 0} orders`],
    ["Average order value", c.averageOrderValue ? JA.money(c.averageOrderValue, (c.revenueByCurrency || [{}])[0].currency) : "—", ""],
    ["Checkout attempts", c.checkoutAttempts || 0, `${c.checkoutCompletionRate || 0}% completed`],
    ["Added to cart", c.cartAdds || 0, `${c.productViews || 0} product views`],
    ["Visit → order", `${c.visitToOrderRate || 0}%`, "conversion rate"],
  ].map(([k, v, s]) => `<div class="stat"><span class="kicker">${k}</span><b>${v}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");

  const status = c.statusBreakdown || [];
  $("#an-revenue").innerHTML = status.length
    ? `<p class="admin-note">${status.map((s) => `${esc(s.status)}: ${s.n}`).join(" · ")}</p>`
    : "";

  const locs = data.locations || [];
  $("#an-loc").innerHTML = locs.length
    ? tableHTML(["Location", "Visitors"], locs.map((l) => `<tr><td>${esc([l.city, l.country].filter(Boolean).join(", "))}</td><td>${l.visitors}</td></tr>`).join(""))
    : `<p class="empty">No locations recorded yet.</p>`;

  const orders = data.recentOrders || [];
  $("#an-orders").innerHTML = orders.length
    ? tableHTML(["Order", "Customer", "Total", "Status"],
      orders.map((o) => `<tr><td>${esc(o.id)}</td><td>${esc(o.customer_name || "")}</td><td>${esc(JA.money(o.total, o.currency))}</td><td><span class="status-pill ${esc(o.status || "pending")}">${esc(orderStatusLabel(o.status))}</span></td></tr>`).join(""))
    : `<p class="empty">No orders yet.</p>`;

  document.querySelectorAll("[data-range]").forEach((b) => {
    b.onclick = () => { dashRange = Number(b.dataset.range); paintDesk("analytics"); };
  });
  const custom = $("#an-custom");
  const customLabel = document.querySelector(".an-rng-custom");
  if (customLabel) customLabel.addEventListener("click", () => {
    if (custom) { custom.hidden = false; custom.focus(); custom.select(); }
  });
  if (custom) custom.addEventListener("change", () => {
    const v = Math.max(1, Math.min(400, Number(custom.value) || 30));
    dashRange = v; custom.value = v; paintDesk("analytics");
  });
  const ref = $("#an-refresh");
  if (ref) ref.onclick = () => { paintDesk("analytics"); JA.toast("Refreshed."); };
}

let liveTimer = null;

function startDashTimer() {
  clearInterval(dashTimer);
  dashTimer = setInterval(() => {
    if (document.body.dataset.page !== "admin") return;
    const on = document.querySelector("#panel-analytics.is-on");
    if (on && !document.hidden) fillAnalytics();
  }, 30000);
  clearInterval(liveTimer);
  liveTimer = setInterval(() => {
    if (document.body.dataset.page !== "admin") return;
    const on = document.querySelector("#panel-analytics.is-on");
    if (on && !document.hidden && $("#an-feed")) fillLiveFeed();
  }, 10000);
}

let serverOrders = [];

function orderStatusLabel(s) {
  return s === "confirmed" ? "Confirmed"
    : s === "declined" ? "Declined"
    : s === "past" ? "Past" : "Pending";
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-receipt-open]");
  if (btn) {
    openReceiptModal(btn.getAttribute("data-receipt-open"),
                     btn.getAttribute("data-receipt-label") || "Payment receipt",
                     btn.getAttribute("data-receipt-name") || "receipt");
  }
});

function fileTypeOf(url) {
  const u = String(url || "").split("?")[0].toLowerCase();
  if (/\.pdf$/.test(u)) return "pdf";
  if (/\.(png|jpe?g|webp|gif|avif|heic)$/.test(u)) return "image";
  return "other";
}

/* Receipts open inside an in-app modal, not by forcing a browser download.
 * Images render directly; PDF/DOC/DOCX are fetched as a Blob and shown in an
 * embedded viewer (the stored file is still served as an attachment). */
function closeReceiptModal() {
  const mod = document.getElementById("receipt-modal");
  if (mod) mod.classList.remove("is-open");
}

function openReceiptModal(url, label, name) {
  const kind = fileTypeOf(url);
  const title = (label || "Receipt");
  const fname = esc(name || String(url).split("/").pop() || "receipt");
  let mod = document.getElementById("receipt-modal");
  if (!mod) {
    mod = document.createElement("div");
    mod.id = "receipt-modal";
    mod.className = "receipt-modal";
    mod.innerHTML = `<div class="receipt-modal-card">
        <button type="button" class="receipt-modal-close" aria-label="Close">×</button>
        <div class="receipt-modal-head"></div>
        <div class="receipt-modal-body"></div>
        <div class="receipt-modal-actions"></div>
      </div>`;
    document.body.appendChild(mod);
    mod.addEventListener("click", (e) => {
      if (e.target === mod || e.target.closest(".receipt-modal-close")) closeReceiptModal();
    });
  }
  mod.classList.add("is-open");
  const head = mod.querySelector(".receipt-modal-head");
  const body = mod.querySelector(".receipt-modal-body");
  const actions = mod.querySelector(".receipt-modal-actions");
  head.textContent = title;
  actions.innerHTML = `<a class="btn btn-line" href="${esc(url)}" target="_blank" rel="noopener">Open full size</a>
    <a class="btn btn-line" href="${esc(url)}" download="${fname}">Download ${kind === "pdf" ? "PDF" : "file"}</a>`;
  if (kind === "image") {
    body.innerHTML = `<img class="proof-preview" src="${esc(url)}" alt="${esc(title)}" />`;
    return;
  }
  body.innerHTML = `<p class="empty">Opening the receipt…</p>`;
  fetch(url, { credentials: "same-origin" })
    .then((r) => { if (!r.ok) throw new Error("bad"); return r.blob(); })
    .then((blob) => {
      const objectUrl = URL.createObjectURL(blob);
      body.innerHTML = `<iframe class="proof-frame receipt-modal-frame" src="${objectUrl}" title="${esc(title)}"></iframe>`;
    })
    .catch(() => {
      body.innerHTML = `<p class="empty">This file could not be opened here. Use the download link below.</p>`;
    });
}

function receiptViewer(url, label, name) {
  if (!url) return `<p class="empty">No receipt attached.</p>`;
  const kind = fileTypeOf(url);
  const safe = esc(url);
  const title = esc(label || "Receipt");
  const fname = esc(name || String(url).split("/").pop() || "receipt");
  // A page with dozens of orders cannot hold a PDF viewer for every one of
  // them - it makes the tab fall over, on a phone worst of all. Images are
  // cheap and render at once; other files open in the modal when asked.
  const body = kind === "image"
    ? `<img class="proof-preview" src="${safe}" alt="${title}" loading="lazy" />`
    : kind === "pdf"
      ? `<button type="button" class="btn btn-line" data-receipt-open="${safe}" data-receipt-label="${title}" data-receipt-name="${fname}">View the PDF here</button>`
      : `<button type="button" class="btn btn-line" data-receipt-open="${safe}" data-receipt-label="${title}" data-receipt-name="${fname}">View this file</button>`;
  return `
    <p class="proof-label">${title} — the original file, opened right here</p>
    <div class="proof-frame-wrap">${body}</div>
    <p class="proof-actions">
      <a class="btn btn-line" href="${safe}" target="_blank" rel="noopener">Open full size</a>
      <a class="btn btn-line" href="${safe}" download="${fname}">Download ${esc(kind === "pdf" ? "PDF" : "file")}</a>
    </p>`;
}

function orderActionsHTML(o) {
  const s = o.status || "pending";
  const del = `<button type="button" class="btn btn-line btn-danger" data-del-order="${esc(o.id)}">Delete</button>`;
  if (s === "pending") {
    return `<button class="btn" data-confirm="${esc(o.id)}">Confirm payment</button>
      <button class="btn btn-line" data-decline="${esc(o.id)}">Decline</button>
      ${del}`;
  }
  if (s === "confirmed") {
    return `<button class="btn btn-line" data-reopen="${esc(o.id)}">Back to pending</button>
      ${del}`;
  }
  return `<button class="btn btn-line" data-reopen="${esc(o.id)}">Reopen (back to pending)</button>
    ${del}`;
}

function orderCardHTML(o) {
  const c = o.customer || {};
  const shot = o.proofUrl || (JA.getProof && JA.getProof(o.id, o.proof)) || "";
  const when = o.at ? new Date(o.at).toLocaleString() : "";
  const s = o.status || "pending";
  const nItems = (o.items || []).reduce((n, i) => n + (Number(i.qty) || 0), 0);
  return `
    <details class="adx-order" data-order="${esc(o.id)}">
      <summary class="adx-order-row">
        <span class="adx-order-id">${esc(o.id)}</span>
        <span class="adx-order-who"><strong>${esc(c.name || "Customer")}</strong><small>${esc(when)} · ${nItems} item(s)</small></span>
        <span class="adx-order-total">${esc(JA.money(o.total, o.currency))}</span>
        <span class="status-pill ${esc(s)}">${esc(orderStatusLabel(s))}</span>
      </summary>
      <div class="adx-order-body">
        <div class="order-card-top">
          <div>
            <p><strong>${esc(c.name || "Customer")}</strong></p>
            <p>${esc(c.email || "")}</p>
            <p>${esc(c.phone || "")} · ${esc([c.city, c.zone].filter(Boolean).join(" / "))}</p>
            <p>${esc([c.address, c.country].filter(Boolean).join(", "))}</p>
            ${c.note ? `<p class="order-note"><em>Note:</em> ${esc(c.note)}</p>` : ""}
            <p>${esc(when)}</p>
          </div>
          <div>
            <p style="margin-top:8px"><strong>${esc(JA.money(o.total, o.currency))}</strong> · ${o.currency === "NGN" ? "Naira" : "CFA"}</p>
            <p class="admin-note">Pay by ${esc(o.payment || o.currency || "")}</p>
          </div>
        </div>
        <ul class="order-items">${(o.items || []).map((i) => `<li>${i.qty}× ${esc(i.name)}${i.color ? " · " + esc(i.color) : ""}</li>`).join("")}</ul>
        ${shot
          ? receiptViewer(shot, `Payment receipt for ${o.id} — stored on the server, never cleaned up`,
                          `${o.id}-receipt`)
          : `<p class="empty">No receipt attached.</p>`}
        <div class="order-actions">${orderActionsHTML(o)}</div>
      </div>
    </details>`;
}

let orderFilter = "all";

function ordersPanel() {
  return `
    <div class="adx-order-filters" id="order-filters">
      ${["all", "pending", "past", "confirmed", "declined"].map((s) =>
        `<button type="button" class="an-rng${orderFilter === s ? " is-on" : ""}" data-ofilter="${s}">${s === "all" ? "All" : orderStatusLabel(s)}</button>`).join("")}
      <a class="wix-link-btn" href="api/admin/orders.csv" style="margin-left:auto">Download CSV</a>
    </div>
    <p class="admin-note">Tap an order to see everything — customer details, items, the payment receipt and the action buttons. Every checkout is kept forever, even after you confirm or decline it.</p>
    <div id="orders-box"><p class="empty">Loading orders…</p></div>
    <h3 class="admin-h">Receipts customers uploaded</h3>
    <div id="proofs-box"><p class="empty">Loading receipts…</p></div>`;
}

const PROOF_PAGE = 20;
let proofsShown = PROOF_PAGE;

async function fillProofs() {
  const box = $("#proofs-box");
  if (!box) return;
  proofsShown = Math.max(proofsShown, PROOF_PAGE);
  let rows = [];
  try {
    const res = await fetch("api/admin/payment-proofs", { credentials: "same-origin", cache: "no-store" });
    if (res.ok) rows = ((await res.json()) || {}).proofs || [];
  } catch (e) { rows = []; }
  if (!rows.length) {
    box.innerHTML = `<p class="empty">No receipts uploaded yet. Customers send them from the payment page.</p>`;
    return;
  }
  const shown = rows.slice(0, proofsShown);
  box.innerHTML = `<div class="table-wrap"><table class="proofs-table">
    <thead><tr><th>Sent</th><th>Order</th><th>Customer</th><th>Contact</th><th>Method</th><th>Receipt</th><th>Emailed</th></tr></thead>
    <tbody>${shown.map((p) => `<tr>
      <td data-label="Sent"><span class="cell-nowrap">${esc((p.at || "").replace("T", " ").slice(0, 16))}</span></td>
      <td data-label="Order"><span class="cell-nowrap">${esc(p.order_id || "")}</span></td>
      <td data-label="Customer">${esc(p.name || "")}<br /><small>${esc(p.items || "")}</small></td>
      <td data-label="Contact"><span class="cell-nowrap">${esc(p.phone || "")}</span><br /><small>${esc(p.email || "")}</small></td>
      <td data-label="Method">${esc(p.method || "")}</td>
      <td data-label="Receipt">${p.file_url
        ? (fileTypeOf(p.file_url) === "image"
            ? `<a href="${esc(p.file_url)}" target="_blank" rel="noopener"><img class="proof-thumb" src="${esc(p.file_url)}" alt="Receipt" loading="lazy" /></a>`
            : `<button type="button" class="btn btn-line" data-receipt-open="${esc(p.file_url)}" data-receipt-label="Receipt for ${esc(p.order_id || "")}" data-receipt-name="${esc(p.file_name || "receipt")}">View ${esc((p.file_name || "").split(".").pop().toUpperCase())}</button>`)
          + `<br /><a class="btn btn-line" href="${esc(p.file_url)}" download="${esc(p.file_name || "receipt")}">Download</a>
             <small>${Math.max(1, Math.round((p.file_size || 0) / 1024))} KB</small>`
        : "—"}</td>
      <td data-label="Emailed">${p.emailed ? "yes" : `<span style="color:#c0392b" title="${esc(p.email_info || "")}">no</span>`}</td>
    </tr>`).join("")}</tbody></table></div>
    <p class="admin-note">The original file is attached to the email we send you, and kept here so you can open it any time.</p>`
    + (rows.length > proofsShown
      ? `<p class="admin-more"><button type="button" class="btn btn-line" id="proofs-more">Show ${Math.min(PROOF_PAGE, rows.length - proofsShown)} more of ${rows.length}</button></p>`
      : `<p class="admin-note">Showing all ${rows.length} receipts.</p>`);
  const more = $("#proofs-more");
  if (more) more.onclick = () => { proofsShown += PROOF_PAGE; fillProofs(); };
}

const ORDER_PAGE = 15;
let ordersShown = ORDER_PAGE;

function renderOrderPage() {
  const box = $("#orders-box");
  if (!box) return;
  const list = orderFilter === "all"
    ? serverOrders
    : orderFilter === "past"
    ? serverOrders.filter((o) => (o.status || "pending") !== "pending")
    : serverOrders.filter((o) => (o.status || "pending") === orderFilter);
  if (!list.length) {
    box.innerHTML = `<p class="empty">${orderFilter === "all"
      ? "No orders yet. Every completed checkout lands here and stays here."
      : "No " + orderStatusLabel(orderFilter).toLowerCase() + " orders."}</p>`;
    return;
  }
  const page = list.slice(0, ordersShown);
  box.innerHTML = page.map(orderCardHTML).join("")
    + (list.length > ordersShown
      ? `<p class="admin-more"><button type="button" class="btn btn-line" id="orders-more">
           Show ${Math.min(ORDER_PAGE, list.length - ordersShown)} more
           of ${list.length}</button></p>`
      : `<p class="admin-note">Showing all ${list.length} order(s).</p>`);
  const more = $("#orders-more");
  if (more) more.onclick = () => { ordersShown += ORDER_PAGE; renderOrderPage(); };
  bindOrderButtons();
}

async function fillOrders() {
  const box = $("#orders-box");
  if (!box) return;
  serverOrders = await JA.adminOrders({ limit: 200 });
  ordersShown = ORDER_PAGE;
  document.querySelectorAll("[data-ofilter]").forEach((b) => {
    b.onclick = () => {
      orderFilter = b.dataset.ofilter;
      ordersShown = ORDER_PAGE;
      document.querySelectorAll("[data-ofilter]").forEach((x) => x.classList.toggle("is-on", x === b));
      renderOrderPage();
    };
  });
  renderOrderPage();
}

function bindOrderButtons() {
  const box = $("#orders-box");
  if (!box) return;
  box.querySelectorAll("[data-confirm]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.confirm;
      b.disabled = true;
      const res = await JA.setOrderStatus(id, "confirmed");
      if (!res || res.ok === false) { JA.toast((res && res.error) || "Could not update the order."); b.disabled = false; return; }
      const o = serverOrders.find((x) => x.id === id);
      if (o && JA.sendReceipt) { JA.toast("Sending receipt…"); await JA.sendReceipt({ ...o, status: "confirmed" }); }
      JA.toast("Confirmed · " + id);
      fillOrders();
    };
  });
  box.querySelectorAll("[data-decline]").forEach((b) => {
    b.onclick = async () => {
      await JA.setOrderStatus(b.dataset.decline, "declined");
      JA.toast("Declined · " + b.dataset.decline);
      fillOrders();
    };
  });
  box.querySelectorAll("[data-reopen]").forEach((b) => {
    b.onclick = async () => {
      await JA.setOrderStatus(b.dataset.reopen, "pending");
      fillOrders();
    };
  });
  box.querySelectorAll("[data-del-order]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.delOrder;
      if (!confirm("Delete order " + id + " permanently? Its payment receipt is removed too. This cannot be undone.")) return;
      b.disabled = true;
      const res = await JA.deleteOrder(id);
      if (!res || res.ok === false) {
        JA.toast((res && res.error) || "Could not delete the order.");
        b.disabled = false;
        return;
      }
      JA.toast("Deleted " + id + ".");
      serverOrders = serverOrders.filter((o) => o.id !== id);
      renderOrderPage();
      fillProofs();
    };
  });
}

function accountPanel() {
  return `
    <div class="admin-card">
      <h3 class="admin-h">Your account</h3>
      <p class="admin-note">Signed in as <strong id="acct-email">…</strong>. The shop uses <strong>one shared admin password</strong> — changing it here updates it for every admin account, so any teammate can sign in with it from their own phone or laptop.</p>
      <form id="pw-form" class="form-grid" style="max-width:560px">
        <div class="field"><label>Current password</label><input type="password" name="current" required autocomplete="current-password" /></div>
        <div class="field"><label>New shared password</label><input type="password" name="next" required autocomplete="new-password" /></div>
        <div class="field"><label>Repeat new shared password</label><input type="password" name="again" required autocomplete="new-password" /></div>
        <div class="field full"><button class="btn" id="pw-btn">Change shared password</button></div>
      </form>
      <p class="admin-note">At least 10 characters, with an upper case letter, a lower case letter and a number. It applies to every admin account, so the one password is what any of you type on the sign-in screen. If you ever forget it, use <em>Forgot password? Reset it by email</em> on the sign-in screen — a 6-digit code is sent to that email. You can also change it right here at any time; it saves straight to the database with no email needed.</p>
    </div>
    <details class="adx-advanced" style="margin-top:22px">
      <summary class="admin-h">Advanced settings</summary>
      <div class="admin-card">
        <h3 class="admin-h">Connection &amp; sync</h3>
        <p class="admin-note" id="sync-note">Checking for unsaved changes…</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn btn-line" id="retry-sync">Retry now</button>
          <button class="btn btn-line" id="reload-cat">Reload catalogue</button>
          <button class="btn" id="sync-github">Sync to GitHub</button>
        </div>
        <div id="sync-status" class="admin-note" style="margin-top:12px"></div>
        <p class="admin-note" style="margin-top:12px">Saved changes go straight to the store. If your Wi-Fi drops, they wait in this device and push themselves up when the connection returns. The <strong>Sync to GitHub</strong> button also saves the latest product data into the repository so it never disappears on a redeploy.</p>
      </div>
    </details>`;
}

function bindAccount() {
  const email = $("#acct-email");
  if (email) {
    JA.adminSession(true).then((e) => { email.textContent = e || "unknown"; });
  }
  $("#pw-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const current = String(fd.get("current") || "");
    const next = String(fd.get("next") || "");
    const again = String(fd.get("again") || "");
    if (next !== again) { JA.toast("The two new passwords do not match."); return; }
    const btn = $("#pw-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
    const res = await JA.changePassword(current, next);
    if (btn) { btn.disabled = false; btn.textContent = "Change password"; }
    if (!res.ok) { JA.toast(res.error || "Could not change the password."); return; }
    e.target.reset();
    JA.toast("Password changed. Use it the next time you sign in.");
  });
  const note = $("#sync-note");
  const paintSync = () => {
    if (!note) return;
    const n = JA.syncPending ? JA.syncPending() : 0;
    note.textContent = n
      ? `${n} change(s) are waiting for a connection. They will upload by themselves.`
      : "Everything you saved is live on the store.";
  };
  paintSync();
  if (window.JA_NET) window.JA_NET.onStatus(paintSync);
  $("#retry-sync")?.addEventListener("click", () => {
    if (window.JA_NET) { window.JA_NET.flush(); JA.toast("Retrying…"); paintSync(); }
  });
  $("#reload-cat")?.addEventListener("click", async () => {
    if (JA.reloadCatalog) {
      JA.toast("Reloading…");
      await JA.reloadCatalog();
      paintDesk("products");
    }
  });

  // Supabase + GitHub dual-sync status and the manual "Sync to GitHub" button.
  const statusBox = $("#sync-status");
  async function refreshSyncStatus() {
    if (!statusBox) return;
    try {
      const res = await fetch("api/admin/sync/status", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) { statusBox.textContent = "Could not read the sync status."; return; }
      const d = await res.json();
      const bits = [];
      bits.push("Supabase: " + (d.supabase ? "connected" : "not configured (local files)"));
      bits.push(d.gitToken ? "GitHub push: on" : "GitHub push: off");
      if (d.gitRepo) bits.push("repo: " + d.gitRepo);
      if (d.gitBranch) bits.push("branch: " + d.gitBranch);
      statusBox.innerHTML = bits.map((b) => JA.escape(b)).join(" &middot; ")
        + "<br><small>" + (d.onWrite ? "Admin changes are committed to the repo automatically." : "Automatic repo commit is off — use the Sync button.") + "</small>";
    } catch (e) {
      statusBox.textContent = "Sync status unavailable.";
    }
  }
  refreshSyncStatus();
  $("#sync-github")?.addEventListener("click", async () => {
    const b = $("#sync-github");
    if (b) { b.disabled = true; b.textContent = "Syncing…"; }
    try {
      const res = await fetch("api/admin/sync/repo", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "X-CSRF-Token": JA.csrf() || "" },
      });
      const d = await res.json();
      if (!res.ok || d.ok === false) {
        JA.toast((d && d.error) || "Sync failed — see the response below.");
        if (statusBox) statusBox.textContent = (d && d.error) || "Sync failed.";
        return;
      }
      JA.toast(d.pushed ? "Synced, committed and pushed to GitHub." : "Committed to the repo." + (d.note ? " " + d.note : ""));
      if (statusBox) {
        statusBox.textContent = [
          d.committed ? "Committed to the repository." : "Nothing to sync.",
          d.pushed ? "Pushed to " + (d.branch || "main") + "." : (d.note || "Not pushed — no GitHub token configured."),
        ].filter(Boolean).join(" ");
      }
      refreshSyncStatus();
    } catch (e) {
      JA.toast("Could not reach the sync endpoint.");
      if (statusBox) statusBox.textContent = "Sync request failed — check your connection.";
    } finally {
      if (b) { b.disabled = false; b.textContent = "Sync to GitHub"; }
    }
  });
}

/* ------------------------------------------------ Marketing & growth tab */
function marketingPanel() {
  return `
    <div class="admin-card" id="mk-settings-card">
      <h3 class="admin-h">Referral & abandoned-cart settings</h3>
      <p class="admin-note">Loading…</p>
    </div>
    <div class="admin-card" id="mk-coupons-card">
      <h3 class="admin-h">Coupons</h3>
      <p class="admin-note">Loading…</p>
    </div>
    <div class="admin-card" id="mk-referrals-card">
      <h3 class="admin-h">Referral codes</h3>
      <p class="admin-note">Loading…</p>
    </div>
    <div class="admin-card" id="mk-abandoned-card">
      <h3 class="admin-h">Abandoned checkouts</h3>
      <p class="admin-note">Loading…</p>
    </div>
    <div class="admin-card" id="mk-backup-card">
      <h3 class="admin-h">Backups</h3>
      <p class="admin-note">Product data is backed up to GitHub automatically every night at midnight. Customer orders stay on the server (they are never committed to the public repository). You can also run a backup right now.</p>
      <button type="button" class="btn" id="mk-backup-now">Back up now</button>
      <p class="admin-note" id="mk-backup-out" hidden></p>
    </div>`;
}

async function fillMarketing() {
  const api = (path, opts) => window.JA_NET.api(path, opts);
  const num = (v) => esc(String(v == null ? "" : v));

  // ---- settings ----
  try {
    const d = await api("api/admin/growth/settings");
    const s = d.settings || {};
    const card = $("#mk-settings-card");
    if (card) {
      card.innerHTML = `
        <h3 class="admin-h">Referral & abandoned-cart settings</h3>
        <form id="mk-set-form" class="admin-form">
          <label class="mk-toggle"><input type="checkbox" name="referralEnabled" ${s.referralEnabled ? "checked" : ""} />
            Referral programme ON — qualifying orders get a shareable code</label>
          <div class="admin-grid">
            <label>Minimum spend for a code (₦)<input name="minSpendNgn" type="number" min="0" value="${num(s.minSpendNgn)}" /></label>
            <label>NGN → CFA rate (1 ₦ = ? F CFA)<input name="cfaRate" type="number" min="0.01" max="100" step="0.0001" value="${num(s.cfaRate)}" /></label>
            <label>Friend's discount % (code used at checkout)<input name="buyerPercent" type="number" min="1" max="50" value="${num(s.buyerPercent)}" /></label>
            <label>Referrer reward coupon % (max 10)<input name="referrerPercent" type="number" min="1" max="10" value="${num(s.referrerPercent)}" /></label>
            <label>Orders needed for the reward<input name="milestone" type="number" min="1" max="100" value="${num(s.milestone)}" /></label>
          </div>
          <p class="admin-note" id="mk-cfa-note"></p>
          <label class="mk-toggle"><input type="checkbox" name="abandonedEnabled" ${s.abandonedEnabled ? "checked" : ""} />
            Abandoned-cart emails ON — remind shoppers who stopped at checkout</label>
          <div class="admin-grid">
            <label>Send the reminder after (hours)<input name="abandonedHours" type="number" min="1" max="168" value="${num(s.abandonedHours)}" /></label>
            <label>Email subject<input name="abandonedSubject" value="${esc(s.abandonedSubject || "")}" /></label>
          </div>
          <label>Email template — {name}, {items} and {link} are filled in automatically
            <textarea name="abandonedTemplate" rows="5">${esc(s.abandonedTemplate || "")}</textarea></label>
          <button class="btn" type="submit">Save settings</button>
        </form>`;
      // Live preview: what the NGN threshold means in F CFA at the set rate.
      const cfaNote = () => {
        const f = $("#mk-set-form");
        const note = $("#mk-cfa-note");
        if (!f || !note) return;
        const spend = Number(f.minSpendNgn.value) || 0;
        const rate = Number(f.cfaRate.value) || 0;
        note.textContent = rate > 0
          ? `CFA shoppers qualify from ${Math.round(spend * rate).toLocaleString()} F CFA (₦${spend.toLocaleString()} × ${rate}).`
          : "";
      };
      cfaNote();
      ["minSpendNgn", "cfaRate"].forEach((n) => {
        const el = $("#mk-set-form") && $("#mk-set-form")[n];
        if (el) el.addEventListener("input", cfaNote);
      });
      $("#mk-set-form").onsubmit = async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        const patch = {
          referralEnabled: e.target.referralEnabled.checked,
          abandonedEnabled: e.target.abandonedEnabled.checked,
          minSpendNgn: Number(fd.get("minSpendNgn")),
          cfaRate: Number(fd.get("cfaRate")),
          buyerPercent: Number(fd.get("buyerPercent")),
          referrerPercent: Number(fd.get("referrerPercent")),
          milestone: Number(fd.get("milestone")),
          abandonedHours: Number(fd.get("abandonedHours")),
          abandonedSubject: fd.get("abandonedSubject"),
          abandonedTemplate: fd.get("abandonedTemplate"),
        };
        try {
          await api("api/admin/growth/settings", { method: "POST", json: patch });
          JA.toast("Marketing settings saved.");
          fillMarketing();
        } catch (err) { JA.toast(err.message || "Could not save."); }
      };
    }
  } catch (e) { /* not signed in / offline */ }

  // ---- coupons ----
  try {
    const d = await api("api/admin/coupons");
    const card = $("#mk-coupons-card");
    if (card) {
      const rows = (d.coupons || []).map((c) => `
        <tr>
          <td><strong>${esc(c.code)}</strong>${c.kind === "reward" ? ' <small>(auto reward)</small>' : ""}</td>
          <td>${num(c.percent)}%</td>
          <td>${num(c.uses)}${c.max_uses ? " / " + num(c.max_uses) : ""}</td>
          <td>${c.expires_at ? esc(c.expires_at) : "—"}</td>
          <td>${c.active ? "Active" : "Off"}</td>
          <td class="mk-row-actions">
            <button type="button" class="btn btn-line" data-mk-cp-toggle="${esc(c.code)}" data-on="${c.active ? 1 : 0}">${c.active ? "Turn off" : "Turn on"}</button>
            <button type="button" class="btn btn-line" data-mk-cp-del="${esc(c.code)}">Delete</button>
          </td>
        </tr>`).join("");
      card.innerHTML = `
        <h3 class="admin-h">Coupons</h3>
        <form id="mk-cp-form" class="mk-inline-form">
          <input name="code" placeholder="Code (blank = auto)" maxlength="24" />
          <input name="percent" type="number" placeholder="%" min="1" max="90" required style="width:80px" />
          <input name="maxUses" type="number" placeholder="Max uses" min="1" style="width:110px" />
          <input name="expiresAt" type="date" title="Expiry date (optional)" />
          <input name="note" placeholder="Note (optional)" maxlength="200" />
          <button class="btn" type="submit">Create coupon</button>
        </form>
        ${rows ? `<table class="mk-table"><thead><tr><th>Code</th><th>%</th><th>Uses</th><th>Expires</th><th>State</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
               : `<p class="empty">No coupons yet. Create one above — customers type it in the promo box at checkout.</p>`}`;
      $("#mk-cp-form").onsubmit = async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        const body = { code: fd.get("code"), percent: Number(fd.get("percent")), note: fd.get("note") };
        if (fd.get("maxUses")) body.maxUses = Number(fd.get("maxUses"));
        if (fd.get("expiresAt")) body.expiresAt = fd.get("expiresAt") + " 23:59:59";
        try {
          const res = await api("api/admin/coupons", { method: "POST", json: body });
          JA.toast("Coupon created: " + res.code);
          fillMarketing();
        } catch (err) { JA.toast(err.message || "Could not create the coupon."); }
      };
      card.querySelectorAll("[data-mk-cp-toggle]").forEach((b) => {
        b.onclick = async () => {
          try {
            await api("api/admin/coupons/" + encodeURIComponent(b.dataset.mkCpToggle),
              { method: "PATCH", json: { active: b.dataset.on !== "1" } });
            fillMarketing();
          } catch (err) { JA.toast(err.message || "Could not update."); }
        };
      });
      card.querySelectorAll("[data-mk-cp-del]").forEach((b) => {
        b.onclick = async () => {
          if (!confirm("Delete coupon " + b.dataset.mkCpDel + "? Customers will no longer be able to use it.")) return;
          try {
            await api("api/admin/coupons/" + encodeURIComponent(b.dataset.mkCpDel), { method: "DELETE" });
            fillMarketing();
          } catch (err) { JA.toast(err.message || "Could not delete."); }
        };
      });
    }
  } catch (e) {}

  // ---- referral codes ----
  try {
    const d = await api("api/admin/referrals");
    const card = $("#mk-referrals-card");
    if (card) {
      const rows = (d.referrals || []).map((r) => `
        <tr>
          <td><strong>${esc(r.code)}</strong></td>
          <td>${esc(r.name || "")}<br /><small>${esc(r.email)}</small></td>
          <td>${num(r.uses)}</td>
          <td>${r.reward_issued ? "Rewarded — " + esc(r.reward_coupon || "") : "Not yet"}</td>
          <td><small>${esc(r.created_at || "")}</small></td>
        </tr>`).join("");
      card.innerHTML = `
        <h3 class="admin-h">Referral codes</h3>
        <p class="admin-note">Codes are minted automatically for qualifying orders. When a code reaches the milestone, the referrer's reward coupon is issued and emailed automatically.</p>
        ${rows ? `<table class="mk-table"><thead><tr><th>Code</th><th>Customer</th><th>Uses</th><th>Reward</th><th>Created</th></tr></thead><tbody>${rows}</tbody></table>`
               : `<p class="empty">No referral codes yet.</p>`}`;
    }
  } catch (e) {}

  // ---- abandoned checkouts ----
  try {
    const d = await api("api/admin/abandoned");
    const card = $("#mk-abandoned-card");
    if (card) {
      const st = d.stats || {};
      const rows = (d.carts || []).slice(0, 30).map((a) => `
        <tr>
          <td>${esc(a.email)}</td>
          <td>${(a.items || []).map((i) => `${i.qty}× ${esc(i.name || i.id)}`).join(", ")}</td>
          <td>${a.completed_at ? "Recovered ✓" : a.reminded_at ? "Reminded" : "Waiting"}</td>
          <td><small>${esc(a.updated_at || "")}</small></td>
        </tr>`).join("");
      card.innerHTML = `
        <h3 class="admin-h">Abandoned checkouts</h3>
        <p class="admin-note"><strong>${num(st.total || 0)}</strong> captured · <strong>${num(st.reminded || 0)}</strong> reminded · <strong>${num(st.recovered || 0)}</strong> recovered</p>
        ${rows ? `<table class="mk-table"><thead><tr><th>Email</th><th>Cart</th><th>State</th><th>Last seen</th></tr></thead><tbody>${rows}</tbody></table>`
               : `<p class="empty">No abandoned checkouts recorded yet.</p>`}`;
    }
  } catch (e) {}

  // ---- manual backup ----
  const bk = $("#mk-backup-now");
  if (bk && !bk.dataset.bound) {
    bk.dataset.bound = "1";
    bk.onclick = async () => {
      bk.disabled = true;
      bk.textContent = "Backing up…";
      const out = $("#mk-backup-out");
      try {
        const res = await api("api/admin/backup", { method: "POST", json: {} });
        if (out) { out.hidden = false; out.textContent = res.ok ? "Backup completed. Product data was pushed to GitHub; customer orders stay on the server." : (res.error || res.note || "Backup finished with warnings."); }
      } catch (err) {
        if (out) { out.hidden = false; out.textContent = err.message || "Backup failed."; }
      }
      bk.disabled = false;
      bk.textContent = "Back up now";
    };
  }
}

const TAB_TITLES = {
  analytics: "Dashboard",
  products: "Products",
  orders: "Orders",
  marketing: "Marketing",
  categories: "Categories",
  settings: "Settings",
  account: "Account",
};

const ADX_ICONS = {
  analytics: `<svg viewBox="0 0 24 24"><path d="M4 19V9h3v10H4zm6.5 0V5h3v14h-3zm6.5 0v-7h3v7h-3z"/></svg>`,
  products: `<svg viewBox="0 0 24 24"><path d="M4 8l8-4 8 4v9l-8 4-8-4V8zm8 4l8-4M12 12v9M12 12L4 8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  orders: `<svg viewBox="0 0 24 24"><path d="M5 4h14v16l-2.3-1.5L14.4 20l-2.4-1.5L9.6 20l-2.3-1.5L5 20V4zm3 5h8M8 12.5h8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  marketing: `<svg viewBox="0 0 24 24"><path d="M3 11l12-5v12L3 13v-2zm12-1.5L20 6v12l-5-3.5M7 14v5h3v-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  categories: `<svg viewBox="0 0 24 24"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1m0-12.8l-2.1 2.1M7.7 16.3l-2.1 2.1" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
  account: `<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.5 20c1.4-3.6 4.2-5.4 7.5-5.4s6.1 1.8 7.5 5.4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`,
};

function paintDesk(tab = "analytics") {
  const pending = serverOrders.filter((o) => (o.status || "pending") === "pending").length;
  const navBtn = (id, badge) => `
    <button type="button" data-tab="${id}" class="adx-nav-btn ${tab === id ? "is-on" : ""}">
      ${ADX_ICONS[id]}<span>${TAB_TITLES[id]}</span>${badge ? `<em class="adx-badge">${badge}</em>` : ""}
    </button>`;

  $("#admin-root").innerHTML = `
    <div class="adx">
      <aside class="adx-side">
        <div class="adx-brand">
          <img src="images/brand/logo.jpg" alt="" />
          <div><strong>Jaura Store</strong><span>Store manager</span></div>
        </div>
        <nav class="adx-nav">
          ${navBtn("analytics")}
          ${navBtn("products")}
          ${navBtn("orders", pending || "")}
          ${navBtn("marketing")}
          ${navBtn("categories")}
          ${navBtn("settings")}
          ${navBtn("account")}
        </nav>
        <div class="adx-side-foot">
          <a class="adx-nav-btn" href="index.html"><svg viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8M9 5H5v14h14v-4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><span>View store</span></a>
          <button type="button" class="adx-nav-btn" id="logout"><svg viewBox="0 0 24 24"><path d="M9 5H5v14h4M13 8l4 4-4 4M17 12H8" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><span>Sign out</span></button>
        </div>
      </aside>
      <main class="adx-main">
        <header class="adx-head">
          <h1>${TAB_TITLES[tab] || "Dashboard"}</h1>
          <div class="adx-head-actions">
            <a class="btn btn-line" href="index.html">View store</a>
            <button type="button" class="btn btn-line" id="logout-m">Sign out</button>
          </div>
        </header>
        <section class="panel ${tab === "analytics" ? "is-on" : ""}" id="panel-analytics">
          ${tab === "analytics" ? analyticsPanel() : ""}
        </section>
        <section class="panel ${tab === "products" ? "is-on" : ""}" id="panel-products">
          ${tab !== "products" ? ""
            : editingId === "new" ? `<div id="form-slot">${productForm()}</div>`
            : (editingId ? `<div id="form-slot">${productForm(JA.product(editingId) || {})}</div>`
            : productsTable())}
        </section>
        <section class="panel ${tab === "orders" ? "is-on" : ""}" id="panel-orders">
          ${tab === "orders" ? ordersPanel() : ""}
        </section>
        <section class="panel ${tab === "marketing" ? "is-on" : ""}" id="panel-marketing">
          ${tab === "marketing" ? marketingPanel() : ""}
        </section>
        <section class="panel ${tab === "categories" ? "is-on" : ""}" id="panel-categories">
          ${tab === "categories" ? categoryManager() : ""}
        </section>
        <section class="panel ${tab === "settings" ? "is-on" : ""}" id="panel-settings">
          ${tab === "settings" ? settingsForm() : ""}
        </section>
        <section class="panel ${tab === "account" ? "is-on" : ""}" id="panel-account">
          ${tab === "account" ? accountPanel() : ""}
        </section>
      </main>
    </div>
    <nav class="admin-app-nav">
      <button type="button" data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">
        ${ADX_ICONS.analytics}<span>Dashboard</span>
      </button>
      <button type="button" data-tab="products" class="${tab === "products" ? "is-on" : ""}">
        ${ADX_ICONS.products}<span>Products</span>
      </button>
      <button type="button" data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">
        ${ADX_ICONS.orders}<span>Orders</span>${pending ? `<em class="adx-badge">${pending}</em>` : ""}
      </button>
      <button type="button" data-tab="settings" class="${tab === "settings" ? "is-on" : ""}">
        ${ADX_ICONS.settings}<span>Settings</span>
      </button>
      <button type="button" data-tab="account" class="${tab === "account" ? "is-on" : ""}">
        ${ADX_ICONS.account}<span>Account</span>
      </button>
    </nav>`;

  const signOut = async () => { await JA.logoutAdmin(); paintLogin("Signed out."); };
  $("#logout").onclick = signOut;
  const logoutM = $("#logout-m");
  if (logoutM) logoutM.onclick = signOut;
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => paintDesk(b.dataset.tab);
  });

  if (tab === "analytics") { fillAnalytics(); startDashTimer(); }
  if (tab === "orders") { fillOrders(); fillProofs(); }
  if (tab === "marketing") fillMarketing();
  if (tab === "account") bindAccount();
  if (tab === "settings") bindHeroVideo();

  const form = $("#prod-form");
  const existing = editingId && editingId !== "new" ? JA.product(editingId) : null;
  if (form) {
    form.addEventListener("submit", (e) => handleProductSubmit(e, existing));
    form.dataset.submitBound = "1";        // the form on screen can save
  }
  $("#cancel-edit")?.addEventListener("click", () => { editingId = null; paintDesk("products"); });
  $("#add-product")?.addEventListener("click", () => { editingId = "new"; paintDesk("products"); });
  bindMedia();
  bindOptions();
  bindCategories();
  bindCfaPreview();
  bindReviewsAdmin(existing ? existing.id : "");

  document.querySelectorAll("[data-edit]").forEach((b) => {
    const open = () => {
      editingId = b.dataset.edit;
      paintDesk("products");
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    b.onclick = open;
    b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
  });
  document.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();                 // a delete tap must never open the editor
      if (confirm("Delete this product from the website? Customers will not see it.")) {
        await JA.removeProduct(b.dataset.del);
        JA.toast("Deleted from the website.");
        editingId = null;
        paintDesk("products");
      }
    };
  });

  $("#prod-search")?.addEventListener("input", applyProductFilter);
  $("#prod-cat")?.addEventListener("change", applyProductFilter);
  if (dashCat) {
    const sel = document.getElementById("prod-cat");
    if (sel) sel.value = dashCat;
    applyProductFilter();
  }
  $("#back-all-products")?.addEventListener("click", () => {
    dashCat = "";
    paintDesk("products");
  });

  $("#set-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    JA.saveSettings({
      rate: 0.44,
      whatsapp: fd.get("whatsapp"),
      phoneBj: fd.get("phoneBj"),
      phoneNg: fd.get("phoneNg"),
      email: fd.get("email"),
      bankCfa: fd.get("bankCfa"),
      bankNgn: fd.get("bankNgn"),
    });
    const from = String(fd.get("bannerFrom") || "").trim();
    const to = String(fd.get("bannerTo") || "").trim();
    saveSiteConfig({ bannerFrom: from, bannerTo: to }).catch(() => {});
    JA.toast("Settings saved.");
    JA.mountChrome();
  });
}


function categoryManager() {
  const cats = JA.categories();
  const rows = cats.map((c, i) => {
    const n = JA.products().filter((p) => p.category === c.id).length;
    return `<article class="wix-cat-card" data-cat-i="${i}" data-cat-id="${JA.escape(c.id)}">
      <div class="wix-cat-pic">
        ${_catAssetHTML(c.image)}
        <label class="wix-cat-up">Change asset<input type="file" accept="image/*,.pdf,.doc,.docx,application/pdf" data-cat-img="${i}" hidden /></label>
      </div>
      <div class="wix-cat-fields">
        <input name="cat-id-${i}" type="hidden" value="${JA.escape(c.id)}" />
        <label>Name (English)</label>
        <input name="cat-name-${i}" value="${JA.escape(c.name || "")}" />
        <label>Name (French)</label>
        <input name="cat-fr-${i}" value="${JA.escape(c.nameFr || "")}" />
        <label class="wix-tog"><span>Show on website</span>
          <input type="checkbox" name="cat-on-${i}" ${c.hidden ? "" : "checked"} />
        </label>
        <p class="admin-note">${n} product${n === 1 ? "" : "s"}</p>
        <button type="button" class="wix-opt-del" data-cat-del="${JA.escape(c.id)}">Delete category</button>
      </div>
    </article>`;
  }).join("");
  return `<div class="wix-cats-admin">
    <h2>Categories</h2>
    <p class="admin-note">Add or delete categories here. They show on the shop and in filters. Beauty and Skincare are now one category: <strong>Beauty &amp; skincare</strong>.</p>
    <div id="cat-list">${rows}</div>
    <div class="wix-cat-new">
      <h3>Add a category</h3>
      <div class="wix-2">
        <div class="field"><label>Name</label><input id="new-cat-name" placeholder="e.g. Jewellery" /></div>
        <div class="field"><label>French name</label><input id="new-cat-fr" placeholder="ex. Bijoux" /></div>
      </div>
      <button type="button" class="wix-link-btn" id="add-cat">+ Add category</button>
    </div>
    <button type="button" class="btn wix-save" id="save-cats">Save categories</button>
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
    const asset = row.querySelector(".wix-cat-pic img")?.getAttribute("src")
      || row.querySelector(".wix-cat-pic a.media-doc-chip")?.getAttribute("href")
      || "";
    out.push({
      id,
      name,
      nameFr: (row.querySelector(`[name="cat-fr-${i}"]`)?.value || "").trim(),
      image: asset,
      hidden: !row.querySelector(`[name="cat-on-${i}"]`)?.checked,
    });
  });
  return out;
}

function bindCategories() {
  const list = document.getElementById("cat-list");
  if (!list) return;
  const persist = (msg) => {
    JA.saveCategories(collectCats());
    JA.toast(msg || "Categories saved. They show on the shop now.");
  };
  list.addEventListener("change", async (e) => {
    const input = e.target.closest("[data-cat-img]");
    if (!input || !input.files || !input.files[0]) return;
    const f = input.files[0];
    const isDoc = /\.(pdf|doc|docx)$/i.test(f.name || "") || /pdf|word|msword|document/.test(f.type || "");
    if (isDoc && f.size > 8 * 1024 * 1024) {
      JA.toast("That asset is " + (f.size / 1048576).toFixed(1) + " MB. The limit is 8 MB.");
      input.value = "";
      return;
    }
    const card = input.closest("[data-cat-i]");
    const pic = card?.querySelector(".wix-cat-pic");
    try {
      if (window.JA_NET) {
        const res = await window.JA_NET.api("api/admin/uploads/category", {
          method: "POST", blob: f, field: "file",
          filename: f.name || "category.jpg", queue: true, timeout: 300000, label: "Category asset",
        });
        if (res && res.url) {
          input.dataset.catUrl = res.url;
          if (pic) pic.innerHTML = _catAssetHTML(res.url);
          persist("Asset saved.");
          return;
        }
      }
      // static hosting fallback: keep the old data-URL path for images
      const data = await fileToData(f);
      if (pic) pic.innerHTML = `<img src="${data}" alt="" />`;
      persist("Photo saved.");
    } catch (err) {
      JA.toast((err && err.message) || "Could not read that asset. Try another from your gallery.");
    } finally {
      input.value = "";
    }
  });
  list.addEventListener("click", (e) => {
    const card = e.target.closest("[data-cat-id]");
    if (card && !e.target.closest("input,label,button,a,select")) {
      // Tap a category card to open that category's products, fully editable.
      dashCat = card.getAttribute("data-cat-id");
      editingId = null;
      paintDesk("products");
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    const del = e.target.closest("[data-cat-del]");
    if (!del) return;
    const id = del.getAttribute("data-cat-del");
    const n = JA.products().filter((p) => p.category === id).length;
    if (n) {
      if (!confirm("Move " + n + " product(s) into Beauty & skincare and delete this category?")) return;
      if (JA.deleteCategory) JA.deleteCategory(id, "beauty");
    } else {
      if (!confirm("Delete this category?")) return;
      if (JA.deleteCategory) JA.deleteCategory(id, "beauty");
    }
    JA.toast("Category deleted.");
    paintDesk("categories");
  });
  document.getElementById("add-cat")?.addEventListener("click", () => {
    const name = (document.getElementById("new-cat-name")?.value || "").trim();
    const nameFr = (document.getElementById("new-cat-fr")?.value || "").trim();
    if (!name) { JA.toast("Type a category name."); return; }
    const id = slugify(name) || ("cat-" + Date.now().toString(36));
    if (collectCats().some((c) => c.id === id) || JA.categories().some((c) => c.id === id)) {
      JA.toast("That category already exists.");
      return;
    }
    const next = collectCats().concat([{ id, name, nameFr, image: "images/brand/logo.jpg", hidden: false }]);
    JA.saveCategories(next);
    JA.toast("Category added.");
    paintDesk("categories");
  });
  document.getElementById("save-cats")?.addEventListener("click", () => {
    persist();
    paintDesk("categories");
  });
}

function settingsForm() {
  const s = JA.settings();
  return `
  <div class="admin-card adx-hero-card">
    <h3 class="admin-h">Homepage hero video</h3>
    <p class="admin-note">Upload a video (MP4/WebM/MOV, up to 40 MB) and it plays silently on a loop at the top of the homepage — like a Wix video hero. You can also attach a <strong>PDF or document</strong> (up to 8 MB): the homepage keeps its static hero and shows a labelled "View / Download" link instead. A photo sets the hero poster. You can change or remove it any time, no code needed.</p>
    <div id="hero-video-now"><p class="empty">Checking the current hero…</p></div>
    <div class="adx-hero-actions">
      <label class="btn adx-upload-btn">Upload video / document
        <input type="file" id="hero-video-file" accept="image/*,video/*,.pdf,.doc,.docx,application/pdf" hidden />
      </label>
      <button type="button" class="btn btn-line" id="hero-video-remove" hidden>Remove hero asset</button>
    </div>
    <p class="admin-note" id="hero-video-msg"></p>
  </div>
  <form id="set-form" class="form-grid admin-card" style="margin-top:22px">
    <h3 class="admin-h full">Contact &amp; payment details</h3>
    <p class="admin-note full">Naira is the only price you enter on products. The website converts F CFA at <strong>1 ₦ = 0.44 F CFA</strong>.</p>
    <h3 class="admin-h full">Benin delivery window</h3>
    <p class="admin-note full">These dates appear on the moving banner under the header. Shoppers in Benin are told they will receive their order between these two days.</p>
    <div class="field"><label>Delivery window starts</label><input type="date" name="bannerFrom" id="banner-from" value="2026-09-15" /></div>
    <div class="field"><label>Delivery window ends</label><input type="date" name="bannerTo" id="banner-to" value="2026-09-25" /></div>
    <div class="field"><label>WhatsApp (digits only)</label><input name="whatsapp" value="${s.whatsapp}" /></div>
    <div class="field"><label>Phone Benin</label><input name="phoneBj" value="${s.phoneBj}" /></div>
    <div class="field"><label>Phone Nigeria</label><input name="phoneNg" value="${s.phoneNg}" /></div>
    <div class="field"><label>Email</label><input name="email" value="${s.email}" /></div>
    <div class="field full"><label>Pay-in-CFA instructions</label><textarea name="bankCfa" rows="4">${JA.escape(s.bankCfa)}</textarea></div>
    <div class="field full"><label>Pay-in-Naira instructions</label><textarea name="bankNgn" rows="4">${JA.escape(s.bankNgn)}</textarea></div>
    <div class="field full"><button class="btn">Save settings</button></div>
  </form>`;
}

async function saveSiteConfig(patch) {
  return window.JA_NET
    ? window.JA_NET.api("api/admin/site", { method: "POST", json: patch })
    : Promise.resolve(null);
}

function paintHeroVideoNow(site) {
  const box = $("#hero-video-now");
  const rm = $("#hero-video-remove");
  if (!box) return;
  site = site || {};
  const video = site.heroVideo || "";
  const doc = site.heroDoc || "";
  const poster = site.heroPoster || "";
  if (video) {
    box.innerHTML = `<video class="adx-hero-preview" src="${JA.escape(video)}" ${poster ? `poster="${JA.escape(JA.asset(poster))}"` : ""} muted loop playsinline controls preload="metadata"></video>
      <p class="admin-note">This video is live on the homepage right now.</p>`;
    if (rm) rm.hidden = false;
  } else if (doc) {
    box.innerHTML = `<a class="media-doc-chip adx-hero-doc" href="${JA.escape(JA.asset(doc))}" target="_blank" rel="noopener">${/\.docx?$/i.test(doc) ? "DOC" : "PDF"}<span>View / Download the hero document</span></a>
      <p class="admin-note">This document is linked from the homepage hero (the static hero stays).</p>`;
    if (rm) rm.hidden = false;
  } else if (poster) {
    box.innerHTML = `<img class="adx-hero-preview" src="${JA.escape(JA.asset(poster))}" alt="Hero poster" />
      <p class="admin-note">This photo is the homepage hero poster.</p>`;
    if (rm) rm.hidden = false;
  } else {
    box.innerHTML = `<p class="empty">No hero asset uploaded — the homepage shows its default static hero.</p>`;
    if (rm) rm.hidden = true;
  }
}

function bindHeroVideo() {
  const file = $("#hero-video-file");
  const msg = $("#hero-video-msg");
  if (!file) return;
  fetch("api/site", { cache: "no-store" })
    .then((r) => r.json())
    .then((d) => {
      const site = (d && d.site) || {};
      paintHeroVideoNow(site);
      const from = $("#banner-from");
      const to = $("#banner-to");
      if (from && site.bannerFrom) from.value = site.bannerFrom;
      if (to && site.bannerTo) to.value = site.bannerTo;
    })
    .catch(() => paintHeroVideoNow({}));
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    if (!window.JA_NET) { JA.toast("Hero upload needs the live server."); return; }
    const isVideo = /^video\//.test(f.type || "") || _VIDEO_EXT.test(String(f.name || ""));
    const isDoc = /\.(pdf|doc|docx)$/i.test(f.name || "");
    const cap = isVideo ? 40 * 1024 * 1024 : 8 * 1024 * 1024;
    if (f.size > cap) {
      if (msg) msg.textContent = "That file is " + (f.size / 1048576).toFixed(1) + " MB. The limit is " + (cap / (1024 * 1024)) + " MB.";
      JA.toast("That file is too big — " + (cap / (1024 * 1024)) + " MB max.");
      file.value = "";
      return;
    }
    if (msg) msg.textContent = "Uploading " + (f.name || "asset") + " (" + (f.size / 1048576).toFixed(1) + " MB)… keep this tab open.";
    const res = await window.JA_NET.api("api/admin/uploads/hero", {
      method: "POST", blob: f, field: "file", filename: f.name || "hero.mp4", timeout: 180000,
    });
    file.value = "";
    if (!res || !res.url) {
      if (msg) msg.textContent = (res && res.error) || "The upload failed. Check your connection and try again.";
      JA.toast((res && res.error) || "Could not upload the hero asset.");
      return;
    }
    const patch = res.kind === "video" ? { heroVideo: res.url, heroDoc: "", heroPoster: "" }
      : res.kind === "document" ? { heroVideo: "", heroDoc: res.url, heroPoster: "" }
      : { heroVideo: "", heroPoster: res.url, heroDoc: "" };
    const saved = await saveSiteConfig(patch);
    if (saved && saved.ok !== false) {
      if (msg) msg.textContent = res.kind === "video" ? "Done — the homepage hero now plays your video."
        : res.kind === "document" ? "Done — the homepage hero links to your document."
        : "Done — the homepage hero now uses your photo.";
      JA.toast("Hero asset is on the homepage.");
      paintHeroVideoNow(patch);
    } else {
      if (msg) msg.textContent = (saved && saved.error) || "Uploaded, but saving the setting failed. Try again.";
    }
  });
  $("#hero-video-remove")?.addEventListener("click", async () => {
    if (!confirm("Remove the hero asset? The homepage goes back to its default hero.")) return;
    const saved = await saveSiteConfig({ heroVideo: "", heroDoc: "", heroPoster: "" });
    if (saved && saved.ok !== false) {
      JA.toast("Hero asset removed — default hero is back.");
      paintHeroVideoNow({});
      if (msg) msg.textContent = "";
    } else {
      JA.toast((saved && saved.error) || "Could not remove the hero asset.");
    }
  });
}

async function bootAdmin() {
  await JA.ready;
  JA.mountChrome();
  await (JA.loadServerCategories ? JA.loadServerCategories() : Promise.resolve());
  const ok = await JA.isAdmin();
  if (ok) paintDesk();
  else paintLogin();
}
document.addEventListener("DOMContentLoaded", bootAdmin);
