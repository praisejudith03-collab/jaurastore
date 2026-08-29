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

function paintLogin() {
  $("#admin-root").innerHTML = `
    <div class="admin-login">
      <div class="kicker">Atelier</div>
      <h1 class="serif-title" style="margin-bottom:12px">Jaura Store</h1>
      <p style="color:var(--muted);margin-bottom:20px">Edit products, prices, stock and see website analytics.</p>
      <form id="login-form" class="field">
        <label>Access pin</label>
        <input type="password" name="pin" required autocomplete="current-password" />
        <button class="btn" style="margin-top:16px">Enter</button>
      </form>
    </div>`;
  $("#login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const pin = new FormData(e.target).get("pin");
    if (JA.loginAdmin(pin)) paintDesk();
    else JA.toast("Incorrect pin.");
  });
}


let editingId = null;

function productImages(p) {
  if (p && p.images && p.images.length) return p.images.slice(0, 20);
  if (p && p.image) return [p.image];
  return [];
}
function mediaStripHTML(imgs) {
  const tiles = imgs.map((src, i) => `
    <div class="wix-tile${i === 0 ? " is-main" : ""}" data-img-i="${i}">
      <img src="${JA.asset(src)}" alt="" />
      ${i === 0 ? `<span>Main</span>` : `<span>${i + 1}</span>`}
      <button type="button" class="wix-tile-x" data-del-img="${i}" aria-label="Remove">×</button>
    </div>`).join("");
  const plus = imgs.length < 20 ? `<label class="wix-tile wix-plus">+<input type="file" id="more-media" accept="image/*" multiple hidden /></label>` : "";
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
      if (window.__editImages.length >= 15) break;
      try {
        window.__editImages.push(await fileToData(file));
      } catch (err) {
        JA.toast("Could not read that photo. Try another from your gallery.");
      }
    }
    input.value = "";
    box.innerHTML = mediaStripHTML(window.__editImages);
  });
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
  const inStock = Number(p.stock) > 0;
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
        <input name="stock" id="stock-qty" type="number" min="0" value="${p.stock ?? 0}" />
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
  let images = (window.__editImages || []).filter(Boolean).slice(0, 20);
  if (!images.length && existing) {
    images = (existing.images && existing.images.length) ? existing.images.slice(0, 20) : (existing.image ? [existing.image] : []);
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
  try {
    JA.upsertProduct({
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
  } catch (err) {
    JA.toast("Too many large photos for this phone. Remove a few or pick smaller pictures, then Save.");
    return;
  }
  JA.toast(status === "out" ? "Saved · Out of stock." : "Product saved · " + images.length + " photo(s).");
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
    const inStock = Number(p.stock) > 0;
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

function analyticsPanel() {
  const s = (JA.getStats && JA.getStats()) || {};
  const ords = JA.orders();
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const key = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    days.push({ key, label: d.toLocaleDateString(undefined, { weekday: "short" }), n: (s.days && s.days[key] && s.days[key].visits) || 0 });
  }
  const maxV = Math.max(1, ...days.map((d) => d.n));
  const top = Object.entries(s.products || {}).sort((a, b) => (b[1].carts + b[1].views) - (a[1].carts + a[1].views)).slice(0, 8);
  const revNgn = ords.reduce((n, o) => n + (o.currency === "NGN" ? Number(o.total) || 0 : 0), 0);
  const revCfa = ords.reduce((n, o) => n + (o.currency !== "NGN" ? Number(o.total) || 0 : 0), 0);
  const pending = ords.filter((o) => (o.status || "pending") === "pending").length;
  return `
    <p class="admin-note">Anyone who opens the Jaura Store link is counted as a visitor, with their city and country (like Wix). Live visitors refresh automatically.</p>
    <div class="stats">
      <div class="stat"><span class="kicker">Live now</span><b id="live-count">…</b></div>
      <div class="stat"><span class="kicker">Visitors</span><b id="vis-count">${s.visits || 0}</b></div>
      <div class="stat"><span class="kicker">Add to cart</span><b>${s.carts || 0}</b></div>
      <div class="stat"><span class="kicker">Orders</span><b>${ords.length}</b></div>
    </div>
    <h3 class="admin-h">Live visitors</h3>
    <div id="live-box" class="live-box">Loading live visitors…</div>
    <h3 class="admin-h">Visitor locations</h3>
    <div id="loc-box" class="empty">Waiting for visitors…</div>
    <h3 class="admin-h">Most visited pages</h3>
    <div id="page-box" class="empty">Waiting for page views…</div>
    <div class="stats">
      <div class="stat"><span class="kicker">Pending</span><b>${pending}</b></div>
      <div class="stat"><span class="kicker">Sales ₦</span><b>${JA.money(revNgn, "NGN")}</b></div>
      <div class="stat"><span class="kicker">Sales F CFA</span><b>${JA.money(revCfa, "CFA")}</b></div>
      <div class="stat"><span class="kicker">Checkouts started</span><b>${s.checkouts || 0}</b></div>
    </div>
    <h3 class="admin-h">Last 7 days · visits</h3>
    <div class="an-bars">${days.map((d) => `
      <div class="an-col" title="${d.key}: ${d.n}">
        <div class="an-bar" style="height:${Math.round((d.n / maxV) * 120)}px"></div>
        <span>${d.label}</span>
        <em>${d.n}</em>
      </div>`).join("")}</div>
    <h3 class="admin-h">Top products</h3>
    ${top.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Product</th><th>Views</th><th>Added to cart</th></tr></thead>
      <tbody>${top.map(([id, x]) => `<tr><td>${JA.escape(x.name || id)}</td><td>${x.views || 0}</td><td>${x.carts || 0}</td></tr>`).join("")}</tbody>
    </table></div>` : `<p class="empty">No product activity yet. Open the shop and tap products to start the count.</p>`}
    <h3 class="admin-h">Recent activity</h3>
    <ul class="an-log">${(s.events || []).slice(0, 12).map((e) => `<li><strong>${JA.escape(e.type)}</strong> · ${JA.escape(e.name || e.page || "")} · ${e.at ? new Date(e.at).toLocaleString() : ""}</li>`).join("") || "<li>None yet</li>"}</ul>
  `;
}

async function fillRemoteAnalytics() {
  if (!JA.fetchRemoteEvents) return;
  const events = await JA.fetchRemoteEvents();
  const now = Date.now();
  const liveMap = {};
  events.forEach((e) => {
    if ((e.type === "live" || e.type === "visit") && e.sid && now - e.t < 90000) {
      liveMap[e.sid] = e;
    }
  });
  const live = Object.values(liveMap);
  const visEl = document.getElementById("live-count");
  if (visEl) visEl.textContent = live.length;
  const box = document.getElementById("live-box");
  if (box) {
    box.innerHTML = live.length
      ? live.map((e) => `<div class="live-row"><i></i><span>${JA.escape([e.city, e.region, e.country].filter(Boolean).join(", ") || "Unknown")}</span><em>${JA.escape(e.page || "")}</em></div>`).join("")
      : `<p class="empty">No one is on the site right now.</p>`;
  }
  const loc = {};
  const pages = {};
  const prods = {};
  const sids = new Set();
  events.forEach((e) => {
    if (e.sid) sids.add(e.sid);
    const place = [e.city, e.country].filter(Boolean).join(", ");
    if (place) loc[place] = (loc[place] || 0) + 1;
    if (e.page) pages[e.page] = (pages[e.page] || 0) + 1;
    if (e.id && (e.type === "view" || e.type === "cart")) {
      if (!prods[e.id]) prods[e.id] = { name: e.name || e.id, views: 0, carts: 0 };
      if (e.name) prods[e.id].name = e.name;
      if (e.type === "view") prods[e.id].views += 1;
      if (e.type === "cart") prods[e.id].carts += 1;
    }
  });
  const visCount = document.getElementById("vis-count");
  if (visCount && sids.size) visCount.textContent = sids.size;
  const locBox = document.getElementById("loc-box");
  if (locBox) {
    const rows = Object.entries(loc).sort((a, b) => b[1] - a[1]).slice(0, 12);
    locBox.innerHTML = rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Location</th><th>Sessions</th></tr></thead><tbody>${
          rows.map(([k, n]) => `<tr><td>${JA.escape(k)}</td><td>${n}</td></tr>`).join("")
        }</tbody></table></div>`
      : `<p class="empty">No locations yet. Ask someone to open the shop link.</p>`;
  }
  const pageBox = document.getElementById("page-box");
  if (pageBox) {
    const rows = Object.entries(pages).sort((a, b) => b[1] - a[1]).slice(0, 10);
    pageBox.innerHTML = rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Page</th><th>Views</th></tr></thead><tbody>${
          rows.map(([k, n]) => `<tr><td>${JA.escape(k)}</td><td>${n}</td></tr>`).join("")
        }</tbody></table></div>`
      : `<p class="empty">No page views yet.</p>`;
  }
  const topRemote = Object.entries(prods).sort((a, b) => (b[1].views + b[1].carts) - (a[1].views + a[1].carts)).slice(0, 8);
  if (topRemote.length) {
    const holder = document.querySelector("#panel-analytics .table-wrap");
    if (holder && holder.parentElement) {
      /* keep existing top products table if local is empty — rewrite first table after Top products */
    }
  }
  window.__jaRemoteOrders = {};
  const forms = [];
  events.forEach((e) => {
    if (e.type === "checkout" && e.order && e.order.id) {
      window.__jaRemoteOrders[e.order.id] = e.order;
      forms.push(e.order);
    }
  });
  const remoteBox = document.getElementById("remote-forms");
  if (remoteBox && forms.length) {
    const localIds = new Set((JA.orders() || []).map((o) => o.id));
    const extra = forms.filter((o) => !localIds.has(o.id));
    remoteBox.innerHTML = extra.map((o) => {
      const c = o.customer || {};
      return `<article class="order-card">
        <div class="order-card-top">
          <div>
            <div class="kicker">${JA.escape(o.id)}</div>
            <strong>${JA.escape(c.name || "")}</strong>
            <p>${JA.escape(c.email || "")}</p>
            <p>${JA.escape(c.phone || "")} · ${JA.escape(c.city || "")} · ${JA.escape(c.zone || "")}</p>
            <p>${o.at ? new Date(o.at).toLocaleString() : ""}</p>
          </div>
          <div>
            <p class="status-pill pending">Pending</p>
            <p style="margin-top:8px"><strong>${JA.money(o.total, o.currency)}</strong></p>
          </div>
        </div>
        <ul class="order-items">${(o.items || []).map((i) => `<li>${i.qty}× ${JA.escape(i.name || "")}${i.color ? " · " + JA.escape(i.color) : ""}</li>`).join("")}</ul>
        ${c.address ? `<p style="font-size:13px;color:var(--muted)">${JA.escape(c.address)}</p>` : ""}
        ${c.note ? `<p style="font-size:13px;color:var(--muted)">Note: ${JA.escape(c.note)}</p>` : ""}
        <p class="empty">Payment screenshot is in the jaurastore@gmail.com inbox if the customer uploaded one.</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px">
          <button class="btn" data-confirm="${JA.escape(o.id)}">Confirm purchase</button>
        </div>
      </article>`;
    }).join("");
    remoteBox.querySelectorAll("[data-confirm]").forEach((b) => {
      b.onclick = async () => {
        const o = window.__jaRemoteOrders[b.dataset.confirm];
        if (o && JA.sendReceipt) {
          JA.toast("Sending receipt…");
          await JA.sendReceipt(o);
          JA.toast("Payment confirmed · receipt emailed to " + ((o.customer && o.customer.email) || "customer"));
        }
      };
    });
  }
}

function paintDesk(tab = "analytics") {
  const all = JA.products();
  const ords = JA.orders();
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
        <div class="stat"><span class="kicker">In stock</span><b>${all.filter((p) => p.stock > 0).length}</b></div>
        <div class="stat"><span class="kicker">Pending orders</span><b>${ords.filter((o) => (o.status || "pending") === "pending").length}</b></div>
        <div class="stat"><span class="kicker">Currencies</span><b>2</b></div>
      </div>
      <div class="tabs">
        <button data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">Analytics</button>
        <button data-tab="products" class="${tab === "products" ? "is-on" : ""}">Edit products</button>
        <button data-tab="bulk" class="${tab === "bulk" ? "is-on" : ""}">Bulk upload</button>
        <button data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">Form submissions</button>
        <button data-tab="categories" class="${tab === "categories" ? "is-on" : ""}">Categories</button>
        <button data-tab="settings" class="${tab === "settings" ? "is-on" : ""}">Settings</button>
      </div>
      <section class="panel ${tab === "analytics" ? "is-on" : ""}" id="panel-analytics">
        ${analyticsPanel()}
      </section>
      <section class="panel ${tab === "products" ? "is-on" : ""}" id="panel-products">
        ${editingId === "new" ? `<div id="form-slot">${productForm()}</div>`
          : (editingId ? `<div id="form-slot">${productForm(JA.product(editingId) || {})}</div>`
          : productsTable())}
      </section>
      <section class="panel ${tab === "bulk" ? "is-on" : ""}" id="panel-bulk">
        <p style="color:var(--muted);max-width:640px;margin-bottom:16px">
          Import 200+ products from a CSV. Columns:
          <code>name, category, priceNgn, compareNgn, stock, badge, description, colors</code>.
          Enter Naira only — F CFA is converted on the website at 1 ₦ = 0.44.
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
        <div class="confirm-how">
          <h3>How to confirm an order</h3>
          <ol>
            <li>Customer checks out and uploads the bank screenshot.</li>
            <li>You get an email at <strong>jaurastore@gmail.com</strong> — subject starts with <em>JauraStore order JA-…</em>. The payment picture is the file attached to that email.</li>
            <li>Open this tab. You will see their form (name, phone, Gmail, address) and the receipt photo.</li>
            <li>Tap <strong>Confirm purchase</strong>. Jaura Store emails them a thank-you receipt at the Gmail they used.</li>
          </ol>
        </div>
        <div id="remote-forms"></div>
        ${ords.length ? ords.map((o) => `
          <article class="order-card">
            <div class="order-card-top">
              <div>
                <div class="kicker">${o.id}</div>
                <strong>${JA.escape(o.customer.name || "")}</strong>
                <p>${JA.escape(o.customer.email || "")}</p>
                <p>${JA.escape(o.customer.phone || "")} · ${JA.escape(o.customer.city || "")} · ${JA.escape(o.customer.zone || "")}</p>
                <p>${new Date(o.at).toLocaleString()}</p>
              </div>
              <div>
                <p class="status-pill ${o.status || "pending"}">${o.status === "confirmed" ? "Confirmed" : o.status === "declined" ? "Declined" : "Pending"}</p>
                <p style="margin-top:8px"><strong>${JA.money(o.total, o.currency)}</strong> · ${o.currency === "NGN" ? "Naira" : "CFA"}</p>
              </div>
            </div>
            <ul class="order-items">${(o.items || []).map((i) => `<li>${i.qty}× ${JA.escape(i.name)}${i.color ? " · " + JA.escape(i.color) : ""}</li>`).join("")}</ul>
            ${o.customer.address ? `<p style="font-size:13px;color:var(--muted)">${JA.escape(o.customer.address)}</p>` : ""}
            ${o.customer.note ? `<p style="font-size:13px;color:var(--muted)">Note: ${JA.escape(o.customer.note)}</p>` : ""}
            ${(() => {
              const shot = (JA.getProof && JA.getProof(o.id, o.proof)) || (String(o.proof || "").startsWith("data:") ? o.proof : "");
              return shot
                ? `<p class="proof-label">Payment screenshot</p><a href="${shot}" target="_blank" rel="noopener"><img class="proof-preview" src="${shot}" alt="Payment screenshot for ${o.id}" /></a>`
                : `<p class="empty">No screenshot attached.</p>`;
            })()}
            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px">
              <button class="btn" data-confirm="${o.id}">Confirm purchase</button>
              <button class="btn btn-line" data-decline="${o.id}">Decline</button>
            </div>
          </article>`).join("") : `<p class="empty">No orders yet.</p>`}
      </section>
      <section class="panel ${tab === "categories" ? "is-on" : ""}" id="panel-categories">
        ${categoryManager()}
      </section>
      <section class="panel ${tab === "settings" ? "is-on" : ""}" id="panel-settings">
        ${settingsForm()}
      </section>
    </div>
    <nav class="admin-app-nav">
      <button type="button" data-tab="products" class="${tab === "products" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 10l8-7 8 7v9H4z"/></svg><span>Products</span>
      </button>
      <button type="button" data-tab="analytics" class="${tab === "analytics" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 19V9h3v10H4zm6 0V5h3v14h-3zm6 0v-7h3v7h-3z"/></svg><span>Analytics</span>
      </button>
      <button type="button" data-tab="orders" class="${tab === "orders" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><path d="M4 5h16v10H7l-3 3V5z"/></svg><span>Inbox</span>
      </button>
      <button type="button" data-tab="settings" class="${tab === "settings" ? "is-on" : ""}">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.4 1A7 7 0 0 0 14 6l-.3-2.6H10.3L10 6a7 7 0 0 0-2.5.9L5 6l-2 3.5L5.1 11A7 7 0 0 0 5 12c0 .3 0 .7.1 1L3 14.5 5 18l2.4-1A7 7 0 0 0 10 18.1l.3 2.6h3.4l.3-2.6a7 7 0 0 0 2.5-.9l2.4 1 2-3.5-2.1-1.5c.1-.3.1-.7.1-1z"/></svg><span>Manage</span>
      </button>
    </nav>`;

  $("#logout").onclick = () => { JA.logoutAdmin(); paintLogin(); };
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => paintDesk(b.dataset.tab);
  });

  const form = $("#prod-form");
  const existing = editingId && editingId !== "new" ? JA.product(editingId) : null;
  form?.addEventListener("submit", (e) => handleProductSubmit(e, existing));
  $("#cancel-edit")?.addEventListener("click", () => { editingId = null; paintDesk("products"); });
  $("#add-product")?.addEventListener("click", () => { editingId = "new"; paintDesk("products"); });
  bindMedia();
  bindOptions();
  bindCategories();
  bindCfaPreview();

  document.querySelectorAll("[data-edit]").forEach((b) => {
    b.onclick = () => {
      editingId = b.dataset.edit;
      paintDesk("products");
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
  });
  document.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = () => {
      if (confirm("Delete this product from the website? Customers will not see it.")) {
        JA.removeProduct(b.dataset.del);
        JA.toast("Deleted from the website.");
        paintDesk("products");
      }
    };
  });
  document.getElementById("del-picked")?.addEventListener("click", () => {
    const ids = [...document.querySelectorAll("[data-pick]:checked")].map((el) => el.getAttribute("data-pick"));
    if (!ids.length) { JA.toast("Tick the products to delete first."); return; }
    if (!confirm("Delete " + ids.length + " product(s) from the website?")) return;
    ids.forEach((id) => JA.removeProduct(id));
    JA.toast(ids.length + " product(s) deleted.");
    paintDesk("products");
  });
  document.querySelectorAll("[data-stock]").forEach((b) => {
    b.onclick = () => {
      const p = JA.product(b.dataset.stock);
      if (!p) return;
      const qty = Number(b.dataset.qty);
      JA.upsertProduct({ ...p, stock: qty });
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
  document.querySelectorAll("[data-confirm]").forEach((b) => {
    b.onclick = async () => {
      const id = b.dataset.confirm;
      const local = JA.getOrder(id);
      const remote = window.__jaRemoteOrders && window.__jaRemoteOrders[id];
      const o = local || remote;
      if (local) JA.updateOrder(id, { status: "confirmed" });
      if (o && JA.sendReceipt) {
        JA.toast("Sending receipt…");
        await JA.sendReceipt({ ...o, status: "confirmed" });
        JA.toast("Payment confirmed · receipt emailed to " + ((o.customer && o.customer.email) || "customer"));
      } else JA.toast("Purchase confirmed · " + id);
      paintDesk("orders");
    };
  });
  document.querySelectorAll("[data-decline]").forEach((b) => {
    b.onclick = () => {
      JA.updateOrder(b.dataset.decline, { status: "declined" });
      JA.toast("Order declined · " + b.dataset.decline);
      paintDesk("orders");
    };
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
      adminPin: fd.get("adminPin") || "jaura2026",
      bankCfa: fd.get("bankCfa"),
      bankNgn: fd.get("bankNgn"),
    });
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
        <img src="${JA.asset(c.image)}" alt="" />
        <label class="wix-cat-up">Change photo<input type="file" accept="image/*" data-cat-img="${i}" hidden /></label>
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
    <div class="field"><label>Atelier pin</label><input name="adminPin" value="${s.adminPin}" /></div>
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
  if (JA.isAdmin()) paintDesk();
  else paintLogin();
}
document.addEventListener("DOMContentLoaded", bootAdmin);
