/* J Aura Store — shared commerce layer. Images and brand assets are used as-is. */
const JA = (() => {
    const KEYS = {
    cart: "jaura_cart",
    currency: "jaura_currency",
    custom: "jaura_custom_products",
    deleted: "jaura_deleted",
    settings: "jaura_settings",
    orders: "jaura_orders",
    session: "jaura_admin",
    fx: "jaura_fx",
    wish: "jaura_wish",
    customer: "jaura_customer",
    stats: "jaura_stats",
    cats: "jaura_categories",
    reviews: "jaura_reviews",
    pending: "jaura_pending_products",
    events: "jaura_pending_events",
  };

  const FALLBACK = {
    "nav.home": "Home",
    "nav.shop": "Shop",
    "nav.shopAll": "All Products",
    "nav.allProducts": "All Products",
    "nav.categories": "Categories",
    "nav.faq": "FAQ",
    "nav.vision": "Vision",
    "nav.contact": "Contact",
    "nav.checkout": "Checkout",
    "nav.checkoutForm": "Checkout Form",
    "nav.bag": "Bag",
    "nav.track": "Track order",
    "nav.pay": "Send payment receipt",
    "nav.search": "Search",
    "nav.cart": "Cart",
    "nav.menu": "Menu",
    "nav.close": "Close",
    "nav.delivery": "Delivery",
    "nav.care": "Customer Care",
    "nav.accountSettings": "Account Settings",
    "nav.wishlist": "MY WISHLIST",
    "nav.account": "MY ACCOUNT",
    "search.placeholder": "Search...",
    "search.hint": "Type a name, category or keyword",
    "search.all": "All",
    "search.type": "Type a name or category",
    "search.browse": "Browse all products →",
    "card.add": "Add to cart",
    "card.oos": "Out of stock",
    "badge.sale": "Sale",
    "badge.new": "New",
    "badge.bestseller": "Bestseller",
    "cart.empty": "Your cart is empty.",
    "lang.group": "Language",
    "footer.client": "Client Relations",
    "footer.phone": "Phone",
    "footer.email": "Email",
    "footer.waHelp": "Our team is ready to assist you on WhatsApp for a smooth shopping experience.",
    "footer.contactUs": "Contact Us",
    "footer.follow": "Follow us",
    "footer.blurb": "Premium fashion, beauty and lifestyle essentials. Wholesale luxury for West Africa.",
    "footer.visit": "Visit & deliver",
    "promo.welcome": "Welcome to JauraStore",
    "promo.reduced": "Prices have been reduced",
    "promo.referral": "Order above ₦20,000 (8,800 CFA) and get your personal referral code — share it and your friends enjoy a discount at checkout.",
    "promo.shop": "Shop now",
    "promo.kicker": "Everything you love, all in one store",
    "conv.banner": "Benin 🇧🇯 customers: place your order now and get it between {from} and {to}",
    "ck.bjMin": "Benin deliveries: minimum order 5,000 F CFA (about 12,000 naira).",
  };

  const tx = (key, vars) => {
    try {
      if (window.I18N && typeof window.I18N.t === "function") {
        const s = window.I18N.t(key, vars);
        if (s && s !== key) return s;
      }
    } catch (e) {}
    let s = FALLBACK[key] || "";
    if (!s) {
      const tail = String(key || "").split(".").pop();
      s = tail.replace(/([A-Z])/g, " $1").replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase()).trim();
    }
    if (vars) {
      Object.keys(vars).forEach((k) => {
        s = s.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return s;
  };

  const DEFAULT_CATS = [
    { id: "clothing", name: "Clothings for men and women", image: "images/categories/fashion.jpg" },
    { id: "household", name: "Household items", image: "images/categories/household.jpg" },
    { id: "ankara", name: "Ankara ready to wear", image: "images/categories/fashion.jpg" },
    { id: "accessories", name: "Accessories", image: "images/categories/gadgets.jpg" },
    { id: "beauty", name: "Beauty & skincare", nameFr: "Beauté & soins", image: "images/categories/beauty.jpg" },
    { id: "shoes", name: "Shoes", image: "images/categories/shoes.jpg" },
    { id: "gadgets", name: "Gadgets / Electronics", image: "images/categories/gadgets.jpg" },
    { id: "packaging", name: "Packaging", image: "images/categories/household.jpg" },
    { id: "bags", name: "Bags", image: "images/categories/bags.jpg" },
    { id: "hair-care", name: "Hair care", image: "images/categories/beauty.jpg" },
    { id: "nails", name: "Nails", image: "images/categories/beauty.jpg" },
    { id: "gift-set", name: "Gift set", image: "images/categories/household.jpg" },
    { id: "children", name: "Children items", image: "images/categories/fashion.jpg" },
    { id: "decor", name: "Decor", image: "images/categories/household.jpg" },
  ];

  const DEFAULT_SETTINGS = {
    storeName: "J Aura Store",
    rate: 0.44,
    whatsapp: "2290168953101",
    phoneBj: "+229 01 68 95 31 01",
    phoneNg: "+234 916 167 0236",
    email: "jaurastore@gmail.com",
    tiktok: "https://www.tiktok.com/@j_aura_store",
    bankCfaName: "OKORAFOR GIFT",
    bankCfaBank: "MTN MoMo Benin",
    bankCfaAccount: "01 52 01 99 30",
    bankCfa: "CFA (Benin) — MTN MoMo\nName: OKORAFOR GIFT\nMoMo: 01 52 01 99 30\nTogo Moov: +229 01 68 95 31 10 — OKORAFOR GOODNESS\nPut your order ID in the transfer remark.",
    bankNgnName: "OKORAFOR PRAISE",
    bankNgnBank: "UBA",
    bankNgnAccount: "23474678931",
    bankNgn: "Naira — UBA\nName: OKORAFOR PRAISE\nBank: UBA\nAccount number: 23474678931\nPut your order ID in the transfer remark.",
    shippingNote: "",
    logoUrl: "",
    shopBannerUrl: "",
  };

  let seed = [];
  let ready = null;
  const NGN_TO_CFA = 0.44;
  let fx = { ngnToXof: NGN_TO_CFA, at: "house 1 ₦ = 0.44 CFA", day: "" };

  const read = (k, fallback) => {
    try {
      const raw = localStorage.getItem(k);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  };
  const write = (k, v) => localStorage.setItem(k, JSON.stringify(v));

  // Build the contact address at runtime (split + join) so an email-obfuscation
  // middlebox can never rewrite it to "[email protected]" in the footer. The
  // mailto href uses the same rebuilt string, so tapping it still opens the
  // mail app with the address pre-filled.
  const emailText = () => {
    const e = String(settings().email || DEFAULT_SETTINGS.email || "").trim();
    const at = e.indexOf("@");
    if (at < 1) return ["jaurastore", String.fromCharCode(64), "gmail.com"].join("");
    const user = e.slice(0, at);
    const domain = e.slice(at + 1);
    return [user, domain].join(String.fromCharCode(64));
  };

  const settings = () => {
    const s = { ...DEFAULT_SETTINGS, ...read(KEYS.settings, {}) };
    s.rate = NGN_TO_CFA;
    if (/set your bank|set in Admin|Phone \/ transfer/i.test(s.bankNgn || "") || !/23474678931|UBA/i.test(s.bankNgn || "")) {
      s.bankNgn = DEFAULT_SETTINGS.bankNgn;
      s.bankNgnName = DEFAULT_SETTINGS.bankNgnName;
      s.bankNgnBank = DEFAULT_SETTINGS.bankNgnBank;
      s.bankNgnAccount = DEFAULT_SETTINGS.bankNgnAccount;
    }
    if (/set your bank|set in Admin|MTN MoMo Benin: \+229/i.test(s.bankCfa || "") || !/52019930|OKORAFOR GIFT/i.test(s.bankCfa || "")) {
      s.bankCfa = DEFAULT_SETTINGS.bankCfa;
      s.bankCfaName = DEFAULT_SETTINGS.bankCfaName;
      s.bankCfaBank = DEFAULT_SETTINGS.bankCfaBank;
      s.bankCfaAccount = DEFAULT_SETTINGS.bankCfaAccount;
    }
    return s;
  };
  const saveSettings = (s) => write(KEYS.settings, { ...settings(), ...s });

  // ------------------------------------------------------------ the catalogue
  // The server catalogue is the single source of truth. Local edits are kept
  // only while they are still waiting to sync, so a second device never shows
  // a stale copy of a product someone else already changed.
  let catalogMeta = { server: false };

  function pendingMap() { return read(KEYS.pending, {}) || {}; }
  function markPending(id) { const p = pendingMap(); p[id] = Date.now(); write(KEYS.pending, p); }
  function clearPending(id) { const p = pendingMap(); delete p[id]; write(KEYS.pending, p); }

  function applyServerProduct(p) {
    if (!p || !p.id) return;
    const i = seed.findIndex((x) => x.id === p.id);
    if (i >= 0) seed[i] = p;
    else seed.unshift(p);
    write(KEYS.custom, read(KEYS.custom, []).filter((x) => x.id !== p.id));
    clearPending(p.id);
    if (window.JA_SEED) {
      const j = window.JA_SEED.findIndex((x) => x.id === p.id);
      if (j >= 0) window.JA_SEED[j] = p; else window.JA_SEED.unshift(p);
    }
  }

  // One copy of every product, ever. The same piece can reach the browser
  // under two ids (a Supabase row re-created next to its seed row, or a
  // locally queued edit next to the synced server copy), so products are
  // keyed by id, then slug, then sku and the first copy seen wins. The list
  // passed in is ordered so the preferred copy comes first.
  function dedupeProducts(list) {
    const byId = new Map(), bySlug = new Map(), bySku = new Map();
    const out = [];
    (Array.isArray(list) ? list : []).forEach((p) => {
      if (!p || !p.id) return;
      const id = String(p.id).trim();
      const slug = String(p.slug || "").trim().toLowerCase();
      const sku = String(p.sku || "").trim().toLowerCase();
      if (byId.has(id)) return;
      if (slug && bySlug.has(slug)) return;
      if (sku && bySku.has(sku)) return;
      byId.set(id, p);
      if (slug) bySlug.set(slug, p);
      if (sku) bySku.set(sku, p);
      out.push(p);
    });
    return out;
  }

  async function loadSeed() {
    if (seed.length) return seed;
    try {
      const res = await fetch("api/catalog", { credentials: "same-origin" });
      if (res.ok) {
        const d = await res.json();
        if (d && Array.isArray(d.products) && d.products.length) {
          catalogMeta = Object.assign({ server: true }, d.meta || {});
          // keep only edits that have not reached the server yet
          const pend = pendingMap();
          const stillPending = (read(KEYS.custom, []) || []).filter((p) => p && pend[p.id]);
          write(KEYS.custom, stillPending);
          // The whole catalogue, one copy of each product: the server merges
          // seed + admin + Supabase rows, and any duplicate that survives
          // that merge (same product under two ids) is dropped here so the
          // shop can never render the same piece twice.
          seed = dedupeProducts(d.products);
          window.JA_SEED = seed;
          return seed;
        }
      }
    } catch (e) { /* offline or static hosting: fall back below */ }
    // The bundled catalogue (js/products-data.js) is loaded on every page and is
    // the static fallback; the server catalogue (api/catalog) is the source of
    // truth. We deliberately do not fetch data/seed.json in the browser: that
    // was a legacy static-JSON dependency that duplicated the catalogue and is
    // now served on the server (api/catalog) instead.
    if (Array.isArray(window.JA_SEED) && window.JA_SEED.length) {
      seed = dedupeProducts(window.JA_SEED);
      return seed;
    }
    return [];
  }

  function todayStamp() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function applyFx(rate, at) {
    const n = Number(rate);
    if (!(n > 0) || n > 5) return false;
    fx = { ngnToXof: n, at: at || "", day: todayStamp() };
    try { write(KEYS.fx, fx); } catch (e) {}
    return true;
  }

  async function loadFx() {
    fx = { ngnToXof: NGN_TO_CFA, at: "house 1 ₦ = 0.44 CFA", day: todayStamp() };
    return fx;
  }

  function hasNgn(p) {
    return Number(p && p.priceNgn) > 0;
  }
  function toCfa(ngn) {
    return Math.round(Number(ngn || 0) * NGN_TO_CFA);
  }

  function products() {
    try {
      const deletedRaw = read(KEYS.deleted, []);
      const deleted = new Set(Array.isArray(deletedRaw) ? deletedRaw : []);
      let custom = read(KEYS.custom, []);
      if (!Array.isArray(custom)) custom = [];
      custom = custom.filter((p) => p && p.id);
      const customIds = new Set(custom.map((p) => p.id));
      const baseList = (seed && seed.length ? seed : (window.JA_SEED || []));
      const base = baseList.filter((p) => p && p.id && !deleted.has(p.id) && !customIds.has(p.id));
      const remap = (p) => {
        if (!p) return p;
        let out = p.category === "skincare" ? { ...p, category: "beauty" } : p;
        if (Number(out.priceNgn) > 0) {
          out = {
            ...out,
            priceCfa: toCfa(out.priceNgn),
            compareCfa: Number(out.compareNgn) > 0 ? toCfa(out.compareNgn) : null,
          };
        }
        return out;
      };
      const all = dedupeProducts([...custom, ...base].map(remap).filter(Boolean));
      if ((document.body.dataset.page || "") === "admin") return all;
      return all.filter((p) => p.online !== false);
    } catch (e) {
      return (seed && seed.length ? seed : (window.JA_SEED || [])).slice();
    }
  }

  function product(idOrSlug) {
    return products().find((p) => p.id === idOrSlug || p.slug === idOrSlug);
  }

  function searchProducts(q, cat) {
    const terms = String(q || "").toLowerCase().split(/\s+/).filter(Boolean);
    let list = products();
    if (cat && cat !== "all") {
      const want = cat === "skincare" ? "beauty" : cat;
      list = list.filter((p) => p.category === want);
    }
    if (!terms.length) return list;
    return list
      .map((p) => {
        const name = p.name.toLowerCase();
        const blob = (name + " " + (p.nameFr || "") + " " + p.category + " " + categoryName(p.category) + " " + (p.description || "")).toLowerCase();
        let score = 0;
        terms.forEach((t) => {
          if (name === t) score += 20;
          else if (name.startsWith(t)) score += 12;
          else if (name.includes(t)) score += 7;
          if (p.category.includes(t) || categoryName(p.category).toLowerCase().includes(t)) score += 5;
          if (blob.includes(t)) score += 2;
        });
        return { p, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.p);
  }

  function displayName(p) {
    if (!p) return "";
    try {
      if (window.I18N && I18N.lang() === "fr" && p.nameFr) return p.nameFr;
    } catch (e) {}
    return p.name || "";
  }

  function normalizeCatList(list) {
    const out = [];
    const seen = new Set();
    (list || []).forEach((c) => {
      if (!c || !c.id) return;
      const id = c.id === "skincare" ? "beauty" : c.id;
      if (seen.has(id)) return;
      seen.add(id);
      const def = DEFAULT_CATS.find((x) => x.id === id) || {};
      const mergedBeauty = id === "beauty" && (!c.name || c.name === "Beauty" || c.name === "Skincare");
      out.push({
        id,
        name: mergedBeauty ? "Beauty & skincare" : (c.name || def.name || id),
        nameFr: mergedBeauty ? "Beauté & soins" : (c.nameFr || def.nameFr || ""),
        image: c.image || def.image || "",
        hidden: !!c.hidden,
      });
    });
    return out;
  }
  function categories() {
    const saved = read(KEYS.cats, null);
    const base = DEFAULT_CATS.map((c) => ({ ...c, nameFr: c.nameFr || "", hidden: false }));
    const all = Array.isArray(saved) && saved.length ? normalizeCatList(saved) : normalizeCatList(base);
    if ((document.body.dataset.page || "") === "admin") return all;
    return all.filter((c) => !c.hidden);
  }
  async function loadServerCategories() {
    // Every page loads the owner's server-side category table (photos
    // included), not just the admin portal — the public storefront is where
    // shoppers see them.
    try {
      const r = await fetch("api/categories", { credentials: "same-origin", cache: "no-store" });
      const d = await r.json();
      if (d && Array.isArray(d.categories) && d.categories.length) {
        write(KEYS.cats, d.categories.map((c) => ({
          id: c.id, name: c.name, nameFr: c.nameFr || "",
          image: c.image || "", hidden: !!c.hidden,
        })));
      }
    } catch (e) { /* fall back to local defaults */ }
    return categories();
  }
  function saveCategories(list) {
    const cleaned = normalizeCatList(list || []).map((c) => ({
      id: c.id,
      name: c.name,
      nameFr: c.nameFr || "",
      image: c.image || "",
      hidden: !!c.hidden,
    }));
    write(KEYS.cats, cleaned);
    // In the admin portal also persist to the server so every device and the
    // live storefront see the same category table.
    if ((document.body.dataset.page || "") === "admin") {
      (async () => {
        try {
          await fetch("api/admin/categories", {
            method: "PUT",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": await (window.JA_NET && window.JA_NET.csrf ? window.JA_NET.csrf() : Promise.resolve("")),
            },
            credentials: "same-origin",
            body: JSON.stringify({ categories: cleaned }),
          });
        } catch (e) { /* admin still has the local copy */ }
      })();
    }
  }
  function moveCategoryProducts(fromId, toId) {
    if (!fromId || !toId || fromId === toId) return 0;
    let n = 0;
    products().forEach((p) => {
      if (p.category === fromId || (fromId === "beauty" && p.category === "skincare")) {
        upsertProduct({ ...p, category: toId });
        n += 1;
      }
    });
    return n;
  }
  function deleteCategory(id, moveTo) {
    if (!id) return 0;
    const dest = (moveTo && moveTo !== id) ? moveTo : (id === "beauty" ? "household" : "beauty");
    const moved = moveCategoryProducts(id, dest);
    saveCategories(categories().filter((c) => c.id !== id && c.id !== "skincare"));
    return moved;
  }
  function categoryName(id) {
    const c = categories().find((x) => x.id === id) || DEFAULT_CATS.find((x) => x.id === id);
    if (!c) return id;
    try {
      if (window.I18N && I18N.lang() === "fr" && c.nameFr) return c.nameFr;
    } catch (e) {}
    if (c.name) return c.name;
    const key = "cat." + id;
    const translated = tx(key);
    if (translated && translated !== key) return translated;
    return id;
  }

  function currency() {
    return localStorage.getItem(KEYS.currency) || "NGN";
  }
  function setCurrency(c) {
    localStorage.setItem(KEYS.currency, c === "NGN" ? "NGN" : "CFA");
    document.dispatchEvent(new CustomEvent("ja:currency"));
  }

  function money(n, cur = currency()) {
    const val = Math.round(Number(n) || 0);
    if (cur === "NGN") return "₦" + val.toLocaleString("en-NG");
    return "F CFA " + val.toLocaleString("fr-FR");
  }

  function priceOf(p, cur = currency()) {
    if (hasNgn(p)) {
      if (cur === "NGN") return Number(p.priceNgn);
      return toCfa(p.priceNgn);
    }
    return Number(p.priceCfa) || 0;
  }
  function compareOf(p, cur = currency()) {
    if (hasNgn(p)) {
      const was = Number(p.compareNgn) || 0;
      if (cur === "NGN") return was;
      return was > 0 ? toCfa(was) : 0;
    }
    return Number(p.compareCfa) || 0;
  }
  function displayCur(p, cur = currency()) {
    return hasNgn(p) ? cur : "CFA";
  }

  function priceBits(now, was, cur) {
    return was && was > now
      ? `<s>${money(was, cur)}</s><span class="now">${money(now, cur)}</span>`
      : `<span class="now">${money(now, cur)}</span>`;
  }
  function priceHTML(p) {
    const cur = displayCur(p);
    const now = priceOf(p, cur);
    const was = compareOf(p, cur);
    return `<span class="price" data-price-for="${p.id}">${priceBits(now, was, cur)}</span>`;
  }

  function cart() {
    return read(KEYS.cart, []);
  }
  function saveCart(items) {
    write(KEYS.cart, items);
    document.dispatchEvent(new CustomEvent("ja:cart"));
  }
  const BULK_QTY = 10;
  const BULK_OFF = 0.10;
  function stockFold(s) {
    return String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9]/g, "");
  }
  function stockVariantValues(variant) {
    const v = String(variant || "");
    if (!v || v === "__default__") return [];
    const out = [];
    v.split(/[·;|]/).forEach((part) => {
      const p = String(part || "").trim();
      if (!p) return;
      const ci = p.indexOf(":");
      const val = (ci >= 0 ? p.slice(ci + 1) : p).trim();
      if (val) out.push(val);
    });
    return out;
  }
  function stockFor(idOrProduct, variant) {
    const p = typeof idOrProduct === "string" ? product(idOrProduct) : idOrProduct;
    if (!p) return 0;
    const base = Math.max(0, Math.round(Number(p.stock) || 0));
    const os = p.optionStock;
    if (!os || typeof os !== "object" || !Object.keys(os).length) return base;
    const vals = stockVariantValues(variant);
    if (!vals.length) return base;
    const folded = {};
    Object.keys(os).forEach((k) => {
      const fk = stockFold(k);
      if (fk) folded[fk] = Math.max(0, Math.round(Number(os[k]) || 0));
    });
    for (let i = 0; i < vals.length; i += 1) {
      const fk = stockFold(vals[i]);
      if (fk && Object.prototype.hasOwnProperty.call(folded, fk)) return folded[fk];
    }
    return base;
  }
  function cartQtyFor(id, variant) {
    const items = cart().filter((i) => i.id === id);
    if (variant === undefined) {
      return items.reduce((n, i) => n + (Number(i.qty) || 0), 0);
    }
    const want = variant === "__default__" ? "" : String(variant || "");
    return items
      .filter((i) => String(i.color || "") === want)
      .reduce((n, i) => n + (Number(i.qty) || 0), 0);
  }
  function stockLeft(idOrProduct, variant) {
    const p = typeof idOrProduct === "string" ? product(idOrProduct) : idOrProduct;
    if (!p) return 0;
    const v = variant === undefined ? "" : variant;
    const avail = stockFor(p, v);
    const inCart = cartQtyFor(p.id, v);
    return Math.max(0, avail - inCart);
  }
  function stockProblems() {
    const groups = {};
    cart().forEach((i) => {
      const key = String(i.id) + "\u0000" + String(i.color || "");
      if (!groups[key]) {
        groups[key] = { id: i.id, variant: String(i.color || ""), requested: 0 };
      }
      groups[key].requested += Number(i.qty) || 0;
    });
    const out = [];
    Object.keys(groups).forEach((key) => {
      const g = groups[key];
      const p = product(g.id);
      if (!p) return;
      const avail = stockFor(p, g.variant);
      if (g.requested > avail) {
        out.push({
          id: g.id,
          name: displayName(p) || p.name || g.id,
          variant: g.variant,
          available: avail,
          requested: g.requested,
          left: avail,
          asked: g.requested,
        });
      }
    });
    return out;
  }
  function stockProblemLine(problems) {
    let list = problems;
    if (list && !Array.isArray(list)) list = [list];
    if (!list) list = stockProblems();
    if (!list.length) return "";
    const p = list[0];
    return `Only ${p.available} left of "${p.name}" — you asked for ${p.requested}.`;
  }
  function bulkUnit(p, qty, cur) {
    const unit = priceOf(p, cur);
    if ((Number(qty) || 0) >= BULK_QTY) return Math.round(unit * (1 - BULK_OFF));
    return unit;
  }
  function addToCart(id, qty = 1, color = "") {
    const p = product(id);
    const want = Math.max(1, Math.round(Number(qty) || 1));
    if (!p) {
      toast(tx("toast.unavailable"));
      return;
    }
    const avail = stockFor(p, color);
    if (avail <= 0) {
      toast(tx("toast.unavailable"));
      return;
    }
    const already = cartQtyFor(id, color);
    const room = Math.max(0, avail - already);
    if (room <= 0) {
      toast(stockProblemLine([{ name: displayName(p) || p.name, available: avail, requested: already + want }]));
      return;
    }
    const add = Math.min(want, room);
    const items = cart();
    const found = items.find((i) => i.id === id && String(i.color || "") === String(color || ""));
    if (found) found.qty = Math.min(avail, (Number(found.qty) || 0) + add);
    else items.push({ id, qty: add, color: String(color || "") });
    saveCart(items);
    if (add < want) {
      toast(stockProblemLine([{ name: displayName(p) || p.name, available: avail, requested: already + want }]));
    }
    track("cart", { id, name: p.name, qty: add });
    const totalQty = cartQtyFor(id);
    if (totalQty >= BULK_QTY) toast(tx("cart.bulkOn"));
    openMini();
  }
  function setQty(id, color, qty) {
    const want = Math.round(Number(qty) || 0);
    if (want <= 0) {
      saveCart(cart().filter((i) => !(i.id === id && String(i.color || "") === String(color || ""))));
      return;
    }
    const p = product(id);
    const avail = p ? stockFor(p, color) : want;
    const capped = Math.min(want, Math.max(0, avail));
    if (p && want > avail) {
      toast(stockProblemLine([{ name: displayName(p) || p.name, available: avail, requested: want }]));
    }
    if (capped <= 0) {
      saveCart(cart().filter((i) => !(i.id === id && String(i.color || "") === String(color || ""))));
      return;
    }
    saveCart(cart().map((i) => (i.id === id && String(i.color || "") === String(color || "") ? { ...i, qty: capped } : i)));
  }
  function clearCart() { saveCart([]); }
  function cartCount() { return cart().reduce((n, i) => n + i.qty, 0); }
  function wish() { return read(KEYS.wish, []); }
  function isWished(id) { return wish().includes(id); }
  function toggleWish(id) {
    if (!id) return;
    const w = wish();
    const i = w.indexOf(id);
    if (i >= 0) w.splice(i, 1);
    else w.unshift(id);
    write(KEYS.wish, w);
    document.dispatchEvent(new CustomEvent("ja:wish"));
    toast(i >= 0 ? tx("toast.unwish") : tx("toast.wish"));
  }
  function wishDetailed() {
    return wish().map((id) => product(id)).filter(Boolean);
  }
  function cartDetailed() {
    return cart().map((i) => {
      const p = product(i.id);
      if (!p) return null;
      const cur = displayCur(p);
      const unit = priceOf(p, cur);
      const qtyAll = cartQtyFor(i.id);
      const bulk = qtyAll >= BULK_QTY;
      const payUnit = bulkUnit(p, qtyAll, cur);
      return { ...i, product: p, cur, unit, bulk, payUnit, line: payUnit * i.qty };
    }).filter(Boolean);
  }
  function cartTotal(cur = currency()) {
    return cartDetailed().reduce((n, i) => {
      const use = i.cur || displayCur(i.product, cur);
      return n + bulkUnit(i.product, cartQtyFor(i.id), use) * i.qty;
    }, 0);
  }

  function toast(msg) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function upsertProduct(p) {
    const next = { ...(p || {}) };
    if (Number(next.priceNgn) > 0) {
      next.priceCfa = toCfa(next.priceNgn);
      next.compareCfa = Number(next.compareNgn) > 0 ? toCfa(next.compareNgn) : null;
    }
    // 1. show it immediately, 2. push it to the server (queued if offline)
    const custom = read(KEYS.custom, []);
    const i = custom.findIndex((x) => x.id === next.id);
    if (i >= 0) custom[i] = next;
    else custom.unshift(next);
    write(KEYS.custom, custom);
    markPending(next.id);
    if (!window.JA_NET) return Promise.resolve({ ok: false, offline: true });
    return window.JA_NET.api("api/admin/products", {
      method: "POST",
      json: { product: next },
      queue: true,
      label: "Product",
      onDone: (data) => { if (data && data.product) applyServerProduct(data.product); },
    }).then((d) => {
      if (d && d.mirrored === false) {
        toast("Saved on the server only — not yet on the cloud copy. Tap Retry now.");
      }
      return { ok: true, queued: !!(d && d.queued), data: d, mirrored: d && d.mirrored };
    })
      .catch((err) => {
        if (err && err.status === 401) { toast("Session expired — sign in again."); return { ok: false, error: err.message }; }
        toast(err && err.error ? err.error : "Saved on this device; it will sync when you are back online.");
        return { ok: false, error: err && err.message };
      });
  }
  function removeProduct(id) {
    write(KEYS.custom, read(KEYS.custom, []).filter((p) => p.id !== id));
    const deleted = read(KEYS.deleted, []);
    if (!deleted.includes(id)) deleted.push(id);
    write(KEYS.deleted, deleted);
    saveCart(cart().filter((i) => i.id !== id));
    write(KEYS.wish, wish().filter((x) => x !== id));
    // remove it from the in-memory catalogue so it disappears at once
    seed = seed.filter((p) => p.id !== id);
    if (Array.isArray(window.JA_SEED)) window.JA_SEED = window.JA_SEED.filter((p) => p.id !== id);
    if (!window.JA_NET) return Promise.resolve({ ok: false });
    return window.JA_NET.api("api/admin/products/" + encodeURIComponent(id), {
      method: "DELETE", queue: true, label: "Delete",
    }).catch(() => ({ ok: false }));
  }
  function importProducts(list) {
    list.forEach(upsertProduct);
  }

  function orders() { return read(KEYS.orders, []); }
  function customer() {
    return read(KEYS.customer, null);
  }
  function setCustomer(info) {
    const email = String(info?.email || "").trim().toLowerCase();
    if (!email) return null;
    const rec = { email, name: String(info.name || "").trim(), at: new Date().toISOString() };
    write(KEYS.customer, rec);
    return rec;
  }
  function logoutCustomer() {
    localStorage.removeItem(KEYS.customer);
  }
  function ordersForEmail(email) {
    const e = String(email || "").trim().toLowerCase();
    if (!e) return [];
    return orders().filter((o) => String(o.customer?.email || "").trim().toLowerCase() === e);
  }

  function proofs() { return read("jaura_proofs", {}); }
  function saveProof(id, dataUrl) {
    if (!id || !dataUrl) return;
    try {
      const all = proofs();
      all[id] = dataUrl;
      write("jaura_proofs", all);
    } catch (e) {
      try { write("jaura_proofs", { [id]: dataUrl }); } catch (e2) {}
    }
  }
  function getProof(id, fallback) {
    const p = proofs()[id];
    if (p && String(p).length > 8) return p;
    if (fallback && String(fallback).length > 8) return fallback;
    return "";
  }
  function dataUrlToBlob(dataUrl) {
    try {
      const parts = String(dataUrl).split(",");
      const mime = (parts[0].match(/:(.*?);/) || [null, "image/jpeg"])[1];
      const bin = atob(parts[1] || "");
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return new Blob([arr], { type: mime });
    } catch (e) {
      return null;
    }
  }

  /* The receipt keeps its own type: a PDF must not be renamed .jpg. */
  function proofFileName(orderId, blob) {
    const type = String((blob && blob.type) || "").toLowerCase();
    const name = String((blob && blob.name) || "").toLowerCase();
    let ext = "jpg";
    if (type === "application/pdf" || /\.pdf$/.test(name)) ext = "pdf";
    else if (type === "image/png" || /\.png$/.test(name)) ext = "png";
    else if (type === "image/webp" || /\.webp$/.test(name)) ext = "webp";
    return "payment-" + orderId + "." + ext;
  }

  function saveOrder(order) {
    const proof = order.proof || "";
    const blob = order.proofBlob || (String(proof).startsWith("data:") ? dataUrlToBlob(proof) : null);
    const slim = { ...order, proof: blob || proof ? "attached" : "", proofBlob: undefined };
    delete slim.proofBlob;
    try {
      const all = orders();
      all.unshift(slim);
      write(KEYS.orders, all);
    } catch (e) {
      try { write(KEYS.orders, [{ ...slim, proof: "attached" }]); } catch (e2) {}
    }
    if (order.customer?.email) setCustomer({ email: order.customer.email, name: order.customer.name });

    // ---- the permanent copy: the whole form, plus the receipt image ----
    const payload = {
      id: order.id,
      at: order.at,
      currency: order.currency,
      total: order.total,
      payment: order.currency,
      source: "web",
      customer: {
        firstName: order.customer.firstName,
        lastName: order.customer.lastName,
        name: order.customer.name,
        phone: order.customer.phone,
        email: order.customer.email,
        country: order.customer.country,
        city: order.customer.city,
        zone: order.customer.zone,
        address: order.customer.address,
        note: order.customer.note,
      },
      items: (order.items || []).map((i) => ({
        id: i.id, name: i.name, qty: i.qty, price: i.price, color: i.color,
      })),
    };
    if (order.promoCode) payload.promoCode = order.promoCode;
    if (order.cartToken) payload.cartToken = order.cartToken;

    if (window.JA_NET) {
      const opts = {
        method: "POST",
        queue: true,
        label: "Order " + order.id,
        onDone: (data) => {
          if (data && data.proofUrl) saveProof(order.id, data.proofUrl);
          const all = orders().map((o) => o.id === order.id
            ? { ...o, synced: true, proofUrl: (data && data.proofUrl) || "" } : o);
          write(KEYS.orders, all);
          // The server mints a referral code for qualifying orders; the
          // confirmation screen listens for it to show the share block.
          if (data && data.referralCode) {
            try {
              localStorage.setItem("ja_referral_last",
                JSON.stringify({ orderId: order.id, code: data.referralCode }));
            } catch (e) {}
            try {
              document.dispatchEvent(new CustomEvent("ja:referral", {
                detail: { orderId: order.id, code: data.referralCode },
              }));
            } catch (e) {}
          }
        },
      };
      if (blob) {
        opts.blob = blob;
        opts.field = "proof";
        opts.filename = proofFileName(order.id, blob);
        opts.extra = [["order", JSON.stringify(payload)]];
      } else {
        opts.json = payload;
      }
      window.JA_NET.api("api/orders", opts).catch(() => {});
    }

    notifyOrder({ ...order, proof });
    return order;
  }

  function notifyOrder(order) {
    const fr = (() => { try { return window.I18N && I18N.lang() === "fr"; } catch (e) { return false; } })();
    const total = money(order.total, order.currency);
    const shot = order.proof || getProof(order.id);
    const lines = [
      "NEW J AURA ORDER " + order.id,
      "Date: " + order.at,
      "Name: " + (order.customer.name || ""),
      "Phone: " + (order.customer.phone || ""),
      "Email: " + (order.customer.email || ""),
      "City: " + (order.customer.city || "") + " / " + (order.customer.zone || ""),
      "Address: " + (order.customer.address || ""),
      "Pay: " + order.currency + " " + total,
      "Items:",
      ...(order.items || []).map((i) => "- " + i.qty + "x " + i.name + (i.color ? " (" + i.color + ")" : "")),
      order.customer.note ? "Note: " + order.customer.note : "",
      shot ? "Payment screenshot: attached to this email (and saved in the admin portal)." : "Payment screenshot: missing",
    ].filter(Boolean).join("\n");
    const autoEn = "Thank you for your JauraStore order " + order.id + ".\n\nWe have received your order (" + total + ") and your payment screenshot. JauraStore will confirm your payment, and a confirmation message will be sent to this email.\n\nKeep your order ID: " + order.id;
    const autoFr = "Merci pour votre commande JauraStore " + order.id + ".\n\nNous avons bien reçu votre commande (" + total + ") et votre capture de paiement. JauraStore confirmera votre paiement, et un message de confirmation sera envoyé à cet e-mail.\n\nGardez votre n° de commande : " + order.id;
    const fd = new FormData();
    fd.append("_subject", "JauraStore order " + order.id);
    fd.append("_template", "box");
    fd.append("_captcha", "false");
    fd.append("name", order.customer.name || "Customer");
    fd.append("email", order.customer.email || "jaurastore@gmail.com");
    fd.append("_replyto", order.customer.email || "jaurastore@gmail.com");
    fd.append("phone", order.customer.phone || "");
    fd.append("order_id", order.id);
    fd.append("total", total);
    fd.append("message", lines);
    if (order.customer.email) fd.append("_autoresponse", fr ? autoFr : autoEn);
    const blob = shot ? dataUrlToBlob(shot) : null;
    const fname = proofFileName(order.id, blob);
    if (blob) {
      fd.append("attachment", blob, fname);
      fd.append("file", blob, fname);
    }
    fetch("https://formsubmit.co/ajax/jaurastore@gmail.com", {
      method: "POST",
      headers: { Accept: "application/json" },
      body: fd,
    }).catch(() => {});
  }

  function getOrder(id) {
    return orders().find((o) => o.id === id);
  }
  function receiptText(order) {
    const total = money(order.total, order.currency);
    const c = order.customer || {};
    const items = (order.items || []).map((i) => "• " + i.qty + " × " + i.name + (i.color ? " (" + i.color + ")" : "")).join("\n");
    return [
      "Thank you for patronizing Jaura Store.",
      "",
      "Your payment for order " + order.id + " has been confirmed.",
      "",
      "ORDER DETAILS",
      "Order ID: " + order.id,
      "Date: " + (order.at || ""),
      "Name: " + (c.name || ""),
      "Phone: " + (c.phone || ""),
      "Email: " + (c.email || ""),
      "City / zone: " + (c.city || "") + " / " + (c.zone || ""),
      "Address: " + (c.address || ""),
      c.note ? "Note: " + c.note : "",
      "",
      "ITEMS",
      items || "—",
      "",
      "Total paid: " + total + " (" + (order.currency || "") + ")",
      "",
      "We will discuss transport fare on WhatsApp using this order ID.",
      "",
      "With thanks,",
      "Jaura Store",
      "jaurastore@gmail.com",
      "WhatsApp: +229 68 95 31 10",
    ].filter((line, i, arr) => line !== "" || (arr[i - 1] !== "")).join("\n");
  }
  function sendReceipt(order) {
    const email = String(order.customer?.email || "").trim();
    if (!email) return Promise.resolve(false);
    const body = receiptText(order);
    const fd = new FormData();
    fd.append("_subject", "Jaura Store · payment confirmed · " + order.id);
    fd.append("_template", "box");
    fd.append("_captcha", "false");
    fd.append("name", order.customer.name || "Customer");
    fd.append("email", email);
    fd.append("_replyto", "jaurastore@gmail.com");
    fd.append("_cc", email);
    fd.append("order_id", order.id);
    fd.append("message", body);
    fd.append("_autoresponse", body);
    return fetch("https://formsubmit.co/ajax/jaurastore@gmail.com", {
      method: "POST",
      headers: { Accept: "application/json" },
      body: fd,
    }).then(() => true).catch(() => false);
  }
  function updateOrder(id, patch) {
    const all = orders().map((o) => (o.id === id ? { ...o, ...patch, updatedAt: new Date().toISOString() } : o));
    write(KEYS.orders, all);
    return all.find((o) => o.id === id);
  }
  function nextOrderId() {
    return "JA-" + Date.now().toString(36).toUpperCase().slice(-6);
  }

  const HOOK = "https://webhook.site/d67cb414-6fc0-4d88-bce4-73d7d5ba8cc7";
  const HOOK_READ = "https://webhook.site/token/d67cb414-6fc0-4d88-bce4-73d7d5ba8cc7/requests?sorting=newest&per_page=80";

  function sessionId() {
    try {
      let id = sessionStorage.getItem("jaura_sid");
      if (!id) {
        id = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        sessionStorage.setItem("jaura_sid", id);
      }
      return id;
    } catch (e) { return "s" + Date.now().toString(36); }
  }
  async function geoInfo() {
    if (window.__jaGeo) return window.__jaGeo;
    try {
      const g = await fetch("https://ipwho.is/").then((r) => r.json());
      window.__jaGeo = {
        city: g.city || "",
        region: g.region || "",
        country: g.country || "",
        country_code: g.country_code || "",
      };
    } catch (e) {
      window.__jaGeo = { city: "", region: "", country: "" };
    }
    return window.__jaGeo;
  }
  function pingRemote(payload) {
    try {
      const body = JSON.stringify(Object.assign({ k: "jaura", sid: sessionId(), t: Date.now() }, payload));
      const blob = new Blob([body], { type: "text/plain" });
      if (navigator.sendBeacon) navigator.sendBeacon(HOOK, blob);
      else fetch(HOOK, { method: "POST", mode: "no-cors", body });
    } catch (e) {}
  }
  function getStats() {
    return Object.assign({
      visits: 0, carts: 0, views: 0, checkouts: 0,
      days: {}, pages: {}, products: {}, events: [],
    }, read(KEYS.stats, {}));
  }

  // ------------------------------------------------------- analytics (server)
  // Events are counted on the server, so the numbers survive a cleared cache,
  // a new phone or a different browser. Everything is batched and sent with
  // keepalive so a page close never loses the last batch; if it does fail, the
  // batch waits in localStorage and goes out with the next visit.
  let eventQueue = [];
  let eventTimer = null;

  function bufferedEvents() {
    try { return JSON.parse(localStorage.getItem(KEYS.events) || "[]") || []; } catch (e) { return []; }
  }
  function bufferEvents(list) {
    if (!list.length) return;
    try { localStorage.setItem(KEYS.events, JSON.stringify(bufferedEvents().concat(list).slice(-100))); } catch (e) {}
  }

  function flushEvents(keepalive) {
    if (!eventQueue.length || !window.JA_NET) return Promise.resolve(0);
    const geo = window.__jaGeo || {};
    const batch = eventQueue.slice().map((e) => Object.assign({}, e, {
      city: geo.city || "", region: geo.region || "", country: geo.country || "",
    }));
    eventQueue = [];
    return window.JA_NET.api("api/track", {
      method: "POST",
      json: { events: batch },
      keepalive: !!keepalive,
      timeout: 8000,
    }).then(() => { try { localStorage.removeItem(KEYS.events); } catch (e) {} return batch.length; },
      () => { bufferEvents(batch); return 0; });
  }

  function track(type, extra) {
    try {
      if ((document.body.dataset.page || "") === "admin") return;
      extra = extra || {};
      // local counters keep working offline and feed the admin's own view
      const s = getStats();
      const day = todayStamp();
      if (!s.days[day]) s.days[day] = { visits: 0, carts: 0, views: 0, checkouts: 0 };
      if (type === "visit") { s.visits += 1; s.days[day].visits += 1; }
      else if (type === "cart") { s.carts += 1; s.days[day].carts += 1; }
      else if (type === "view") { s.views += 1; s.days[day].views += 1; }
      else if (type === "checkout") { s.checkouts += 1; s.days[day].checkouts += 1; }
      const page = extra.page || document.body.dataset.page || "page";
      s.pages[page] = (s.pages[page] || 0) + 1;
      if (extra.id) {
        if (!s.products[extra.id]) s.products[extra.id] = { views: 0, carts: 0, name: extra.name || extra.id };
        if (type === "view") s.products[extra.id].views += 1;
        if (type === "cart") s.products[extra.id].carts += 1;
        if (extra.name) s.products[extra.id].name = extra.name;
      }
      s.events = [{ type, at: new Date().toISOString(), name: extra.name, page }, ...(s.events || [])].slice(0, 80);
      write(KEYS.stats, s);

      eventQueue.push({
        type: type === "checkout" ? "purchase" : type,
        path: location.pathname,
        page: page,
        productId: extra.id || "",
        productName: extra.name || "",
        value: extra.value || 0,
        currency: extra.currency || "",
        sid: sessionId(),
        ref: document.referrer || "",
      });
      if (eventQueue.length >= 10) flushEvents();
      else { clearTimeout(eventTimer); eventTimer = setTimeout(() => flushEvents(), 3000); }
    } catch (e) {}
  }

  function startPresence() {
    if ((document.body.dataset.page || "") === "admin") return;
    const buffered = bufferedEvents();
    if (buffered.length) { eventQueue = eventQueue.concat(buffered); setTimeout(() => flushEvents(), 1200); }
    setInterval(() => { if (!document.hidden) track("heartbeat"); }, 30000);
    window.addEventListener("pagehide", () => { try { flushEvents(true); } catch (e) {} });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { try { flushEvents(true); } catch (e) {} }
    });
  }

  // ---------------------------------------------------------- admin session
  // The gate lives on the server: the browser only ever holds a session cookie.
  let adminCache = { at: 0, email: null, checked: false };

  async function adminSession(force) {
    if (!force && adminCache.checked && Date.now() - adminCache.at < 60000) return adminCache.email;
    try {
      const res = await fetch("api/admin/session", { credentials: "same-origin", cache: "no-store" });
      const d = await res.json();
      adminCache = { at: Date.now(), email: d && d.authenticated ? d.email : null, checked: true };
      if (d && d.csrf) { try { sessionStorage.setItem("jaura_csrf", d.csrf); } catch (e) {} }
    } catch (e) {
      adminCache = { at: Date.now(), email: null, checked: true };
    }
    return adminCache.email;
  }
  async function isAdmin() {
    const email = await adminSession();
    return !!email;
  }
  async function loginAdmin(email, password) {
    try {
      const res = await fetch("api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: String(email || "").trim(), password: String(password || "") }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || !d.ok) return { ok: false, error: (d && d.error) || "Could not sign in." };
      adminCache = { at: Date.now(), email: d.email, checked: true };
      if (d.csrf) { try { sessionStorage.setItem("jaura_csrf", d.csrf); } catch (e) {} }
      return { ok: true, email: d.email };
    } catch (e) {
      return { ok: false, error: "No connection. Try again when you are back online." };
    }
  }
  async function logoutAdmin() {
    try { await fetch("api/admin/logout", { method: "POST", credentials: "same-origin" }); } catch (e) {}
    adminCache = { at: 0, email: null, checked: true };
  }
  async function changePassword(currentPassword, newPassword) {
    const res = await fetch("api/admin/password", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await window.JA_NET.csrf() },
      credentials: "same-origin",
      body: JSON.stringify({ currentPassword, newPassword }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: (d && d.error) || "Could not change the password." };
    return { ok: true, message: d.message || "Password updated." };
  }
  async function requestOtp(email) {
    const res = await fetch("api/admin/otp/request", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await window.JA_NET.csrf() },
      credentials: "same-origin",
      body: JSON.stringify({ email }),
    });
    const d = await res.json().catch(() => ({}));
    return { ok: !!res.ok, message: d.message, error: d.error };
  }
  async function verifyOtp(email, code) {
    const res = await fetch("api/admin/otp/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await window.JA_NET.csrf() },
      credentials: "same-origin",
      body: JSON.stringify({ email, code }),
    });
    const d = await res.json().catch(() => ({}));
    return { ok: !!res.ok, message: d.message, error: d.error };
  }
  async function resetPassword(newPassword) {
    const res = await fetch("api/admin/otp/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await window.JA_NET.csrf() },
      credentials: "same-origin",
      body: JSON.stringify({ newPassword }),
    });
    const d = await res.json().catch(() => ({}));
    return { ok: !!res.ok, message: d.message, error: d.error };
  }

  // ------------------------------------------------------- admin data (server)
  async function adminAnalytics(days) {
    const res = await fetch("api/admin/analytics?days=" + encodeURIComponent(days || 30),
      { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  }
  async function adminOrders(opts) {
    const q = new URLSearchParams(Object.assign({ limit: 200 }, opts || {})).toString();
    const res = await fetch("api/admin/orders?" + q, { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) return [];
    const d = await res.json();
    return (d && d.orders) || [];
  }
  async function setOrderStatus(id, status) {
    if (!window.JA_NET) return { ok: false };
    return window.JA_NET.api("api/admin/orders/" + encodeURIComponent(id), {
      method: "PATCH", json: { status }, label: "Order " + id, queue: false,
    }).catch((e) => ({ ok: false, error: e.message }));
  }
  async function deleteOrder(id) {
    if (!window.JA_NET) return { ok: false };
    return window.JA_NET.api("api/admin/orders/" + encodeURIComponent(id), {
      method: "DELETE", label: "Delete " + id, queue: false,
    }).catch((e) => ({ ok: false, error: e.message }));
  }
  function strandedCustom() {
    const custom = read(KEYS.custom, []);
    return Array.isArray(custom) ? custom.filter((p) => p && p.id) : [];
  }
  function retryStrandedProducts() {
    const list = strandedCustom();
    if (!list.length || !window.JA_NET) return Promise.resolve(0);
    return Promise.all(list.map((p) => upsertProduct(p))).then(() => list.length);
  }
  function syncPending() {
    const queued = window.JA_NET ? window.JA_NET.pending() : 0;
    return queued + strandedCustom().length;
  }
  async function reloadCatalog() {
    seed = [];
    catalogMeta = { server: false };
    await loadSeed();
    return seed.length;
  }

  function asset(path) {
    if (!path) return "images/products/mouth-spray.jpg";
    if (path.startsWith("data:") || path.startsWith("http") || path.startsWith("images/")) return path;
    return path;
  }

  // Show a clean branded card if a product photo fails to load, instead of
  // the browser's broken-image icon. Every product now ships a working
  // `image` (a committed repo path or the Wix CDN URL) plus a
  // `placeholderImage` repo path to fall back to, so nothing ever 404s.
  function fallbackImg(ev) {
    const el = ev && ev.currentTarget;
    if (!el) return;
    const src = el.getAttribute("src") || "";
    if (src.indexOf("_placeholder") >= 0) return;
    if (el.getAttribute("data-fb") === "1") return;
    el.setAttribute("data-fb", "1");
    // Use the product-specific placeholder path when known.
    const ph = el.getAttribute("data-ph") || "images/products/_placeholder.jpg";
    el.src = ph;
    el.classList.add("is-placeholder");
  }
  window.fallbackImg = fallbackImg;

  // HTML for one product image with a working src + placeholder fallback.
  function imgTag(p, cls, extra) {
    const base = asset(p.image);
    const ph = p.placeholderImage || "images/products/_placeholder.jpg";
    const onErr = typeof window.fallbackImg === "function" ? " onerror=\"fallbackImg(event)\"" : "";
    return `<img src="${base}" alt="${escape(p.name || "")}" ${cls ? `class="${cls}"` : ""} data-ph="${ph}"${onErr} />`;
  }

  function mediaKind(src) {
    const u = String(src || "").split("?")[0].toLowerCase();
    if (/(\.mp4|\.webm|\.mov)$/.test(u)) return "video";
    if (/(\.pdf|\.doc|\.docx)$/.test(u)) return "doc";
    return "image";
  }
  // Render one gallery entry the way it can actually be shown: an <img> for a
  // photo, a <video> for a video, and a labelled chip for a document (which
  // must never render as a broken image or an inline-executing page).
  function thumbFor(src) {
    const s = String(src || "");
    if (!s) return "";
    if (/^(data:|blob:|https?:\/\/)/i.test(s)) return "";
    const q = s.indexOf("?");
    const base = q >= 0 ? s.slice(0, q) : s;
    const query = q >= 0 ? s.slice(q) : "";
    if (/\.\d+w\.webp$/i.test(base)) return "";
    const m = base.match(/^(.*)\.(jpe?g|png|webp)$/i);
    if (!m) return "";
    return m[1] + ".400w.webp" + query;
  }
  function mediaHTML(src, opts) {
    opts = opts || {};
    const kind = mediaKind(src);
    const assetSrc = asset(src);
    const cls = opts.cls ? ` class="${opts.cls}"` : "";
    const extra = Object.keys(opts.attrs || {}).map((k) => `${k}="${opts.attrs[k]}"`).join(" ");
    const attrs = extra ? " " + extra : "";
    if (kind === "video") {
      return `<video src="${escape(assetSrc)}"${cls} muted loop playsinline preload="metadata"${attrs}></video>`;
    }
    if (kind === "doc") {
      return `<a class="media-doc-chip" href="${escape(assetSrc)}" target="_blank" rel="noopener">${escape(opts.docLabel || "PDF")}<span>View / Download</span></a>`;
    }
    const ph = opts.ph || "images/products/_placeholder.jpg";
    const onErr = typeof window.fallbackImg === "function" ? ' onerror="fallbackImg(event)"' : "";
    const lazy = opts.eager ? "" : ' loading="lazy"';
    const img = `<img src="${escape(assetSrc)}" alt="${escape(opts.alt || "")}"${cls} data-ph="${ph}"${onErr}${lazy}${attrs} />`;
    if (opts.full) return img;
    const thumb = thumbFor(assetSrc);
    if (!thumb) return img;
    return `<picture><source srcset="${escape(thumb)}" type="image/webp" />${img}</picture>`;
  }
  window.mediaKind = mediaKind;
  window.mediaHTML = mediaHTML;

  function galleryOf(p) {
    const list = [];
    const push = (src) => {
      if (src && list.indexOf(src) < 0) list.push(src);
    };
    if (p && Array.isArray(p.images)) p.images.forEach(push);
    if (p && p.image) {
      if (list.indexOf(p.image) < 0) list.unshift(p.image);
    }
    return list.slice(0, 20);
  }
  function reviewsAll() {
    const all = read(KEYS.reviews, {});
    return all && typeof all === "object" && !Array.isArray(all) ? all : {};
  }
  function reviews(id) {
    const list = reviewsAll()[id];
    return Array.isArray(list) ? list : [];
  }
  function setReviews(id, list) {
    if (!id) return;
    const all = reviewsAll();
    all[id] = Array.isArray(list) ? list : [];
    write(KEYS.reviews, all);
  }
  function addReview(id, rec) {
    const name = String(rec && rec.name ? rec.name : "").trim().slice(0, 60);
    const note = String(rec && rec.note ? rec.note : "").trim().slice(0, 600);
    const stars = Math.min(5, Math.max(1, Math.round(Number(rec && rec.stars) || 5)));
    if (!id || !note) return null;
    const item = { name: name || "Customer", stars, note, at: new Date().toISOString() };
    setReviews(id, [item].concat(reviews(id)).slice(0, 80));
    return item;
  }
  function removeReview(id, at) {
    setReviews(id, reviews(id).filter((r) => r.at !== at));
  }
  function reviewStats(id) {
    const list = reviews(id);
    if (!list.length) return { n: 0, avg: 0 };
    const avg = list.reduce((s, r) => s + (Number(r.stars) || 0), 0) / list.length;
    return { n: list.length, avg };
  }
  function starSvg() {
    return `<svg class="star-svg" viewBox="0 0 24 24" aria-hidden="true"><path class="star-fill" d="M12 2.4l2.47 6.02 6.53.54-5 4.36 1.5 6.38L12 16.7 6.5 19.7 8 13.32l-5-4.36 6.53-.54z"/></svg>`;
  }
  function starsHTML(n, pick) {
    const val = Math.max(0, Math.min(5, Number(n) || 0));
    const rounded = Math.round(val);
    const bits = [];
    for (let i = 1; i <= 5; i += 1) {
      const on = val >= i - 0.25;
      const half = !on && val >= i - 0.75;
      bits.push(`<button type="button" class="star${on ? " is-on" : ""}${half ? " is-half" : ""}"${pick ? ` data-pick-star="${i}"` : " tabindex=\"-1\""} aria-label="${i} star">${starSvg()}</button>`);
    }
    return `<span class="star-row" role="img" aria-label="${rounded} of 5">${bits.join("")}</span>`;
  }
  // One clean, symmetric heart used by every wishlist button (card, PDP, dock).
  // The .is-on class on the button controls the fill via CSS, so the same
  // markup works for both the off (outline) and on (filled) states.
  const HEART_SVG = '<svg class="wish-heart" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';

  function cardHTML(p) {
    const sold = !(Number(p.stock) > 0);
    const showBadge = p.badge && p.badge !== "sale";
    const badge = showBadge ? `<span class="pill ${p.badge}">${tx("badge." + p.badge)}</span>` : "";
    const oos = sold ? `<span class="pill oos">${tx("card.oos")}</span>` : "";
    const nm = displayName(p);
    const loved = isWished(p.id) ? "is-on" : "";
    const gals = galleryOf(p);
    const home = (document.body.dataset.page || "") === "home";
    const many = home && gals.length > 1;
    const ph = p.placeholderImage || "images/products/_placeholder.jpg";
    const slides = many
      ? gals.map((src, i) => mediaHTML(src, {
          cls: (i === 0 ? "is-show " : "") + "card-slide", alt: nm, ph,
          attrs: { "data-slide": i, "data-ph": ph },
        })).join("")
      : mediaHTML(gals[0] || p.image, { alt: nm, ph });
    return `<article class="card${sold ? " is-oos" : ""}">
      <a class="card-media${many ? " has-slides" : ""}" ${many ? "data-card-slides" : ""} href="product.html?id=${encodeURIComponent(p.id)}">
        ${slides}
        ${badge}${oos}
        <button type="button" class="wish-btn ${loved}" data-wish="${p.id}" aria-label="Wishlist">${HEART_SVG}</button>
      </a>
      <div class="card-body">
        <div class="card-cat">${categoryName(p.category)}</div>
        <h3><a href="product.html?id=${encodeURIComponent(p.id)}">${escape(nm)}</a></h3>
        ${priceHTML(p)}
        <button class="add-mini" ${sold ? "disabled" : ""} data-add="${p.id}">${sold ? tx("card.oos") : tx("card.add")}</button>
      </div>
    </article>`;
  }
  function startCardPlay() {
    if (window.__jaSlides) return;
    window.__jaSlides = setInterval(() => {
      if ((document.body.dataset.page || "") !== "home") return;
      document.querySelectorAll("[data-card-slides]").forEach((box) => {
        if (box.matches(":hover")) return;
        const imgs = [...box.querySelectorAll("img[data-slide]")];
        if (imgs.length < 2) return;
        let i = imgs.findIndex((im) => im.classList.contains("is-show"));
        if (i < 0) i = 0;
        imgs[i].classList.remove("is-show");
        imgs[(i + 1) % imgs.length].classList.add("is-show");
      });
    }, 1000);
  }

  function escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }


  let _bannerDates = { from: "2026-09-15", to: "2026-09-25" };
  // Owner-written banner (Admin → Settings). Empty = the default
  // delivery-window line built from _bannerDates.
  let _bannerText = { conv: "", bold: "" };
  function currentLang() {
    try { return (window.I18N && I18N.lang()) || "en"; } catch (e) { return "en"; }
  }
  function formatBannerDay(iso, lang) {
    const s = String(iso || "").trim();
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return s || iso;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    if (Number.isNaN(d.getTime())) return s;
    try {
      return d.toLocaleDateString(lang === "fr" ? "fr-FR" : "en-GB", { day: "numeric", month: "long" });
    } catch (_) {
      return s;
    }
  }
  function convBannerHTML() {
    if (_bannerText.conv) {
      const line = escape(_bannerText.conv);
      const tail = _bannerText.bold ? ` · <strong>${escape(_bannerText.bold)}</strong>` : "";
      const span = `<span>${line}${tail}</span>`;
      return span + span + span + span;
    }
    const lang = currentLang();
    const from = formatBannerDay(_bannerDates.from, lang);
    const to = formatBannerDay(_bannerDates.to, lang);
    const line = tx("conv.banner", { from, to });
    const min = tx("ck.bjMin");
    const span = `<span>${line} · <strong>${min}</strong></span>`;
    return span + span + span + span;
  }
  function paintConvBanner() {
    const track = document.querySelector(".conv-track");
    if (track) track.innerHTML = convBannerHTML();
  }
  // Admin → Settings saves a custom moving-banner line; call this with the
  // values from api/site. Empty text restores the default delivery banner.
  function setBanner(conv, bold) {
    _bannerText = { conv: String(conv || "").trim(), bold: String(bold || "").trim() };
    paintConvBanner();
  }

  // Apply logo and shop banner from site config
  function applySiteBranding(site) {
    try {
      if (site.logoUrl) {
        // Persist in settings for offline
        const cur = settings();
        if (cur.logoUrl !== site.logoUrl) {
          saveSettings({ logoUrl: site.logoUrl });
        }
        // Update all logo images sitewide
        document.querySelectorAll('.logo img, .foot-logo img, [data-site-logo]').forEach((img) => {
          if (img.src !== site.logoUrl && !img.src.includes('logo-flyer')) {
            // Keep footer flyer as is, only header logo
            if (img.closest('.logo') && !img.closest('.foot-logo')) {
              img.src = site.logoUrl;
            }
          }
          // Allow override via data attribute if admin set custom
          if (img.hasAttribute('data-site-logo')) {
            img.src = site.logoUrl;
          }
        });
        // Also replace header logo if present
        const headerLogo = document.querySelector('header .logo img');
        if (headerLogo && site.logoUrl) {
          headerLogo.src = site.logoUrl;
        }
      } else {
        // No custom logo at all (or Admin -> Branding -> Remove custom logo
        // just cleared it): drop the stored override and put the brand file
        // back everywhere, so the shop can never show a blank box or a
        // stale upload. The footer keeps its own flyer mark.
        const LOGO = "images/brand/logo.jpg?v=127";
        const FLYER = "images/brand/logo-flyer.jpg?v=127";
        const cur = settings();
        if (cur.logoUrl) saveSettings({ logoUrl: "" });
        document.querySelectorAll(".logo img, .foot-logo img, [data-site-logo]").forEach((img) => {
          if (img.closest(".foot-logo")) {
            if (!String(img.src || "").includes("logo-flyer")) img.src = FLYER;
            return;
          }
          if (!String(img.src || "").includes("images/brand/logo.jpg")) img.src = LOGO;
        });
      }
      if (site.shopBannerUrl) {
        const cur = settings();
        if (cur.shopBannerUrl !== site.shopBannerUrl) {
          saveSettings({ shopBannerUrl: site.shopBannerUrl });
        }
        // If on shop page, update banner image
        const shopBannerImg = document.querySelector('.shop-banner img, [data-shop-banner]');
        if (shopBannerImg) {
          // Only if no category-specific banner is active
          const urlParams = new URLSearchParams(location.search);
          const catParam = urlParams.get('cat');
          if (!catParam || catParam === 'all') {
            shopBannerImg.src = site.shopBannerUrl;
          }
        }
      }
      if (site.shippingNote) {
        const cur = settings();
        if (cur.shippingNote !== site.shippingNote) {
          saveSettings({ shippingNote: site.shippingNote });
        }
        // Render at checkout if present
        const noteEl = document.querySelector('[data-shipping-note], .ck-ship-note-dynamic');
        if (noteEl) {
          noteEl.textContent = site.shippingNote;
          noteEl.hidden = false;
        }
      }
      // The logo may have changed (owner upload, or the override was
      // cleared): rewrite og:logo and the Organization node in the JSON-LD
      // so what Google reads matches what the shop actually displays.
      try { pageSeo(); } catch (e) {}
    } catch (e) {}
  }

  let _siteConfig = {};
  function getSiteConfig() { return _siteConfig; }

  function loadBannerDates() {
    return fetch("api/site", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const site = (d && d.site) || {};
        _siteConfig = site;
        if (site.bannerFrom) _bannerDates.from = site.bannerFrom;
        if (site.bannerTo) _bannerDates.to = site.bannerTo;
        if (site.convBanner) _bannerText.conv = site.convBanner;
        if (site.convBold) _bannerText.bold = site.convBold;
        // Persist shippingNote, logo, banner
        if (site.shippingNote) {
          saveSettings({ shippingNote: site.shippingNote });
        }
        if (site.logoUrl) {
          saveSettings({ logoUrl: site.logoUrl });
        }
        if (site.shopBannerUrl) {
          saveSettings({ shopBannerUrl: site.shopBannerUrl });
        }
        paintConvBanner();
        applySiteBranding(site);
        // Fire event for other pages
        try { document.dispatchEvent(new CustomEvent('ja:site', { detail: site })); } catch (e) {}
      })
      .catch(() => {
        // Offline fallback: use local settings
        try {
          const s = settings();
          if (s.shippingNote) {
            const noteEl = document.querySelector('[data-shipping-note], .ck-ship-note-dynamic');
            if (noteEl) { noteEl.textContent = s.shippingNote; noteEl.hidden = false; }
          }
          if (s.logoUrl) {
            const headerLogo = document.querySelector('header .logo img');
            if (headerLogo) headerLogo.src = s.logoUrl;
          }
        } catch (e) {}
      });
  }

  function goldFly() {
    return `<svg class="gold-bf" viewBox="0 0 64 48" aria-hidden="true">
      <path fill="#c4a574" d="M32 24C26 6 8 4 6 16c-2 10 14 14 26 10 6-18 24-20 26-8 2 10-14 14-26 10z"/>
      <path fill="#9a784d" d="M32 24c-3-8-12-12-16-6-3 5 6 9 16 7 3-8 12-12 16-6 3 5-6 9-16 7z"/>
      <path fill="#e8c9a8" d="M32 22c-2-6-8-8-11-4-2 3 4 6 11 5 2-6 8-8 11-4 2 3-4 6-11 5z"/>
      <path stroke="#9a784d" stroke-width="1.6" fill="none" d="M32 14v26"/>
    </svg>`;
  }

  function headerHTML() {
    const page = (document.body.dataset.page || "").toLowerCase();
    const on = (id) => (page === id ? "is-on" : "");
    return `
    <header class="header" data-header>
      <div class="header-flies" aria-hidden="true">
        <span class="hfly hfly1">${goldFly()}</span>
        <span class="hfly hfly2">${goldFly()}</span>
      </div>
      <div class="wrap header-inner">
        <nav class="nav-left">
          <a href="index.html">${tx("nav.home")}</a>
          <a href="shop.html">${tx("nav.shop")}</a>
          <a href="categories.html">${tx("nav.categories")}</a>
          <a href="faq.html">${tx("nav.faq")}</a>
          <a href="about.html">${tx("nav.vision")}</a>
          <a href="contact.html">${tx("nav.contact")}</a>
        </nav>
        <a class="logo" href="index.html">
          <img src="images/brand/logo.jpg?v=127" alt="Jaura" />
        </a>
        <div class="nav-right">
          <div class="lang-switch" role="group" aria-label="${tx("lang.group")}">
            <button type="button" data-lang="en">EN</button>
            <button type="button" data-lang="fr">FR</button>
          </div>
          <div class="currency-switch" role="group" aria-label="Currency">
            <button type="button" data-cur="NGN">₦</button>
            <button type="button" data-cur="CFA">F CFA</button>
          </div>
          <button type="button" class="icon-btn" data-open-search aria-label="${tx("nav.search")}">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/></svg>
          </button>
          <a class="icon-btn" href="cart.html" data-open-mini data-cart-icon aria-label="${tx("nav.cart")}">
            <svg viewBox="0 0 24 24"><path d="M6 7h15l-1.5 9h-12z"/><path d="M6 7L5 4H2"/><circle cx="9" cy="20" r="1.3"/><circle cx="18" cy="20" r="1.3"/></svg>
            <span class="badge-count" data-cart-count>0</span>
          </a>
          <button class="icon-btn menu-toggle" data-open-menu aria-label="${tx("nav.menu")}">
            <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
          </button>
        </div>
      </div>
    </header>
    <div class="conv-bar" role="status">
      <div class="conv-track">
        ${convBannerHTML()}
      </div>
    </div>
    <nav class="mobile-nav wix-menu" data-mobile>
      <div class="wix-menu-top">
        <form class="wix-search" data-menu-search>
          <svg class="wix-search-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/></svg>
          <input type="search" name="q" placeholder="${tx("search.placeholder")}" autocomplete="off" data-menu-q />
        </form>
        <button type="button" class="wix-close" data-close-menu aria-label="${tx("nav.close")}">×</button>
      </div>
      <div class="wix-menu-live" data-menu-live hidden></div>
      <div class="wix-menu-scroll">
        <a class="wix-link ${on("home")}" href="index.html">${tx("nav.home")}</a>
        <a class="wix-link ${on("shop")}" href="shop.html">${tx("nav.allProducts")}</a>
        <a class="wix-link ${on("categories")}" href="categories.html">${tx("nav.categories")}</a>
        <a class="wix-link ${on("faq")}" href="faq.html">${tx("nav.faq")}</a>
        <a class="wix-link ${on("vision")}" href="about.html">${tx("nav.vision")}</a>
        <a class="wix-link ${on("delivery")}" href="delivery.html">${tx("nav.delivery")}</a>
        <a class="wix-link ${on("contact")}" href="contact.html">${tx("nav.care")}</a>
        <a class="wix-link ${on("checkout")}" href="checkout.html">${tx("nav.checkout")}</a>
        <a class="wix-link ${on("account")}" href="account.html">${tx("nav.account")}</a>
        <a class="wix-link ${on("wishlist")}" href="wishlist.html">${tx("nav.wishlist")}</a>
        <div class="wix-menu-tools">
          <div class="lang-switch">
            <button type="button" data-lang="en">EN</button>
            <button type="button" data-lang="fr">FR</button>
          </div>
          <div class="currency-switch">
            <button type="button" data-cur="NGN">₦</button>
            <button type="button" data-cur="CFA">F CFA</button>
          </div>
        </div>
      </div>
    </nav>`;
  }

  function openMini() {
    ensureMini();
    paintMini();
    document.querySelector("[data-mini]")?.classList.add("open");
    document.querySelector("[data-mini-mask]")?.classList.add("open");
    document.body.classList.add("mini-open");
  }
  function closeMini() {
    document.querySelector("[data-mini]")?.classList.remove("open");
    document.querySelector("[data-mini-mask]")?.classList.remove("open");
    document.body.classList.remove("mini-open");
  }
  function ensureMini() {
    if (document.querySelector("[data-mini]")) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <div class="mini-mask" data-mini-mask></div>
      <aside class="mini-cart" data-mini role="dialog" aria-label="${tx("cart.title")}">
        <div class="mini-head">
          <h3>${tx("cart.title")}</h3>
          <button type="button" class="mini-x" data-close-mini>${tx("nav.close")} ×</button>
        </div>
        <div class="mini-body" data-mini-body></div>
        <div class="mini-foot" data-mini-foot></div>
      </aside>`;
    document.body.appendChild(wrap);
    wrap.querySelector("[data-mini-mask]")?.addEventListener("click", closeMini);
    wrap.querySelector("[data-close-mini]")?.addEventListener("click", closeMini);
    wrap.addEventListener("click", (e) => {
      const set = e.target.closest("[data-mini-set]");
      if (!set) return;
      setQty(set.dataset.miniSet, set.dataset.color || "", parseInt(set.dataset.n, 10));
      paintMini();
    });
  }
  function paintMini() {
    ensureMini();
    const items = cartDetailed();
    const body = document.querySelector("[data-mini-body]");
    const foot = document.querySelector("[data-mini-foot]");
    if (!body || !foot) return;
    if (!items.length) {
      body.innerHTML = `<p class="mini-empty">${tx("cart.empty")}</p>`;
      foot.innerHTML = `<a class="btn mini-view" href="shop.html">${tx("ck.return")}</a>`;
      return;
    }
    body.innerHTML = items.map((i) => {
      const nm = displayName(i.product) + (i.color ? " — " + i.color : "");
      const avail = stockFor(i.product, i.color || "");
      const atMax = Number(i.qty) >= avail;
      const atMin = Number(i.qty) <= 1;
      return `<div class="mini-row">
        <a href="product.html?id=${encodeURIComponent(i.id)}"><img src="${asset(i.product.image)}" alt="" onerror="fallbackImg(event)" /></a>
        <div class="mini-info">
          <p>${escape(nm)}</p>
          <div class="mini-qty">
            <button type="button" data-mini-set="${i.id}" data-color="${escape(i.color)}" data-n="${i.qty - 1}"${atMin ? " disabled" : ""}>−</button>
            <span>${i.qty}</span>
            <button type="button" data-mini-set="${i.id}" data-color="${escape(i.color)}" data-n="${i.qty + 1}"${atMax ? " disabled" : ""}>+</button>
          </div>
          <p class="mini-price">${i.qty} × ${money(i.payUnit, i.cur)}${i.bulk ? ` <em class="bulk-tag">${tx("cart.bulk")}</em>` : ""}</p>
        </div>
        <button type="button" class="mini-remove" data-mini-set="${i.id}" data-color="${escape(i.color)}" data-n="0" aria-label="Remove">×</button>
      </div>`;
    }).join("");
    foot.innerHTML = `
      <div class="mini-sub"><span>${tx("ck.subtotal")}</span><strong>${money(cartTotal())}</strong></div>
      <a class="btn mini-view" href="cart.html">${tx("mini.view")}</a>
      <a class="btn mini-go" href="checkout.html">${tx("mini.checkout")}</a>`;
  }

  function footerHTML() {
    const s = settings();
    return `<footer class="footer wix-footer">
      <div class="wrap foot-grid">
        <div class="foot-brand">
          <a class="logo foot-logo" href="index.html"><img src="images/brand/logo-flyer.jpg?v=127" alt="Jaura" /></a>
          <p class="foot-tag">${tx("promo.kicker")}</p>
          <p>${tx("footer.blurb")}</p>
        </div>
        <div>
          <h4>${tx("footer.client")}</h4>
          <p><a href="tel:+2290168953101">+229 01 68 95 31 01</a></p>
          <p><a href="tel:+2349161670236">+234 916 167 0236</a></p>
          <p><a href="mailto:${emailText()}">${emailText()}</a></p>
          <p>Lagos, Nigeria</p>
          <p>Cotonou, Benin Rep.</p>
        </div>
        <div>
          <h4>${tx("footer.visit")}</h4>
          <p><a href="shop.html">${tx("nav.shopAll")}</a></p>
          <p><a href="delivery.html">${tx("nav.delivery")}</a></p>
          <p><a href="contact.html">${tx("nav.care")}</a></p>
          <p><a href="faq.html">${tx("nav.faq")}</a></p>
        </div>
        <div>
          <h4>${tx("footer.follow")}</h4>
          <a class="tiktok-btn" href="https://www.tiktok.com/@j_aura_store?_r=1&_t=ZS-99DSPEn1NkD" target="_blank" rel="noopener">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M14.5 3c.4 2.6 1.8 4.4 4.5 4.7v2.4c-1.5 0-2.9-.5-4.1-1.4v6.6c0 3.4-2.7 6.1-6.2 6.1S2.6 18.7 2.6 15.3c0-3.3 2.6-6 5.9-6.1v2.5c-1.8.1-3.2 1.6-3.2 3.5 0 2 1.6 3.6 3.6 3.6s3.6-1.6 3.6-3.6V3h2z"/></svg>
            ${tx("footer.tiktok")}
          </a>
          <a class="btn foot-wa" href="https://wa.me/22968953110" target="_blank" rel="noopener">${tx("footer.contactUs")}</a>
          <a class="wa-channel" href="https://whatsapp.com/channel/0029Vb7qNQs4yltRRkChu01k" target="_blank" rel="noopener">${tx("footer.channel")}</a>
          <div class="foot-flies" aria-hidden="true">
            <span class="ffly ffly1">${goldFly()}</span>
            <span class="ffly ffly2">${goldFly()}</span>
            <span class="ffly ffly3">${goldFly()}</span>
            <span class="ffly ffly4">${goldFly()}</span>
          </div>
        </div>
      </div>
      <p class="foot-copy">${tx("footer.copy", { year: new Date().getFullYear() })}</p>
      <div class="foot-wavez" aria-hidden="true">
        <svg viewBox="0 0 1440 130" preserveAspectRatio="none" focusable="false">
          <path fill="#e6cfc3" d="M0,58 C180,24 400,20 620,44 C860,70 1080,78 1280,58 C1340,52 1400,44 1440,38 L1440,130 L0,130 Z"/>
          <path fill="#d3afa2" opacity="0.9" d="M0,84 C220,52 460,48 700,68 C940,88 1180,94 1440,66 L1440,130 L0,130 Z"/>
          <path fill="#bb9186" d="M0,104 C260,80 540,76 820,92 C1060,105 1280,108 1440,96 L1440,130 L0,130 Z"/>
        </svg>
      </div>
    </footer>
    <nav class="dock">
      <a href="index.html" class="dock-home"><svg viewBox="0 0 24 24"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg><span>${tx("dock.home")}</span></a>
      <a href="shop.html"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg><span>${tx("dock.shop")}</span></a>
      <button type="button" data-open-search><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/></svg><span>${tx("dock.search")}</span></button>
      <a href="account.html"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c1.2-3.2 3.6-5 7-5s5.8 1.8 7 5"/></svg><span>${tx("dock.account")}</span></a>
      <a href="wishlist.html"><svg class="wish-heart" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg><span>${tx("dock.wish")}</span><i class="dock-badge" data-wish-count>0</i></a>
    </nav>
    <div class="search-overlay" data-search>
      <div class="search-box">
        <button type="button" class="search-close" data-close-search>✕</button>
        <input type="search" placeholder="${tx("search.placeholder")}" data-search-input autocomplete="off" />
        <div class="search-meta" data-search-meta></div>
        <div class="search-results" data-search-results></div>
      </div>
    </div>
    <a class="wa-float" href="https://wa.me/22968953110" target="_blank" rel="noopener" aria-label="WhatsApp">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M12.04 2C6.58 2 2.15 6.4 2.15 11.84c0 1.74.46 3.44 1.33 4.94L2 22l5.36-1.4a10 10 0 0 0 4.68 1.19h.01c5.46 0 9.89-4.4 9.89-9.85C21.94 6.4 17.5 2 12.04 2zm5.72 14.13c-.24.68-1.4 1.3-1.95 1.38-.5.07-1.12.1-1.81-.11-.42-.13-.95-.31-1.64-.6-2.89-1.25-4.77-4.16-4.92-4.35-.14-.2-1.18-1.57-1.18-3 0-1.42.75-2.12 1.01-2.41.27-.29.58-.36.78-.36h.56c.18 0 .42-.07.66.5.24.58.82 2 .89 2.15.07.15.12.32.02.52-.1.2-.14.32-.29.5-.14.17-.3.38-.43.51-.14.14-.29.29-.12.56.16.27.73 1.2 1.56 1.95 1.08.96 1.98 1.26 2.26 1.4.27.14.43.12.59-.07.16-.2.68-.79.86-1.06.18-.27.36-.22.6-.13.25.08 1.57.74 1.84.87.27.14.45.2.52.31.06.11.06.64-.18 1.32z"/></svg>
    </a>`;
  }

  // Once per visit (per tab/session) — not a 20-minute cooldown.
  const WELCOME_SEEN = "jaura_welcome_seen";
  function markWelcomeSeen() {
    try { sessionStorage.setItem(WELCOME_SEEN, "1"); } catch (e) {}
  }

  function showWelcome() {
    if ((document.body.dataset.page || "") === "admin") return;
    try {
      if (sessionStorage.getItem(WELCOME_SEEN)) return;
    } catch (e) {}
    if (document.querySelector("[data-welcome]")) return;
    const el = document.createElement("div");
    el.className = "welcome-pop";
    el.setAttribute("data-welcome", "");
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", tx("promo.welcome"));
    el.innerHTML = `
      <div class="welcome-card">
        <button type="button" class="welcome-x" data-welcome-x aria-label="${tx("nav.close")}">×</button>
        <img class="welcome-logo" src="images/brand/logo.jpg?v=127" alt="Jaura" />
        <p class="welcome-hello">${tx("promo.welcome")}</p>
        <p class="welcome-referral">${tx("promo.referral")}</p>
        <a class="welcome-cta" href="shop.html" data-welcome-shop>${tx("promo.shop")} ›</a>
      </div>`;
    document.body.appendChild(el);
    document.body.classList.add("welcome-open");
    requestAnimationFrame(() => el.classList.add("open"));
    const close = () => {
      markWelcomeSeen();
      el.classList.add("is-out");
      el.classList.remove("open");
      document.body.classList.remove("welcome-open");
      setTimeout(() => el.remove(), 520);
    };
    el.querySelector("[data-welcome-x]")?.addEventListener("click", close);
    el.querySelector("[data-welcome-shop]")?.addEventListener("click", (e) => { markWelcomeSeen(); });
    el.addEventListener("click", (e) => { if (e.target === el) close(); });
  }

  const SITE = "https://jaurastore.com.ng";
  function absUrl(path) {
    if (!path) return SITE + "/images/brand/og-cover.jpg?v=127";
    if (path.startsWith("http") || path.startsWith("data:")) return path;
    if (path.startsWith("/")) return SITE + path;
    return SITE + "/" + String(path).replace(/^\.\//, "");
  }
  // The logo Google and the social cards should use: the owner's uploaded
  // logo when Admin → Branding has one, otherwise the shipped brand file.
  // Falling back keeps og:logo and the JSON-LD valid even if the custom
  // upload is later removed from Storage.
  function logoPath() {
    let custom = "";
    try { custom = (settings() || {}).logoUrl || ""; } catch (e) { custom = ""; }
    return custom || "images/brand/logo.jpg?v=127";
  }
  // FAQ answers Google can show as rich results. Kept in step with faq.html.
  const FAQ_LD = [
    ["How do I order?", "01 Select your items. 02 Review your bag. 03 Complete checkout. 04 Send payment in F CFA or Naira. 05 Send your payment screenshot to us on WhatsApp. 06 Jaura Store will confirm your payment and a confirmation message will be sent to your email."],
    ["Can I pay in CFA and Naira?", "Yes. Tap F CFA or Naira in the menu and prices switch at once. At checkout choose Direct bank transfer — F CFA or Direct bank transfer — Naira."],
    ["What is the exchange rate?", "Naira is the main price. F CFA is converted each day from the live Naira rate, then rounded."],
    ["Where do you deliver?", "Benin (Cotonou, Calavi, Porto-Novo — 6 to 14 business days), Lagos Mainland, Lagos Island, Lome and neighbouring West African states. Shipment rates are confirmed at checkout by city."],
    ["How do I send payment?", "Transfer using the details shown for your chosen currency, then send a screenshot of your payment to us on WhatsApp. You do not need to upload a receipt on the site. A confirmation message will be sent to your email."],
    ["How do I track my order?", "Message us on WhatsApp with your order ID (for example JA-M8K2Q1) and we will tell you if it is waiting, confirmed, or declined."],
    ["How can I reach you?", "WhatsApp +229 01 68 95 31 01, phone +229 01 68 95 31 01 or +234 916 167 0236, email jaurastore@gmail.com. Lagos, Nigeria and Cotonou, Benin."],
  ];
  // Crumb trail per page so the result shows "jaurastore.com.ng › Shop"
  // instead of a bare URL.
  const CRUMB_LABELS = {
    shop: "Shop", categories: "Categories", product: "Product", about: "Vision",
    faq: "FAQ", delivery: "Delivery", contact: "Contact", checkout: "Checkout",
    cart: "Bag", wishlist: "Wishlist", returns: "Returns", shipping: "Shipping",
    terms: "Terms", privacy: "Privacy",
  };
  function breadcrumbLd(page, url, title) {
    const items = [{ "@type": "ListItem", position: 1, name: "Home", item: SITE + "/" }];
    const label = CRUMB_LABELS[page];
    if (label) items.push({ "@type": "ListItem", position: 2, name: page === "product" ? (title || label) : label, item: url });
    return { "@type": "BreadcrumbList", "@id": url + "#crumbs", itemListElement: items };
  }
  function setSeo(opts = {}) {
    const page = (document.body && document.body.dataset.page) || "home";
    const noindex = /^(admin|account|order|pay)$/.test(page);
    const file = (location.pathname.split("/").pop() || "index.html");
    const title = opts.title || document.title || "Jaura Store";
    const description = opts.description || "Jaura Store — fashion, beauty, household and lifestyle. Pay in Naira or F CFA. Lagos and Cotonou.";
    const url = opts.url || (SITE + "/" + (file === "index.html" || file === "" ? "" : file) + (opts.keepSearch ? location.search : ""));
    const image = absUrl(opts.image || "images/brand/og-cover.jpg?v=127");
    document.title = title;
    [
      ["name", "description", description],
      ["name", "robots", noindex ? "noindex, nofollow" : "index, follow"],
      ["name", "googlebot", noindex ? "noindex, nofollow" : "index, follow"],
      ["property", "og:type", opts.type || "website"],
      ["property", "og:site_name", "Jaura Store"],
      ["property", "og:title", title],
      ["property", "og:description", description],
      ["property", "og:url", url],
      ["property", "og:image", image],
      ["property", "og:locale", (window.I18N && I18N.lang() === "fr") ? "fr_FR" : "en_US"],
      ["name", "twitter:card", "summary_large_image"],
      ["name", "twitter:title", title],
      ["name", "twitter:description", description],
      ["name", "twitter:image", image],
      // Explicit dimensions let Facebook / WhatsApp / X draw the big card
      // straight away instead of waiting to fetch and measure the file.
      ["property", "og:image:width", "1200"],
      ["property", "og:image:height", "630"],
      ["property", "og:image:alt", "Jaura Store — everything you love, all in one store"],
      ["property", "og:logo", absUrl(logoPath())],
      ["name", "author", "Jaura Store"],
      ["name", "publisher", "Jaura Store"],
      ["name", "theme-color", "#0f0e0c"],
    ].forEach(([attr, key, val]) => {
      let el = document.head.querySelector(`meta[${attr}="${key}"]`);
      if (!el) {
        el = document.createElement("meta");
        el.setAttribute(attr, key);
        document.head.appendChild(el);
      }
      el.setAttribute("content", val);
    });
    let can = document.head.querySelector('link[rel="canonical"]');
    if (!can) {
      can = document.createElement("link");
      can.setAttribute("rel", "canonical");
      document.head.appendChild(can);
    }
    can.setAttribute("href", url);
    if (!document.querySelector('link[rel="icon"]')) {
      const ic = document.createElement("link");
      ic.rel = "icon";
      ic.type = "image/png";
      ic.href = "images/brand/favicon.png?v=127";
      document.head.appendChild(ic);
    }
    let ld = document.getElementById("jaura-jsonld");
    if (!ld) {
      ld = document.createElement("script");
      ld.type = "application/ld+json";
      ld.id = "jaura-jsonld";
      document.head.appendChild(ld);
    }
    // The catalogue Google is told about: one entry per live category, so
    // the sitelinks and the knowledge panel can show what the shop sells.
    let cats = [];
    try { cats = (typeof categories === "function" ? categories() : []) || []; } catch (e) { cats = []; }
    if (!cats.length) cats = DEFAULT_CATS;
    const offerCatalog = {
      "@type": "OfferCatalog",
      name: "Jaura Store catalogue",
      itemListElement: cats.map((c) => ({
        "@type": "Offer",
        itemOffered: {
          "@type": "ProductCollection",
          name: (c && (c.name || c.id)) || "",
          url: SITE + "/shop.html?cat=" + encodeURIComponent((c && c.id) || ""),
          image: (c && c.image) ? absUrl(c.image) : image,
        },
      })),
    };
    ld.textContent = JSON.stringify(opts.jsonLd || {
      "@context": "https://schema.org",
      "@graph": graph()
    });

    // Extra nodes describing THIS page: crumb trail, the page's own type
    // (FAQPage / ContactPage / AboutPage / CollectionPage) and, on the
    // listing pages, an ItemList of the categories on show. These are what
    // give the Google result its sitelinks and the FAQ drop-downs.
    function graph() {
      const g = [
        {
          "@type": ["Organization", "OnlineStore"],
          "@id": SITE + "/#store",
          name: "Jaura Store",
          url: SITE,
          logo: absUrl(logoPath()),
          image,
          email: "jaurastore@gmail.com",
          telephone: "+22968953110",
          currenciesAccepted: "NGN, XOF",
          sameAs: [
            "https://www.tiktok.com/@j_aura_store",
            "https://whatsapp.com/channel/0029Vb7qNQs4yltRRkChu01k"
          ],
          address: [
            { "@type": "PostalAddress", addressLocality: "Cotonou", addressCountry: "BJ" },
            { "@type": "PostalAddress", addressLocality: "Lagos", addressCountry: "NG" }
          ],
          hasOfferCatalog: offerCatalog
        },
        {
          "@type": "WebSite",
          "@id": SITE + "/#website",
          url: SITE,
          name: "Jaura Store",
          publisher: { "@id": SITE + "/#store" },
          inLanguage: ["en", "fr"],
          // sitelinks search box: Google shows a search field under the
          // result and sends the query straight to the shop page
          potentialAction: {
            "@type": "SearchAction",
            target: {
              "@type": "EntryPoint",
              urlTemplate: SITE + "/shop.html?q={search_term_string}"
            },
            "query-input": "required name=search_term_string"
          }
        }
      ];

      // Crumb trail on every page except the bare home page.
      if (page !== "home") g.push(breadcrumbLd(page, url, title));

      // The page's own schema.org type.
      const PAGE_TYPE = { faq: "FAQPage", contact: "ContactPage", about: "AboutPage", shop: "CollectionPage", categories: "CollectionPage" };
      const ptype = PAGE_TYPE[page];
      if (ptype) {
        const node = {
          "@type": ptype,
          "@id": url + "#page",
          url,
          name: title,
          description,
          isPartOf: { "@id": SITE + "/#website" },
          about: { "@id": SITE + "/#store" },
          primaryImageOfPage: image,
        };
        if (page !== "home") node.breadcrumb = { "@id": url + "#crumbs" };
        if (ptype === "FAQPage") {
          node.mainEntity = FAQ_LD.map(([q, a]) => ({
            "@type": "Question",
            name: q,
            acceptedAnswer: { "@type": "Answer", text: a },
          }));
        }
        g.push(node);
      }

      // Listing pages carry an ItemList of the live categories, which is what
      // lets Google build the "Clothes · Shoes · Bags …" sitelink row.
      if (page === "shop" || page === "categories" || page === "home") {
        g.push({
          "@type": "ItemList",
          "@id": url + "#categories",
          name: "Jaura Store categories",
          numberOfItems: cats.length,
          itemListOrder: "https://schema.org/ItemListOrderAscending",
          itemListElement: cats.map((c, i) => ({
            "@type": "ListItem",
            position: i + 1,
            name: (c && (c.name || c.id)) || "",
            url: SITE + "/shop.html?cat=" + encodeURIComponent((c && c.id) || ""),
            image: (c && c.image) ? absUrl(c.image) : image,
          })),
        });
      }
      return g;
    }
  }
  function pageSeo() {
    const page = (document.body && document.body.dataset.page) || "home";
    const map = {
      home: { title: "Jaura Store | Fashion, beauty & lifestyle · ₦ and F CFA", description: "Shop Jaura Store — clothes, shoes, bags, ankara, household, beauty and gadgets. Pay in Naira or F CFA. Lagos and Cotonou." },
      shop: { title: "All Products · Jaura Store", description: "Browse 250+ pieces at Jaura Store. Filter by category, colour and size. Pay in ₦ or F CFA." },
      categories: { title: "Categories · Jaura Store", description: "Shop Jaura Store by category: clothes, shoes, bags, ankara, household, beauty, gadgets and more." },
      product: { title: "Product · Jaura Store", description: "Shop this piece at Jaura Store in Naira or F CFA." },
      about: { title: "Vision · Jaura Store", description: "Jaura Store vision — curated fashion and lifestyle from Cotonou and Lagos. Pay in F CFA or Naira." },
      faq: { title: "FAQ · Jaura Store", description: "How to order from Jaura Store, delivery to Benin, Lagos and West Africa, payment in CFA or Naira." },
      delivery: { title: "Delivery · Jaura Store", description: "Jaura Store delivery: Benin 6–14 days, Lagos Mainland and Island, Lomé and West Africa. Fare on WhatsApp." },
      contact: { title: "Contact · Jaura Store", description: "WhatsApp Jaura Store +229 68 95 31 10. Email jaurastore@gmail.com. Lagos and Cotonou." },
      checkout: { title: "Checkout · Jaura Store", description: "Jaura Store checkout — pay by UBA Naira, MTN MoMo CFA or Moov Togo, then upload your receipt." },
      cart: { title: "Bag · Jaura Store", description: "Your Jaura Store bag." },
      wishlist: { title: "Wishlist · Jaura Store", description: "Saved pieces at Jaura Store." },
    };
    const row = map[page] || { title: "Jaura Store", description: map.home.description };
    const keepSearch = page === "shop" || page === "product";
    setSeo({ ...row, keepSearch, url: keepSearch ? undefined : (SITE + "/" + (page === "home" ? "" : page + ".html")) });
  }

  function mountChrome() {
    const top = document.getElementById("site-header");
    const bot = document.getElementById("site-footer");
    if (top) top.innerHTML = headerHTML();
    if (bot) bot.innerHTML = footerHTML();
    try { loadBannerDates(); } catch (e) {}
    try {
      const dock = bot && bot.querySelector(".dock");
      if (dock) document.body.appendChild(dock);
      const search = bot && bot.querySelector("[data-search]");
      if (search) document.body.appendChild(search);
      const wa = bot && bot.querySelector(".wa-float");
      if (wa) document.body.appendChild(wa);
    } catch (e) {}
    try { pageSeo(); } catch (e) {}
    try { startCardPlay(); } catch (e) {}
    refreshChrome();
    bindChrome();
    try { if (window.I18N) window.I18N.apply(); } catch (e) {}
    showWelcome();
    try {
      if ((document.body.dataset.page || "") !== "admin") {
        track("visit", { page: document.body.dataset.page || "home" });
        startPresence();
      }
    } catch (e) {}
  }

  function refreshChrome() {
    const n = cartCount();
    document.querySelectorAll("[data-cart-count]").forEach((el) => {
      el.textContent = n;
    });
    document.querySelectorAll("[data-wish-count]").forEach((el) => {
      el.textContent = wish().length;
    });
    if (document.querySelector("[data-mini].open")) paintMini();
    document.querySelectorAll("[data-cur]").forEach((btn) => {
      btn.classList.toggle("is-on", btn.dataset.cur === currency());
    });
    const currentLang = window.I18N ? window.I18N.lang() : "en";
    document.querySelectorAll("[data-lang]").forEach((btn) => {
      btn.classList.toggle("is-on", btn.dataset.lang === currentLang);
    });
  }

  function bindChrome() {
    document.querySelectorAll("[data-cur]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setCurrency(btn.dataset.cur);
        toast(btn.dataset.cur === "NGN" ? tx("toast.ngn") : tx("toast.cfa"));
      });
    });
    document.querySelectorAll("[data-lang]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const next = btn.getAttribute("data-lang") === "fr" ? "fr" : "en";
        try { localStorage.setItem("jaura_lang", next); } catch (err) {}
        try { sessionStorage.setItem("jaura_lang", next); } catch (err) {}
        try { document.cookie = "jaura_lang=" + next + ";path=/;max-age=31536000;SameSite=Lax"; } catch (err) {}
        if (window.I18N) window.I18N.setLang(next);
        toast(next === "fr" ? tx("toast.langFr") : tx("toast.langEn"));
      });
    });
    const overlay = document.querySelector("[data-search]");
    const input = document.querySelector("[data-search-input]");
    let activeCat = "all";
    const paintSearch = () => {
      const box = document.querySelector("[data-search-results]");
      const meta = document.querySelector("[data-search-meta]");
      if (!box) return;
      const q = input?.value || "";
      const hits = searchProducts(q, activeCat);
      const shown = (q ? hits : products()).slice(0, 12);
      if (meta) {
        meta.textContent = q
          ? tx(hits.length === 1 ? "search.results" : "search.resultsMany", { n: hits.length })
          : tx("search.type");
      }
      box.innerHTML = shown.map((p) => `
        <a class="search-hit" href="product.html?id=${encodeURIComponent(p.id)}">
          <img src="${asset(p.image)}" alt="${escape(p.name)}" onerror="fallbackImg(event)" />
          <span><small>${categoryName(p.category)}</small><br>${escape(displayName(p))}</span>
          <span>${money(priceOf(p))}</span>
        </a>`).join("") + (q && hits.length > 12
        ? `<a class="search-more" href="shop.html?q=${encodeURIComponent(q)}">${tx("search.seeAll", { n: hits.length })}</a>`
        : `<a class="search-more" href="shop.html">${tx("search.browse")}</a>`);
    };
    const chips = document.querySelector("[data-search-chips]");
    if (chips && !chips.dataset.ready) {
      chips.dataset.ready = "1";
      chips.innerHTML = `<button type="button" data-schip="all" class="is-on">${tx("search.all")}</button>` +
        categories().map((c) => `<button type="button" data-schip="${c.id}">${categoryName(c.id)}</button>`).join("");
      chips.addEventListener("click", (e) => {
        const b = e.target.closest("[data-schip]");
        if (!b) return;
        activeCat = b.dataset.schip;
        chips.querySelectorAll("button").forEach((x) => x.classList.toggle("is-on", x === b));
        paintSearch();
      });
    }
    const openSearch = () => {
      document.querySelector("[data-mobile]")?.classList.remove("open");
      overlay?.classList.add("open");
      paintSearch();
      setTimeout(() => input?.focus(), 40);
    };
    document.querySelectorAll("[data-open-search]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        openSearch();
      });
    });
    document.querySelector("[data-menu-search]")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = (e.target.querySelector("input")?.value || "").trim();
      location.href = q ? "shop.html?q=" + encodeURIComponent(q) : "shop.html";
    });
    const menuQ = document.querySelector("[data-menu-q]");
    const menuLive = document.querySelector("[data-menu-live]");
    const paintMenuLive = () => {
      if (!menuLive) return;
      const q = (menuQ?.value || "").trim();
      if (!q) {
        menuLive.hidden = true;
        menuLive.innerHTML = "";
        return;
      }
      const hits = searchProducts(q).slice(0, 8);
      menuLive.hidden = false;
      menuLive.innerHTML = (hits.length
        ? hits.map((p) => `<a class="wix-hit" href="product.html?id=${encodeURIComponent(p.id)}">
            <img src="${asset(p.image)}" alt="" onerror="fallbackImg(event)" />
            <span>${escape(displayName(p))}</span>
            <em>${money(priceOf(p))}</em>
          </a>`).join("")
        : `<p class="wix-hit-empty">${tx("shop.empty")}</p>`) +
        `<a class="wix-hit-more" href="shop.html?q=${encodeURIComponent(q)}">${tx("search.seeAll", { n: searchProducts(q).length })}</a>`;
    };
    menuQ?.addEventListener("input", paintMenuLive);
    overlay?.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.remove("open");
    });
    document.querySelector("[data-close-search]")?.addEventListener("click", () => {
      overlay?.classList.remove("open");
    });
    input?.addEventListener("input", paintSearch);
    input?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") overlay.classList.remove("open");
      if (e.key === "Enter") {
        e.preventDefault();
        location.href = "shop.html?q=" + encodeURIComponent(input.value);
      }
    });
    document.querySelectorAll(".has-drop > span").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.parentElement.classList.toggle("open");
      });
    });
    if (!document.body.dataset.dropBound) {
      document.body.dataset.dropBound = "1";
      document.addEventListener("click", (e) => {
        if (!e.target.closest(".has-drop")) {
          document.querySelectorAll(".has-drop.open").forEach((n) => n.classList.remove("open"));
        }
      });
    }
    const openMenu = () => {
      document.querySelector("[data-mobile]")?.classList.add("open");
      document.body.classList.add("menu-open");
    };
    const closeMenu = () => {
      document.querySelector("[data-mobile]")?.classList.remove("open");
      document.body.classList.remove("menu-open");
    };
    document.querySelectorAll("[data-open-mini]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        openMini();
      });
    });
    document.querySelector("[data-open-menu]")?.addEventListener("click", openMenu);
    document.querySelector("[data-close-menu]")?.addEventListener("click", closeMenu);
    if (!document.body.dataset.addBound) {
      document.body.dataset.addBound = "1";
      document.body.addEventListener("click", (e) => {
        const wishBtn = e.target.closest("[data-wish]");
        if (wishBtn) {
          e.preventDefault();
          e.stopPropagation();
          toggleWish(wishBtn.dataset.wish);
          const on = isWished(wishBtn.dataset.wish);
          document.querySelectorAll(`[data-wish="${wishBtn.dataset.wish}"]`).forEach((b) => {
            b.classList.toggle("is-on", on);
          });
          refreshChrome();
          return;
        }
        const btn = e.target.closest("[data-add]");
        if (btn) {
          const piece = product(btn.dataset.add);
          if (piece && (piece.options || []).length) {
            location.href = "product.html?id=" + encodeURIComponent(piece.id);
            return;
          }
          addToCart(btn.dataset.add);
        }
      });
    }
    if (!document.body.dataset.scrollBound) {
      document.body.dataset.scrollBound = "1";
      window.addEventListener("scroll", () => {
        document.querySelector("[data-header]")?.classList.toggle("is-solid", window.scrollY > 12);
      }, { passive: true });
    }
  }

  document.addEventListener("ja:cart", refreshChrome);
  document.addEventListener("ja:wish", refreshChrome);
  document.addEventListener("ja:currency", () => {
    refreshChrome();
    document.dispatchEvent(new CustomEvent("ja:rerender"));
  });
  document.addEventListener("ja:lang", () => {
    mountChrome();
    if (window.I18N) I18N.apply();
    document.dispatchEvent(new CustomEvent("ja:rerender"));
  });

  ready = loadSeed();

  return {
    ready, CATEGORIES: DEFAULT_CATS, categories, loadServerCategories, saveCategories, deleteCategory, moveCategoryProducts, settings, saveSettings, setBanner,
    products, product, searchProducts, categoryName, displayName,
    currency, setCurrency, money, priceOf, compareOf, priceHTML, toCfa, bulkUnit, BULK_QTY,
    cart, addToCart, setQty, clearCart, cartCount, cartDetailed, cartTotal,
    cartQtyFor, stockFor, stockLeft, stockProblems, stockProblemLine,
    wish, isWished, toggleWish, wishDetailed, openMini, closeMini,
    toast, upsertProduct, removeProduct, importProducts, applyServerProduct, syncPending, retryStrandedProducts, reloadCatalog,
    orders, saveOrder, getOrder, updateOrder, nextOrderId, sendReceipt,
    isAdmin, loginAdmin, logoutAdmin, adminSession, changePassword, requestOtp, verifyOtp, resetPassword,
    adminAnalytics, adminOrders, setOrderStatus, deleteOrder, flushEvents,
    customer, setCustomer, logoutCustomer, ordersForEmail, getProof, dataUrlToBlob,
    cardHTML, asset, escape, mountChrome, track, getStats, setSeo, absUrl, SITE,
    galleryOf, startCardPlay, reviews, addReview, removeReview, setReviews, reviewStats, starsHTML,
    mediaHTML, mediaKind, getSiteConfig, applySiteBranding,
  };
})();