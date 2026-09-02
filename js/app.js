function param(name) {
  return new URLSearchParams(location.search).get(name);
}

function compressImage(file, max = 1100, quality = 0.72) {
  return new Promise((resolve, reject) => {
    if (!file) {
      reject(new Error("Please upload an image screenshot."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function t(key, vars) {
  try {
    if (window.I18N && typeof window.I18N.t === "function") {
      const s = window.I18N.t(key, vars);
      if (s && s !== key) return s;
    }
  } catch (e) {}
  const tail = String(key || "").split(".").pop();
  let s = tail.replace(/([A-Z])/g, " $1").replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase()).trim();
  if (vars) {
    Object.keys(vars).forEach((k) => {
      s = s.split("{" + k + "}").join(String(vars[k]));
    });
  }
  return s;
}

function catCover(c) {
  const img = (c && c.image) || "";
  // A document can never render in an <img>, so fall back to the cover art.
  if (img && JA.mediaKind && JA.mediaKind(img) !== "image") return "images/brand/logo.jpg";
  return img ? (JA.asset ? JA.asset(img) : img) : "images/brand/logo.jpg";
}

function renderCategories() {
  const box = document.querySelector("[data-cat-list]");
  if (!box) return;
  box.innerHTML = JA.categories().map((c) => {
    const n = JA.products().filter((p) => p.category === c.id).length;
    return `<a class="cat-tile" href="shop.html?cat=${c.id}">
      <img src="${catCover(c)}" alt="" />
      <small>${t("cats.shop")}</small>
      <h3>${JA.escape(JA.categoryName(c.id))}</h3>
      <span>${t(n === 1 ? "cats.piece" : "cats.pieces", { n })}</span>
    </a>`;
  }).join("");
}

function newestTwelve() {
  const all = JA.products();
  const featured = all.filter((p) => p.featured);
  const rest = all.filter((p) => !p.featured);
  return featured.concat(rest).slice(0, 12);
}

function startCatSlide() {
  const el = document.querySelector("[data-home-cats]");
  if (!el) return;
  clearInterval(window.__jaCat);
  window.__jaCat = setInterval(() => {
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 8) return;
    const next = el.scrollLeft + Math.max(120, el.clientWidth * 0.45);
    el.scrollTo({ left: next >= max - 6 ? 0 : next, behavior: "smooth" });
  }, 2800);
}

/* Homepage hero video (Fix: owner-editable from Admin → Settings).
   If the owner uploaded a video it autoplays silently on a loop behind the
   hero text; with no video the static hero stays exactly as it is. The last
   known URL is cached so the video starts instantly on repeat visits. */
function mountHeroVideo() {
  const vid = document.getElementById("hero-video");
  const scrim = document.getElementById("hero-scrim");
  const hero = document.getElementById("home-hero");
  if (!vid || !hero) return;
  const KEY = "jaura.site";
  const copy = document.getElementById("hero-video-copy");
  const docSlot = document.getElementById("hero-doc-slot");
  const apply = (site) => {
    site = site || {};
    const url = (site.heroVideo || "").toString();
    const doc = (site.heroDoc || "").toString();
    const poster = (site.heroPoster || "").toString();
    if (docSlot) {
      docSlot.hidden = !doc;
      docSlot.querySelector?.("a")?.setAttribute("href", JA.asset ? JA.asset(doc) : doc);
      docSlot.querySelector?.("a")?.setAttribute("download", doc.split("/").pop() || "hero-document");
    }
    if (poster) vid.setAttribute("poster", JA.asset ? JA.asset(poster) : poster);
    if (url) {
      if (vid.getAttribute("src") !== url) vid.src = url;
      vid.muted = true;
      vid.preload = "auto";
      vid.setAttribute("preload", "auto");
      vid.setAttribute("playsinline", "");
      vid.setAttribute("webkit-playsinline", "");
      vid.setAttribute("disablepictureinpicture", "");
      try { vid.disablePictureInPicture = true; } catch (e) {}
      vid.hidden = false;
      if (scrim) scrim.hidden = false;
      if (copy) copy.hidden = false;
      hero.classList.add("has-video");
      const play = () => vid.play().catch(() => {});
      play();
      vid.addEventListener("canplay", play, { once: true });
      document.addEventListener("touchstart", play, { once: true });
    } else {
      vid.hidden = true;
      vid.removeAttribute("src");
      if (scrim) scrim.hidden = true;
      if (copy) copy.hidden = true;
      hero.classList.remove("has-video");
    }
  };
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) {}
  if (cached) apply(cached);
  fetch("api/site", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const site = (d && d.site) || {};
      try { localStorage.setItem(KEY, JSON.stringify(site)); } catch (e) {}
      apply(site);
    })
    .catch(() => {});   // offline / static hosting: keep whatever is showing
}

function renderHome() {
  const newIn = document.querySelector("[data-new]");
  if (newIn) {
    let list = [];
    try { list = newestTwelve(); } catch (e) { list = []; }
    if (!list.length) {
      const raw = (typeof JA.products === "function" ? JA.products() : []) || window.JA_SEED || [];
      list = raw.slice(0, 12);
    }
    try {
      newIn.innerHTML = list.map(JA.cardHTML).join("");
    } catch (e) {
      newIn.innerHTML = list.map((p) => {
        const img = (p.images && p.images[0]) || p.image || "";
        const id = p.id || "";
        const name = p.name || "";
        return `<article class="card"><a class="card-media" href="product.html?id=${encodeURIComponent(id)}"><img src="${img}" alt="" onerror="fallbackImg(event)"></a><div class="card-body"><h3><a href="product.html?id=${encodeURIComponent(id)}">${name}</a></h3></div></article>`;
      }).join("");
    }
  }
  const cats = document.querySelector("[data-home-cats]");
  if (cats) {
    cats.innerHTML = JA.categories().map((c) => {
      return `<a class="home-cat" href="shop.html?cat=${c.id}">
        <img src="${catCover(c)}" alt="" />
        <span>${JA.escape(JA.categoryName(c.id))}</span>
      </a>`;
    }).join("");
    startCatSlide();
  }
  const best = document.querySelector("[data-bestsellers]");
  if (best) {
    const picks = JA.products().filter((p) => p.badge === "bestseller");
    const extra = JA.products().filter((p) => p.featured || p.badge === "sale");
    const seen = new Set();
    const row = [];
    picks.concat(extra).forEach((p) => {
      if (!seen.has(p.id) && row.length < 12) { seen.add(p.id); row.push(p); }
    });
    best.innerHTML = row.map(JA.cardHTML).join("");
    clearInterval(window.__jaBest);
    window.__jaBest = setInterval(() => {
      const max = best.scrollWidth - best.clientWidth;
      if (max <= 8) return;
      const next = best.scrollLeft + Math.max(140, best.clientWidth * 0.5);
      best.scrollTo({ left: next >= max - 6 ? 0 : next, behavior: "smooth" });
    }, 3000);
  }
}

const shopFilter = { min: 0, max: 0, color: "", size: "", inited: false, cat: "" };

function colorKey(v) {
  const s = String(v || "").trim();
  if (!s || /^#/.test(s)) return "";
  return s;
}
function productColors(p) {
  const out = [];
  (p.colors || []).forEach((c) => { const k = colorKey(c); if (k) out.push(k); });
  (p.options || []).forEach((o) => {
    const t = String(o.title || "").toLowerCase();
    if (/colou?r|scents/.test(t)) (o.values || []).forEach((v) => { const k = colorKey(v); if (k) out.push(k); });
  });
  return [...new Set(out)];
}
function productSizes(p) {
  const out = [];
  (p.options || []).forEach((o) => {
    const t = String(o.title || "").toLowerCase();
    if (/size|length/.test(t) && !/colou?r/.test(t)) (o.values || []).forEach((v) => out.push(String(v)));
  });
  return [...new Set(out)];
}
function applyShopFilters(list) {
  return list.filter((p) => {
    const price = JA.priceOf(p);
    if (shopFilter.min != null && price < shopFilter.min) return false;
    if (shopFilter.max != null && shopFilter.max > 0 && price > shopFilter.max) return false;
    if (shopFilter.color) {
      const want = shopFilter.color.toLowerCase();
      if (!productColors(p).some((c) => c.toLowerCase() === want)) return false;
    }
    if (shopFilter.size && !productSizes(p).includes(shopFilter.size)) return false;
    return true;
  });
}
function paintFilterDrawer(baseList) {
  const body = document.querySelector("[data-filt-body]");
  if (!body) return;
  const prices = baseList.map((p) => JA.priceOf(p)).filter((n) => n > 0);
  const lo = prices.length ? Math.min(...prices) : 0;
  const hi = prices.length ? Math.max(...prices) : 0;
  if (!shopFilter.inited) {
    shopFilter.min = lo;
    shopFilter.max = hi;
    shopFilter.inited = true;
  }
  if (hi && shopFilter.max > hi) shopFilter.max = hi;
  if (shopFilter.min < lo) shopFilter.min = lo;
  const colorCount = {};
  const sizeCount = {};
  baseList.forEach((p) => {
    productColors(p).forEach((c) => { colorCount[c] = (colorCount[c] || 0) + 1; });
    productSizes(p).forEach((s) => { sizeCount[s] = (sizeCount[s] || 0) + 1; });
  });
  const colors = Object.keys(colorCount).sort((a, b) => a.localeCompare(b));
  const sizes = Object.keys(sizeCount).sort((a, b) => {
    const na = Number(a), nb = Number(b);
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    return a.localeCompare(b);
  });
  const minV = shopFilter.min || lo;
  const maxV = shopFilter.max || hi;
  body.innerHTML = `
    <section class="filt-block">
      <h3>${t("filt.price")}</h3>
      <div class="filt-range">
        <input type="range" data-fmin min="${lo}" max="${hi}" value="${minV}" />
        <input type="range" data-fmax min="${lo}" max="${hi}" value="${maxV}" />
      </div>
      <div class="filt-price-row">
        <p data-fprice-lab>${t("filt.priceLab")} ${JA.money(minV)} — ${JA.money(maxV)}</p>
        <button type="button" class="filt-apply" data-apply-filter>${t("filt.apply")}</button>
      </div>
    </section>
    <section class="filt-block">
      <h3>${t("filt.color")}</h3>
      ${colors.length ? colors.map((c) => `
        <button type="button" class="filt-row ${shopFilter.color === c ? "is-on" : ""}" data-fcolor="${JA.escape(c)}">
          <span>${JA.escape(c)}</span>
          <i>${colorCount[c]}</i>
        </button>`).join("") : `<p class="filt-empty">${t("filt.noColor")}</p>`}
    </section>
    <section class="filt-block">
      <h3>${t("filt.size")}</h3>
      ${sizes.length ? sizes.map((s) => `
        <button type="button" class="filt-row ${shopFilter.size === s ? "is-on" : ""}" data-fsize="${JA.escape(s)}">
          <span>${JA.escape(s)}</span>
          <i>${sizeCount[s]}</i>
        </button>`).join("") : `<p class="filt-empty">${t("filt.noSize")}</p>`}
    </section>
    <button type="button" class="filt-clear" data-clear-filter>${t("filt.clear")}</button>`;
  const lab = body.querySelector("[data-fprice-lab]");
  const minEl = body.querySelector("[data-fmin]");
  const maxEl = body.querySelector("[data-fmax]");
  const syncLab = () => {
    let a = Number(minEl.value), b = Number(maxEl.value);
    if (a > b) { const t = a; a = b; b = t; }
    shopFilter.min = a;
    shopFilter.max = b;
    if (lab) lab.textContent = t("filt.priceLab") + " " + JA.money(a) + " — " + JA.money(b);
  };
  minEl?.addEventListener("input", syncLab);
  maxEl?.addEventListener("input", syncLab);
}
function openShopFilter() {
  const draw = document.querySelector("[data-filt-draw]");
  const mask = document.querySelector("[data-filt-mask]");
  if (!draw) return;
  draw.removeAttribute("hidden");
  mask?.removeAttribute("hidden");
  draw.classList.add("open");
  mask?.classList.add("open");
  document.body.classList.add("filt-open");
}
function closeShopFilter() {
  document.querySelector("[data-filt-draw]")?.classList.remove("open");
  document.querySelector("[data-filt-mask]")?.classList.remove("open");
  document.body.classList.remove("filt-open");
  setTimeout(() => {
    document.querySelector("[data-filt-draw]")?.setAttribute("hidden", "");
    document.querySelector("[data-filt-mask]")?.setAttribute("hidden", "");
  }, 280);
}
function bindShopFilter() {
  if (document.body.dataset.filtBound) return;
  document.body.dataset.filtBound = "1";
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-open-filter]")) {
      e.preventDefault();
      openShopFilter();
      return;
    }
    if (e.target.closest("[data-close-filter]") || e.target.closest("[data-filt-mask]")) {
      closeShopFilter();
      return;
    }
    if (e.target.closest("[data-apply-filter]")) {
      renderShop();
      closeShopFilter();
      return;
    }
    if (e.target.closest("[data-clear-filter]")) {
      shopFilter.color = "";
      shopFilter.size = "";
      shopFilter.inited = false;
      renderShop();
      return;
    }
    const col = e.target.closest("[data-fcolor]");
    if (col) {
      const v = col.getAttribute("data-fcolor");
      shopFilter.color = shopFilter.color === v ? "" : v;
      renderShop();
      openShopFilter();
      return;
    }
    const sz = e.target.closest("[data-fsize]");
    if (sz) {
      const v = sz.getAttribute("data-fsize");
      shopFilter.size = shopFilter.size === v ? "" : v;
      renderShop();
      openShopFilter();
    }
  });
}

function renderShop() {
  const cat = param("cat") || "all";
  if (shopFilter.cat !== cat) {
    shopFilter.cat = cat;
    shopFilter.inited = false;
    shopFilter.color = "";
    shopFilter.size = "";
  }
  const live = document.querySelector("[data-shop-q]");
  const q = (live?.value || param("q") || "").trim();
  const sort = document.querySelector("[data-sort]")?.value || "newest";
  let list = q ? JA.searchProducts(q, cat) : JA.products().filter((p) => cat === "all" || p.category === cat);

  list = [...list];
  try { paintFilterDrawer(list); } catch (e) {}
  try { list = applyShopFilters(list); } catch (e) {}
  if (sort === "price-asc") list.sort((a, b) => JA.priceOf(a) - JA.priceOf(b));
  if (sort === "price-desc") list.sort((a, b) => JA.priceOf(b) - JA.priceOf(a));
  if (sort === "name") list.sort((a, b) => a.name.localeCompare(b.name));

  // Clean pagination: 24 products per page with numbered controls plus
  // Previous/Next. Every product stays reachable — the pages cover the whole
  // list the server returns (see store.js dedupeProducts / catalog.merged).
  const per = 24;
  const pages = Math.max(1, Math.ceil(list.length / per));
  const page = Math.min(Math.max(1, parseInt(param("page") || "1", 10) || 1), pages);
  const slice = list.slice((page - 1) * per, page * per);

  const title = document.querySelector("[data-shop-title]");
  if (title) title.textContent = q ? t("shop.resultsFor", { q }) : (cat === "all" ? t("shop.all") : JA.categoryName(cat));
  const count = document.querySelector("[data-shop-count]");
  if (count) count.textContent = list.length + " products";
  if (live && param("q") && !live.dataset.filled) {
    live.value = param("q");
    live.dataset.filled = "1";
  }

  const filters = document.querySelector("[data-filters]");
  if (filters) {
    filters.innerHTML = `<a class="cat-pill ${cat === "all" ? "is-on" : ""}" href="shop.html">${t("shop.all")}</a>` +
      JA.categories().map((c) =>
        `<a class="cat-pill ${cat === c.id ? "is-on" : ""}" href="shop.html?cat=${c.id}">${JA.categoryName(c.id)}</a>`
      ).join("");
  }

  const grid = document.querySelector("[data-shop-grid]");
  if (grid) grid.innerHTML = slice.length ? slice.map(JA.cardHTML).join("") : `<p class="empty">${t("shop.empty")}</p>`;

  const pager = document.querySelector("[data-pager]");
  if (pager && pages > 1) {
    // data-href, not onclick: inline handlers are blocked by our CSP
    const url = (n) => `shop.html?cat=${cat}&q=${encodeURIComponent(q)}&page=${n}`;
    // page-number window: 1 … around current … last, with ellipses
    const nums = [];
    for (let n = 1; n <= pages; n++) {
      if (n === 1 || n === pages || Math.abs(n - page) <= 1) nums.push(n);
      else if (nums[nums.length - 1] !== "…") nums.push("…");
    }
    pager.innerHTML =
      `<button type="button" class="pager-nav" ${page <= 1 ? "disabled" : ""} data-goto="${JA.escape(url(page - 1))}" aria-label="Previous page">‹ <span data-i18n="pager.prev">Prev</span></button>`
      + nums.map((n) => n === "…"
        ? `<span class="pager-gap">…</span>`
        : `<button type="button" ${n === page ? 'class="is-on" disabled aria-current="page"' : ""} data-goto="${JA.escape(url(n))}">${n}</button>`).join("")
      + `<button type="button" class="pager-nav" ${page >= pages ? "disabled" : ""} data-goto="${JA.escape(url(page + 1))}" aria-label="Next page"><span data-i18n="pager.next">Next</span> ›</button>`;
    pager.querySelectorAll("[data-goto]:not([disabled])").forEach((b) => {
      b.addEventListener("click", () => { location.href = b.dataset.goto; });
    });
    if (window.I18N && typeof window.I18N.apply === "function") try { window.I18N.apply(pager); } catch (e) {}
  } else if (pager) pager.innerHTML = "";
}

async function renderMostViewed() {
  const host = document.querySelector("[data-most-viewed]");
  if (!host || host.dataset.done === "1") return;
  let items = [];
  try {
    const res = await fetch("api/most-viewed?limit=12", { credentials: "same-origin", cache: "no-store" });
    const d = await res.json();
    if (d && d.ok) {
      items = (d.items || [])
        .map((x) => ({ views: x.views || 0, carts: x.carts || 0, p: JA.product(x.productId) }))
        .filter((x) => x.p);
    }
  } catch (e) { items = []; }
  if (items.length < 4) return;                 // an empty rail is worse than none
  host.dataset.done = "1";
  const cur = JA.currency();
  host.innerHTML = `
    <div class="mv-head">
      <h2 class="serif-title">Most viewed right now</h2>
      <a class="mv-more" href="shop.html">Shop all ›</a>
    </div>
    <div class="mv-rail">${items.map((x) => `
      <a class="mv-card" href="product.html?id=${encodeURIComponent(x.p.id)}">
        <img src="${JA.asset(x.p.image)}" alt="" loading="lazy" onerror="fallbackImg(event)" />
        <strong>${JA.escape(JA.displayName(x.p))}</strong>
        <span>${JA.escape(JA.money(JA.priceOf(x.p, cur), cur))}</span>
        <em>${x.views} view${x.views === 1 ? "" : "s"}${x.carts ? " · " + x.carts + " in carts" : ""}</em>
      </a>`).join("")}</div>`;
}

function namedSwatch(val) {
  const v = String(val || "").trim();
  if (/^#[0-9A-Fa-f]{3,8}$/.test(v)) return v;
  const map = {
    black: "#1a1410", white: "#f7f3ee", red: "#b03a2e", gold: "#c4a574",
    silver: "#c5c8ce", brown: "#6b4a32", burgundy: "#6b1d2a", pink: "#e8b4ad",
    purple: "#6b3d7a", blue: "#3d5a8b", green: "#3d5a3d", grey: "#8a847c",
    gray: "#8a847c", nude: "#e8d5c4", peach: "#f3d6d0", ash: "#9aa0a6",
    "mint green": "#a4c6a4", mint: "#a4c6a4",
    "black&brown": "#3a2a22", "black & brown": "#3a2a22",
  };
  const key = v.toLowerCase().replace(/\s+/g, " ").trim();
  return map[key] || "";
}

function starsOf(n, pick) {
  try {
    if (JA.starsHTML) return JA.starsHTML(n, pick);
  } catch (e) {}
  const s = Math.max(0, Math.min(5, Math.round(Number(n) || 0)));
  return `<span class="star-row">${"★".repeat(s)}${"☆".repeat(5 - s)}</span>`;
}

function renderProduct() {
  const root = document.querySelector("[data-pdp]");
  if (!root) return;
  let p = null;
  try {
    p = JA.product(param("id") || param("sku") || param("slug"));
  } catch (e) { p = null; }
  if (!p) {
    root.innerHTML = `<p class="empty">${t("pdp.missing")} <a href="shop.html">${t("pdp.return")}</a>.</p>`;
    return;
  }
  try {
    paintProduct(root, p);
  } catch (e) {
    console.error(e);
    root.innerHTML = `<div class="pdp-gallery"><div class="pdp-img"><img src="${JA.asset(p.image)}" alt="" /></div></div>
      <div>
        <h1>${JA.escape(p.name || "")}</h1>
        ${JA.priceHTML(p)}
        <div class="pdp-actions"><button class="btn" data-buy>${t("pdp.add")}</button></div>
      </div>`;
    root.querySelector("[data-buy]")?.addEventListener("click", () => JA.addToCart(p.id, 1));
  }
}

function paintProduct(root, p) {
  try { JA.track("view", { id: p.id, name: p.name, page: "product" }); } catch (e) {}
  try {
    const name = JA.displayName(p);
    const desc = String(p.description || "").trim() || (name + " at Jaura Store. Pay in ₦ or F CFA.");
    const url = (JA.SITE || "https://jaurastore.com.ng") + "/product.html?id=" + encodeURIComponent(p.id);
    const img = (p.images && p.images[0]) || p.image;
    const cur = Number(p.priceNgn) > 0 ? "NGN" : "XOF";
    const price = Number(p.priceNgn) > 0 ? p.priceNgn : p.priceCfa;
    JA.setSeo({
      title: name + " · Jaura Store",
      description: desc.slice(0, 160),
      url,
      image: img,
      type: "product",
      jsonLd: {
        "@context": "https://schema.org",
        "@type": "Product",
        name,
        sku: p.sku || p.id,
        image: JA.absUrl ? JA.absUrl(img) : img,
        description: desc,
        brand: { "@type": "Brand", name: "Jaura Store" },
        offers: {
          "@type": "Offer",
          url,
          priceCurrency: cur,
          price: String(price || 0),
          availability: Number(p.stock) > 0 ? "https://schema.org/InStock" : "https://schema.org/OutOfStock",
          seller: { "@type": "Organization", name: "Jaura Store" }
        }
      }
    });
  } catch (e) {}
  const opts = (p.options && p.options.length) ? p.options : (
    (p.colors || []).length ? [{ title: t("pdp.colour"), type: "DROP_DOWN", values: p.colors }] : []
  );
  const optHTML = opts.map((opt, oi) => {
    const isColor = /colou?r|metal|type/i.test(opt.title || "") || opt.type === "COLOR";
    const btns = (opt.values || []).map((v) => {
      const hex = namedSwatch(v);
      const showDot = isColor && hex;
      return `<button type="button" class="opt-chip${showDot ? " has-dot" : ""}" data-opt="${oi}" data-val="${JA.escape(v)}">
        ${showDot ? `<i class="opt-dot" style="background:${hex}"></i>` : ""}
        <span>${JA.escape(/^#/.test(v) ? "" : v) || ""}</span>
      </button>`;
    }).join("");
    return `<div class="pdp-opt" data-opt-wrap="${oi}">
      <div class="kicker">${JA.escape(opt.title)} *</div>
      <div class="swatches">${btns}</div>
    </div>`;
  }).join("");
  const desc = String(p.description || "").trim();
  const showDesc = desc && desc.toLowerCase() !== String(p.name || "").toLowerCase();
  const extra = (p.additionalInfo || []).map((sec) =>
    `<div class="pdp-info"><strong>${JA.escape(sec.title || t("pdp.details"))}</strong><p>${JA.escape(sec.description || "")}</p></div>`
  ).join("");
  const gallery = (JA.galleryOf ? JA.galleryOf(p) : ((p.images && p.images.length ? p.images : [p.image]) || [])).filter(Boolean).slice(0, 20);
  const stockN = Number(p.stock) || 0;
  const rev = (JA.reviews && JA.reviews(p.id)) || [];
  const revStats = (JA.reviewStats && JA.reviewStats(p.id)) || { n: 0, avg: 0 };
  const mainHTML = (idx) => JA.mediaHTML(gallery[idx], {
    alt: p.name, ph: p.placeholderImage, attrs: { "data-main-img": "" },
  });
  root.innerHTML = `
    <div class="pdp-gallery">
      <div class="pdp-img" data-media-slot>
        ${gallery.length > 1 ? `<button type="button" class="pdp-nav pdp-prev" data-gal="-1" aria-label="Previous">‹</button>` : ""}
        ${mainHTML(0)}
        ${gallery.length > 1 ? `<button type="button" class="pdp-nav pdp-next" data-gal="1" aria-label="Next">›</button>` : ""}
      </div>
      ${gallery.length > 1 ? `<div class="pdp-thumbs">${gallery.map((src, i) => `<button type="button" class="pdp-thumb${i === 0 ? " is-on" : ""}" data-src="${JA.escape(JA.asset(src))}" data-thumb="${i}">${JA.mediaHTML(src, { alt: p.name, ph: p.placeholderImage })}</button>`).join("")}</div>` : ""}
    </div>
    <div>
      <div class="kicker">${JA.categoryName(p.category)}</div>
      <h1>${JA.escape(JA.displayName(p))}</h1>
      ${JA.priceHTML(p)}
      ${stockN > 0 ? "" : `<p class="pdp-stock">${t("pdp.oos")}</p>`}
      ${(() => {
        const unit = JA.priceOf(p);
        const ten = Math.round(unit * 0.9) * 10;
        return `<div class="pdp-bulk">
          <strong>${t("pdp.bulk")}</strong>
          <p>${t("pdp.bulkPrice", { price: JA.money(ten) })}</p>
        </div>`;
      })()}
      <button type="button" class="wish-btn pdp-wish ${JA.isWished(p.id) ? "is-on" : ""}" data-wish="${p.id}"><svg class="wish-heart" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg><span>${t("nav.wishlist")}</span></button>
      ${showDesc ? `<p class="pdp-desc">${JA.escape(desc)}</p>` : ""}
      ${optHTML}
      ${extra}
      <div class="kicker">${t("pdp.qty")}</div>
      <div class="qty">
        <button type="button" data-q="-">−</button>
        <input type="number" min="1" value="1" data-qty />
        <button type="button" data-q="+">+</button>
      </div>
      <div class="pdp-actions">
        <button class="btn" data-buy ${p.stock <= 0 ? "disabled" : ""}>${p.stock <= 0 ? t("pdp.oos") : t("pdp.add")}</button>
        <a class="btn btn-line" href="checkout.html">${t("pdp.payIn", { cur: JA.currency() === "NGN" ? "₦" : "F CFA" })}</a>
      </div>
      <p style="font-size:13px;color:var(--taupe)">${t("pdp.hint", { sku: p.sku || p.id })}</p>
      <section class="pdp-reviews" data-reviews="${JA.escape(p.id)}">
        <h3>${t("rev.title")}</h3>
        <p class="rev-avg">${revStats.n ? starsOf(revStats.avg) + " " + t(revStats.n === 1 ? "rev.count" : "rev.countMany", { n: revStats.n }) : t("rev.empty")}</p>
        <div class="rev-list">${rev.length ? rev.map((r) => `
          <article class="rev-note">
            ${starsOf(r.stars)}
            <strong>${JA.escape(r.name || "Customer")}</strong>
            <p>${JA.escape(r.note || "")}</p>
          </article>`).join("") : ""}</div>
        <form class="rev-form" data-rev-form>
          <h4>${t("rev.write")}</h4>
          <p class="rev-gate-note">${t("rev.gate")}</p>
          <label>${t("rev.name")}<input name="name" maxlength="60" required autocomplete="name" /></label>
          <label>${t("rev.email")}<input name="email" type="email" maxlength="120" required autocomplete="email" /></label>
          <p class="rev-pick-lab">${t("rev.stars")}</p>
          <div class="rev-pick" data-star-pick>${starsOf(5, true)}</div>
          <input type="hidden" name="stars" value="5" />
          <label>${t("rev.note")}<textarea name="note" rows="3" maxlength="600" required></textarea></label>
          <button class="btn" type="submit">${t("rev.send")}</button>
        </form>
      </section>
    </div>`;

  const showSlide = (i) => {
    const slot = root.querySelector("[data-media-slot]");
    const thumbs = [...root.querySelectorAll("[data-thumb]")];
    if (!gallery.length || !slot) return;
    const n = ((i % gallery.length) + gallery.length) % gallery.length;
    // swap the whole media element so a video/document thumb replaces the
    // image instead of trying to load a video into an <img>.
    slot.innerHTML = mainHTML(n);
    thumbs.forEach((th, ti) => th.classList.toggle("is-on", ti === n));
    root.dataset.slide = String(n);
  };
  root.dataset.slide = "0";
  root.querySelectorAll("[data-src]").forEach((b) => {
    b.addEventListener("click", () => showSlide(Number(b.dataset.thumb) || 0));
  });
  root.querySelectorAll("[data-gal]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.preventDefault();
      showSlide((Number(root.dataset.slide) || 0) + Number(b.dataset.gal));
    });
  });
  const chosen = {};
  root.querySelectorAll("[data-opt]").forEach((b) => {
    b.addEventListener("click", () => {
      const oi = b.dataset.opt;
      root.querySelectorAll(`[data-opt="${oi}"]`).forEach((x) => x.classList.remove("is-on"));
      b.classList.add("is-on");
      chosen[oi] = b.dataset.val;
    });
  });
  const qty = root.querySelector("[data-qty]");
  root.querySelectorAll("[data-q]").forEach((b) => {
    b.addEventListener("click", () => {
      const n = parseInt(qty.value, 10) || 1;
      qty.value = Math.max(1, n + (b.dataset.q === "+" ? 1 : -1));
    });
  });
  root.querySelector("[data-buy]")?.addEventListener("click", () => {
    for (let i = 0; i < opts.length; i += 1) {
      if (!chosen[i]) {
        JA.toast(t("pdp.needOpt", { name: opts[i].title }));
        return;
      }
    }
    const variant = opts.map((opt, i) => opt.title.replace(/\s+/g, " ").trim() + ": " + chosen[i]).join(" · ");
    JA.addToCart(p.id, parseInt(qty.value, 10) || 1, variant);
  });
  const frame = root.querySelector(".pdp-img");
  if (frame && gallery.length > 1) {
    let x0 = 0;
    frame.addEventListener("touchstart", (e) => { x0 = e.changedTouches[0].clientX; }, { passive: true });
    frame.addEventListener("touchend", (e) => {
      const dx = e.changedTouches[0].clientX - x0;
      if (Math.abs(dx) < 40) return;
      showSlide((Number(root.dataset.slide) || 0) + (dx < 0 ? 1 : -1));
    }, { passive: true });
  }
  const pickBox = root.querySelector("[data-star-pick]");
  const starInp = root.querySelector("[name=stars]");
  const paintPick = (n) => {
    if (!pickBox) return;
    pickBox.innerHTML = starsOf(n, true);
    pickBox.querySelectorAll("[data-pick-star], .star").forEach((el, i) => {
      const v = Number(el.getAttribute("data-pick-star") || i + 1);
      el.classList.toggle("is-on", v <= n);
      el.classList.remove("is-half");
    });
  };
  paintPick(5);
  pickBox?.addEventListener("click", (e) => {
    const b = e.target.closest(".star");
    if (!b) return;
    e.preventDefault();
    const n = Number(b.getAttribute("data-pick-star") || [...pickBox.querySelectorAll(".star")].indexOf(b) + 1);
    if (starInp) starInp.value = String(n);
    paintPick(n);
  });
  root.querySelector("[data-rev-form]")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const note = String(fd.get("note") || "").trim();
    const name = String(fd.get("name") || "").trim();
    const email = String(fd.get("email") || "").trim();
    if (!note || !name || !email) { JA.toast(t("rev.need")); return; }
    const body = { productId: p.id, name, email, note, stars: Number(fd.get("stars") || 5) };
    const btn = e.target.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    const req = window.JA_NET
      ? JA_NET.api("api/reviews", { method: "POST", json: body })
      : Promise.reject(new Error("offline"));
    req.then((d) => {
      if (d && d.ok) {
        if (JA.setReviews) JA.setReviews(p.id, d.reviews || []);
        JA.toast(t("rev.thanks"));
        renderProduct();
      } else {
        JA.toast((d && d.error) || t("rev.notBought"));
      }
    }).catch((err) => {
      if (err && err.status === 403) JA.toast(t("rev.notBought"));
      else JA.toast((err && err.message) || t("rev.notBought"));
    }).finally(() => { if (btn) btn.disabled = false; });
  });
  // Pull the shared, server-stored reviews so every visitor sees the same
  // verified list (the local copy is only a cache for offline viewing).
  fetch("api/reviews/" + encodeURIComponent(p.id))
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d || !d.ok || !Array.isArray(d.reviews)) return;
      const cur = (JA.reviews && JA.reviews(p.id)) || [];
      if (JA.setReviews && JSON.stringify(cur) !== JSON.stringify(d.reviews)) {
        JA.setReviews(p.id, d.reviews);
        const box = document.querySelector(`[data-reviews="${(window.CSS && CSS.escape) ? CSS.escape(p.id) : p.id}"]`);
        if (box) {
          const n = d.reviews.length;
          const avgEl = box.querySelector(".rev-avg");
          if (avgEl) avgEl.innerHTML = n ? starsOf(d.average) + " " + t(n === 1 ? "rev.count" : "rev.countMany", { n }) : t("rev.empty");
          const list = box.querySelector(".rev-list");
          if (list) list.innerHTML = d.reviews.map((r) => `
            <article class="rev-note">
              ${starsOf(r.stars)}
              <strong>${JA.escape(r.name || "Customer")}</strong>
              <p>${JA.escape(r.note || "")}</p>
            </article>`).join("");
        }
      }
    })
    .catch(() => {});

  const related = JA.products().filter((x) => x.category === p.category && x.id !== p.id).slice(0, 4);
  const rel = document.querySelector("[data-related]");
  if (rel) rel.innerHTML = related.map(JA.cardHTML).join("");
}

function renderCart() {
  const rows = document.querySelector("[data-cart-rows]");
  const items = JA.cartDetailed();
  if (!rows) return;
  if (!items.length) {
    rows.innerHTML = `<div class="empty splend-empty">
      <p>${t("cart.empty")}</p>
      <p>${t("cart.emptyLead")}</p>
      <a class="btn" href="shop.html">${t("ck.return")}</a>
    </div>`;
  } else {
    rows.innerHTML = `<table class="cart-table">
      <thead><tr><th></th><th>${t("ck.product")}</th><th>Price</th><th>${t("pdp.qty")}</th><th>${t("ck.subtotal")}</th></tr></thead>
      <tbody>${items.map((i) => `
      <tr class="cart-row-tr">
        <td><a href="product.html?id=${i.id}"><img src="${JA.asset(i.product.image)}" alt="" onerror="fallbackImg(event)" /></a></td>
        <td>
          <a href="product.html?id=${i.id}"><strong>${JA.escape(JA.displayName(i.product))}</strong></a>
          ${i.color ? `<div class="card-cat">${JA.escape(i.color)}</div>` : ""}
        </td>
        <td>${i.bulk ? `<s>${JA.money(i.unit, i.cur)}</s> ${JA.money(i.payUnit, i.cur)}` : JA.priceHTML(i.product)}${i.bulk ? `<div class="bulk-tag">${t("cart.bulk")}</div>` : ""}</td>
        <td>
          <div class="qty">
            <button type="button" data-set="${i.id}" data-color="${JA.escape(i.color)}" data-n="${i.qty - 1}">−</button>
            <input value="${i.qty}" readonly />
            <button type="button" data-set="${i.id}" data-color="${JA.escape(i.color)}" data-n="${i.qty + 1}">+</button>
          </div>
        </td>
        <td><strong>${JA.money(i.line, i.cur)}</strong>
          <button class="icon-btn" data-set="${i.id}" data-color="${JA.escape(i.color)}" data-n="0" aria-label="Remove">✕</button>
        </td>
      </tr>`).join("")}</tbody>
    </table>`;
  }
  rows.querySelectorAll("[data-set]").forEach((b) => {
    b.addEventListener("click", () => JA.setQty(b.dataset.set, b.dataset.color, parseInt(b.dataset.n, 10)));
  });
  const sum = document.querySelector("[data-summary]");
  if (sum) {
    const cur = JA.currency();
    sum.innerHTML = `
      <h3>${t("cart.summary")}</h3>
      <div class="line"><span>${t("cart.items", { n: JA.cartCount() })}</span><span>${JA.money(JA.cartTotal(cur), cur)}</span></div>
      <div class="line total"><span>${t("cart.toPay", { cur: cur === "NGN" ? "₦" : "F CFA" })}</span><span>${JA.money(JA.cartTotal(cur), cur)}</span></div>
      <p style="font-size:13px;color:var(--taupe);margin:12px 0 18px">${t("cart.switchHint")}</p>
      <a class="btn" href="checkout.html" style="width:100%">${t("cart.checkout", { cur: cur === "NGN" ? "₦" : "F CFA" })}</a>`;
  }
}

function fareWaUrl(order) {
  const num = "22968953110";
  const c = (order && order.customer) || {};
  const loc = [c.city, c.zone, c.address].filter(Boolean).join(" / ");
  const text = "Hello Jaura Store,\n\nI have paid for my order.\nOrder ID: " + (order.id || "") + "\n\nI would like to know my specific transport fare.\n\nMy delivery location (from my checkout form): " + (loc || "not stated") + "\n\nI understand that transportation fare ranges depending on location and the weight of the products.\n\nThank you.";
  return "https://wa.me/" + num + "?text=" + encodeURIComponent(text);
}

function showOrderDone(order) {
  const root = document.querySelector("[data-checkout-root]") || document.querySelector("[data-checkout]");
  if (!root) return;
  const payName = order.currency === "NGN" ? t("ck.payNgn") : t("ck.payCfa");
  const locale = (window.I18N && I18N.lang() === "fr") ? "fr-FR" : "en-GB";
  const note = order.currency === "NGN" ? JA.settings().bankNgn : JA.settings().bankCfa;
  root.innerHTML = `
    <ol class="ck-steps" style="margin-bottom:28px">
      <li><a href="cart.html">${t("cart.stepCart")}</a></li>
      <li><a href="checkout.html">${t("cart.stepCheck")}</a></li>
      <li class="is-on">${t("cart.stepDone")}</li>
    </ol>
    <div class="order-done">
      <p class="kicker">${t("ck.doneKicker")}</p>
      <h2 class="serif-title">${t("ck.thanks")}</h2>
      <p class="order-id-label">${t("ck.orderNo")}</p>
      <p class="order-id" id="ja-order-id">${JA.escape(order.id)}</p>
      <p class="ck-id-help">${t("ck.idHelp")}</p>
      <p class="ck-confirm-note">${t("ck.emailNote")}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 18px">
        <button type="button" class="btn" data-copy-id="${JA.escape(order.id)}">${t("ck.copyId")}</button>
        <a class="btn btn-line" href="${fareWaUrl(order)}" target="_blank" rel="noopener">${t("ck.waId")}</a>
      </div>
      <div data-referral-slot></div>
      <ul class="woo-meta">
        <li><span>${t("ck.orderNo")}</span><strong>${JA.escape(order.id)}</strong></li>
        <li><span>${t("ck.date")}</span><strong>${new Date(order.at).toLocaleString(locale)}</strong></li>
        <li><span>${t("ck.total")}</span><strong>${JA.money(order.total, order.currency)}</strong></li>
        <li><span>${t("ck.payMethod")}</span><strong>${payName}</strong></li>
      </ul>
      <p class="status-pill ${order.status}">${t("ck.waiting")}</p>
      <p>${t("ck.saveId")}</p>
      <p class="ck-fare-help">${t("ck.fareRange")}</p>
      <div class="pay-box" style="margin-top:16px">
        <p class="proof-label">${t("ck.account")}</p>
        <p class="pay-note">${JA.escape(note)}</p>
      </div>
      ${((JA.getProof && JA.getProof(order.id, order.proof)) || (String(order.proof || "").startsWith("data:") ? order.proof : "")) ? `<p class="proof-label">${t("ck.uploadReceipt")}</p><img class="proof-preview" src="${(JA.getProof && JA.getProof(order.id, order.proof)) || order.proof}" alt="Payment screenshot" />` : ""}
      <table class="ck-table" style="margin-top:22px">
        <thead><tr><th>${t("ck.product")}</th><th>${t("ck.total")}</th></tr></thead>
        <tbody>${order.items.map((i) => `<tr><td>${i.qty}× ${JA.escape(i.name)}${i.color ? " · " + JA.escape(i.color) : ""}</td><td>${JA.money(i.price * i.qty, order.currency)}</td></tr>`).join("")}</tbody>
        <tfoot><tr class="ck-total"><th>${t("ck.total")}</th><td>${JA.money(order.total, order.currency)}</td></tr></tfoot>
      </table>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:22px">
        <a class="btn" href="https://wa.me/${JA.settings().whatsapp}?text=${encodeURIComponent("Hello JauraStore, my order ID is " + order.id + ". Here is my payment screenshot.")}" target="_blank" rel="noopener">${t("ck.uploadNow")}</a>
        <a class="btn btn-line" href="shop.html">${t("ck.return")}</a>
      </div>
    </div>`;
  paintReferralSlot(order.id);
}

/* The server mints a referral code for qualifying orders. It arrives in the
   order-sync response (store.js stashes it and fires "ja:referral"), which
   can land before or after this screen is painted — cover both. */
function paintReferralSlot(orderId) {
  const paint = (code) => {
    const slot = document.querySelector("[data-referral-slot]");
    if (!slot || !code || slot.dataset.done) return;
    slot.dataset.done = "1";
    const shopUrl = location.origin + location.pathname.replace(/[^/]*$/, "") + "shop.html";
    const shareText = t("ref.shareText", { code, url: shopUrl });
    slot.innerHTML = `
      <div class="referral-card">
        <p class="kicker">${t("ref.kicker")}</p>
        <p class="referral-code">${JA.escape(code)}</p>
        <p class="referral-blurb">${t("ref.blurb")}</p>
        <div class="referral-actions">
          <button type="button" class="btn" data-ref-share>${t("ref.share")}</button>
          <a class="btn btn-line" data-ref-wa href="https://wa.me/?text=${encodeURIComponent(shareText)}" target="_blank" rel="noopener">WhatsApp</a>
          <button type="button" class="btn btn-line" data-ref-copy>${t("ref.copy")}</button>
        </div>
      </div>`;
    slot.querySelector("[data-ref-copy]")?.addEventListener("click", () => {
      navigator.clipboard?.writeText(code).then(() => JA.toast(t("ref.copied")));
    });
    slot.querySelector("[data-ref-share]")?.addEventListener("click", () => {
      if (navigator.share) {
        navigator.share({ title: "J Aura Store", text: shareText, url: shopUrl }).catch(() => {});
      } else {
        navigator.clipboard?.writeText(shareText).then(() => JA.toast(t("ref.copied")));
      }
    });
  };
  let stash = null;
  try { stash = JSON.parse(localStorage.getItem("ja_referral_last") || "null"); } catch (e) {}
  if (stash && stash.orderId === orderId && stash.code) paint(stash.code);
  document.addEventListener("ja:referral", (e) => {
    if (e.detail && e.detail.orderId === orderId && e.detail.code) paint(e.detail.code);
  });
}

function checkoutCurrency(form) {
  return form.querySelector("[name=currency]:checked")?.value || JA.currency();
}

/* The promo/referral code applied at checkout — validated by the server. */
let ckPromo = null;
function ckDiscountFor(sub) {
  if (!ckPromo || !ckPromo.percent) return 0;
  return Math.round(sub * ckPromo.percent / 100);
}

function paintCheckoutTotals(form) {
  const cur = checkoutCurrency(form);
  const items = JA.cartDetailed();
  const lines = document.querySelector("[data-ck-lines]");
  if (lines) {
    lines.innerHTML = items.map((i) => `
      <tr>
        <td>
          <div class="ck-line">
            <img src="${JA.asset(i.product.image)}" alt="" onerror="fallbackImg(event)" />
            <span>${JA.escape(JA.displayName(i.product))}${i.color ? " — " + JA.escape(i.color) : ""} <b>× ${i.qty}</b></span>
          </div>
        </td>
        <td>${JA.money(JA.priceOf(i.product, cur) * i.qty, cur)}</td>
      </tr>`).join("");
  }
  const sub = document.querySelector("[data-ck-sub]");
  const tot = document.querySelector("[data-ck-total]");
  const subVal = JA.cartTotal(cur);
  const disc = ckDiscountFor(subVal);
  const discRow = document.querySelector("[data-ck-disc-row]");
  const discCell = document.querySelector("[data-ck-disc]");
  if (sub) sub.textContent = JA.money(subVal, cur);
  if (discRow) discRow.hidden = !disc;
  if (discCell) discCell.textContent = disc
    ? "− " + JA.money(disc, cur) + " (" + ckPromo.percent + "%)" : "—";
  if (tot) tot.textContent = JA.money(subVal - disc, cur);
  const s = JA.settings();
  const ngnBox = document.querySelector("[data-bank-ngn]");
  const cfaBox = document.querySelector("[data-bank-cfa]");
  if (ngnBox) ngnBox.hidden = cur !== "NGN";
  if (cfaBox) cfaBox.hidden = cur !== "CFA";
  const ngnName = document.querySelector("[data-ngn-name]");
  const ngnBank = document.querySelector("[data-ngn-bank]");
  const ngnAcc = document.querySelector("[data-ngn-acc]");
  const cfaName = document.querySelector("[data-cfa-name]");
  const cfaAcc = document.querySelector("[data-cfa-acc]");
  if (ngnName) ngnName.textContent = s.bankNgnName || "OKORAFOR PRAISE";
  if (ngnBank) ngnBank.textContent = s.bankNgnBank || "UBA";
  if (ngnAcc) ngnAcc.textContent = s.bankNgnAccount || "23474678931";
  if (cfaName) cfaName.textContent = s.bankCfaName || "OKORAFOR GIFT";
  if (cfaAcc) cfaAcc.textContent = s.bankCfaAccount || "01 52 01 99 30";
  form.querySelectorAll(".pay-card").forEach((card) => {
    card.classList.toggle("is-on", card.querySelector("input")?.checked);
  });
  const countryNote = document.querySelector("[data-ck-country-note]");
  if (countryNote) {
    const country = String(form.querySelector("[name=country]")?.value || "").toLowerCase();
    countryNote.hidden = !(country.includes("benin") || country.includes("togo"));
  }
}

function renderCheckout() {
  const form = document.querySelector("[data-checkout]");
  const empty = document.querySelector("[data-empty]");
  if (!form) return;
  if (form.dataset.done === "1") return;

  // A reminder email's "recover my cart" link: restore the saved cart first.
  const recTok = new URLSearchParams(location.search).get("recover");
  if (recTok && form.dataset.recovered !== "1") {
    form.dataset.recovered = "1";
    fetch("api/cart/recover/" + encodeURIComponent(recTok))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || !d.ok || !Array.isArray(d.items) || !d.items.length) return;
        try {
          localStorage.setItem("jaura_cart", JSON.stringify(d.items.map((i) => ({
            id: i.id, qty: Math.max(1, Number(i.qty) || 1), color: i.color || "",
          }))));
          if (d.currency) localStorage.setItem("jaura_currency", d.currency === "NGN" ? "NGN" : "CFA");
          localStorage.setItem("ja_cart_token", recTok);
        } catch (e) {}
        location.replace("checkout.html");
      })
      .catch(() => {});
  }

  const items = JA.cartDetailed();
  if (!items.length) {
    if (empty) empty.hidden = false;
    form.hidden = true;
    return;
  }
  if (empty) empty.hidden = true;
  form.hidden = false;

  const curNow = JA.currency();
  const radio = form.querySelector(`[name=currency][value="${curNow}"]`);
  if (radio) radio.checked = true;

  paintCheckoutTotals(form);
  const me = JA.customer && JA.customer();
  if (me?.email && form.querySelector("[name=email]") && !form.querySelector("[name=email]").value) {
    form.querySelector("[name=email]").value = me.email;
  }

  if (form.dataset.bound) return;
  form.dataset.bound = "1";
  // Delivery only: a "Pick up" choice is never offered, in any language.
  document.querySelectorAll("[data-delivery-zones] .fare-opt, .fare-list .fare-opt").forEach((opt) => {
    const input = opt.querySelector("input");
    const text = (opt.textContent || "") + " " + (input && input.value ? input.value : "");
    if (/pick\s*-?\s*up|collect\s+in\s+store|self\s*-?\s*collect/i.test(text)) opt.remove();
  });
  document.querySelectorAll("select[data-delivery-zones] option").forEach((opt) => {
    const text = (opt.textContent || "") + " " + (opt.value || "");
    if (/pick\s*-?\s*up|collect\s+in\s+store|self\s*-?\s*collect/i.test(text)) opt.remove();
  });
  try { JA.track("checkout_start", { page: "checkout" }); } catch (e) {}

  form.addEventListener("change", (e) => {
    if (e.target.name === "currency") {
      const next = e.target.value;
      if (next !== JA.currency()) JA.setCurrency(next);
    }
    paintCheckoutTotals(form);
  });

  // ---- referral / promo code (server-validated, discount shown at once) ----
  const promoInput = document.querySelector("[data-ck-promo-input]");
  const promoBtn = document.querySelector("[data-ck-promo-apply]");
  const promoMsg = document.querySelector("[data-ck-promo-msg]");
  const setPromoMsg = (text, good) => {
    if (!promoMsg) return;
    promoMsg.hidden = !text;
    promoMsg.textContent = text || "";
    promoMsg.classList.toggle("is-good", !!good);
    promoMsg.classList.toggle("is-bad", !!text && !good);
  };
  promoBtn?.addEventListener("click", () => {
    const code = String(promoInput?.value || "").trim().toUpperCase();
    if (!code) {
      ckPromo = null;
      setPromoMsg("", true);
      paintCheckoutTotals(form);
      return;
    }
    promoBtn.disabled = true;
    const req = window.JA_NET
      ? JA_NET.api("api/promo/check", { method: "POST", json: { code } })
      : fetch("api/promo/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) })
        .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(d))));
    req.then((d) => {
      if (d && d.ok) {
        ckPromo = { code: d.code, percent: d.percent, kind: d.kind };
        setPromoMsg(t("ck.promoOk", { p: d.percent }), true);
      } else {
        ckPromo = null;
        setPromoMsg(t("ck.promoBad"), false);
      }
    }).catch(() => {
      ckPromo = null;
      setPromoMsg(t("ck.promoBad"), false);
    }).finally(() => {
      promoBtn.disabled = false;
      paintCheckoutTotals(form);
    });
  });
  promoInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); promoBtn?.click(); }
  });

  // ---- abandoned-cart capture: save the email + cart as soon as we can,
  // so a stalled checkout gets a recovery email with its cart intact ----
  const cartToken = () => {
    let tk = "";
    try { tk = localStorage.getItem("ja_cart_token") || ""; } catch (e) {}
    if (!tk) {
      tk = "CT-" + Math.random().toString(36).slice(2, 10).toUpperCase() + Date.now().toString(36).toUpperCase();
      try { localStorage.setItem("ja_cart_token", tk); } catch (e) {}
    }
    return tk;
  };
  const captureCart = () => {
    const email = String(form.querySelector("[name=email]")?.value || "").trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;
    const detailed = JA.cartDetailed();
    if (!detailed.length || !window.JA_NET) return;
    JA_NET.api("api/cart/abandon", {
      method: "POST",
      json: {
        token: cartToken(),
        email,
        currency: checkoutCurrency(form),
        items: detailed.map((i) => ({
          id: i.id, name: JA.displayName(i.product), qty: i.qty, color: i.color || "",
        })),
      },
    }).catch(() => {});
  };
  form.querySelector("[name=email]")?.addEventListener("blur", captureCart);
  form.querySelector("[name=email]")?.addEventListener("change", captureCart);
  if (form.querySelector("[name=email]")?.value) captureCart();

  const shot = form.querySelector("[name=proof]");
  const preview = form.querySelector("[data-proof-preview]");
  let proofJob = null;            // the compression still running, if any
  let proofFailed = false;
  let proofFile = null;           // a PDF (or any non-image) travels as-is
  const isPdf = (f) => /pdf/i.test(f?.type || "")
    || /\.(pdf|doc|docx)$/i.test(f?.name || "") || /word|msword|document|pdf/i.test(f?.type || "");

  shot?.addEventListener("change", () => {
    const file = shot.files?.[0];
    if (!file) return;
    proofFailed = false;
    proofFile = null;
    delete form.dataset.proof;

    // A PDF cannot be drawn on a canvas, and shrinking it would mean the shop
    // no longer holds the customer's real receipt - send it untouched.
    if (isPdf(file)) {
      if (file.size > 8 * 1024 * 1024) {
        proofFailed = true;
        JA.toast("That PDF is over 8 MB. Please send a smaller file.");
        return;
      }
      proofFile = file;
      proofJob = Promise.resolve(file);
      if (preview) {
        preview.hidden = true;
        const box = form.querySelector("[data-proof-pdf]");
        if (box) {
          box.hidden = false;
          box.innerHTML = `<iframe class="proof-frame" src="${URL.createObjectURL(file)}"
            title="Your payment receipt"></iframe>`;
        }
      }
      return;
    }
    if (preview) {
      const box = form.querySelector("[data-proof-pdf]");
      if (box) { box.hidden = true; box.innerHTML = ""; }
    }
    proofJob = compressImage(file).then((data) => {
      if (preview) {
        preview.src = data;
        preview.hidden = false;
      }
      form.dataset.proof = data;
      return data;
    }).catch(() => {
      proofFailed = true;         // a camera photo can be huge - wait for it
      return null;
    });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector(".ck-place");
    const data = Object.fromEntries(new FormData(form).entries());
    const cur = data.currency || JA.currency();
    const liveItems = JA.cartDetailed();
    if (!liveItems.length) return;
    // Benin deliveries: 5,000 F CFA or its 11,400 naira equivalent. Guard
    // before any proof handling / queueing so an under-minimum order is
    // never saved locally or sent to the server.
    const beninZone = /benin|cotonou|calavi|porto/i.test(String(data.zone || ""));
    const totalNow = JA.cartTotal(cur);
    if (beninZone && cur === "CFA" && totalNow < 5000) {
      JA.toast(t("ck.minOrderCfa"));
      shot?.focus?.();
      return;
    }
    if (beninZone && cur === "NGN" && totalNow < 11400) {
      JA.toast(t("ck.minOrderNgn"));
      shot?.focus?.();
      return;
    }
    // A big phone photo can still be compressing when the customer taps
    // "Place order". Wait for it instead of refusing the order.
    if (!form.dataset.proof && proofJob) {
      if (btn) { btn.disabled = true; btn.textContent = t("ck.preparing"); }
      await proofJob.catch(() => null);
    }
    if (!form.dataset.proof && !proofFile) {
      if (btn) { btn.disabled = false; btn.textContent = t("ck.place"); }
      JA.toast(proofFailed ? t("toast.badImg") : t("toast.needShot"));
      shot?.focus();
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = t("ck.placing");
    }
    const clean = (v) => String(v || "").replace(/[<>]/g, "").trim().slice(0, 400);
    const fullName = [clean(data.firstName), clean(data.lastName)].filter(Boolean).join(" ") || clean(data.name);
    const proofBlob = proofFile
      || ((JA.dataUrlToBlob && form.dataset.proof) ? JA.dataUrlToBlob(form.dataset.proof) : null);
    const subNow = JA.cartTotal(cur);
    const discNow = ckDiscountFor(subNow);
    const order = JA.saveOrder({
      id: JA.nextOrderId(),
      proofBlob: proofBlob || undefined,
      at: new Date().toISOString(),
      status: "pending",
      promoCode: ckPromo ? ckPromo.code : "",
      cartToken: (() => { try { return localStorage.getItem("ja_cart_token") || ""; } catch (e) { return ""; } })(),
      customer: {
        name: fullName,
        firstName: clean(data.firstName),
        lastName: clean(data.lastName),
        phone: clean(data.phone),
        email: clean(data.email),
        city: clean(data.city),
        zone: clean(data.zone),
        country: clean(data.country),
        address: clean(data.address),
        note: clean(data.note),
      },
      currency: cur,
      total: subNow - discNow,
      proof: form.dataset.proof || "",
      items: liveItems.map((i) => ({
        id: i.id,
        name: i.product.name,
        qty: i.qty,
        color: i.color,
        price: JA.priceOf(i.product, cur),
      })),
    });
    JA.clearCart();
    try { localStorage.removeItem("ja_cart_token"); } catch (err) {}
    ckPromo = null;
    form.dataset.done = "1";
    showOrderDone(order);
    const waiting = JA.syncPending ? JA.syncPending() : 0;
    if (waiting) {
      const root2 = document.querySelector("[data-checkout-root]");
      if (root2) {
        const note = document.createElement("p");
        note.className = "ck-queued";
        note.textContent = "No internet right now — your order and screenshot are saved on this phone and will reach us the moment you are back online. Keep your order ID.";
        root2.insertBefore(note, root2.firstChild);
      }
    }
    if (btn) { btn.disabled = false; btn.textContent = t("ck.place"); }
    document.querySelector("[data-copy-id]")?.addEventListener("click", (ev) => {
      const id = ev.currentTarget.getAttribute("data-copy-id");
      navigator.clipboard?.writeText(id).then(() => JA.toast("Order ID copied: " + id));
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

function renderConfirm() {
  const root = document.querySelector("[data-confirm]");
  if (!root) return;
  const q = new URLSearchParams(location.search);
  const id = (q.get("id") || "").trim().toUpperCase();
  const action = (q.get("action") || "confirm").toLowerCase();
  const token = q.get("token") || "";
  const ask = action === "decline" ? "decline" : "confirm";

  const shell = (inner) => `
    <div class="order-done">
      <div class="kicker">${ask === "confirm" ? "Confirm payment" : "Decline order"}</div>
      <h1 class="serif-title">${JA.escape(id || "Order")}</h1>
      ${inner}
    </div>`;

  if (!id || !token) {
    root.innerHTML = shell(`<p class="empty">This link is incomplete. Open the email
      again, or sign in to the admin portal to confirm the order.</p>`);
    return;
  }

  // The page never acts on its own: a mail scanner pre-fetching the link must
  // not confirm anything. It waits for a human to press the button.
  root.innerHTML = shell(`
    <p id="c-summary" class="empty">Loading this order…</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:18px">
      <button type="button" class="btn" data-c-go="${ask}">
        ${ask === "confirm" ? "Yes — payment received" : "Decline this order"}
      </button>
      <a class="btn btn-line" href="admin.html">Open admin portal</a>
    </div>
    <p id="c-result" class="ck-fare-help" style="margin-top:18px"></p>`);

  fetch(`api/orders/${encodeURIComponent(id)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const el = document.getElementById("c-summary");
      if (!el) return;
      if (!d) { el.textContent = "We could not read this order."; return; }
      const cur = d.currency === "NGN" ? "\u20a6" : "F CFA";
      el.innerHTML = `
        <p><strong>${JA.escape(String(d.id || id))}</strong> ·
           <span class="status-pill ${JA.escape(d.status || "pending")}">${JA.escape(d.status || "pending")}</span></p>
        <p>${JA.escape(d.customer_name || "")}${d.city ? " · " + JA.escape(d.city) : ""}</p>
        <p>Total <strong>${cur}${JA.escape(String(d.total || ""))}</strong></p>`;
    })
    .catch(() => {});

  const btn = root.querySelector("[data-c-go]");
  btn?.addEventListener("click", async () => {
    btn.disabled = true;
    const res = document.getElementById("c-result");
    res.textContent = "Working…";
    try {
      const r = await fetch(
        `api/orders/${encodeURIComponent(id)}/confirm?action=${encodeURIComponent(ask)}`
        + `&token=${encodeURIComponent(token)}`, { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        root.innerHTML = shell(`
          <p class="ck-confirm-note">${ask === "confirm"
            ? "Payment confirmed. The customer has been emailed a receipt."
            : "Order declined. The customer has been emailed about it."}</p>
          <p class="status-pill ${d.status}">${JA.escape(d.status)}</p>
          <p style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:18px">
            <a class="btn" href="admin.html">Open admin portal</a>
            <a class="btn btn-line" href="shop.html">Back to the shop</a>
          </p>`);
        return;
      }
      res.textContent = (d && d.error) || "That link did not work. Sign in to the admin portal instead.";
      btn.disabled = false;
    } catch (e) {
      res.textContent = "No connection. Try again, or confirm from the admin portal.";
      btn.disabled = false;
    }
  });
}

function renderWishlist() {
  const box = document.querySelector("[data-wish-grid]");
  if (!box) return;
  const items = JA.wishDetailed();
  if (!items.length) {
    box.innerHTML = `<div class="empty splend-empty">
      <p>${t("wish.empty")}</p>
      <p>${t("wish.add")}</p>
      <a class="btn" href="shop.html">${t("wish.shop")}</a>
    </div>`;
    return;
  }
  box.innerHTML = `<div class="product-grid">${items.map(JA.cardHTML).join("")}</div>`;
}

function renderAccount() {
  const root = document.querySelector("[data-account-root]");
  if (!root) return;
  const me = JA.customer && JA.customer();
  if (!me || !me.email) {
    root.innerHTML = `
      <form class="order-lookup" data-account-login>
        <div class="field"><label>${t("account.email")}</label><input name="email" type="email" required placeholder="you@email.com" autocomplete="email" /></div>
        <button class="btn" type="submit" style="margin-top:16px">${t("account.login")}</button>
      </form>
      <p style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:22px">
        <a class="btn btn-line" href="https://wa.me/${JA.settings().whatsapp}" target="_blank" rel="noopener">${t("footer.contactUs")}</a>
      </p>`;
    root.querySelector("[data-account-login]")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const email = String(new FormData(e.target).get("email") || "").trim();
      if (!email) { JA.toast(t("account.needEmail")); return; }
      JA.setCustomer({ email });
      renderAccount();
    });
    return;
  }
  const list = (JA.ordersForEmail ? JA.ordersForEmail(me.email) : JA.orders().filter((o) => String(o.customer?.email || "").toLowerCase() === me.email));
  root.innerHTML = `
    <p class="ck-confirm-note">${t("account.hello", { email: me.email })}</p>
    <p style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px">
      <button type="button" class="btn btn-line" data-account-out>${t("account.logout")}</button>
      <a class="btn btn-line" href="https://wa.me/${JA.settings().whatsapp}" target="_blank" rel="noopener">${t("footer.contactUs")}</a>
    </p>
    <h2 class="serif-title" style="font-size:22px;margin-bottom:14px">${t("account.orders")}</h2>
    ${list.length ? list.slice(0, 20).map((o) => `
      <a class="order-card" href="https://wa.me/${JA.settings().whatsapp}?text=${encodeURIComponent("Hello JauraStore, I have a question about order " + o.id + ".")}" target="_blank" rel="noopener">
        <div class="order-card-top">
          <strong>${JA.escape(o.id)}</strong>
          <span class="status-pill ${o.status || "pending"}">${t(o.status === "confirmed" ? "order.confirmed" : o.status === "declined" ? "order.declined" : "order.pending")}</span>
        </div>
        <p>${JA.escape(o.customer?.name || "")} · ${JA.money(o.total, o.currency)}</p>
        <p style="font-size:12px;color:var(--muted)">${new Date(o.at).toLocaleString()}</p>
      </a>`).join("") : `<p class="empty">${t("account.empty")}</p>`}`;
  root.querySelector("[data-account-out]")?.addEventListener("click", () => {
    JA.logoutCustomer();
    renderAccount();
  });
}

function bindContactForm() {
  const form = document.querySelector("[data-contact-form]");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "1";
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    const btn = form.querySelector("button[type=submit]");
    if (btn) { btn.disabled = true; }
    try {
      await fetch("https://formsubmit.co/ajax/jaurastore@gmail.com", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          _subject: "JauraStore message from " + (data.name || ""),
          _template: "box",
          _captcha: "false",
          name: data.name || "",
          email: data.email || "jaurastore@gmail.com",
          message: data.message || "",
        }),
      });
      JA.toast(t("contact.sent"));
      form.reset();
    } catch {
      JA.toast(t("contact.sent"));
    }
    if (btn) btn.disabled = false;
  });
}

function watchReveal() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) {
        en.target.classList.add("in");
        io.unobserve(en.target);
      }
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
  document.querySelectorAll(".reveal, .reveal-left, .reveal-right").forEach((el) => io.observe(el));
}

async function boot() {
  try { await (JA && JA.ready); } catch (e) {}
  try { JA.mountChrome(); } catch (e) { console.error(e); }
  document.querySelectorAll(".lux-reel video").forEach((v) => {
    v.muted = true;
    v.setAttribute("playsinline", "");
    v.setAttribute("webkit-playsinline", "");
    const play = () => v.play().catch(() => {});
    play();
    v.addEventListener("canplay", play, { once: true });
    document.addEventListener("touchstart", play, { once: true });
    document.addEventListener("click", play, { once: true });
  });
  const page = document.body.dataset.page;
  if (page === "home") mountHeroVideo();
  const draw = () => {
    if (page === "home") renderHome();
    if (page === "categories") renderCategories();
    if (page === "shop") renderShop();
    if (page === "product") renderProduct();
    if (page === "home" || page === "shop") renderMostViewed();
    try { JA.startCardPlay && JA.startCardPlay(); } catch (e) {}
    if (page === "cart") renderCart();
    if (page === "checkout") renderCheckout();
    if (page === "confirm") renderConfirm();
    if (page === "wishlist") renderWishlist();
    if (page === "account") renderAccount();
    if (page === "contact") bindContactForm();
    if (window.I18N && typeof window.I18N.apply === "function") window.I18N.apply();
    watchReveal();
  };
  draw();
  document.addEventListener("ja:rerender", draw);
  document.addEventListener("ja:cart", () => {
    if (page === "cart") draw();
  });
  document.addEventListener("ja:wish", () => {
    if (page === "wishlist") draw();
  });
  document.querySelector("[data-sort]")?.addEventListener("change", renderShop);
  document.querySelector("[data-shop-q]")?.addEventListener("input", renderShop);
  if (page === "shop") bindShopFilter();
}

document.addEventListener("DOMContentLoaded", boot);
