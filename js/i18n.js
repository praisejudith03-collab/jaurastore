/* J Aura Store — English / French */
window.I18N = (() => {
  const KEY = "jaura_lang";
  /* An explicit `?lang=` choice is remembered for the BROWSING SESSION only
   * (see the precedence note above readStored()): with no visible language
   * control anywhere in the storefront, a permanent stored choice would be a
   * one-way door. The persistent localStorage key is therefore RETIRED, and
   * migrateStaleLocale() clears it so an old value cannot outlive the device. */

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
    "cat.perfume": "Perfume",
    "ticker": "• SHOP LUXURY • LAGOS & COTONOU • WHATSAPP +229 68 95 31 10 • PAY IN ₦ OR CFA •",
    "conv.banner": "Benin 🇧🇯 customers: place your order now and we deliver in the next batch",
    "home.kicker": "Welcome. Ready to shop?",
    "home.heroLine": "Experience effortless elegance and curated essentials",
    "home.explore": "Explore",
    "pager.prev": "Prev",
    "pager.next": "Next",
    "shop.loadingLive": "Loading live products and stock…",
    "ck.orderMissing": "We could not load this order on this device.",
    "ck.queuedSaved": "No internet right now — your order and receipt are saved on this phone and will be sent automatically when you are back online. Keep your order ID.",
    "ck.queuedWaiting": "No internet right now — keep this page open while your order is waiting to send. Keep your order ID.",
    "toast.orderIdCopied": "Order ID copied: {id}",
    "store.paused": "The store is temporarily paused. Please chat with us on WhatsApp.",
    "cart.oos": "Out of Stock",
    "cart.onlyLeft": "Only {n} items remaining in stock",
    "cart.verifyFailed": "We couldn't verify current stock. Please try again.",
    "contact.whatsapp": "Please contact the store through the WhatsApp link on this page.",
    "sync.sending": "Sending…",
    "pager.prevLabel": "Previous page",
    "pager.nextLabel": "Next page",
    "home.title": "Curated essentials",
    "home.lead": "Everything you love, all in one store. Shop luxury and pay in CFA or ₦.",
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
    "home.uploadProof": "Upload your payment photo",
    "home.uploadProofText": "Already paid? Upload your payment photo yourself — right from your phone, no WhatsApp needed.",
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
    "home.z1p": "Cotonou, Calavi, Porto-Novo and surrounding regions. Delivery within 4 to 12 business days.",
    "home.z2k": "Lagos Mainland",
    "home.z2t": "Yaba to Ikeja",
    "home.z2p": "Yaba, Ikeja, Surulere and all central Mainland districts. Delivery within 24 to 72 hours.",
    "home.z3k": "Lagos Island",
    "home.z3t": "Lekki, VI, Ikoyi",
    "home.z3p": "Major Island coastal zones. Delivery within 24 to 72 hours.",
    "home.z4k": "West Africa",
    "home.z4t": "Lomé & neighbours",
    "home.z4p": "Togo and various locations across neighbouring West African states. Delivery within 4 to 12 business days.",
    "footer.blurb": "Premium fashion, beauty and lifestyle essentials. Wholesale luxury for West Africa.",
    "footer.maison": "Maison",
    "footer.collection": "The collection",
    "footer.vision": "The J Aura vision",
    "footer.client": "Client Relations",
    "footer.atelier": "Atelier login",
    "footer.visit": "Visit & deliver",
    "footer.follow": "Follow us",
    "footer.v1": "Cotonou, Calavi, Porto-Novo",
    "footer.v2": "Lagos Mainland & Island",
    "footer.v3": "Lomé and West Africa",
    "footer.v4": "4–12 business days in Benin",
    "footer.whatsapp": "WhatsApp boutique",
    "footer.phone": "Phone",
    "footer.email": "Email",
    "footer.waHelp": "Our team is ready to assist you on WhatsApp for a smooth shopping experience.",
    "footer.contactUs": "Contact Us",
    "footer.copy": "© {year} J Aura Store. All rights reserved.",
    "footer.pay": "Pay in CFA or ₦ · 1 ₦ = {rate} CFA",
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
    "pdp.productNote": "Product note",
    "pdp.add": "Add to cart",
    "pdp.oos": "Out of stock",
    "pdp.payIn": "Pay in {cur}",
    "pdp.hint": "Tap ₦ or CFA in the menu to switch prices · SKU {sku}",
    // Bulk-discount copy is DYNAMIC: the threshold and percentage come from
    // the shop-wide tiers or the product's own bulkQty/bulkPercent, so the
    // label is generic and the numbers travel with it.
    "bulk.label": "Bulk discount",
    "cart.bulkApplied": "Bulk discount applied — {p}% off this item.",
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
    "promo.referral": "Order above ₦20,000 (8,800 CFA) and get your personal referral code — share it and your friends enjoy a discount at checkout.",
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
    "ck.completedTitle": "Order Completed",
    "ck.customerInfo": "Your checkout information",
    "ck.name": "Name",
    "ck.formInstruction": "Please fill this form before checking out.",
    "ck.errorSummary": "Please fix the highlighted fields before placing your order.",
    "ck.emptyRedirect": "Your cart is empty. Add items to your cart first. Taking you to the shop now.",
    "ck.errorFirstName": "Enter your first name.",
    "ck.errorFirstNameInvalid": "Enter a valid first name using letters only.",
    "ck.errorLastName": "Enter your last name.",
    "ck.errorLastNameInvalid": "Enter a valid last name using letters only.",
    "ck.errorCountry": "Choose your country or region.",
    "ck.errorAddress": "Enter your street address.",
    "ck.errorAddressInvalid": "Enter a little more detail for your street address.",
    "ck.errorCity": "Enter your town or city.",
    "ck.errorCityInvalid": "Enter a valid town or city.",
    "ck.errorZone": "Choose a delivery zone.",
    "ck.errorPhone": "Enter your phone number.",
    "ck.errorPhoneInvalid": "Enter a valid phone number, including the country code if possible.",
    "ck.errorEmail": "Enter your email address.",
    "ck.errorEmailInvalid": "Enter a valid email address, for example name@example.com.",
    "ck.errorProof": "Upload your payment receipt before placing the order.",
    "ck.errorRequired": "Please fill in this detail to complete your order.",
    "ck.errorRequiredInvalid": "Please fill in this detail to complete your order.",
    "ck.empty": "Your cart is currently empty. Add products before checkout.",
    "ck.return": "Return to shop",
    "ck.billing": "Billing details",
    "ck.guestTitle": "Checking out as a guest?",
    "ck.guestIntro": "No account is needed. Enter your email below and we’ll use it to send your order confirmation and any cart reminder.",
    "ck.guestAccountLink": "Want an account? Sign in or create one",
    "ck.guestEmailHint": "Required for guest checkout confirmations and reminders. We won’t create an account.",
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
    "ck.zonePlaceholder": "Choose a delivery zone",
    "ck.zoneGroupNaira": "Nigeria (₦ Naira)",
    "ck.zoneGroupCfa": "Benin & Togo (CFA)",
    "ck.zoneGroupPickup": "Pickup / collection",
    "ck.shipLagos": "Lagos (Mainland & Island): within 24 to 72 hours.",
    "ck.shipNigeria": "Other Nigerian states & hubs (Ogun, Abuja, Rivers, Edo, Delta, Ekiti, Osun, Oyo, Kwara, Abia, Anambra and more): within 3 to 7 business days.",
    "ck.shipBenin": "Benin Republic 🇧🇯 (Cotonou, Abomey-Calavi, Porto-Novo): within 4 to 12 business days.",
    "ck.shipTogo": "Togo 🇹🇬 (Lomé): within 4 to 12 business days.",
    "ck.zoneEta.lagos": "Estimated delivery for this zone: within 24 to 72 hours.",
    "ck.zoneEta.ngOther": "Estimated delivery for this zone: within 3 to 7 business days.",
    "ck.zoneEta.west": "Estimated delivery for this zone: within 4 to 12 business days.",
    "ck.zoneEta.pickup": "Ready for pickup — no delivery wait.",
    "ck.bjMin": "Benin deliveries: minimum order 5,000 CFA (about 12,000 naira).",
    "ck.zoneBenin": "Benin — Cotonou, Calavi, Porto-Novo",
    "ck.zoneMain": "Lagos Mainland — Yaba, Ikeja, Surulere",
    "ck.zoneIsland": "Lagos Island — Lekki, VI, Ikoyi",
    "ck.zoneWa": "West Africa — Lomé & neighbours",
    "ck.fare.pickup": "Pick up only",
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
    "ck.interstateNotice": "🚚 Inter-State Delivery: Your order will be dispatched via courier. Tracking & dispatch updates will be sent directly to your phone/WhatsApp.",
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
    "ck.paymentMethod": "Choose a payment method",
    "ck.payCfa": "CFA payment",
    "ck.payCfaHint": "Choose the Benin or Togo CFA payment method.",
    "ck.payNgn": "Naira bank transfer",
    "ck.payNgnHint": "Pay in Nigerian Naira using the details below.",
    "ck.payBeninCfa": "Benin CFA payment",
    "ck.payBeninCfaHint": "Pay using the Benin provider and account below.",
    "ck.payTogoCfa": "Togo CFA payment",
    "ck.payTogoCfaHint": "Pay using the Togo provider and account below.",
    "ck.beninPayDetails": "Benin CFA payment details",
    "ck.togoPayDetails": "Togo CFA payment details",
    "ck.togoFeeNotice": "Moov Money Togo may charge a fee for cross-border transfers.",
    "ck.payUnconfigured": "Payment details are not configured yet. Please contact us on WhatsApp.",
    "ck.provider": "Provider",
    "ck.accountHolder": "Account holder",
    "ck.account": "Payment details",
    "ck.advance": "Choose how to pay",
    "ck.payIntro": "Choose one of the three payment methods. The order currency follows your selection.",
    "ck.ourBank": "Payment details",
    "ck.bank": "Bank",
    "ck.accNo": "Account / phone number",
    "ck.exactPayment": "Please send the exact amount for your order to be confirmed.",
    "ck.fareNote": "Transport fare is not fixed. It ranges by your delivery location and the weight of your products. After payment, tap WhatsApp to get your specific fare.",
    "ck.uploadReceipt": "Upload the bank payment receipt",
    "ck.legal": "By placing your order, you are providing your Name, Address, Phone Number, Order ID, and Ordered Items. Your order will be confirmed soon.",
    "ck.fileTypes": "JPG, PNG, PDF or DOC — up to 24 MB. Large images are compressed under 1 MB before upload.",
    "ck.recaptcha": "This site is protected by reCAPTCHA and the Google <a href=\"https://policies.google.com/privacy\" target=\"_blank\" rel=\"noopener\">Privacy Policy</a> and <a href=\"https://policies.google.com/terms\" target=\"_blank\" rel=\"noopener\">Terms of Service</a> apply.",
    "ck.place": "Place Your Order",
    "ck.placing": "Placing your order…",
    "ck.preparing": "Preparing your photo…",
    "ck.doneKicker": "Order complete",
    "ck.thanks": "Thank you. Your order has been received.",
    "ck.orderNo": "Order number",
    "ck.date": "Date",
    "ck.payMethod": "Payment method",
    "ck.waiting": "Waiting for confirmation",
    "ck.emailConfirmation": "A confirmation message will be sent to your email once your order and payment are confirmed.",
    "ck.saveId": "Save this order ID. JauraStore will confirm your payment. Your receipt is saved. Your transport fare will be discussed on WhatsApp.",
    "ck.idHelp": "Give this ID to JauraStore or put it on your transfer.",
    "ck.track": "Track this order",
    "ck.fareRange": "Transportation fare ranges depending on your location and the weight of the products. Tap the button below to WhatsApp us with your order ID and location so we can give you your specific fare.",
    "ck.uploadHere": "Upload payment receipt",
    "ck.uploadNow": "WhatsApp for transport fare",
    "ck.copyId": "Copy order ID",
    "ck.waId": "WhatsApp this ID",
    "ck.waPick": "Confirm on WhatsApp with:",
    "ck.waPickCheckout": "WhatsApp line for your order:",
    "ck.waNg": "Nigeria WhatsApp",
    "ck.waBj": "Benin / Togo WhatsApp",
    "dock.home": "Home",
    "dock.shop": "Shop",
    "dock.search": "Search",
    "dock.account": "Account",
    "dock.wish": "Wishlist",
    "dock.cart": "Cart",
    "home.bestsellers": "Bestsellers",
    "home.mostViewed": "Most viewed right now",
    "home.shopAllArrow": "Shop all ›",
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
    "toast.cfa": "Prices now in CFA",
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
    "order.waitMsg": "Upload your payment receipt here or send it on WhatsApp. JauraStore will confirm your payment, and your receipt is saved.",
    "order.lead": "Use the ID from your confirmation, for example JA-M8K2Q1. No account is needed.",
    "order.whatsapp": "Ask about this order on WhatsApp",
    "pay.kicker": "Payment",
    "pay.title": "Send your payment screenshot",
    "pay.lead": "Upload the receipt or screenshot from your bank or MoMo. We will confirm your payment.",
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
    "about.p3": "Shop in CFA or Naira. One house, two currencies, the same standard of care.",
    "about.enter": "Enter the collection",
    "about.founders": "Founders",
    "about.names": "J Aura Store",
    "about.foundersP": "A sister house built on taste, trust and access. Every piece is chosen to feel considered — whether it is a bag with a scarf, a gold cutlery set, or a quiet skincare essential.",
    "contact.kicker": "Client relations",
    "contact.title": "We are ready on WhatsApp",
    "contact.lead": "Our team is ready to assist you for a smooth shopping experience. Complete checkout, send payment in CFA or Naira, then send your payment screenshot to us on WhatsApp. Your receipt is saved. JauraStore will confirm your payment.",
    "contact.phone": "Phone",
    "contact.email": "Email",
    "contact.houses": "Houses",
    "contact.hours": "Support hours",
    "contact.wa": "Contact us on WhatsApp",
    "faq.kicker": "Help",
    "faq.title": "Frequently asked",
    "faq.q1": "How do I order?",
    "faq.a1": "01 Select your items. 02 Review your bag. 03 Complete checkout. 04 Send payment in CFA or ₦. 05 Upload your payment receipt on checkout. 06 JauraStore will confirm your payment and your receipt is saved. Transport fare is discussed on WhatsApp.",
    "faq.q2": "Can I pay in CFA and Naira?",
    "faq.a2": "Yes. Naira is the price. CFA on the website is converted at 1 ₦ = {rate}. Tap ₦ or CFA in the menu to choose how you pay. At checkout choose Direct bank transfer — CFA or Direct bank transfer — ₦ Naira.",
    "faq.q3": "What is the exchange rate?",
    "faq.a3": "Naira is the main price. House rate: 1 ₦ = {rate} CFA. If a piece has no Naira price yet, it stays in CFA.",
    "faq.q4": "Where do you deliver?",
    "faq.a4": "Lagos (Mainland & Island): within 24 to 72 hours. Other Nigerian states & hubs (Ogun, Abuja, Rivers, Edo, Delta, Ekiti, Osun, Oyo, Kwara, Abia, Anambra and more): within 3 to 7 business days. Benin Republic (Cotonou, Abomey-Calavi, Porto-Novo): within 4 to 12 business days. Togo (Lomé): within 4 to 12 business days. Shipment rates are confirmed at checkout by city.",
    "faq.q5": "How do I send payment?",
    "faq.a5": "Pay by bank transfer in CFA (MoMo) or ₦ (UBA). After checkout, upload your payment screenshot on the form. Your receipt is saved. JauraStore will confirm your payment. Transport fare is discussed on WhatsApp.",
    "faq.q6": "How do I track my order?",
    "faq.a6": "Message us on WhatsApp with your order ID (for example JA-M8K2Q1) and we will tell you if it is waiting, confirmed, or declined.",
    "faq.q7": "How can I reach you?",
    "faq.a7": "WhatsApp +229 68 95 31 10 · Phone +229 68 95 31 10 / +234,916,167 0236 · jaurastore@gmail.com · Lagos, Nigeria and Cotonou, Benin.",
    "lang.group": "Language",
    "wish.title": "My wishlist",
    "wish.empty": "Your wishlist is empty.",
    "wish.add": "Tap the heart on a product to save it here.",
    "wish.shop": "Browse products",
    "account.title": "My account",
    "account.lead": "Create an account or sign in to manage your profile and see orders linked to you.",
    "account.orders": "Your orders",
    "account.empty": "No orders linked to this account yet.",
    "account.email": "Email *",
    "account.login": "Log in",
    "account.logout": "Log out",
    "account.hello": "Signed in as {email}",
    "account.needEmail": "Enter the email from your order.",
    "account.password": "Password *",
    "account.register": "Create account",
    "account.registerLead": "Guest checkout still works. An account lets you see your own orders.",
    "account.name": "Name",
    "account.pwHint": "At least 8 characters, with a letter and a number.",
    "account.newPassword": "New password *",
    "account.savePassword": "Save password",
    "account.back": "Back to sign in",
    "account.profile": "Profile",
    "account.saveProfile": "Save profile",
    "account.saved": "Profile saved.",
    "account.changePassword": "Change password",
    "account.currentPassword": "Current password *",
    "account.pwSaved": "Password updated.",
    "account.guestOk": "You can still check out as a guest without an account.",
    "account.badLogin": "Invalid email or password.",
    "account.badRegister": "Could not create that account.",
    "account.currency": "Preferred currency",
    "contact.message": "Message *",
    "contact.send": "Send to JauraStore",
    "contact.sent": "Message form disabled. Please use WhatsApp to contact the store.",
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
    "rev.rated": "Rated {avg} out of 5 from {n} reviews",
    "rev.email": "Email used for your order",
    "rev.gate": "Reviews are for verified buyers — use the email address from your order.",
    "rev.notBought": "Reviews are for customers who bought this product. Use the same email as your order.",
    "rev.filters": "Review filters",
    "rev.sort": "Sort",
    "rev.filter": "Filter",
    "rev.newest": "Newest first",
    "rev.oldest": "Oldest first",
    "rev.highest": "Highest rated",
    "rev.lowest": "Lowest rated",
    "rev.allRatings": "All ratings",
    "rev.noMatch": "No reviews match this filter.",
    "ck.discount": "Discount",
    "ck.promoLabel": "Referral / Promo code",
    "ck.promoPh": "e.g. JA-ABC23",
    "ck.promoApply": "Apply",
    "ck.promoOk": "Code applied — {p}% off your order.",
    "ck.promoBad": "That code was not recognised or has expired.",
    "ref.kicker": "Your referral code",
    "ref.blurb": "Share this code with friends. They get a discount at checkout — and when they order, you earn a reward coupon.",
    "ref.share": "Share referral code",
    "ref.copy": "Copy code",
    "ref.copied": "Copied — send it to a friend!",
    "ref.shareText": "Shop at J Aura Store and get a discount with my referral code {code} at checkout! {url}",
    "nf.title": "This page is not in the boutique",
    "nf.lead": "The link may have changed. Continue shopping.",
    "nf.back": "Back to Jaura Store",
    "price.onRequest": "Price on request",
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
    "cat.perfume": "Parfum",
    "ticker": "• BOUTIQUE LUXE • LAGOS & COTONOU • WHATSAPP +229 68 95 31 10 • PAYEZ EN ₦ OU CFA •",
    "conv.banner": "Clients du Bénin 🇧🇯 : passez commande maintenant, livraison au prochain envoi",
    "home.kicker": "Bienvenue. Prêt à faire vos achats ?",
    "home.heroLine": "Élégance, en toute simplicité et des essentiels choisis",
    "home.explore": "Explorer",
    "pager.prev": "Préc.",
    "pager.next": "Suiv.",
    "shop.loadingLive": "Chargement des produits et du stock en direct…",
    "ck.orderMissing": "Impossible de charger cette commande sur cet appareil.",
    "ck.queuedSaved": "Pas de connexion pour le moment — votre commande et votre reçu sont enregistrés sur ce téléphone et seront envoyés automatiquement dès votre retour en ligne. Conservez votre numéro de commande.",
    "ck.queuedWaiting": "Pas de connexion pour le moment — gardez cette page ouverte pendant l'envoi de votre commande. Conservez votre numéro de commande.",
    "toast.orderIdCopied": "N° de commande copié : {id}",
    "store.paused": "La boutique est momentanément en pause. Écrivez-nous sur WhatsApp.",
    "cart.oos": "Rupture de stock",
    "cart.onlyLeft": "Il ne reste que {n} article(s) en stock",
    "cart.verifyFailed": "Impossible de vérifier le stock actuel. Veuillez réessayer.",
    "contact.whatsapp": "Contactez la boutique via le lien WhatsApp de cette page.",
    "sync.sending": "Envoi…",
    "pager.prevLabel": "Page précédente",
    "pager.nextLabel": "Page suivante",
    "home.title": "Essentiels choisis",
    "home.lead": "Tout ce que vous aimez, dans une seule boutique. Payez en CFA ou en ₦.",
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
    "home.uploadProof": "Envoyer votre photo de paiement",
    "home.uploadProofText": "Déjà payé ? Envoyez vous-même la photo de votre paiement — depuis votre téléphone, sans passer par WhatsApp.",
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
    "home.z1p": "Cotonou, Calavi, Porto-Novo et régions voisines. Livraison sous 4 à 12 jours ouvrés.",
    "home.z2k": "Lagos Mainland",
    "home.z2t": "Yaba à Ikeja",
    "home.z2p": "Yaba, Ikeja, Surulere et tout le Mainland central. Livraison sous 24 à 72 heures.",
    "home.z3k": "Lagos Island",
    "home.z3t": "Lekki, VI, Ikoyi",
    "home.z3p": "Zones côtières de l’Island. Livraison sous 24 à 72 heures.",
    "home.z4k": "Afrique de l’Ouest",
    "home.z4t": "Lomé et voisins",
    "home.z4p": "Togo et plusieurs pays d’Afrique de l’Ouest. Livraison sous 4 à 12 jours ouvrés.",
    "footer.blurb": "Mode, beauté et essentiels de vie. Le luxe en gros en Afrique de l’Ouest.",
    "footer.maison": "Maison",
    "footer.collection": "La collection",
    "footer.vision": "La vision J Aura",
    "footer.client": "Relation client",
    "footer.atelier": "Connexion Atelier",
    "footer.visit": "Visite & livraison",
    "footer.follow": "Suivez-nous",
    "footer.v1": "Cotonou, Calavi, Porto-Novo",
    "footer.v2": "Lagos Mainland & Island",
    "footer.v3": "Lomé et Afrique de l’Ouest",
    "footer.v4": "4–12 jours ouvrés au Bénin",
    "footer.whatsapp": "WhatsApp boutique",
    "footer.phone": "Téléphone",
    "footer.email": "E-mail",
    "footer.waHelp": "Notre équipe est prête à vous aider sur WhatsApp pour un achat simple.",
    "footer.contactUs": "Nous contacter",
    "footer.copy": "© {year} J Aura Store. Tous droits réservés.",
    "footer.pay": "Payez en CFA ou ₦ · 1 ₦ = {rate} CFA",
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
    "pdp.productNote": "Note sur le produit",
    "pdp.add": "Ajouter au panier",
    "pdp.oos": "Rupture de stock",
    "pdp.payIn": "Payer en {cur}",
    "pdp.hint": "Touchez CFA ou ₦ dans le menu pour changer les prix · SKU {sku}",
    "bulk.label": "Remise quantité",
    "cart.bulkApplied": "Remise quantité appliquée — −{p} % sur cet article.",
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
    "promo.referral": "Commandez plus de 20,000 ₦ (8,800 CFA) et recevez votre code de parrainage personnel — partagez-le et vos amis bénéficient d'une réduction au paiement.",
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
    "ck.completedTitle": "Commande terminée",
    "ck.customerInfo": "Vos informations de commande",
    "ck.name": "Nom",
    "ck.formInstruction": "Veuillez remplir ce formulaire avant de passer la commande.",
    "ck.errorSummary": "Corrigez les champs indiqués avant de passer la commande.",
    "ck.emptyRedirect": "Votre panier est vide. Ajoutez d’abord des articles au panier. Redirection vers la boutique.",
    "ck.errorFirstName": "Saisissez votre prénom.",
    "ck.errorFirstNameInvalid": "Saisissez un prénom valide avec des lettres.",
    "ck.errorLastName": "Saisissez votre nom.",
    "ck.errorLastNameInvalid": "Saisissez un nom valide avec des lettres.",
    "ck.errorCountry": "Choisissez votre pays ou région.",
    "ck.errorAddress": "Saisissez votre adresse.",
    "ck.errorAddressInvalid": "Ajoutez plus de détails à votre adresse.",
    "ck.errorCity": "Saisissez votre ville.",
    "ck.errorCityInvalid": "Saisissez une ville valide.",
    "ck.errorZone": "Choisissez une zone de livraison.",
    "ck.errorPhone": "Saisissez votre numéro de téléphone.",
    "ck.errorPhoneInvalid": "Saisissez un numéro valide, avec l’indicatif si possible.",
    "ck.errorEmail": "Saisissez votre adresse e-mail.",
    "ck.errorEmailInvalid": "Saisissez une adresse e-mail valide, par exemple nom@exemple.com.",
    "ck.errorProof": "Ajoutez votre reçu de paiement avant de passer la commande.",
    "ck.errorRequired": "Veuillez remplir ce champ pour finaliser votre commande.",
    "ck.errorRequiredInvalid": "Veuillez remplir ce champ pour finaliser votre commande.",
    "ck.empty": "Votre panier est vide. Ajoutez des produits avant de payer.",
    "ck.return": "Retour boutique",
    "ck.billing": "Facturation",
    "ck.guestTitle": "Vous commandez comme invité ?",
    "ck.guestIntro": "Aucun compte n’est nécessaire. Saisissez votre e-mail ci-dessous pour recevoir la confirmation et un éventuel rappel de panier.",
    "ck.guestAccountLink": "Vous voulez un compte ? Connectez-vous ou créez-en un",
    "ck.guestEmailHint": "Nécessaire pour les confirmations et rappels invités. Aucun compte ne sera créé.",
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
    "ck.zonePlaceholder": "Choisissez une zone de livraison",
    "ck.zoneGroupNaira": "Nigéria (₦ Naira)",
    "ck.zoneGroupCfa": "Bénin & Togo (CFA)",
    "ck.zoneGroupPickup": "Retrait / point relais",
    "ck.shipLagos": "Lagos (Mainland et Island) : sous 24 à 72 heures.",
    "ck.shipNigeria": "Autres États et pôles du Nigéria (Ogun, Abuja, Rivers, Edo, Delta, Ekiti, Osun, Oyo, Kwara, Abia, Anambra, etc.) : sous 3 à 7 jours ouvrés.",
    "ck.shipBenin": "République du Bénin 🇧🇯 (Cotonou, Abomey-Calavi, Porto-Novo) : sous 4 à 12 jours ouvrés.",
    "ck.shipTogo": "Togo 🇹🇬 (Lomé) : sous 4 à 12 jours ouvrés.",
    "ck.zoneEta.lagos": "Délai estimé pour cette zone : sous 24 à 72 heures.",
    "ck.zoneEta.ngOther": "Délai estimé pour cette zone : sous 3 à 7 jours ouvrés.",
    "ck.zoneEta.west": "Délai estimé pour cette zone : sous 4 à 12 jours ouvrés.",
    "ck.zoneEta.pickup": "Prêt pour le retrait — aucun délai de livraison.",
    "ck.bjMin": "Livraisons au Bénin : commande minimum de 5,000 CFA (environ 12,000 nairas).",
    "ck.zoneBenin": "Bénin — Cotonou, Calavi, Porto-Novo",
    "ck.zoneMain": "Lagos Mainland — Yaba, Ikeja, Surulere",
    "ck.zoneIsland": "Lagos Island — Lekki, VI, Ikoyi",
    "ck.zoneWa": "Afrique de l’Ouest — Lomé et voisins",
    "ck.fare.pickup": "Retrait uniquement",
    "ck.fareTitle": "Ajoutez le total des articles au tarif de livraison de votre zone *",
    "ck.fareIntro": "Ce sont des fourchettes. Le tarif exact dépend de votre localité et du poids, et se confirme sur WhatsApp.",
    "ck.fare.mainland": "Lagos Mainland (₦2,000 – ₦5,000)",
    "ck.fare.island": "Lagos Island (₦3,500 – ₦6,000)",
    "ck.fare.ngOther": "Autres États du Nigéria (confirmer sur WhatsApp)",
    "ck.fare.cotonou": "Cotonou (1,000 – 3,000 CFA)",
    "ck.fare.calavi": "Calavi (1,500 – 3,500 CFA)",
    "ck.fare.porto": "Porto-Novo (1,500 – 3,500 CFA)",
    "ck.fare.bjOther": "Autres localités du Bénin (confirmer sur WhatsApp)",
    "ck.fare.lome": "Lomé (2,500 – 3,500 CFA)",
    "ck.fare.tgOther": "Autres localités du Togo (confirmer sur WhatsApp)",
    "ck.interstateNotice": "🚚 Livraison inter-États : Votre commande sera expédiée par coursier. Le suivi et les informations d'expédition vous seront envoyés directement par téléphone/WhatsApp.",
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
    "ck.paymentMethod": "Choisissez un moyen de paiement",
    "ck.payCfa": "Paiement en CFA",
    "ck.payCfaHint": "Choisissez le moyen de paiement CFA du Bénin ou du Togo.",
    "ck.payNgn": "Virement en nairas",
    "ck.payNgnHint": "Payez en nairas avec les coordonnées ci-dessous.",
    "ck.payBeninCfa": "Paiement en CFA du Bénin",
    "ck.payBeninCfaHint": "Payez avec le prestataire et le compte du Bénin ci-dessous.",
    "ck.payTogoCfa": "Paiement en CFA du Togo",
    "ck.payTogoCfaHint": "Payez avec le prestataire et le compte du Togo ci-dessous.",
    "ck.beninPayDetails": "Coordonnées de paiement CFA — Bénin",
    "ck.togoPayDetails": "Coordonnées de paiement CFA — Togo",
    "ck.togoFeeNotice": "Moov Money Togo peut facturer des frais pour les transferts transfrontaliers.",
    "ck.payUnconfigured": "Les coordonnées de paiement ne sont pas encore configurées. Contactez-nous sur WhatsApp.",
    "ck.provider": "Prestataire",
    "ck.accountHolder": "Titulaire du compte",
    "ck.account": "Coordonnées de paiement",
    "ck.advance": "Choisissez comment payer",
    "ck.payIntro": "Choisissez l’un des trois moyens de paiement. La devise de la commande suit votre choix.",
    "ck.ourBank": "Coordonnées de paiement",
    "ck.bank": "Banque",
    "ck.accNo": "Compte / numéro de téléphone",
    "ck.exactPayment": "Veuillez envoyer le montant exact afin que votre commande soit confirmée.",
    "ck.fareNote": "Les frais de transport ne sont pas fixes : ils varient selon votre localité et le poids des articles. Après paiement, ouvrez WhatsApp pour votre tarif précis.",
    "ck.uploadReceipt": "Télécharger le reçu de paiement",
    "ck.legal": "En passant votre commande, vous fournissez votre nom, votre adresse, votre numéro de téléphone, votre numéro de commande et les articles commandés. Votre commande sera bientôt confirmée.",
    "ck.fileTypes": "JPG, PNG, PDF ou DOC — jusqu’à 16 Mo. Les grandes images sont compressées sous 1 Mo avant l’envoi.",
    "ck.recaptcha": "Ce site est protégé par reCAPTCHA ; les <a href=\"https://policies.google.com/privacy\" target=\"_blank\" rel=\"noopener\">règles de confidentialité</a> et les <a href=\"https://policies.google.com/terms\" target=\"_blank\" rel=\"noopener\">conditions d’utilisation</a> de Google s’appliquent.",
    "ck.place": "Passer la commande",
    "ck.placing": "Envoi de la commande…",
    "ck.preparing": "Préparation de votre photo…",
    "ck.doneKicker": "Commande reçue",
    "ck.thanks": "Merci. Votre commande a bien été reçue.",
    "ck.orderNo": "N° de commande",
    "ck.date": "Date",
    "ck.payMethod": "Mode de paiement",
    "ck.waiting": "En attente de confirmation",
    "ck.emailConfirmation": "Un message de confirmation sera envoyé à votre adresse e-mail une fois votre commande et votre paiement confirmés.",
    "ck.saveId": "Notez cet identifiant. JauraStore confirmera votre paiement. Les frais de transport seront discutés sur WhatsApp.",
    "ck.idHelp": "Donnez cet identifiant à JauraStore ou indiquez-le sur le virement.",
    "ck.track": "Suivre cette commande",
    "ck.fareRange": "Les frais de transport varient selon votre localité et le poids des produits. Appuyez ci-dessous pour WhatsApp avec votre n° de commande et votre adresse, afin de recevoir votre tarif.",
    "ck.uploadHere": "Téléverser le reçu de paiement",
    "ck.uploadNow": "WhatsApp pour le transport",
    "ck.copyId": "Copier le n° de commande",
    "ck.waId": "WhatsApp cet identifiant",
    "ck.waPick": "Confirmer sur WhatsApp avec\u00a0:",
    "ck.waPickCheckout": "Ligne WhatsApp pour votre commande\u00a0:",
    "ck.waNg": "WhatsApp Nigeria",
    "ck.waBj": "WhatsApp B\u00e9nin / Togo",
    "dock.home": "Accueil",
    "dock.shop": "Boutique",
    "dock.search": "Recherche",
    "dock.account": "Compte",
    "dock.wish": "Envies",
    "dock.cart": "Panier",
    "home.bestsellers": "Meilleures ventes",
    "home.mostViewed": "Les plus consultés en ce moment",
    "home.shopAllArrow": "Tout voir ›",
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
    "toast.cfa": "Prix affichés en CFA",
    "toast.langEn": "Langue : anglais",
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
    "order.waitMsg": "Téléversez votre reçu de paiement ici ou envoyez-le sur WhatsApp. JauraStore confirmera votre paiement.",
    "order.lead": "Utilisez l’identifiant de votre confirmation, par exemple JA-M8K2Q1. Aucun compte n’est nécessaire.",
    "order.whatsapp": "Demander des nouvelles sur WhatsApp",
    "pay.kicker": "Paiement",
    "pay.title": "Envoyer votre capture",
    "pay.lead": "Aucun envoi n’est nécessaire sur le site. Après le virement, envoyez-nous la capture du paiement sur WhatsApp. JauraStore confirmera votre paiement.",
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
    "about.p3": "Payez en CFA ou en naira. Une maison, deux devises, le même soin.",
    "about.enter": "Entrer dans la collection",
    "about.founders": "Fondatrices",
    "about.names": "J Aura Store",
    "about.foundersP": "Une maison sœur bâtie sur le goût, la confiance et l’accès. Chaque pièce est choisie avec soin — sac et foulard, ménagère dorée ou essentiel de soin.",
    "contact.kicker": "Relation client",
    "contact.title": "Nous sommes sur WhatsApp",
    "contact.lead": "Notre équipe vous accompagne. Terminez la commande, payez en CFA ou naira, puis envoyez-nous la capture du paiement sur WhatsApp. JauraStore confirmera votre paiement.",
    "contact.phone": "Téléphone",
    "contact.email": "E-mail",
    "contact.houses": "Maisons",
    "contact.hours": "Heures d'ouverture",
    "contact.wa": "Nous écrire sur WhatsApp",
    "faq.kicker": "Aide",
    "faq.title": "Questions fréquentes",
    "faq.q1": "Comment commander ?",
    "faq.a1": "01 Choisissez vos articles. 02 Vérifiez le panier. 03 Validez la commande. 04 Payez en CFA ou ₦. 05 Envoyez-nous la capture du paiement sur WhatsApp. 06 JauraStore confirmera votre paiement.",
    "faq.q2": "Puis-je payer en CFA et en naira ?",
    "faq.a2": "Oui. Le naira est le prix. Le CFA sur le site est converti à 1 ₦ = {rate}. Touchez ₦ ou CFA dans le menu pour choisir comment payer. À la caisse, choisissez virement CFA ou virement ₦ naira.",
    "faq.q3": "Quel est le taux ?",
    "faq.a3": "Le naira est le prix principal. Taux : 1 ₦ = {rate} CFA. Sans prix en naira, l’article reste en CFA.",
    "faq.q4": "Où livrez-vous ?",
    "faq.a4": "Lagos (Mainland et Island) : sous 24 à 72 heures. Autres États et pôles du Nigéria (Ogun, Abuja, Rivers, Edo, Delta, Ekiti, Osun, Oyo, Kwara, Abia, Anambra, etc.) : sous 3 à 7 jours ouvrés. République du Bénin (Cotonou, Abomey-Calavi, Porto-Novo) : sous 4 à 12 jours ouvrés. Togo (Lomé) : sous 4 à 12 jours ouvrés. Les frais sont confirmés selon la ville.",
    "faq.q5": "Comment payer ?",
    "faq.a5": "Faites le virement avec les coordonnées de la devise choisie, puis téléchargez le reçu bancaire sur le formulaire de commande. JauraStore confirmera votre paiement. Les frais de transport se discutent sur WhatsApp.",
    "faq.q6": "Comment suivre ma commande ?",
    "faq.a6": "Écrivez-nous sur WhatsApp avec votre identifiant de commande (par exemple JA-M8K2Q1) et nous vous dirons si elle est en attente, confirmée ou refusée.",
    "faq.q7": "Comment vous joindre ?",
    "faq.a7": "WhatsApp +229 68 95 31 10 · Tél. +229 68 95 31 10 / +234,916,167 0236 · jaurastore@gmail.com · Lagos, Nigéria et Cotonou, Bénin.",
    "lang.group": "Langue",
    "wish.title": "Ma liste d'envies",
    "wish.empty": "Votre liste d'envies est vide.",
    "wish.add": "Touchez le cœur sur un produit pour le garder ici.",
    "wish.shop": "Voir les produits",
    "account.title": "Mon compte",
    "account.lead": "Créez un compte ou connectez-vous pour gérer votre profil et voir vos commandes.",
    "account.orders": "Vos commandes",
    "account.empty": "Aucune commande liée à ce compte pour le moment.",
    "account.email": "E-mail *",
    "account.login": "Connexion",
    "account.logout": "Déconnexion",
    "account.hello": "Connecté : {email}",
    "account.needEmail": "Entrez l’e-mail de votre commande.",
    "account.password": "Mot de passe *",
    "account.register": "Créer un compte",
    "account.registerLead": "Le paiement invité reste possible. Un compte vous permet de voir vos commandes.",
    "account.name": "Nom",
    "account.pwHint": "Au moins 8 caractères, avec une lettre et un chiffre.",
    "account.newPassword": "Nouveau mot de passe *",
    "account.savePassword": "Enregistrer le mot de passe",
    "account.back": "Retour à la connexion",
    "account.profile": "Profil",
    "account.saveProfile": "Enregistrer le profil",
    "account.saved": "Profil enregistré.",
    "account.changePassword": "Changer le mot de passe",
    "account.currentPassword": "Mot de passe actuel *",
    "account.pwSaved": "Mot de passe mis à jour.",
    "account.guestOk": "Vous pouvez toujours commander en invité, sans compte.",
    "account.badLogin": "E-mail ou mot de passe invalide.",
    "account.badRegister": "Impossible de créer ce compte.",
    "account.currency": "Devise préférée",
    "contact.message": "Message *",
    "contact.send": "Envoyer à JauraStore",
    "contact.sent": "Formulaire désactivé. Contactez la boutique par WhatsApp.",
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
    "rev.rated": "Note de {avg} sur 5 · {n} avis",
    "rev.email": "E-mail utilisé pour votre commande",
    "rev.gate": "Les avis sont réservés aux acheteurs vérifiés — utilisez l'adresse e-mail de votre commande.",
    "rev.notBought": "Les avis sont réservés aux clients ayant acheté ce produit. Utilisez le même e-mail que votre commande.",
    "rev.filters": "Filtres des avis",
    "rev.sort": "Trier",
    "rev.filter": "Filtrer",
    "rev.newest": "Plus récents",
    "rev.oldest": "Plus anciens",
    "rev.highest": "Mieux notés",
    "rev.lowest": "Moins bien notés",
    "rev.allRatings": "Toutes les notes",
    "rev.noMatch": "Aucun avis ne correspond à ce filtre.",
    "ck.discount": "Réduction",
    "ck.promoLabel": "Code de parrainage / promo",
    "ck.promoPh": "ex. JA-ABC23",
    "ck.promoApply": "Appliquer",
    "ck.promoOk": "Code appliqué — {p}% de réduction sur votre commande.",
    "ck.promoBad": "Ce code n'a pas été reconnu ou a expiré.",
    "ref.kicker": "Votre code de parrainage",
    "ref.blurb": "Partagez ce code avec vos amis. Ils profitent d'une réduction — et quand ils commandent, vous gagnez un coupon cadeau.",
    "ref.share": "Partager mon code",
    "ref.copy": "Copier le code",
    "ref.copied": "Copié — envoyez-le à un ami !",
    "ref.shareText": "Achetez chez J Aura Store et profitez d'une réduction avec mon code de parrainage {code} au paiement ! {url}",
    "nf.title": "Cette page n’est pas dans la boutique",
    "nf.lead": "Le lien a peut-être changé. Continuez vos achats.",
    "nf.back": "Retour à Jaura Store",
    "price.onRequest": "Prix sur demande",
  };

  const dict = { en, fr };

  /* --------------------------------------------------------------
   * LANGUAGE = THE VISITOR'S DEVICE (owner directive 2026-10-19, final).
   *
   *   1. `?lang=fr` / `?lang=en` on the link that brought them in — a support
   *      or WhatsApp broadcast link, or the QA/e2e harness. Remembered for
   *      that BROWSING SESSION so following the link does not flip back to
   *      English on the next page.
   *   2. Otherwise the device/browser locale decides, on EVERY entry:
   *      fr, fr-FR, fr-BJ, fr-CI, … → French storefront;
   *      en, en-NG, en-US, …        → English;
   *      anything else              → English, the authored language.
   *      The list order is the browser's own order, so ["en-NG","fr"] is
   *      English and ["fr-FR","en"] is French.
   *   3. There is NO language switch in the interface and NO permanent stored
   *      choice: the device is the single authority, and changing it changes
   *      the shop. That is why nothing here writes localStorage any more and
   *      why migrateStaleLocale() clears the retired key.
   *
   * HISTORY, so this is not "fixed" back again by accident: on 2026-09-27 the
   * device read was switched off because a visitor could not get out of French
   * (the header/menu toggles had just been removed AND the stored choice was
   * written but never read back). A floating EN|FR pill was tried on
   * 2026-10-19 and the owner removed it the same day: the storefront carries no
   * language control. What makes THIS version safe instead of a repeat of the
   * bug is that the language is now never latched — a wrong-looking French page
   * is one browser-language setting away from English, and no stale storage can
   * keep overriding the device from a previous visit.
   *
   * What the language drives: the interface text, the product/category French
   * fields from the live catalogue, and — through js/store.js currencyLocked()
   * — F CFA as the display currency at the live exchange rate. Geography and IP
   * are never consulted: no such signal exists in this app, and a francophone
   * device in Lagos and an anglophone one in Cotonou are both real customers.
   * -------------------------------------------------------------- */
  function detectBrowserLang() {
    // The device's own language list, in the order the browser reports it.
    // First entry wins over later ones, exactly as a browser picks a page
    // language: ["en-NG","fr"] is English, ["fr-BJ","en"] is French. An
    // unknown language is English - the shop's authored language - rather
    // than a guess.
    let tags = [];
    try {
      if (typeof navigator !== "undefined") {
        if (Array.isArray(navigator.languages) && navigator.languages.length) tags = navigator.languages;
        else if (navigator.language) tags = [navigator.language];
        else if (navigator.userLanguage) tags = [navigator.userLanguage];
      }
    } catch (e) { tags = []; }
    for (let i = 0; i < tags.length; i += 1) {
      const tag = String(tags[i] || "").toLowerCase().trim();
      if (!tag) continue;
      if (tag === "fr" || tag.indexOf("fr-") === 0) return "fr";
      if (tag === "en" || tag.indexOf("en-") === 0) return "en";
    }
    return "en";
  }

  function validLang(v) {
    return v === "fr" || v === "en" ? v : null;
  }

  /** Retire the permanent language store, validate the session one.
   *
   * localStorage: this key used to hold an EXPLICIT, lasting choice made on a
   * visible control. That control is gone by owner instruction (2026-10-19), so
   * a valid value left here would override the device with nothing on screen to
   * undo it — a one-way door. It is therefore cleared outright, valid or not,
   * and the visitor follows their browser from this load on.
   *
   * sessionStorage: only corruption is possible here, because a value is
   * written solely from `?lang=fr|en` / I18N.setLang(). Anything that is not
   * exactly "en" or "fr" is purged so it can never leak French / F CFA onto a
   * fresh English visitor (owner fix 2026-09-27, requirement: clear/migrate
   * stale locale storage). */
  function migrateStaleLocale() {
    try { localStorage.removeItem(KEY); } catch (e) {}
    // sessionStorage: a garbage value here is corruption, not a choice.
    try {
      const v = sessionStorage.getItem(KEY);
      if (v != null && !validLang(v)) sessionStorage.removeItem(KEY);
    } catch (e) {}
  }
  migrateStaleLocale();

  /* An explicit in-session override: set as soon as this tab has read (or
   * written) a choice, so repeated calls within the same load are cheap and
   * consistent. */
  let sessionOverride = null;

  function persist(value) {
    // Session scope, deliberately: sessionStorage dies with the tab, and so
    // does the mirror cookie (no max-age). app.py reads that cookie for the
    // <html lang> of the first paint, so the server and this file resolve the
    // SAME way on every load — when either one has a value, the other must
    // have the same value, or the page flashes from one language to the other.
    try { sessionStorage.setItem(KEY, value); } catch (e) {}
    try { document.cookie = "jaura_lang=" + value + ";path=/;SameSite=Lax"; } catch (e) {}
  }

  function storedChoice() {
    try {
      const v = validLang(sessionStorage.getItem(KEY));
      if (v) return v;
    } catch (e) {}
    return null;
  }

  function readStored() {
    // lang()/t() are called many times per render (every translated string
    // on the page asks), so once this load has already resolved a language -
    // from the URL or from this session - answer from that cached value instead
    // of re-parsing the URL and re-writing sessionStorage/the cookie on every
    // single call. That repeated I/O was pure waste, and on a slow device it
    // could noticeably delay whatever runs after the first batch of
    // translations (e.g. a checkout page's gateway selection).
    if (sessionOverride) return sessionOverride;
    try {
      const picked = validLang(new URLSearchParams(location.search).get("lang"));
      if (picked) {
        sessionOverride = picked;
        persist(picked);                // for the rest of THIS browsing session
        return picked;
      }
    } catch (e) {}
    const stored = storedChoice();
    if (stored) return stored;
    // Nothing asked for a language on the way in: the device decides, on this
    // entry and on every entry (owner directive 2026-10-19). Deliberately NOT
    // persisted — a device locale is an observation, not a choice, so changing
    // the phone's language changes the shop immediately and nothing can latch
    // a visitor into a language they cannot switch out of.
    return detectBrowserLang();
  }

  function lang() {
    return readStored() === "fr" ? "fr" : "en";
  }

  function setLang(next) {
    const value = validLang(next) || "en";
    sessionOverride = value;
    persist(value);
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

  /* The live admin exchange rate as display text (French uses a comma), so
   * rate copy never shows a stale hardcoded figure. null when no rate has
   * been confirmed yet - the {rate} token is then left in place rather than
   * filled with an invented number. */
  function rateToken() {
    try {
      const JA = window.JA;
      const live = JA && (
        (JA.getSiteConfig && Number((JA.getSiteConfig() || {}).cfaRate)) ||
        (JA.settings && Number(JA.settings().rate)));
      if (live > 0) {
        const text = String(live);
        return lang() === "fr" ? text.replace(".", ",") : text;
      }
    } catch (e) {}
    return null;
  }

  // Shown while the live rate is still in flight. Boot paints before
  // GET /api/site can answer, so copy that quotes the rate needs a value
  // that is neither a raw "{rate}" token nor a hardcoded number that could
  // contradict what the owner actually set.
  const RATE_PENDING = "\u2026";

  function expandRate(text) {
    const rate = rateToken();
    const value = rate == null ? RATE_PENDING : rate;
    return String(text).split("{rate}").join(value);
  }

  /* I18N_PHRASES with {rate} expanded, so the French sweep can match DOM text
   * that was rendered with the live rate. Cached per rate token. */
  let expandedPhrasesCache = null;
  let expandedPhrasesRate;
  function phrases() {
    const rate = rateToken();
    if (expandedPhrasesCache && expandedPhrasesRate === rate) return expandedPhrasesCache;
    const table = window.I18N_PHRASES || {};
    const out = {};
    Object.keys(table).forEach((k) => { out[expandRate(k)] = expandRate(table[k]); });
    expandedPhrasesCache = out;
    expandedPhrasesRate = rate;
    return out;
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
    if (s.indexOf("{rate}") >= 0) s = expandRate(s);
    return s;
  }

  /* ------------------------------------------------------------------
   * JavaScript builds most of the wording: the payment form, the category
   * names, the whole store management, every toast. Those strings cannot carry
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
    const phraseTable = phrases();
    Object.keys(phraseTable).forEach((k) => out.add(phraseTable[k]));
    return out;
  }
  let french = null;

  /* sentences worth swapping inside a longer paragraph, longest first so a
   * short phrase never eats part of a longer one */
  let subKeys = null;
  function subKeysSorted() {
    if (subKeys) return subKeys;
    const table = phrases();
    subKeys = Object.keys(table)
      .filter((k) => k.length >= 12 && !/[{}]$/.test(k))
      .sort((a, b) => b.length - a.length);
    return subKeys;
  }

  function translate(s) {
    if (!s || !/[A-Za-z]/.test(s)) return null;
    const flat = s.replace(/\s+/g, " ").trim();
    if (lang() !== "fr") return null;
    const table = phrases();
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

  /* ------------------------------------------------- dynamic product copy
   * The server ships a French description for every catalogue piece it can
   * (data/descriptions_fr.json + the owner's descriptionFr field). This is the
   * last-resort layer for copy that arrives WITHOUT one: an owner-written
   * product description, an old cached row, or a phrase inside a longer
   * paragraph. It never invents a translation for unknown text and it never
   * touches a product NAME (a brand or SKU is not a sentence) - it only
   * replaces vocabulary it is certain about, longest phrase first, so
   * "100% human hair" is translated before the bare word "hair" could be.
   *
   * Every pair below is drawn from the shop's own catalogue vocabulary, so the
   * French a shopper reads here matches the French in the shipped table.
   */
  const COPY_GLOSSARY = [
    ["made with beautiful, high-quality printed materials", "confectionn\u00e9 dans de beaux tissus imprim\u00e9s de haute qualit\u00e9"],
    ["made with beautiful high-quality printed materials", "confectionn\u00e9 dans de beaux tissus imprim\u00e9s de haute qualit\u00e9"],
    ["for all body types with comfort and elegance", "pour toutes les morphologies, avec confort et \u00e9l\u00e9gance"],
    ["works for both iphone and type c", "compatible iPhone et USB-C"],
    ["it blows well and comes with light too", "il souffle bien et \u00e9claire aussi"],
    ["it captures sound and neglect noise", "il capte le son et r\u00e9duit le bruit"],
    ["the exact print may vary from what is shown", "l\u2019imprim\u00e9 exact peut varier par rapport \u00e0 la photo"],
    ["a comprehensive 6-piece skincare kit", "un coffret complet de 6 soins de la peau"],
    ["lightweight, non-greasy sunscreen", "cr\u00e8me solaire l\u00e9g\u00e8re et non grasse"],
    ["designed for brightening, hydrating", "con\u00e7u pour \u00e9claircir, hydrater"],
    ["targets dullness, uneven skin tone", "cible la ternit\u00e9 et le teint irr\u00e9gulier"],
    ["for all body sizes", "pour toutes les morphologies"],
    ["for all body size", "pour toutes les morphologies"],
    ["for all sizes", "pour toutes les tailles"],
    ["for all body types", "pour toutes les morphologies"],
    ["all body size", "toutes les morphologies"],
    ["100% human hair", "cheveux 100 % humains"],
    ["100 % human hair", "cheveux 100 % humains"],
    ["human hair", "cheveux humains"],
    ["full closure", "closure complet"],
    ["t frontal", "frontal en T"],
    ["fringe bob", "bob \u00e0 frange"],
    ["closure bob", "bob closure"],
    ["skull cap", "bonnet"],
    ["press on nails", "faux ongles"],
    ["press on nail", "faux ongles"],
    ["with a glue", "avec de la colle"],
    ["with glue", "avec de la colle"],
    ["pink silk pillowcase", "taie d\u2019oreiller en soie rose"],
    ["pillowcase", "taie d\u2019oreiller"],
    ["wall suction hanger", "crochet ventouse mural"],
    ["non sticking coating", "rev\u00eatement antiadh\u00e9sif"],
    ["non-stick coating", "rev\u00eatement antiadh\u00e9sif"],
    ["sandwich maker", "appareil \u00e0 sandwich"],
    ["dish washer", "\u00e9gouttoir \u00e0 vaisselle"],
    ["magnetic powerbank", "batterie externe magn\u00e9tique"],
    ["powerbank", "batterie externe"],
    ["wireless chargers", "chargeurs sans fil"],
    ["wireless charger", "chargeur sans fil"],
    ["magnetic wireless chargers", "chargeurs sans fil magn\u00e9tiques"],
    ["bluetooth headset", "casque Bluetooth"],
    ["wireless earphones", "\u00e9couteurs sans fil"],
    ["wireless mic", "micro sans fil"],
    ["torch light", "lampe torche"],
    ["interchangeable straps", "bracelets interchangeables"],
    ["7 straps", "7 bracelets"],
    ["smartwatch", "montre connect\u00e9e"],
    ["wristwatch", "montre"],
    ["press on nail", "faux ongles"],
    ["gift set", "coffret cadeau"],
    ["jewelry set", "parure de bijoux"],
    ["jewellery set", "parure de bijoux"],
    ["key chain", "porte-cl\u00e9s"],
    ["key holder", "porte-cl\u00e9s"],
    ["hand cream", "cr\u00e8me pour les mains"],
    ["hair band", "bandeau cheveux"],
    ["sun cream", "cr\u00e8me solaire"],
    ["face cream", "cr\u00e8me visage"],
    ["facial cleanser", "nettoyant visage"],
    ["essence toner", "lotion tonique"],
    ["face serum", "s\u00e9rum visage"],
    ["vitamin c", "vitamine C"],
    ["anti-aging", "anti-\u00e2ge"],
    ["anti aging", "anti-\u00e2ge"],
    ["fine lines", "ridules"],
    ["uneven skin tone", "teint irr\u00e9gulier"],
    ["broad-spectrum", "large spectre"],
    ["skincare", "soins de la peau"],
    ["skin texture", "texture de la peau"],
    ["moisturizing", "hydratant"],
    ["brightening", "\u00e9claircissant"],
    ["hydrating", "hydratant"],
    ["high-quality", "de haute qualit\u00e9"],
    ["high quality", "de haute qualit\u00e9"],
    ["quality is great", "la qualit\u00e9 est excellente"],
    ["quality and comfortable", "de qualit\u00e9 et confortables"],
    ["for slim wrist", "pour poignets fins"],
    ["suitable for any occasion", "convient \u00e0 toute occasion"],
    ["for day to day activities", "pour les activit\u00e9s du quotidien"],
    ["for day to day", "pour le quotidien"],
    ["different prints available", "plusieurs imprim\u00e9s disponibles"],
    ["prints may vary", "les imprim\u00e9s peuvent varier"],
    ["before delivery", "avant la livraison"],
    ["randomly selected", "choisi au hasard"],
    ["randomly picked", "choisi au hasard"],
    ["available in all colors", "disponible dans toutes les couleurs"],
    ["available in all colours", "disponible dans toutes les couleurs"],
    ["black available", "disponible en noir"],
    ["comes with a box", "livr\u00e9 avec sa bo\u00eete"],
    ["comes in a box", "livr\u00e9 dans sa bo\u00eete"],
    ["comes with 2 mic", "livr\u00e9 avec 2 micros"],
    ["comes with", "livr\u00e9 avec"],
    ["kit contents", "contenu du coffret"],
    ["it contains", "il contient"],
    ["comprises of", "comprend"],
    ["contains:", "contient :"],
    ["live streaming", "streaming en direct"],
    ["video shooting", "tournage vid\u00e9o"],
    ["content creators", "cr\u00e9ateurs de contenu"],
    ["online tutorials", "tutoriels en ligne"],
    ["latest heels", "talons tendance"],
    ["a dozen", "une douzaine"],
    ["wipes", "lingettes"],
    ["packs", "paquets"],
    ["press on", "faux"],
    ["inch", "pouces"],
    ["available", "disponible"],
    ["rechargeable", "rechargeable"],
    ["lightweight", "l\u00e9ger"],
    ["non-greasy", "non gras"],
    /* Function words, last because they are the shortest. A fallback paragraph
       is read as a whole sentence, so leaving "with" or "and" in English would
       look worse than translating the noun; only words the shop actually uses
       are here, and an unknown brand name is never touched. */
    ["they are", "ils sont"],
    ["it is", "c'est"],
    ["there are", "il y a"],
    ["as well as", "ainsi que"],
    ["sizes", "tailles"],
    ["size", "taille"],
    ["colours", "couleurs"],
    ["colors", "couleurs"],
    ["colour", "couleur"],
    ["color", "couleur"],
    ["they", "ils"],
    ["your", "votre"],
    ["our", "notre"],
    ["you", "vous"],
    ["with", "avec"],
    ["and", "et"],
    ["for", "pour"],
    ["the", "le"],
    ["from", "de"],
    ["are", "sont"],
    ["may", "peut"],
    ["but", "mais"],
    ["will", "va"],
    ["all", "tous"],
    ["note", "remarque"],
    ["exact", "exact"],
    ["before", "avant"],
    ["materials", "tissus"],
    ["material", "tissu"],
    ["printed", "imprim\u00e9"],
    ["beautiful", "beaux"],
    ["body", "corps"],
    ["box", "bo\u00eete"],
    ["watch", "montre"],
    ["set", "ensemble"],
    ["sets", "ensembles"],
    ["hair", "cheveux"],
    ["nail", "ongle"],
    ["nail", "ongle"],
    ["skin", "peau"],
    ["bag", "sac"],
    ["bags", "sacs"],
    ["shoes", "chaussures"],
    ["heels", "talons"]
  ];

  let _copyPairs = null;
  function copyPairs() {
    if (_copyPairs) return _copyPairs;
    const seen = {};
    _copyPairs = COPY_GLOSSARY
      .filter((pair) => pair && pair[0] && pair[1])
      .filter((pair) => {
        const key = pair[0].toLowerCase();
        if (seen[key]) return false;      // first (longest) spelling wins
        seen[key] = true;
        return true;
      })
      .sort((a, b) => b[0].length - a[0].length);
    return _copyPairs;
  }

  function escapeRe(text) {
    return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /** Best-effort French for product copy that arrived without descriptionFr.
   *  Returns "" when nothing could be translated with confidence, so callers
   *  can fall back to the original text instead of showing an empty line. */
  function translateCopy(text) {
    if (lang() !== "fr") return "";
    const raw = String(text == null ? "" : text).trim();
    if (!raw || !/[A-Za-z]/.test(raw)) return "";
    // Legacy imported line breaks arrive as entities; treat them as ends
    // so each known fragment can be translated on its own.
    const plain = raw.replace(/&#0*10;/g, "\n").replace(/<br\s*\/?>/gi, "\n");
    const segments = plain.split(/(?<=[.!?;])\s+|\n+/).map((seg) => seg.trim()).filter(Boolean);
    const out = segments.map((seg) => {
      const known = translate(seg);
      if (known) return known;
      let line = seg;
      copyPairs().forEach((pair) => {
        const re = new RegExp("\\b" + escapeRe(pair[0]) + "\\b", "gi");
        line = line.replace(re, pair[1]);
      });
      // A sentence that began with a capital must still begin with one.
      if (/^[A-Z]/.test(seg) && line && line[0] === line[0].toLowerCase()) {
        line = line.charAt(0).toUpperCase() + line.slice(1);
      }
      return line;
    }).join(" ").trim();
    if (!out) return "";
    // Nothing was actually translated: do not pretend otherwise.
    return out === raw || out.replace(/\s+/g, " ") === raw.replace(/\s+/g, " ") ? "" : out;
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
        r.addedNodes.forEach((n) => {
          if (n.nodeType === 1) queue.push(n);
          else if (n.nodeType === 3 && n.parentNode && n.parentNode.nodeType === 1) queue.push(n.parentNode);
        });
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

  // The rate the current copy was rendered with, so a repaint after the live
  // row lands is the ONLY extra work - and a repeat event costs nothing.
  let appliedRate;

  function applyKeys(root = document) {
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
    appliedRate = rateToken();
  }

  function apply(root = document) {
    applyKeys(root);
    sweep(document.body);
    watch();
  }

  /* Rate copy is painted before GET /api/site can answer (boot draws the page
   * first so a slow network never shows a blank screen), so the moment the
   * live row lands the copy is re-rendered with the real figure. Without this
   * a first-time visitor would keep reading the pending marker. */
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("ja:site", () => {
      if (rateToken() === appliedRate) return;
      try { applyKeys(document); sweep(document.body); } catch (e) {}
    });
  }

  if (document.documentElement) document.documentElement.lang = lang();

  return { lang, setLang, t, apply, sweep, detectBrowserLang, translateCopy };
})();
var I18N = window.I18N;
