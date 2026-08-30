/* J Aura Store — English / French */
window.I18N = (() => {
  const KEY = "jaura_lang";

  const en = {
    "nav.home": "Home",
    "nav.shop": "Shop",
    "nav.shopAll": "All products",
    "nav.categories": "Categories",
    "nav.faq": "FAQ",
    "nav.vision": "Vision",
    "nav.contact": "Contact",
    "nav.checkout": "Checkout",
    "nav.bag": "Bag",
    "nav.track": "Track order",
    "nav.pay": "Send payment receipt",
    "nav.atelier": "Atelier",
    "nav.search": "Search",
    "nav.cart": "Cart",
    "nav.menu": "Menu",
    "nav.close": "Close",
    "nav.allProducts": "All Products",
    "nav.delivery": "Delivery",
    "nav.care": "Customer Care",
    "nav.checkoutForm": "Checkout Form",
    "nav.accountSettings": "Account Settings",
    "nav.wishlist": "MY WISHLIST",
    "nav.account": "MY ACCOUNT",
    "nav.jewels": "ACCESSORIES/ JEWELRIES",
    "search.placeholder": "Search...",
    "search.wix": "Search...",
    "search.overlay": "Search the collection…",
    "search.hint": "Type a name, category or keyword",
    "search.type": "Type a name or category",
    "search.all": "All",
    "search.results": "{n} result",
    "search.resultsMany": "{n} results",
    "search.seeAll": "See all {n} results →",
    "search.browse": "Browse all products →",
    "catbar.clothes": "Clothes",
    "catbar.shoes": "Shoes",
    "catbar.bags": "Bags",
    "catbar.ankara": "Ankara wears",
    "catbar.household": "Household items",
    "catbar.decor": "Home decor",
    "catbar.gadgets": "Electronics & gadgets",
    "catbar.children": "Children items",
    "catbar.all": "Shop all",
    "cat.clothing": "Clothings for men and women",
    "cat.household": "Household items",
    "cat.ankara": "Ankara ready to wear",
    "cat.accessories": "Accessories",
    "cat.beauty": "Beauty & skincare",
    "cat.shoes": "Shoes",
    "cat.gadgets": "Gadgets / Electronics",
    "cat.packaging": "Packaging",
    "cat.skincare": "Beauty & skincare",
    "cat.bags": "Bags",
    "cat.hair-care": "Hair care",
    "cat.nails": "Nails",
    "cat.gift-set": "Gift set",
    "cat.children": "Children items",
    "cat.decor": "Decor",
    "ticker": "• SHOP LUXURY • LAGOS & COTONOU • WHATSAPP +229 68 95 31 10 • PAY IN ₦ OR F CFA •",
    "conv.banner": "Benin 🇧🇯 customers: place your order now and get it between 15 September and 25 September",
    "home.kicker": "Experience effortless elegance",
    "home.heroLine": "Experience effortless elegance and curated essentials",
    "home.title": "Curated essentials",
    "home.lead": "Everything you love, all in one store. Shop luxury and pay in F CFA or ₦.",
    "home.shopNow": "Shop now",
    "home.visionKicker": "The J Aura Vision",
    "home.visionTitle": "Discover J Aura Store",
    "home.visionBody": "We curate premium fashion, beauty, and lifestyle essentials to bring you quality and style in every piece. Our vision is simple: providing direct access to wholesale luxury.",
    "home.howKicker": "How to Order",
    "home.step1": "Select your items",
    "home.step2": "Review your bag",
    "home.step3": "Complete the form",
    "home.step4": "Send payment",
    "home.step5": "Confirm delivery",
    "home.orderNow": "Order now",
    "home.collection": "The collection",
    "home.related": "Related products",
    "home.shopLuxury": "Shop luxury →",
    "home.maison": "Maison",
    "home.standard": "The new standard of modern luxury",
    "home.explore": "Explore",
    "home.justin": "Just in",
    "home.moreHouse": "More from the house",
    "home.elevated": "Elevated shopping experience",
    "home.quieter": "A quieter kind of luxury",
    "home.svc1t": "Luxury service",
    "home.svc1p": "Bespoke attention tailored to your unique style and needs.",
    "home.svc2t": "Personal shopping support",
    "home.svc2p": "Expert guidance and dedicated help for all your fashion choices.",
    "home.svc3t": "Premium shipping",
    "home.svc3p": "Careful delivery so your pieces arrive in perfect condition.",
    "home.svc4t": "Curated quality",
    "home.svc4p": "Hand-picked essentials from manufacturers you can trust.",
    "home.delKicker": "Delivery guide",
    "home.delTitle": "We deliver across West Africa",
    "home.z1k": "Benin",
    "home.z1t": "Cotonou & beyond",
    "home.z1p": "Cotonou, Calavi, Porto-Novo and surrounding regions. Delivery 6 to 14 business days.",
    "home.z2k": "Lagos Mainland",
    "home.z2t": "Yaba to Ikeja",
    "home.z2p": "Yaba, Ikeja, Surulere and all central Mainland districts.",
    "home.z3k": "Lagos Island",
    "home.z3t": "Lekki, VI, Ikoyi",
    "home.z3p": "Major Island coastal zones. Rates confirmed at checkout.",
    "home.z4k": "West Africa",
    "home.z4t": "Lomé & neighbours",
    "home.z4p": "Togo and various locations across neighbouring West African states.",
    "footer.blurb": "Premium fashion, beauty and lifestyle essentials. Wholesale luxury for West Africa.",
    "footer.maison": "Maison",
    "footer.collection": "The collection",
    "footer.vision": "The J Aura vision",
    "footer.client": "Client Relations",
    "footer.track": "Track an order",
    "footer.atelier": "Atelier login",
    "footer.visit": "Visit & deliver",
    "footer.follow": "Follow us",
    "footer.v1": "Cotonou, Calavi, Porto-Novo",
    "footer.v2": "Lagos Mainland & Island",
    "footer.v3": "Lomé and West Africa",
    "footer.v4": "6–14 business days in Benin",
    "footer.whatsapp": "WhatsApp boutique",
    "footer.phone": "Phone",
    "footer.email": "Email",
    "footer.waHelp": "Our team is ready to assist you on WhatsApp for a smooth shopping experience.",
    "footer.contactUs": "Contact Us",
    "footer.copy": "© {year} J Aura Store. All rights reserved.",
    "footer.pay": "Pay in F CFA or ₦ · 1 ₦ = 0.44 F CFA",
    "shop.kicker": "The collection",
    "shop.all": "All Products",
    "shop.categories": "Categories",
    "shop.search": "Search products, categories, keywords…",
    "shop.newest": "Newest",
    "shop.priceAsc": "Price · low to high",
    "shop.priceDesc": "Price · high to low",
    "shop.name": "Name",
    "shop.resultsFor": "Results for “{q}”",
    "shop.count": "{n} pieces · prices in {cur}",
    "shop.empty": "Nothing in this aisle yet.",
    "pdp.colour": "Colour",
    "pdp.qty": "Quantity",
    "pdp.add": "Add to cart",
    "pdp.oos": "Out of stock",
    "pdp.payIn": "Pay in {cur}",
    "pdp.hint": "Tap ₦ or F CFA in the menu to switch prices · SKU {sku}",
    "pdp.bulk": "Buy 10 of this item and get 10% off.",
    "pdp.bulkPrice": "10 pcs: {price} (10% off)",
    "cart.bulk": "10% off · 10+ items",
    "cart.bulkOn": "10% off applied — you have 10 or more of this item.",
    "pdp.more": "More from this aisle",
    "pdp.also": "You may also like",
    "pdp.missing": "This piece is no longer listed.",
    "pdp.return": "Return to the shop",
    "pdp.details": "Details",
    "pdp.choose": "Choose {name}",
    "pdp.needOpt": "Please choose {name} — as on the original listing.",
    "pdp.required": "{name} *",
    "promo.welcome": "Welcome to JauraStore",
    "promo.reduced": "Prices have been reduced",
    "promo.shop": "Shop now",
    "promo.kicker": "Everything you love, all in one store",
    "badge.sale": "Sale",
    "badge.new": "New",
    "badge.bestseller": "Bestseller",
    "card.stock": "{n} in stock",
    "pdp.stock": "{n} in stock",
    "card.add": "Add to cart",
    "card.oos": "Out of stock",
    "cart.title": "Shopping cart",
    "cart.stepCart": "Shopping cart",
    "cart.stepCheck": "Checkout",
    "cart.stepDone": "Order complete",
    "cart.empty": "Your cart is empty.",
    "cart.emptyLead": "Before you proceed to checkout you must add some products to your shopping cart.",
    "cart.begin": "Begin the collection",
    "cart.summary": "Summary",
    "cart.items": "{n} items",
    "cart.toPay": "To pay in {cur}",
    "cart.switchHint": "Switch CFA / ₦ in the menu to change the currency you will pay.",
    "cart.checkout": "Checkout in {cur}",
    "ck.title": "Checkout",
    "ck.empty": "Your cart is currently empty. Add products before checkout.",
    "ck.return": "Return to shop",
    "ck.billing": "Billing details",
    "ck.first": "First name *",
    "ck.last": "Last name *",
    "ck.country": "Country / Region *",
    "ck.benin": "Benin",
    "ck.nigeria": "Nigeria",
    "ck.togo": "Togo",
    "ck.other": "Other West Africa",
    "ck.street": "Street address *",
    "ck.streetPh": "House number and street name",
    "ck.city": "Town / City *",
    "ck.cityPh": "Cotonou, Ikeja, Lekki…",
    "ck.zone": "State / Zone *",
    "ck.zoneBenin": "Benin — Cotonou, Calavi, Porto-Novo",
    "ck.zoneMain": "Lagos Mainland — Yaba, Ikeja, Surulere",
    "ck.zoneIsland": "Lagos Island — Lekki, VI, Ikoyi",
    "ck.zoneWa": "West Africa — Lomé & neighbours",
    "ck.fareTitle": "Add the total amount of the items in your cart to the delivery fare for your zone *",
    "ck.fareIntro": "These are ranges only. The exact fare depends on your location and the weight of the products, and is confirmed on WhatsApp.",
    "ck.fare.mainland": "Lagos Mainland (₦2,000 – ₦5,000)",
    "ck.fare.island": "Lagos Island (₦3,500 – ₦6,000)",
    "ck.fare.ngOther": "Other states in Nigeria (confirm on WhatsApp)",
    "ck.fare.cotonou": "Cotonou (1,000 – 3,000 CFA)",
    "ck.fare.calavi": "Calavi (1,500 – 3,500 CFA)",
    "ck.fare.porto": "Porto-Novo (1,500 – 3,500 CFA)",
    "ck.fare.bjOther": "Other places in Benin (confirm on WhatsApp)",
    "ck.fare.lome": "Lomé (2,500 – 3,500 CFA)",
    "ck.fare.tgOther": "Other places in Togo (confirm on WhatsApp)",
    "ck.phone": "Phone *",
    "ck.email": "Email address *",
    "ck.notes": "Order notes (optional)",
    "ck.notesPh": "Notes about your order, e.g. special delivery instructions.",
    "ck.yourOrder": "Your order",
    "ck.product": "Product",
    "ck.subtotal": "Subtotal",
    "ck.shipping": "Shipping",
    "ck.shipNote": "Discussed on WhatsApp",
    "ck.total": "Total",
    "ck.payment": "Payment",
    "ck.payCfa": "Direct bank transfer — F CFA",
    "ck.payCfaHint": "Pay in F CFA by MTN MoMo (Benin) or Moov Money (Togo).",
    "ck.payNgn": "Direct bank transfer — ₦ Naira",
    "ck.payNgnHint": "Pay in Nigerian Naira to the UBA account below.",
    "ck.account": "Our bank details",
    "ck.advance": "Advance bank payment",
    "ck.payIntro": "Make your payment directly into our bank account first on the details below. Please upload the bank payment receipt and use your Order ID as the payment reference. Your transport fare will be discussed with you on WhatsApp. A confirmation message will be sent to your email. JauraStore will confirm your payment.",
    "ck.ourBank": "Our bank details",
    "ck.bank": "Bank",
    "ck.accNo": "Account number",
    "ck.fareNote": "Transport fare is not fixed. It ranges by your delivery location and the weight of your products. After payment, tap WhatsApp to get your specific fare.",
    "ck.emailNote": "A confirmation message will be sent to your email. JauraStore will confirm your payment.",
    "ck.uploadReceipt": "Upload the bank payment receipt",
    "ck.legal": "Your personal data is used to process your order. Upload your payment receipt here. JauraStore will confirm your payment, and a confirmation message will be sent to your email. Transport fare is discussed on WhatsApp.",
    "ck.place": "Place order",
    "ck.placing": "Placing order…",
    "ck.preparing": "Preparing your photo…",
    "ck.doneKicker": "Order complete",
    "ck.thanks": "Thank you. Your order has been received.",
    "ck.orderNo": "Order number",
    "ck.date": "Date",
    "ck.payMethod": "Payment method",
    "ck.waiting": "Waiting for confirmation",
    "ck.saveId": "Save this order ID. JauraStore will confirm your payment. A confirmation message will be sent to your email. Your transport fare will be discussed on WhatsApp.",
    "ck.idHelp": "Give this ID to JauraStore or put it on your transfer.",
    "ck.track": "Track this order",
    "ck.fareRange": "Transportation fare ranges depending on your location and the weight of the products. Tap the button below to WhatsApp us with your order ID and location so we can give you your specific fare.",
    "ck.uploadHere": "Upload payment receipt",
    "ck.uploadNow": "WhatsApp for transport fare",
    "ck.copyId": "Copy order ID",
    "ck.waId": "WhatsApp this ID",
    "dock.shop": "Shop",
    "dock.search": "Search",
    "dock.account": "Account",
    "dock.wish": "Wishlist",
    "dock.cart": "Cart",
    "home.bestsellers": "Bestsellers",
    "footer.tiktok": "TikTok",
    "footer.channel": "Follow the Jaura Store channel on WhatsApp",
    "shop.filter": "Filter",
    "shop.tagline": "Everything you love, all in one store.",
    "filt.price": "Filter by price",
    "filt.color": "Filter by colour",
    "filt.size": "Filter by size",
    "filt.apply": "Filter",
    "filt.clear": "Clear filters",
    "filt.noColor": "No colour options in this category.",
    "filt.noSize": "No size options in this category.",
    "filt.priceLab": "Price:",
    "filt.close": "Close",
    "toast.added": "Added to your cart.",
    "toast.wish": "Saved to your wishlist.",
    "toast.unwish": "Removed from wishlist.",
    "mini.view": "View cart",
    "mini.checkout": "Checkout",
    "toast.unavailable": "This piece is currently unavailable.",
    "toast.ngn": "Prices now in ₦ Naira",
    "toast.cfa": "Prices now in F CFA",
    "toast.langEn": "Language: English",
    "toast.langFr": "Langue : français",
    "toast.needShot": "Please choose a payment screenshot.",
    "toast.badImg": "Could not read that image. Try another screenshot.",
    "toast.needId": "Enter your order ID first.",
    "toast.noOrder": "No order found for that ID.",
    "toast.uploaded": "Screenshot saved. We will confirm soon.",
    "order.kicker": "Track an order",
    "order.title": "Enter your order ID",
    "order.id": "Order ID",
    "order.lookup": "Look up",
    "order.missing": "No order found for that ID.",
    "order.status": "Order status",
    "order.confirmed": "Payment confirmed",
    "order.declined": "Payment declined",
    "order.pending": "Waiting for confirmation",
    "order.paidIn": "Paid in",
    "order.prep": "Thank you — we are preparing your delivery.",
    "order.declineMsg": "Please contact us on WhatsApp with this order ID.",
    "order.waitMsg": "Upload your payment receipt here or send it on WhatsApp. JauraStore will confirm your payment, and a confirmation message will be sent to your email.",
    "pay.kicker": "Payment",
    "pay.title": "Send your payment screenshot",
    "pay.lead": "Upload the receipt or screenshot from your bank or MoMo. We email it to Jaura Store with the file attached, then confirm your payment.",
    "pay.id": "Order ID *",
    "pay.file": "Payment screenshot",
    "pay.send": "Send receipt to Jaura Store",
    "pay.whatsapp": "Send it on WhatsApp",
    "cats.kicker": "The aisles",
    "cats.title": "Categories",
    "cats.lead": "Shop the same collection as the original house — by aisle.",
    "cats.shop": "Shop",
    "cats.piece": "{n} piece",
    "cats.pieces": "{n} pieces",
    "about.kicker": "Maison",
    "about.title": "The J Aura vision",
    "about.p1": "Discover J Aura Store. We curate premium fashion, beauty and lifestyle essentials to bring you quality and style in every piece.",
    "about.p2": "Our vision is simple: providing direct access to wholesale luxury — from Cotonou and Porto-Novo to Lagos Mainland, the Island, Lomé and neighbouring West African states.",
    "about.p3": "Shop in F CFA or Naira. One house, two currencies, the same standard of care.",
    "about.enter": "Enter the collection",
    "about.founders": "Founders",
    "about.names": "J Aura Store",
    "about.foundersP": "A sister house built on taste, trust and access. Every piece is chosen to feel considered — whether it is a bag with a scarf, a gold cutlery set, or a quiet skincare essential.",
    "contact.kicker": "Client relations",
    "contact.title": "We are ready on WhatsApp",
    "contact.lead": "Our team is ready to assist you for a smooth shopping experience. Complete checkout, send payment in CFA or Naira, then send your payment screenshot to us on WhatsApp. A confirmation message will be sent to your email. JauraStore will confirm your payment.",
    "contact.phone": "Phone",
    "contact.email": "Email",
    "contact.houses": "Houses",
    "contact.wa": "Contact us on WhatsApp",
    "faq.kicker": "Help",
    "faq.title": "Frequently asked",
    "faq.q1": "How do I order?",
    "faq.a1": "01 Select your items. 02 Review your bag. 03 Complete checkout. 04 Send payment in F CFA or ₦. 05 Upload your payment receipt on checkout. 06 JauraStore will confirm your payment and a confirmation message will be sent to your email. Transport fare is discussed on WhatsApp.",
    "faq.q2": "Can I pay in CFA and Naira?",
    "faq.a2": "Yes. Naira is the price. F CFA on the website is converted at 1 ₦ = 0.44. Tap ₦ or F CFA in the menu to choose how you pay. At checkout choose Direct bank transfer — F CFA or Direct bank transfer — ₦ Naira.",
    "faq.q3": "What is the exchange rate?",
    "faq.a3": "Naira is the main price. House rate: 1 ₦ = 0.44 F CFA. If a piece has no Naira price yet, it stays in F CFA.",
    "faq.q4": "Where do you deliver?",
    "faq.a4": "Benin (Cotonou, Calavi, Porto-Novo — 6 to 14 business days), Lagos Mainland, Lagos Island, Lomé and neighbouring West African states. Shipment rates are confirmed at checkout by city.",
    "faq.q5": "How do I send payment?",
    "faq.a5": "Pay by bank transfer in F CFA (MoMo) or ₦ (UBA). After checkout, upload your payment screenshot on the form. A confirmation message will be sent to your email. JauraStore will confirm your payment. Transport fare is discussed on WhatsApp.",
    "faq.q6": "How do I track my order?",
    "faq.a6": "Open Track order and enter your order ID (for example JA-M8K2Q1). Status is waiting, confirmed, or declined.",
    "faq.q7": "How can I reach you?",
    "faq.a7": "WhatsApp +229 01 68 95 31 01 · Phone +229 01 68 95 31 01 / +234 916 167 0236 · jaurastore@gmail.com · Lagos, Nigeria and Cotonou, Benin.",
    "lang.group": "Language",
    "wish.title": "My wishlist",
    "wish.empty": "Your wishlist is empty.",
    "wish.add": "Tap the heart on a product to save it here.",
    "wish.shop": "Browse products",
    "account.title": "My account",
    "account.lead": "Log in with the email you used at checkout to see your last orders.",
    "account.orders": "Last orders",
    "account.empty": "No orders yet for this email on this phone.",
    "account.email": "Email *",
    "account.login": "Log in",
    "account.logout": "Log out",
    "account.hello": "Signed in as {email}",
    "account.needEmail": "Enter the email from your order.",
    "contact.message": "Message *",
    "contact.send": "Send to JauraStore",
    "contact.sent": "Message sent. JauraStore will reply by email.",
    "del.title": "Delivery Locations",
    "del.lead": "Curated coverage across West Africa",
    "del.ng": "Nigeria",
    "del.bj": "Benin Republic",
    "del.tg": "Togo",
    "del.mainland": "Lagos Mainland",
    "del.mainlandP": "Festac, Iyana-Ishashi, Iyana-Ipaja, Ojo, Surulere, Yaba, Gbagada",
    "del.island": "Lagos Island",
    "del.islandP": "Lekki Phase 1, Lekki Phase 2, Oniru, Victoria Island, Ikoyi, Ajah",
    "del.ogun": "Ogun State",
    "del.ogunP": "Selected locations on request",
    "del.ekiti": "Ekiti State",
    "del.ekitiP": "Selected locations on request",
    "del.cotonou": "Cotonou",
    "del.cotonouP": "Akpakpa, Calavi, Jericho, PK10, Fidjrosse, Agla, Zogbo, St Michel etc.",
    "del.porto": "Porto-Novo",
    "del.portoP": "Selected locations on request",
    "del.bjOther": "Other places in Benin",
    "del.bjOtherP": "Confirm on WhatsApp",
    "del.lome": "Lomé",
    "del.lomeP": "Lomé and surrounding districts",
    "del.tgOther": "Other places in Togo",
    "del.tgOtherP": "Confirm on WhatsApp",
    "rev.title": "Customer reviews",
    "rev.write": "Write a review",
    "rev.name": "Your name",
    "rev.stars": "Stars",
    "rev.note": "Your note",
    "rev.send": "Post review",
    "rev.empty": "Be the first to review this piece.",
    "rev.thanks": "Thank you for your review.",
    "rev.need": "Please add your name and a short note.",
    "rev.count": "{n} review",
    "rev.countMany": "{n} reviews",
  };

  const fr = {
    "nav.home": "Accueil",
    "nav.shop": "Boutique",
    "nav.shopAll": "Tous les produits",
    "nav.categories": "Catégories",
    "nav.faq": "FAQ",
    "nav.vision": "Vision",
    "nav.contact": "Contact",
    "nav.checkout": "Paiement",
    "nav.bag": "Panier",
    "nav.track": "Suivre une commande",
    "nav.pay": "Envoyer le reçu",
    "nav.atelier": "Atelier",
    "nav.search": "Recherche",
    "nav.cart": "Panier",
    "nav.menu": "Menu",
    "nav.close": "Fermer",
    "nav.allProducts": "Tous les produits",
    "nav.delivery": "Livraison",
    "nav.care": "Service client",
    "nav.checkoutForm": "Formulaire de commande",
    "nav.accountSettings": "Paramètres du compte",
    "nav.wishlist": "MA LISTE D'ENVIES",
    "nav.account": "MON COMPTE",
    "nav.jewels": "ACCESSORIES/ JEWELRIES",
    "search.placeholder": "Rechercher...",
    "search.wix": "Rechercher...",
    "search.overlay": "Rechercher dans la collection…",
    "search.hint": "Tapez un nom, une catégorie ou un mot-clé",
    "search.type": "Tapez un nom ou une catégorie",
    "search.all": "Tout",
    "search.results": "{n} résultat",
    "search.resultsMany": "{n} résultats",
    "search.seeAll": "Voir les {n} résultats →",
    "search.browse": "Voir tous les produits →",
    "catbar.clothes": "Vêtements",
    "catbar.shoes": "Chaussures",
    "catbar.bags": "Sacs",
    "catbar.ankara": "Ankara",
    "catbar.household": "Articles ménagers",
    "catbar.decor": "Décoration",
    "catbar.gadgets": "Électronique",
    "catbar.children": "Enfants",
    "catbar.all": "Tout voir",
    "cat.clothing": "Vêtements homme et femme",
    "cat.household": "Articles ménagers",
    "cat.ankara": "Ankara prêt-à-porter",
    "cat.accessories": "Accessoires",
    "cat.beauty": "Beauté & soins",
    "cat.shoes": "Chaussures",
    "cat.gadgets": "Gadgets / Électronique",
    "cat.packaging": "Emballage",
    "cat.skincare": "Beauté & soins",
    "cat.bags": "Sacs",
    "cat.hair-care": "Soins capillaires",
    "cat.nails": "Ongles",
    "cat.gift-set": "Coffret cadeau",
    "cat.children": "Articles pour enfants",
    "cat.decor": "Décoration",
    "ticker": "• SHOP LUXE • LAGOS & COTONOU • WHATSAPP +229 68 95 31 10 • PAYEZ EN ₦ OU F CFA •",
    "conv.banner": "Clients du Bénin 🇧🇯 : passez commande maintenant et recevez-la entre le 15 et le 25 septembre",
    "home.kicker": "L’élégance sans effort",
    "home.heroLine": "L’élégance sans effort et des essentiels choisis",
    "home.title": "Essentiels choisis",
    "home.lead": "Tout ce que vous aimez, dans une seule boutique. Payez en F CFA ou en ₦.",
    "home.shopNow": "Acheter",
    "home.visionKicker": "La vision J Aura",
    "home.visionTitle": "Découvrir J Aura Store",
    "home.visionBody": "Nous sélectionnons mode, beauté et essentiels de vie pour vous offrir qualité et style à chaque pièce. Notre vision : l’accès direct au luxe en gros.",
    "home.howKicker": "Comment commander",
    "home.step1": "Choisissez vos articles",
    "home.step2": "Vérifiez votre panier",
    "home.step3": "Remplissez le formulaire",
    "home.step4": "Envoyez le paiement",
    "home.step5": "Confirmez la livraison",
    "home.orderNow": "Commander",
    "home.collection": "La collection",
    "home.related": "Produits associés",
    "home.shopLuxury": "Voir le luxe →",
    "home.maison": "Maison",
    "home.standard": "Le nouveau standard du luxe moderne",
    "home.explore": "Explorer",
    "home.justin": "Nouveautés",
    "home.moreHouse": "Encore de la maison",
    "home.elevated": "Une expérience soignée",
    "home.quieter": "Un luxe plus calme",
    "home.svc1t": "Service luxe",
    "home.svc1p": "Une attention sur mesure, selon votre style et vos besoins.",
    "home.svc2t": "Conseil shopping",
    "home.svc2p": "Un accompagnement dédié pour tous vos choix.",
    "home.svc3t": "Livraison soignée",
    "home.svc3p": "Vos pièces arrivent en parfait état.",
    "home.svc4t": "Qualité choisie",
    "home.svc4p": "Des essentiels sélectionnés auprès de fabricants de confiance.",
    "home.delKicker": "Livraison",
    "home.delTitle": "Nous livrons en Afrique de l’Ouest",
    "home.z1k": "Bénin",
    "home.z1t": "Cotonou et alentours",
    "home.z1p": "Cotonou, Calavi, Porto-Novo et régions voisines. Livraison en 6 à 14 jours ouvrés.",
    "home.z2k": "Lagos Mainland",
    "home.z2t": "Yaba à Ikeja",
    "home.z2p": "Yaba, Ikeja, Surulere et tout le Mainland central.",
    "home.z3k": "Lagos Island",
    "home.z3t": "Lekki, VI, Ikoyi",
    "home.z3p": "Zones côtières de l’Island. Tarifs confirmés à la commande.",
    "home.z4k": "Afrique de l’Ouest",
    "home.z4t": "Lomé et voisins",
    "home.z4p": "Togo et plusieurs pays d’Afrique de l’Ouest.",
    "footer.blurb": "Mode, beauté et essentiels de vie. Le luxe en gros en Afrique de l’Ouest.",
    "footer.maison": "Maison",
    "footer.collection": "La collection",
    "footer.vision": "La vision J Aura",
    "footer.client": "Relation client",
    "footer.track": "Suivre une commande",
    "footer.atelier": "Connexion Atelier",
    "footer.visit": "Visite & livraison",
    "footer.follow": "Suivez-nous",
    "footer.v1": "Cotonou, Calavi, Porto-Novo",
    "footer.v2": "Lagos Mainland & Island",
    "footer.v3": "Lomé et Afrique de l’Ouest",
    "footer.v4": "6–14 jours ouvrés au Bénin",
    "footer.whatsapp": "WhatsApp boutique",
    "footer.phone": "Téléphone",
    "footer.email": "E-mail",
    "footer.waHelp": "Notre équipe est prête à vous aider sur WhatsApp pour un achat simple.",
    "footer.contactUs": "Nous contacter",
    "footer.copy": "© {year} J Aura Store. Tous droits réservés.",
    "footer.pay": "Payez en F CFA ou ₦ · 1 ₦ = 0,44 F CFA",
    "shop.kicker": "La collection",
    "shop.all": "Tous les produits",
    "shop.categories": "Catégories",
    "shop.search": "Rechercher produits, catégories, mots-clés…",
    "shop.newest": "Plus récent",
    "shop.priceAsc": "Prix · croissant",
    "shop.priceDesc": "Prix · décroissant",
    "shop.name": "Nom",
    "shop.resultsFor": "Résultats pour « {q} »",
    "shop.count": "{n} pièces · prix en {cur}",
    "shop.empty": "Rien dans cet rayon pour le moment.",
    "pdp.colour": "Couleur",
    "pdp.qty": "Quantité",
    "pdp.add": "Ajouter au panier",
    "pdp.oos": "Rupture de stock",
    "pdp.payIn": "Payer en {cur}",
    "pdp.hint": "Touchez CFA ou ₦ dans le menu pour changer les prix · SKU {sku}",
    "pdp.more": "Dans le même rayon",
    "pdp.also": "Vous aimerez aussi",
    "pdp.missing": "Cette pièce n’est plus listée.",
    "pdp.return": "Retour à la boutique",
    "pdp.details": "Détails",
    "pdp.choose": "Choisir {name}",
    "pdp.needOpt": "Veuillez choisir {name} — comme sur la fiche d’origine.",
    "pdp.required": "{name} *",
    "promo.welcome": "Bienvenue chez JauraStore",
    "promo.reduced": "Les prix ont été réduits",
    "promo.shop": "Acheter",
    "promo.kicker": "Tout ce que vous aimez, dans une seule boutique",
    "badge.sale": "Promo",
    "badge.new": "Nouveau",
    "badge.bestseller": "Best-seller",
    "card.stock": "{n} en stock",
    "pdp.stock": "{n} en stock",
    "card.add": "Ajouter au panier",
    "card.oos": "Rupture",
    "cart.title": "Panier",
    "cart.stepCart": "Panier",
    "cart.stepCheck": "Paiement",
    "cart.stepDone": "Commande reçue",
    "cart.empty": "Votre panier est vide.",
    "cart.emptyLead": "Avant de passer au paiement, ajoutez des produits à votre panier.",
    "cart.begin": "Commencer la collection",
    "cart.summary": "Récapitulatif",
    "cart.items": "{n} articles",
    "cart.toPay": "À payer en {cur}",
    "cart.switchHint": "Changez CFA / ₦ dans le menu pour la devise de paiement.",
    "cart.checkout": "Payer en {cur}",
    "ck.title": "Paiement",
    "ck.empty": "Votre panier est vide. Ajoutez des produits avant de payer.",
    "ck.return": "Retour boutique",
    "ck.billing": "Facturation",
    "ck.first": "Prénom *",
    "ck.last": "Nom *",
    "ck.country": "Pays / Région *",
    "ck.benin": "Bénin",
    "ck.nigeria": "Nigéria",
    "ck.togo": "Togo",
    "ck.other": "Autre Afrique de l’Ouest",
    "ck.street": "Adresse *",
    "ck.streetPh": "Numéro et nom de rue",
    "ck.city": "Ville *",
    "ck.cityPh": "Cotonou, Ikeja, Lekki…",
    "ck.zone": "État / Zone *",
    "ck.zoneBenin": "Bénin — Cotonou, Calavi, Porto-Novo",
    "ck.zoneMain": "Lagos Mainland — Yaba, Ikeja, Surulere",
    "ck.zoneIsland": "Lagos Island — Lekki, VI, Ikoyi",
    "ck.zoneWa": "Afrique de l’Ouest — Lomé et voisins",
    "ck.fareTitle": "Ajoutez le total des articles au tarif de livraison de votre zone *",
    "ck.fareIntro": "Ce sont des fourchettes. Le tarif exact dépend de votre localité et du poids, et se confirme sur WhatsApp.",
    "ck.fare.mainland": "Lagos Mainland (₦2,000 – ₦5,000)",
    "ck.fare.island": "Lagos Island (₦3,500 – ₦6,000)",
    "ck.fare.ngOther": "Autres États du Nigéria (confirmer sur WhatsApp)",
    "ck.fare.cotonou": "Cotonou (1 000 – 3 000 CFA)",
    "ck.fare.calavi": "Calavi (1 500 – 3 500 CFA)",
    "ck.fare.porto": "Porto-Novo (1 500 – 3 500 CFA)",
    "ck.fare.bjOther": "Autres localités du Bénin (confirmer sur WhatsApp)",
    "ck.fare.lome": "Lomé (2 500 – 3 500 CFA)",
    "ck.fare.tgOther": "Autres localités du Togo (confirmer sur WhatsApp)",
    "ck.phone": "Téléphone *",
    "ck.email": "E-mail *",
    "ck.notes": "Notes de commande (optionnel)",
    "ck.notesPh": "Instructions de livraison, par exemple.",
    "ck.yourOrder": "Votre commande",
    "ck.product": "Produit",
    "ck.subtotal": "Sous-total",
    "ck.shipping": "Livraison",
    "ck.shipNote": "Discutée sur WhatsApp",
    "ck.total": "Total",
    "ck.payment": "Paiement",
    "ck.payCfa": "Virement — F CFA",
    "ck.payCfaHint": "Payez en F CFA par MTN MoMo (Bénin) ou Moov Money (Togo).",
    "ck.payNgn": "Virement — ₦ Naira",
    "ck.payNgnHint": "Payez en naira nigérian sur le compte UBA ci-dessous.",
    "ck.account": "Nos coordonnées bancaires",
    "ck.advance": "Paiement bancaire anticipé",
    "ck.payIntro": "Payez d’abord sur le compte ci-dessous. Téléchargez le reçu bancaire et indiquez votre n° de commande. Les frais de transport seront discutés avec vous sur WhatsApp. Un message de confirmation sera envoyé à votre e-mail. JauraStore confirmera votre paiement.",
    "ck.ourBank": "Nos coordonnées bancaires",
    "ck.bank": "Banque",
    "ck.accNo": "Numéro de compte",
    "ck.fareNote": "Les frais de transport ne sont pas fixes : ils varient selon votre localité et le poids des articles. Après paiement, ouvrez WhatsApp pour votre tarif précis.",
    "ck.emailNote": "Un message de confirmation sera envoyé à votre e-mail. JauraStore confirmera votre paiement.",
    "ck.uploadReceipt": "Télécharger le reçu de paiement",
    "ck.legal": "Vos données servent à traiter la commande. Téléchargez votre reçu ici. JauraStore confirmera votre paiement, et un message de confirmation sera envoyé à votre e-mail. Les frais de transport se discutent sur WhatsApp.",
    "ck.place": "Passer la commande",
    "ck.placing": "Envoi de la commande…",
    "ck.preparing": "Préparation de votre photo…",
    "ck.doneKicker": "Commande reçue",
    "ck.thanks": "Merci. Votre commande a bien été reçue.",
    "ck.orderNo": "N° de commande",
    "ck.date": "Date",
    "ck.payMethod": "Mode de paiement",
    "ck.waiting": "En attente de confirmation",
    "ck.saveId": "Notez cet identifiant. JauraStore confirmera votre paiement. Un message de confirmation sera envoyé à votre e-mail. Les frais de transport seront discutés sur WhatsApp.",
    "ck.idHelp": "Donnez cet identifiant à JauraStore ou indiquez-le sur le virement.",
    "ck.track": "Suivre cette commande",
    "ck.fareRange": "Les frais de transport varient selon votre localité et le poids des produits. Appuyez ci-dessous pour WhatsApp avec votre n° de commande et votre adresse, afin de recevoir votre tarif.",
    "ck.uploadHere": "Upload payment receipt",
    "ck.uploadNow": "WhatsApp pour le transport",
    "ck.copyId": "Copier le n° de commande",
    "ck.waId": "WhatsApp cet identifiant",
    "dock.shop": "Boutique",
    "dock.search": "Recherche",
    "dock.account": "Compte",
    "dock.wish": "Envies",
    "dock.cart": "Panier",
    "home.bestsellers": "Meilleures ventes",
    "footer.tiktok": "TikTok",
    "footer.channel": "Suivre le canal Jaura Store sur WhatsApp",
    "shop.filter": "Filtrer",
    "shop.tagline": "Tout ce que vous aimez, en une seule boutique.",
    "filt.price": "Filtrer par prix",
    "filt.color": "Filtrer par couleur",
    "filt.size": "Filtrer par taille",
    "filt.apply": "Filtrer",
    "filt.clear": "Effacer les filtres",
    "filt.noColor": "Pas d'options de couleur dans cette catégorie.",
    "filt.noSize": "Pas d'options de taille dans cette catégorie.",
    "filt.priceLab": "Prix :",
    "filt.close": "Fermer",
    "toast.added": "Ajouté au panier.",
    "toast.wish": "Ajouté à la liste d'envies.",
    "toast.unwish": "Retiré de la liste d'envies.",
    "mini.view": "Voir le panier",
    "mini.checkout": "Paiement",
    "toast.unavailable": "Cette pièce n’est pas disponible.",
    "toast.ngn": "Prix affichés en ₦ naira",
    "toast.cfa": "Prix affichés en F CFA",
    "toast.langEn": "Language: English",
    "toast.langFr": "Langue : français",
    "toast.needShot": "Choisissez une capture du paiement.",
    "toast.badImg": "Image illisible. Essayez une autre capture.",
    "toast.needId": "Entrez d’abord votre n° de commande.",
    "toast.noOrder": "Aucune commande pour cet identifiant.",
    "toast.uploaded": "Capture enregistrée. Confirmation bientôt.",
    "order.kicker": "Suivi",
    "order.title": "Entrez votre n° de commande",
    "order.id": "N° de commande",
    "order.lookup": "Rechercher",
    "order.missing": "Aucune commande pour cet identifiant.",
    "order.status": "Statut",
    "order.confirmed": "Paiement confirmé",
    "order.declined": "Paiement refusé",
    "order.pending": "En attente de confirmation",
    "order.paidIn": "Payé en",
    "order.prep": "Merci — nous préparons la livraison.",
    "order.declineMsg": "Contactez-nous sur WhatsApp avec ce n° de commande.",
    "order.waitMsg": "Téléversez votre reçu de paiement ici ou envoyez-le sur WhatsApp. JauraStore confirmera votre paiement, et un message de confirmation sera envoyé à votre e-mail.",
    "pay.kicker": "Paiement",
    "pay.title": "Envoyer votre capture",
    "pay.lead": "Aucun envoi n’est nécessaire sur le site. Après le virement, envoyez-nous la capture du paiement sur WhatsApp. Un message de confirmation sera envoyé à votre e-mail. JauraStore confirmera votre paiement.",
    "pay.id": "N° de commande *",
    "pay.file": "Capture du paiement",
    "pay.send": "Envoyer la capture sur WhatsApp",
    "pay.whatsapp": "Envoyer sur WhatsApp",
    "cats.kicker": "Les rayons",
    "cats.title": "Catégories",
    "cats.lead": "La même collection que la maison — par rayon.",
    "cats.shop": "Boutique",
    "cats.piece": "{n} pièce",
    "cats.pieces": "{n} pièces",
    "about.kicker": "Maison",
    "about.title": "La vision J Aura",
    "about.p1": "Découvrez J Aura Store. Nous sélectionnons mode, beauté et essentiels de vie pour la qualité et le style à chaque pièce.",
    "about.p2": "Notre vision : l’accès direct au luxe en gros — de Cotonou et Porto-Novo au Mainland de Lagos, à l’Island, à Lomé et aux pays voisins.",
    "about.p3": "Payez en F CFA ou en naira. Une maison, deux devises, le même soin.",
    "about.enter": "Entrer dans la collection",
    "about.founders": "Fondatrices",
    "about.names": "J Aura Store",
    "about.foundersP": "Une maison sœur bâtie sur le goût, la confiance et l’accès. Chaque pièce est choisie avec soin — sac et foulard, ménagère dorée ou essentiel de soin.",
    "contact.kicker": "Relation client",
    "contact.title": "Nous sommes sur WhatsApp",
    "contact.lead": "Notre équipe vous accompagne. Terminez la commande, payez en CFA ou naira, puis envoyez-nous la capture du paiement sur WhatsApp. Un message de confirmation sera envoyé à votre e-mail. JauraStore confirmera votre paiement.",
    "contact.phone": "Téléphone",
    "contact.email": "E-mail",
    "contact.houses": "Maisons",
    "contact.wa": "Nous écrire sur WhatsApp",
    "faq.kicker": "Aide",
    "faq.title": "Questions fréquentes",
    "faq.q1": "Comment commander ?",
    "faq.a1": "01 Choisissez vos articles. 02 Vérifiez le panier. 03 Validez la commande. 04 Payez en F CFA ou ₦. 05 Envoyez-nous la capture du paiement sur WhatsApp. 06 JauraStore confirmera votre paiement et un message de confirmation sera envoyé à votre e-mail.",
    "faq.q2": "Puis-je payer en CFA et en naira ?",
    "faq.a2": "Oui. Le naira est le prix. Le F CFA sur le site est converti à 1 ₦ = 0,44. Touchez ₦ ou F CFA dans le menu pour choisir comment payer. À la caisse, choisissez virement F CFA ou virement ₦ naira.",
    "faq.q3": "Quel est le taux ?",
    "faq.a3": "Le naira est le prix principal. Taux : 1 ₦ = 0,44 F CFA. Sans prix en naira, l’article reste en F CFA.",
    "faq.q4": "Où livrez-vous ?",
    "faq.a4": "Bénin (Cotonou, Calavi, Porto-Novo — 6 à 14 jours ouvrés), Lagos Mainland, Lagos Island, Lomé et pays voisins. Les frais sont confirmés selon la ville.",
    "faq.q5": "Comment payer ?",
    "faq.a5": "Faites le virement avec les coordonnées de la devise choisie, puis téléchargez le reçu bancaire sur le formulaire de commande. Un message de confirmation sera envoyé à votre e-mail. JauraStore confirmera votre paiement. Les frais de transport se discutent sur WhatsApp.",
    "faq.q6": "Comment suivre ma commande ?",
    "faq.a6": "Ouvrez Suivre une commande et entrez votre identifiant (par exemple JA-M8K2Q1). Statut : en attente, confirmé ou refusé.",
    "faq.q7": "Comment vous joindre ?",
    "faq.a7": "WhatsApp +229 01 68 95 31 01 · Tél. +229 01 68 95 31 01 / +234 916 167 0236 · jaurastore@gmail.com · Lagos, Nigéria et Cotonou, Bénin.",
    "lang.group": "Langue",
    "wish.title": "Ma liste d'envies",
    "wish.empty": "Votre liste d'envies est vide.",
    "wish.add": "Touchez le cœur sur un produit pour le garder ici.",
    "wish.shop": "Voir les produits",
    "account.title": "Mon compte",
    "account.lead": "Connectez-vous avec l’e-mail utilisé à la commande pour voir vos dernières commandes.",
    "account.orders": "Dernières commandes",
    "account.empty": "Aucune commande pour cet e-mail sur ce téléphone.",
    "account.email": "E-mail *",
    "account.login": "Connexion",
    "account.logout": "Déconnexion",
    "account.hello": "Connecté : {email}",
    "account.needEmail": "Entrez l’e-mail de votre commande.",
    "contact.message": "Message *",
    "contact.send": "Envoyer à JauraStore",
    "contact.sent": "Message envoyé. JauraStore répondra par e-mail.",
    "del.title": "Lieux de livraison",
    "del.lead": "Couverture soignée en Afrique de l’Ouest",
    "del.ng": "Nigéria",
    "del.bj": "République du Bénin",
    "del.tg": "Togo",
    "del.mainland": "Lagos Mainland",
    "del.mainlandP": "Festac, Iyana-Ishashi, Iyana-Ipaja, Ojo, Surulere, Yaba, Gbagada",
    "del.island": "Lagos Island",
    "del.islandP": "Lekki Phase 1, Lekki Phase 2, Oniru, Victoria Island, Ikoyi, Ajah",
    "del.ogun": "État d’Ogun",
    "del.ogunP": "Localités sélectionnées sur demande",
    "del.ekiti": "État d’Ekiti",
    "del.ekitiP": "Localités sélectionnées sur demande",
    "del.cotonou": "Cotonou",
    "del.cotonouP": "Akpakpa, Calavi, Jericho, PK10, Fidjrosse, Agla, Zogbo, St Michel, etc.",
    "del.porto": "Porto-Novo",
    "del.portoP": "Localités sélectionnées sur demande",
    "del.bjOther": "Autres localités du Bénin",
    "del.bjOtherP": "Confirmer sur WhatsApp",
    "del.lome": "Lomé",
    "del.lomeP": "Lomé et alentours",
    "del.tgOther": "Autres localités du Togo",
    "del.tgOtherP": "Confirmer sur WhatsApp",
    "rev.title": "Avis clients",
    "rev.write": "Laisser un avis",
    "rev.name": "Votre nom",
    "rev.stars": "Étoiles",
    "rev.note": "Votre commentaire",
    "rev.send": "Publier",
    "rev.empty": "Soyez le premier à laisser un avis.",
    "rev.thanks": "Merci pour votre avis.",
    "rev.need": "Ajoutez votre nom et un court commentaire.",
    "rev.count": "{n} avis",
    "rev.countMany": "{n} avis",
  };

  const dict = { en, fr };

  function readStored() {
    try {
      const q = new URLSearchParams(location.search).get("lang");
      if (q === "fr" || q === "en") return q;
    } catch (e) {}
    try { if (sessionStorage.getItem(KEY) === "fr") return "fr"; } catch (e) {}
    try { if (localStorage.getItem(KEY) === "fr") return "fr"; } catch (e) {}
    try {
      const m = String(document.cookie || "").match(/(?:^|; )jaura_lang=(fr|en)/);
      if (m) return m[1];
    } catch (e) {}
    return "en";
  }

  function lang() {
    return readStored() === "fr" ? "fr" : "en";
  }

  function setLang(next) {
    const value = next === "fr" ? "fr" : "en";
    try { localStorage.setItem(KEY, value); } catch (e) {}
    try { sessionStorage.setItem(KEY, value); } catch (e) {}
    try { document.cookie = "jaura_lang=" + value + ";path=/;max-age=31536000;SameSite=Lax"; } catch (e) {}
    document.documentElement.lang = value;
    document.dispatchEvent(new CustomEvent("ja:lang"));
  }

  function pretty(key) {
    const tail = String(key || "").split(".").pop();
    return tail
      .replace(/([A-Z])/g, " $1")
      .replace(/[-_]/g, " ")
      .replace(/^\w/, (c) => c.toUpperCase())
      .trim();
  }

  function t(key, vars) {
    const table = dict[lang()] || en;
    let s = table[key];
    if (s == null || s === "") s = en[key];
    if (s == null || s === "" || s === key) s = pretty(key);
    if (vars) {
      Object.keys(vars).forEach((k) => {
        s = s.split("{" + k + "}").join(String(vars[k]));
      });
    }
    return s;
  }

  /* ------------------------------------------------------------------
   * JavaScript builds most of the wording: the payment form, the category
   * names, the whole admin portal, every toast. Those strings cannot carry
   * a data-i18n attribute, so once a page has rendered we read the text and
   * swap anything we know the French for. A whole string is replaced or
   * nothing is, which is why product names the owner typed are left alone.
   * ---------------------------------------------------------------- */
  const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };
  const SWEEP_ATTRS = ["placeholder", "aria-label", "title", "alt", "value"];

  function frValues() {
    const table = dict.fr || {};
    const out = new Set();
    Object.keys(table).forEach((k) => out.add(table[k]));
    Object.keys(window.I18N_PHRASES || {}).forEach((k) => out.add(window.I18N_PHRASES[k]));
    return out;
  }
  let french = null;

  /* sentences worth swapping inside a longer paragraph, longest first so a
   * short phrase never eats part of a longer one */
  let subKeys = null;
  function subKeysSorted() {
    if (subKeys) return subKeys;
    const table = window.I18N_PHRASES || {};
    subKeys = Object.keys(table)
      .filter((k) => k.length >= 12 && !/[{}]$/.test(k))
      .sort((a, b) => b.length - a.length);
    return subKeys;
  }

  function translate(s) {
    if (!s || !/[A-Za-z]/.test(s)) return null;
    const flat = s.replace(/\s+/g, " ").trim();
    if (lang() !== "fr") return null;
    const table = window.I18N_PHRASES || {};
    if (table[flat]) return table[flat];
    const rules = window.I18N_RULES || [];
    for (let i = 0; i < rules.length; i += 1) {
      const m = flat.match(rules[i][0]);
      if (m) return rules[i][1](m, flat);
    }
    // a paragraph holding several sentences: swap the ones we know
    if (flat.length > 40) {
      let out = flat;
      subKeysSorted().forEach((k) => {
        if (out.indexOf(k) === -1) return;
        out = out.split(k).join(table[k]);
      });
      if (out !== flat) return out;
    }
    return null;
  }

  function sweep(root) {
    if (!root) return;
    if (lang() !== "fr") return;
    if (!french) french = frValues();
    // text nodes
    const walker = document.createTreeWalker(
      root.nodeType === 1 ? root : document.body, NodeFilter.SHOW_TEXT, null, false);
    const seen = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentNode;
      if (!parent || SKIP_TAGS[parent.nodeName]) continue;
      if (parent.closest && parent.closest("[data-no-i18n]")) continue;
      const raw = node.nodeValue;
      if (!raw || !/[A-Za-z]/.test(raw)) continue;
      const flat = raw.replace(/\s+/g, " ").trim();
      if (french.has(flat)) continue;           // already French
      const out = translate(flat);
      if (out && out !== flat) seen.push([node, out]);
    }
    seen.forEach(([node, out]) => { node.nodeValue = out; });
    // attributes
    if (root.querySelectorAll) {
      root.querySelectorAll("*").forEach((el) => {
        if (el.closest && el.closest("[data-no-i18n]")) return;
        SWEEP_ATTRS.forEach((attr) => {
          if (!el.hasAttribute(attr)) return;
          const v = el.getAttribute(attr);
          if (!v || !/[A-Za-z]/.test(v)) return;
          const flat = v.replace(/\s+/g, " ").trim();
          if (french.has(flat)) return;
          const out = translate(flat);
          if (out && out !== flat) el.setAttribute(attr, out);
        });
      });
    }
  }

  /* Content added later (a toast, the payment form, a freshly loaded admin
   * tab) is swept as it appears, so French keeps up with the page. */
  let sweepTimer = null;
  function watch() {
    if (typeof MutationObserver === "undefined") return;
    const queue = [];
    const obs = new MutationObserver((records) => {
      records.forEach((r) => {
        r.addedNodes.forEach((n) => { if (n.nodeType === 1) queue.push(n); });
        if (r.type === "characterData" && r.target.parentNode) queue.push(r.target.parentNode);
      });
      if (sweepTimer) return;
      sweepTimer = setTimeout(() => {
        sweepTimer = null;
        const seenAttr = [];
        while (queue.length) {
          const node = queue.shift();
          if (!node || !node.isConnected) continue;
          sweep(node);
          if (node.querySelectorAll) {
            node.querySelectorAll("[placeholder],[aria-label],[title],[alt]")
              .forEach((el) => seenAttr.push(el));
          }
        }
        seenAttr.forEach((el) => {
          SWEEP_ATTRS.forEach((attr) => {
            if (!el.hasAttribute(attr)) return;
            const flat = el.getAttribute(attr).replace(/\s+/g, " ").trim();
            const out = translate(flat);
            if (out && out !== flat) el.setAttribute(attr, out);
          });
        });
      }, 60);
    });
    obs.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true,
    });
  }

  function apply(root = document) {
    document.documentElement.lang = lang();
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.dataset.i18n);
    });
    root.querySelectorAll("[data-i18n-html]").forEach((el) => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.setAttribute("placeholder", t(el.dataset.i18nPlaceholder));
    });
    root.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      el.setAttribute("aria-label", t(el.dataset.i18nAria));
    });
    const titleEl = root.querySelector("[data-i18n-title]");
    if (titleEl) document.title = t(titleEl.dataset.i18nTitle);
    sweep(document.body);
    watch();
  }

  if (document.documentElement) document.documentElement.lang = lang();

  return { lang, setLang, t, apply, sweep };
})();
var I18N = window.I18N;
