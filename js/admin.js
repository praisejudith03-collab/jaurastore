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
    <div class="admin-login">
      <div class="kicker">Atelier</div>
      <h1 class="serif-title" style="margin-bottom:12px">Jaura Store</h1>
      <p style="color:var(--muted);margin-bottom:20px">Sign in to edit products, prices and stock, and to read your store analytics. You can sign in from any phone or laptop.</p>
      ${msg ? `<p class="admin-err">${JA.escape(msg)}</p>` : ""}
      <form id="login-form" class="field">
        ${needsEmail ? `<label>Email</label>
        <input type="email" name="email" required autocomplete="username" value="${JA.escape(loginEmail)}" />` : ""}
        <label ${needsEmail ? 'style="margin-top:14px"' : ""} data-no-i18n>Password</label>
        <input type="password" name="password" required autocomplete="current-password" />
        <button class="btn" style="margin-top:18px" id="login-btn" data-no-i18n>Sign in</button>
      </form>
      <button type="button" class="wix-link-btn" id="forgot-btn" style="margin-top:16px">Forgot password? Reset it by email</button>
      <div id="otp-slot"></div>
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
      <p class="admin-note">We email a 6-digit code to the admin address. Enter it below with a new password.</p>
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
  return entry.preview || "";
}
function mediaStripHTML(imgs) {
  const tiles = imgs.map((src, i) => `
    <div class="wix-tile${i === 0 ? " is-main" : ""}${src && src.pending ? " is-pending" : ""}" data-img-i="${i}">
      <img src="${JA.asset(imgSrc(src) || (typeof src === "string" ? src : ""))}" alt="" />
      ${i === 0 ? `<span>Main</span>` : `<span>${i + 1}</span>`}
      <button type="button" class="wix-tile-x" data-del-img="${i}" aria-label="Remove">×</button>
    </div>`).join("");
  const plus = imgs.length < 20 ? `<label class="wix-tile wix-plus">+<input type="file" id="more-media" accept="image/*" capture="environment" multiple hidden /></label>` : "";
  return `<div class="wix-media-row">${tiles}${plus}</div>
    <p class="admin-note">Add more than 5 photos — tap + and pick several at once. Up to 20.</p>
    <button type="button" class="wix-view-media" id="view-media">View All Media (${imgs.length}/20) ›</button>`;
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
  const fake = { ...existing, options: collectOptions(box || document), stock };
  const varBox = document.getElementById("var-box");
  if (varBox) varBox.innerHTML = variantsHTML(fake);
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
    if (!window.JA_NET) {                       // static hosting fallback
      try { window.__editImages.push(await fileToData(file)); } catch (err) {}
      box.innerHTML = mediaStripHTML(window.__editImages);
      return;
    }
    const preview = URL.createObjectURL(file);
    const idx = window.__editImages.push({ pending: true, preview }) - 1;
    box.innerHTML = mediaStripHTML(window.__editImages);
    const res = await window.JA_NET.api("api/admin/uploads/image", {
      method: "POST",
      blob: file,
      field: "file",
      filename: file.name || "photo.jpg",
      queue: true,
      label: "Photo",
    });
    if (res && res.url) {
      window.__editImages[idx] = res.url;
      JA.toast("Photo uploaded.");
    } else if (res && res.queued) {
      // it will finish by itself; keep the placeholder so nothing looks lost
      window.__jaPendingPhoto = (window.__jaPendingPhoto || 0) + 1;
    } else {
      window.__editImages[idx] = { pending: true, preview, failed: true };
      JA.toast("That photo did not upload. It will retry by itself.");
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
function variantsHTML(p) {
  const opt = (p.options || [])[0];
  const vals = (opt && opt.values) || p.colors || [];
  if (!vals.length) return `<p class="admin-note">Add a colour or size option to create variants.</p>`;
  return vals.map((v) => `
    <div class="wix-var">
      <div>
        <strong>${JA.escape(v)}</strong>
        <span>${Number(p.compareNgn) > Number(p.priceNgn) ? `<s>${JA.money(p.compareNgn, "NGN")}</s> ` : ""}${JA.money(p.priceNgn || 0, "NGN")} · ${JA.money(JA.toCfa ? JA.toCfa(p.priceNgn) : Math.round((Number(p.priceNgn) || 0) * 0.44), "CFA")}</span>
      </div>
      <em>${Number(p.stock) > 0 ? "In stock" : "Out of stock"}</em>
    </div>`).join("");
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
    <h3>Variants</h3>
    <div id="var-box">${variantsHTML({ ...p, options: opts })}</div>
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
    const deadline = Date.now() + 20000;
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
      nameFr: existing?.nameFr || "",
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
  const cards = all.map((p) => {
    const ngnNow = Number(p.priceNgn) || 0;
    const ngnWas = Number(p.compareNgn) || 0;
    const ngn = ngnNow > 0 ? JA.money(ngnNow, "NGN") : "";
    const ngnStrike = ngnWas > ngnNow ? JA.money(ngnWas, "NGN") : "";
    const cfaNowN = ngnNow > 0 ? (JA.toCfa ? JA.toCfa(ngnNow) : Math.round(ngnNow * 0.44)) : (Number(p.priceCfa) || 0);
    const cfaWasN = ngnWas > 0 ? (JA.toCfa ? JA.toCfa(ngnWas) : Math.round(ngnWas * 0.44)) : 0;
    const cfaNow = JA.money(cfaNowN, "CFA");
    const cfaWas = cfaWasN > cfaNowN ? JA.money(cfaWasN, "CFA") : "";
    const inStock = p.id ? Number(p.stock) > 0 : true;   // a brand-new product starts in stock
    const stock = inStock ? (p.stock + " in Stock") : "Out of stock";
    const hidden = p.online === false ? `<em class="wix-off">Hidden</em>` : "";
    const rowq = JA.escape((p.name + " " + (p.sku || "") + " " + p.category).toLowerCase());
    return `<article class="wix-row" data-row="${rowq}">
      <label class="wix-pick"><input type="checkbox" data-pick="${p.id}" /></label>
      <img src="${JA.asset(p.image)}" alt="" />
      <div class="wix-row-info">
        <strong>${JA.escape(p.name)}</strong>
        ${ngn ? `<span class="wix-ngn">${ngnStrike ? `<s>${ngnStrike}</s> ` : ""}${ngn}</span>` : ""}
        <span class="wix-cfa">${cfaWas ? `<s>${cfaWas}</s> ` : ""}${cfaNow}</span>
        <span class="wix-stock">${stock}</span>
        ${hidden}
      </div>
      <div class="wix-quick">
        <label>₦ price</label>
        <input class="q-price" data-qp="${esc(p.id)}" value="${esc(Number(p.priceNgn) || 0)}" inputmode="numeric" />
        <label>Stock</label>
        <input class="q-stock" data-qs="${esc(p.id)}" type="number" min="0" value="${esc(Number(p.stock) || 0)}" />
        <label class="wix-tog"><span>Sale</span><input type="checkbox" class="q-sale" data-qbadge="${esc(p.id)}" ${p.badge === "sale" ? "checked" : ""} /></label>
        <label class="wix-tog"><span>Online</span><input type="checkbox" class="q-online" data-qo="${esc(p.id)}" ${p.online !== false ? "checked" : ""} /></label>
        <button type="button" class="wix-link-btn" data-quick="${esc(p.id)}">Save</button>
      </div>
      <button type="button" class="wix-stock-tog ${inStock ? "is-in" : "is-out"}" data-stock="${p.id}" data-qty="${inStock ? 0 : 24}">${inStock ? "In stock" : "Out of stock"}</button>
      <button type="button" class="wix-del-row" data-del="${p.id}">Delete</button>
      <button type="button" class="wix-more" data-edit="${p.id}" aria-label="Edit">⋯</button>
    </article>`;
  }).join("");
  return `<div class="wix-list-head">
      <button type="button" class="wix-cats-link" data-tab="categories">Manage Categories</button>
      <div class="field wix-search"><input id="prod-search" type="search" placeholder="Search" autocomplete="off" /></div>
      <p class="wix-count">Products: ${all.length}</p>
      <button type="button" class="wix-del-row" id="del-picked">Delete selected from website</button>
    </div>
    <div class="wix-list">${cards}</div>
    <button type="button" class="wix-add" id="add-product">+ Add a Product</button>`;
}

let dashRange = 30;
let dashTimer = null;

function esc(v) { return JA.escape(String(v == null ? "" : v)); }

function analyticsPanel() {
  return `
    <div class="an-top">
      <h3 class="admin-h" style="margin:0">Store insights</h3>
      <div class="an-range">
        ${[7, 30, 90].map((d) => `<button type="button" class="an-rng${d === dashRange ? " is-on" : ""}" data-range="${d}">${d} days</button>`).join("")}
        <button type="button" class="an-rng" id="an-refresh">Refresh</button>
      </div>
    </div>
    <p class="admin-note">Traffic is counted on the server, so these numbers follow your store — not one phone or browser. This panel refreshes on its own every 30 seconds.</p>
    <div class="stats" id="an-kpis"><div class="stat"><span class="kicker">Loading</span><b>…</b></div></div>
    <h3 class="admin-h">On the site right now</h3>
    <div id="an-live-box" class="live-box">Loading live visitors…</div>
    <h3 class="admin-h">Visitors &amp; page views</h3>
    <div class="an-chart" id="an-chart"><p class="empty">Loading…</p></div>
    <h3 class="admin-h">Most visited pages</h3>
    <div id="an-pages" class="empty">Loading…</div>
    <h3 class="admin-h">Top products</h3>
    <div id="an-products" class="empty">Loading…</div>
    <h3 class="admin-h">Conversion</h3>
    <div class="stats" id="an-conv"></div>
    <div id="an-revenue"></div>
    <h3 class="admin-h">Visitor locations</h3>
    <div id="an-loc" class="empty">Loading…</div>
    <h3 class="admin-h">Latest orders</h3>
    <div id="an-orders" class="empty">Loading…</div>
    <h3 class="admin-h">Recent activity</h3>
    <ul class="an-log" id="an-activity"></ul>`;
}

function dayLabel(day) {
  const d = new Date(day + "T00:00:00Z");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function trafficChart(series) {
  const max = Math.max(1, ...series.map((d) => Math.max(d.views, d.visitors)));
  return `<div class="an-scroll"><div class="an-bars">${series.map((d) => `
      <div class="an-col" title="${esc(d.day)} · ${d.views} view(s) · ${d.visitors} visitor(s)">
        <div class="an-bar-wrap">
          <div class="an-bar an-bar-views" style="height:${Math.round((d.views / max) * 120)}px" title="${d.views} page views"></div>
          <div class="an-bar an-bar-visitors" style="height:${Math.round((d.visitors / max) * 120)}px" title="${d.visitors} visitors"></div>
        </div>
        <span>${esc(dayLabel(d.day))}</span>
        <em>${d.views}</em>
      </div>`).join("")}</div></div>
    <p class="admin-note"><span class="an-key an-key-views"></span> Page views &nbsp; <span class="an-key an-key-visitors"></span> Unique visitors</p>`;
}

function tableHTML(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
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

  $("#an-kpis").innerHTML = [
    ["Live now", t.liveNow || 0, "on the site"],
    ["Unique visitors", t.uniqueVisitors || 0, `${t.newVisitors || 0} new`],
    ["Visits", t.visits || 0, "sessions"],
    ["Page views", t.pageViews || 0, ""],
    ["Orders", c.orders || 0, `${c.units || 0} items`],
  ].map(([k, v, s]) => `<div class="stat"><span class="kicker">${k}</span><b>${v}</b>${s ? `<i>${esc(s)}</i>` : ""}</div>`).join("");

  const live = data.live || [];
  $("#an-live-box").innerHTML = live.length
    ? live.map((v) => `<div class="live-row"><i></i><span>${esc([v.city, v.country].filter(Boolean).join(", ") || "Unknown location")}</span><em>${esc(v.page || v.path || "")}</em></div>`).join("")
    : `<p class="empty">Nobody is browsing right now.</p>`;

  $("#an-chart").innerHTML = trafficChart(data.series || []);

  const pages = data.topPages || [];
  $("#an-pages").innerHTML = pages.length
    ? tableHTML(["Page", "Views", "Visitors"],
      pages.map((p) => `<tr><td>${esc(p.path)}</td><td>${p.views}</td><td>${p.visitors}</td></tr>`).join(""))
    : `<p class="empty">No page views yet.</p>`;

  const prods = data.topProducts || [];
  $("#an-products").innerHTML = prods.length
    ? tableHTML(["Product", "Views", "Added to cart", "Orders"],
      prods.map((p) => `<tr><td>${esc(p.name || p.productId)}</td><td>${p.views || 0}</td><td>${p.carts || 0}</td><td>${p.purchases || 0}</td></tr>`).join(""))
    : `<p class="empty">No product activity yet. Open the shop and tap a product to start the count.</p>`;

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
      orders.map((o) => `<tr><td>${esc(o.id)}</td><td>${esc(o.customer_name || "")}</td><td>${esc(JA.money(o.total, o.currency))}</td><td>${esc(o.status)}</td></tr>`).join(""))
    : `<p class="empty">No orders yet.</p>`;

  const s = (JA.getStats && JA.getStats()) || {};
  $("#an-activity").innerHTML = (s.events || []).slice(0, 12)
    .map((e) => `<li><strong>${esc(e.type)}</strong> · ${esc(e.name || e.page || "")} · ${e.at ? new Date(e.at).toLocaleString() : ""}</li>`).join("")
    || "<li>Nothing yet on this device.</li>";

  document.querySelectorAll("[data-range]").forEach((b) => {
    b.onclick = () => { dashRange = Number(b.dataset.range); paintDesk("analytics"); };
  });
  const ref = $("#an-refresh");
  if (ref) ref.onclick = () => { paintDesk("analytics"); JA.toast("Refreshed."); };
}

function startDashTimer() {
  clearInterval(dashTimer);
  dashTimer = setInterval(() => {
    if (document.body.dataset.page !== "admin") return;
    const on = document.querySelector("#panel-analytics.is-on");
    if (on && !document.hidden) fillAnalytics();
  }, 30000);
}

let serverOrders = [];

function orderStatusLabel(s) {
  return s === "confirmed" ? "Confirmed" : s === "declined" ? "Declined" : "Pending";
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-pdf-open]");
  if (!btn) return;
  const slot = btn.parentElement && btn.parentElement.querySelector(".proof-pdf-slot");
  if (!slot) return;
  if (slot.querySelector("iframe")) {          // second click closes it again
    slot.innerHTML = "";
    btn.textContent = "Show the PDF here";
    return;
  }
  slot.innerHTML = `<iframe class="proof-frame" src="${btn.getAttribute("data-pdf-open")}"
    title="Payment receipt"></iframe>`;
  btn.textContent = "Hide the PDF";
});

function fileTypeOf(url) {
  const u = String(url || "").split("?")[0].toLowerCase();
  if (u.endsWith(".pdf")) return "pdf";
  if (u.endsWith(".png") || u.endsWith(".jpg") || u.endsWith(".jpeg")
      || u.endsWith(".webp") || u.endsWith(".gif")) return "image";
  return "other";
}

/* Show the receipt itself inside the admin panel - not a link to go and
   find it. PDFs open in an embedded viewer, images render inline, and
   there is always a download button for the original file. */
function receiptViewer(url, label, name) {
  if (!url) return `<p class="empty">No receipt attached.</p>`;
  const kind = fileTypeOf(url);
  const safe = esc(url);
  const title = esc(label || "Receipt");
  const fname = esc(name || String(url).split("/").pop() || "receipt");
  // A page with dozens of orders cannot hold a PDF viewer for every one of
  // them - it makes the tab fall over, on a phone worst of all. Images are
  // cheap and render at once; PDFs open in place when you ask for them.
  const body = kind === "pdf"
    ? `<button type="button" class="btn btn-line" data-pdf-open="${safe}">Show the PDF here</button>
       <div class="proof-pdf-slot"></div>`
    : kind === "image"
      ? `<img class="proof-preview" src="${safe}" alt="${title}" loading="lazy" />`
      : `<p class="admin-note">This file cannot be shown here. Download it to open it.</p>`;
  return `
    <p class="proof-label">${title} — the original file, opened right here</p>
    <div class="proof-frame-wrap">${body}</div>
    <p class="proof-actions">
      <a class="btn btn-line" href="${safe}" target="_blank" rel="noopener">Open full size</a>
      <a class="btn btn-line" href="${safe}" download="${fname}">Download ${esc(kind === "pdf" ? "PDF" : "file")}</a>
    </p>`;
}

function orderCardHTML(o) {
  const c = o.customer || {};
  const shot = o.proofUrl || (JA.getProof && JA.getProof(o.id, o.proof)) || "";
  const when = o.at ? new Date(o.at).toLocaleString() : "";
  return `
    <article class="order-card" data-order="${esc(o.id)}">
      <div class="order-card-top">
        <div>
          <div class="kicker">${esc(o.id)}</div>
          <strong>${esc(c.name || "Customer")}</strong>
          <p>${esc(c.email || "")}</p>
          <p>${esc(c.phone || "")} · ${esc([c.city, c.zone].filter(Boolean).join(" / "))}</p>
          <p>${esc([c.address, c.country].filter(Boolean).join(", "))}</p>
          ${c.note ? `<p class="order-note"><em>Note:</em> ${esc(c.note)}</p>` : ""}
          <p>${esc(when)}</p>
        </div>
        <div>
          <p class="status-pill ${esc(o.status || "pending")}">${esc(orderStatusLabel(o.status))}</p>
          <p style="margin-top:8px"><strong>${esc(JA.money(o.total, o.currency))}</strong> · ${o.currency === "NGN" ? "Naira" : "CFA"}</p>
          <p class="admin-note">Pay by ${esc(o.payment || o.currency || "")}</p>
        </div>
      </div>
      <ul class="order-items">${(o.items || []).map((i) => `<li>${i.qty}× ${esc(i.name)}${i.color ? " · " + esc(i.color) : ""}</li>`).join("")}</ul>
      ${shot
        ? receiptViewer(shot, `Payment receipt for ${o.id} — stored on the server, never cleaned up`,
                        `${o.id}-receipt`)
        : `<p class="empty">No receipt attached.</p>`}
      <div class="order-actions">
        <button class="btn" data-confirm="${esc(o.id)}">Confirm purchase</button>
        <button class="btn btn-line" data-decline="${esc(o.id)}">Decline</button>
        ${(o.status || "pending") !== "pending" ? `<button class="btn btn-line" data-reopen="${esc(o.id)}">Back to pending</button>` : ""}
      </div>
    </article>`;
}

function ordersPanel() {
  return `
    <div class="confirm-how">
      <h3>Every checkout is kept</h3>
      <ol>
        <li>A customer fills the checkout form and uploads their bank screenshot.</li>
        <li>The whole form — name, phone, email, address, delivery location, payment choice and notes — is saved on the server, together with the screenshot.</li>
        <li>It stays in this tab forever, even after you confirm or decline it. Nothing is cleaned up.</li>
        <li>Tap <strong>Confirm purchase</strong> to email them a receipt, or <strong>Decline</strong> if the payment did not land.</li>
      </ol>
      <p><a class="btn btn-line" href="api/admin/orders.csv">Download all orders (CSV)</a></p>
    </div>
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
  box.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Sent</th><th>Order</th><th>Customer</th><th>Contact</th><th>Method</th><th>Receipt</th><th>Emailed</th></tr></thead>
    <tbody>${shown.map((p) => `<tr>
      <td>${esc((p.at || "").replace("T", " ").slice(0, 16))}</td>
      <td>${esc(p.order_id || "")}</td>
      <td>${esc(p.name || "")}<br /><small>${esc(p.items || "")}</small></td>
      <td>${esc(p.phone || "")}<br /><small>${esc(p.email || "")}</small></td>
      <td>${esc(p.method || "")}</td>
      <td>${p.file_url
        ? (fileTypeOf(p.file_url) === "image"
            ? `<a href="${esc(p.file_url)}" target="_blank" rel="noopener"><img class="proof-thumb" src="${esc(p.file_url)}" alt="Receipt" loading="lazy" /></a>`
            : `<a class="btn btn-line" href="${esc(p.file_url)}" target="_blank" rel="noopener">View ${esc((p.file_name || "").split(".").pop().toUpperCase())}</a>`)
          + `<br /><a class="btn btn-line" href="${esc(p.file_url)}" download="${esc(p.file_name || "receipt")}">Download</a>
             <small>${Math.max(1, Math.round((p.file_size || 0) / 1024))} KB</small>`
        : "—"}</td>
      <td>${p.emailed ? "yes" : `<span style="color:#c0392b" title="${esc(p.email_info || "")}">no</span>`}</td>
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
  const page = serverOrders.slice(0, ordersShown);
  box.innerHTML = page.map(orderCardHTML).join("")
    + (serverOrders.length > ordersShown
      ? `<p class="admin-more"><button type="button" class="btn btn-line" id="orders-more">
           Show ${Math.min(ORDER_PAGE, serverOrders.length - ordersShown)} more
           of ${serverOrders.length}</button></p>`
      : `<p class="admin-note">Showing all ${serverOrders.length} orders.</p>`);
  const more = $("#orders-more");
  if (more) more.onclick = () => { ordersShown += ORDER_PAGE; renderOrderPage(); };
  bindOrderButtons();
}

async function fillOrders() {
  const box = $("#orders-box");
  if (!box) return;
  serverOrders = await JA.adminOrders({ limit: 200 });
  ordersShown = ORDER_PAGE;
  if (!serverOrders.length) {
    box.innerHTML = `<p class="empty">No orders yet. Every completed checkout lands here and stays here.</p>`;
    return;
  }
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
      <p class="admin-note">At least 10 characters, with an upper case letter, a lower case letter and a number. It applies to every admin account, so the one password is what any of you type on the sign-in screen. If you ever forget it, use <em>Forgot password? Reset it by email</em> on the sign-in screen — a 6-digit code is emailed to the admin address.</p>
    </div>
    <div class="admin-card" style="margin-top:22px">
      <h3 class="admin-h">Connection &amp; sync</h3>
      <p class="admin-note" id="sync-note">Checking for unsaved changes…</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn btn-line" id="retry-sync">Retry now</button>
        <button class="btn btn-line" id="reload-cat">Reload catalogue</button>
        <button class="btn" id="sync-github">Sync to GitHub</button>
      </div>
      <div id="sync-status" class="admin-note" style="margin-top:12px"></div>
      <p class="admin-note" style="margin-top:12px">Saved changes go straight to the store. If your Wi-Fi drops, they wait in this device and push themselves up when the connection returns. The <strong>Sync to GitHub</strong> button also saves the latest product data into the repository so it never disappears on a redeploy.</p>
    </div>`;
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

function paintDesk(tab = "analytics") {
  const all = JA.products();
  const pending = serverOrders.filter((o) => (o.status || "pending") === "pending").length;
  $("#admin-root").innerHTML = `
    <div class="admin-shell wrap">
      <div class="admin-top">
        <div>
          <div class="kicker">Atelier</div>
          <h1 class="serif-title">Admin · Jaura Store</h1>
        </div>
        <div style="display:flex;gap:10px">
          <a class="btn btn-line" href="index.html">View store</a>
          <button class="btn btn-line" id="logout">Sign out</button>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><span class="kicker">Products</span><b>${all.length}</b></div>
        <div class="stat"><span class="kicker">In stock</span><b>${all.filter((p) => Number(p.stock) > 0).length}</b></div>
        <div class="stat"><span class="kicker">Pending orders</span><b id="pending-orders">${pending || "…"}</b></div>
        <div class="stat"><span class="kicker">Live on site</span><b id="live-pill">…</b></div>
      </div>
      <div class="tabs">
        <button data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">Insights</button>
        <button data-tab="products" class="${tab === "products" ? "is-on" : ""}">Edit products</button>
        <button data-tab="bulk" class="${tab === "bulk" ? "is-on" : ""}">Bulk upload</button>
        <button data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">Orders</button>
        <button data-tab="categories" class="${tab === "categories" ? "is-on" : ""}">Categories</button>
        <button data-tab="settings" class="${tab === "settings" ? "is-on" : ""}">Store settings</button>
        <button data-tab="account" class="${tab === "account" ? "is-on" : ""}">My account</button>
      </div>
      <section class="panel ${tab === "analytics" ? "is-on" : ""}" id="panel-analytics">
        ${tab === "analytics" ? analyticsPanel() : ""}
      </section>
      <section class="panel ${tab === "products" ? "is-on" : ""}" id="panel-products">
        ${editingId === "new" ? `<div id="form-slot">${productForm()}</div>`
          : (editingId ? `<div id="form-slot">${productForm(JA.product(editingId) || {})}</div>`
          : productsTable())}
      </section>
      <section class="panel ${tab === "bulk" ? "is-on" : ""}" id="panel-bulk">
        <p style="color:var(--muted);max-width:640px;margin-bottom:16px">
          Import products from a CSV. Columns:
          <code>name, category, priceNgn, compareNgn, stock, badge, description, colors</code>.
          Enter Naira only — F CFA is converted at 1 ₦ = 0.44.
          Category must be one of: ${(JA.categories ? JA.categories() : JA.CATEGORIES).map((c) => c.id).join(", ")}.
          Photos can be attached afterwards by editing each piece — originals are never altered.
        </p>
        <p><a class="btn btn-line" id="dl-template" href="#">Download CSV template</a></p>
        <div class="field" style="max-width:520px;margin-top:18px">
          <label>CSV file</label>
          <input type="file" id="csv-file" accept=".csv,text/csv" />
        </div>
        <button class="btn" id="run-import" style="margin-top:16px">Import products</button>
        <p id="import-msg" style="margin-top:12px;color:var(--taupe)"></p>
      </section>
      <section class="panel ${tab === "orders" ? "is-on" : ""}" id="panel-orders">
        ${tab === "orders" ? ordersPanel() : ""}
      </section>
      <section class="panel ${tab === "categories" ? "is-on" : ""}" id="panel-categories">
        ${categoryManager()}
      </section>
      <section class="panel ${tab === "settings" ? "is-on" : ""}" id="panel-settings">
        ${settingsForm()}
      </section>
      <section class="panel ${tab === "account" ? "is-on" : ""}" id="panel-account">
        ${tab === "account" ? accountPanel() : ""}
      </section>
    </div>
    <nav class="admin-app-nav">
      <button type="button" data-tab="products" class="${tab === "products" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 10l8-7 8 7v9H4z"/></svg><span>Products</span>
      </button>
      <button type="button" data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 19V9h3v10H4zm6 0V5h3v14h-3zm6 0v-7h3v7h-3z"/></svg><span>Insights</span>
      </button>
      <button type="button" data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 5h16v10H7l-3 3V5z"/></svg><span>Orders</span>
      </button>
      <button type="button" data-tab="account" class="${tab === "account" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.4"/><path d="M4.5 20c1.4-3.6 4.2-5.4 7.5-5.4s6.1 1.8 7.5 5.4z"/></svg><span>Account</span>
      </button>
    </nav>`;

  $("#logout").onclick = async () => { await JA.logoutAdmin(); paintLogin("Signed out."); };
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => paintDesk(b.dataset.tab);
  });

  if (tab === "analytics") { fillAnalytics(); startDashTimer(); refreshLivePill(); }
  if (tab === "orders") { fillOrders(); fillProofs(); }
  if (tab === "account") bindAccount();

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
    b.onclick = () => {
      editingId = b.dataset.edit;
      paintDesk("products");
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
  });
  document.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (confirm("Delete this product from the website? Customers will not see it.")) {
        await JA.removeProduct(b.dataset.del);
        JA.toast("Deleted from the website.");
        paintDesk("products");
      }
    };
  });
  document.querySelectorAll("[data-quick]").forEach((b) => {
    b.onclick = async () => {
      const pid = b.dataset.quick;
      const p = JA.product(pid);
      if (!p) return;
      const price = `[data-qp="${CSS.escape(pid)}"]`;
      const stock = `[data-qs="${CSS.escape(pid)}"]`;
      const badge = `[data-qbadge="${CSS.escape(pid)}"]`;
      const online = `[data-qo="${CSS.escape(pid)}"]`;
      const next = {
        ...p,
        priceNgn: parseInt(document.querySelector(price)?.value || "0", 10) || 0,
        priceCfa: 0,
        stock: parseInt(document.querySelector(stock)?.value || "0", 10) || 0,
        badge: document.querySelector(badge)?.checked ? "sale" : "",
        online: document.querySelector(online)?.checked !== false,
      };
      await JA.upsertProduct(next);
      JA.toast("Quick edit saved.");
      paintDesk("products");
    };
  });
  document.getElementById("del-picked")?.addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("[data-pick]:checked")].map((el) => el.getAttribute("data-pick"));
    if (!ids.length) { JA.toast("Tick the products to delete first."); return; }
    if (!confirm("Delete " + ids.length + " product(s) from the website?")) return;
    for (const id of ids) await JA.removeProduct(id);
    JA.toast(ids.length + " product(s) deleted.");
    paintDesk("products");
  });
  document.querySelectorAll("[data-stock]").forEach((b) => {
    b.onclick = async () => {
      const p = JA.product(b.dataset.stock);
      if (!p) return;
      const qty = Number(b.dataset.qty);
      await JA.upsertProduct({ ...p, stock: qty });
      JA.toast(qty > 0 ? p.name + " is in stock." : p.name + " is out of stock.");
      paintDesk("products");
    };
  });

  $("#dl-template")?.addEventListener("click", (e) => {
    e.preventDefault();
    const header = "name,category,priceNgn,compareNgn,stock,badge,description,colors\n";
    const sample = "Aura Mini Crossbody,bags,9200,12000,14,new,Structured everyday bag,\"Black, Tan\"\n";
    const blob = new Blob([header + sample], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "jaura-products-template.csv";
    a.click();
  });

  $("#prod-search")?.addEventListener("input", (e) => {
    const q = String(e.target.value || "").toLowerCase().trim();
    document.querySelectorAll("#panel-products .wix-row").forEach((row) => {
      row.style.display = !q || (row.getAttribute("data-row") || "").includes(q) ? "" : "none";
    });
  });

  $("#run-import")?.addEventListener("click", runCSV);

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
    JA.toast("Settings saved.");
    JA.mountChrome();
  });
}

async function refreshLivePill() {
  const el = $("#live-pill");
  if (!el) return;
  const data = await JA.adminAnalytics(1);
  if (el) el.textContent = (data && data.totals && data.totals.liveNow) || 0;
}

function categoryManager() {
  const cats = JA.categories();
  const rows = cats.map((c, i) => {
    const n = JA.products().filter((p) => p.category === c.id).length;
    return `<article class="wix-cat-card" data-cat-i="${i}" data-cat-id="${JA.escape(c.id)}">
      <div class="wix-cat-pic">
        <img src="${JA.asset(c.image)}" alt="" />
        <label class="wix-cat-up">Change photo<input type="file" accept="image/*" capture="environment" data-cat-img="${i}" hidden /></label>
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

function collectCats() {
  const out = [];
  document.querySelectorAll("[data-cat-i]").forEach((row) => {
    const i = row.getAttribute("data-cat-i");
    const id = (row.querySelector(`[name="cat-id-${i}"]`)?.value || "").trim();
    const name = (row.querySelector(`[name="cat-name-${i}"]`)?.value || "").trim();
    if (!id || !name) return;
    const img = row.querySelector(".wix-cat-pic img")?.getAttribute("src") || "";
    out.push({
      id,
      name,
      nameFr: (row.querySelector(`[name="cat-fr-${i}"]`)?.value || "").trim(),
      image: img,
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
    try {
      const data = await fileToData(input.files[0]);
      const card = input.closest("[data-cat-i]");
      const img = card?.querySelector(".wix-cat-pic img");
      if (img) img.src = data;
      persist("Photo saved.");
    } catch (err) {
      JA.toast("Could not read that photo. Try another from your gallery.");
    }
  });
  list.addEventListener("click", (e) => {
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
  return categoryManager() + `<form id="set-form" class="form-grid" style="background:var(--paper);border:1px solid var(--line);padding:20px;margin-top:28px">
    <p class="admin-note full">Naira is the only price you enter on products. The website converts F CFA at <strong>1 ₦ = 0.44 F CFA</strong>.</p>
    <div class="field"><label>WhatsApp (digits only)</label><input name="whatsapp" value="${s.whatsapp}" /></div>
    <div class="field"><label>Phone Benin</label><input name="phoneBj" value="${s.phoneBj}" /></div>
    <div class="field"><label>Phone Nigeria</label><input name="phoneNg" value="${s.phoneNg}" /></div>
    <div class="field"><label>Email</label><input name="email" value="${s.email}" /></div>
    <div class="field full"><label>Pay-in-CFA instructions</label><textarea name="bankCfa" rows="4">${JA.escape(s.bankCfa)}</textarea></div>
    <div class="field full"><label>Pay-in-Naira instructions</label><textarea name="bankNgn" rows="4">${JA.escape(s.bankNgn)}</textarea></div>
    <div class="field full"><button class="btn">Save settings</button></div>
  </form>`;
}

function parseCSV(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return [];
  const headers = splitCSVLine(lines[0]).map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cols = splitCSVLine(line);
    const row = {};
    headers.forEach((h, i) => { row[h] = cols[i] ?? ""; });
    return row;
  });
}
function splitCSVLine(line) {
  const out = [];
  let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (q && line[i + 1] === '"') { cur += '"'; i++; }
      else q = !q;
    } else if (ch === "," && !q) { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

function runCSV() {
  const file = $("#csv-file")?.files?.[0];
  const msg = $("#import-msg");
  if (!file) { msg.textContent = "Choose a CSV first."; return; }
  const reader = new FileReader();
  reader.onload = () => {
    const rows = parseCSV(String(reader.result));
    const validCats = new Set((JA.categories ? JA.categories() : JA.CATEGORIES).map((c) => c.id));
    let n = 0;
    rows.forEach((r, idx) => {
      if (!r.name) return;
      const cat = (r.category || "household").trim();
      const catsNow = JA.categories ? JA.categories() : JA.CATEGORIES;
      const image = (catsNow.find((c) => c.id === cat) || catsNow[1] || {}).image;
      const priceNgn = Number(r.priceNgn) || 0;
      const compareNgn = r.compareNgn ? Number(r.compareNgn) : null;
      JA.upsertProduct({
        id: "ja-csv-" + Date.now().toString(36) + "-" + idx,
        sku: "JAU" + String(idx + 1).padStart(3, "0"),
        slug: slugify(r.name) + "-" + idx,
        name: r.name.trim(),
        category: validCats.has(cat) ? cat : "household",
        priceNgn,
        compareNgn,
        priceCfa: priceNgn > 0 ? 0 : (Number(r.priceCfa) || 0),
        compareCfa: null,
        image,
        description: r.description || "",
        stock: r.stock === "" ? 10 : Number(r.stock),
        badge: r.badge || "",
        featured: false,
        colors: String(r.colors || "").split(",").map((s) => s.trim()).filter(Boolean),
      });
      n += 1;
    });
    msg.textContent = `Imported ${n} products. Open Products to add photos — images stay exactly as you upload them.`;
    JA.toast(`${n} products added.`);
  };
  reader.readAsText(file);
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
