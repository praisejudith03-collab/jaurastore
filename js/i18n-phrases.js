/* J Aura Store — French wording for text that lives inside JavaScript.
 *
 * The pages use data-i18n attributes for the text written in HTML, but a lot
 * of what you read is built by JavaScript: the payment form, the category
 * names, the whole admin portal, and every toast. Those strings are below.
 *
 * How it works: after a page renders, I18N sweeps the text and swaps any
 * string that matches one of these entries. Only a whole string is replaced,
 * never part of one, so product names the owner typed are left alone.
 *
 * Add a line here and it is French everywhere. Nothing else to change.
 */
window.I18N_PHRASES = {
  // ---------------------------------------------------------------- payment
  "Payment confirmation": "Confirmation de paiement",
  "Send your payment receipt": "Envoyez votre reçu de paiement",
  "Upload the receipt or screenshot from your bank or MoMo. We email it to":
    "Téléversez le reçu ou la capture de votre banque ou MoMo. Nous l'envoyons par e-mail à",
  "with your details, so nothing gets lost on WhatsApp.":
    "avec vos coordonnées, pour que rien ne se perde sur WhatsApp.",
  "Pay into": "Payez sur",
  "Full name *": "Nom complet *",
  "Phone number *": "Numéro de téléphone *",
  "Email address *": "Adresse e-mail *",
  "Order ID": "Numéro de commande",
  "if you have one": "si vous en avez un",
  "Products as ordered": "Produits commandés",
  "e.g. 2× Valentino bag (Black), 1× Zara heels":
    "ex. 2× sac Valentino (noir), 1× talons Zara",
  "Quantity": "Quantité",
  "Amount paid": "Montant payé",
  "Payment method *": "Moyen de paiement *",
  "Note (optional)": "Remarque (facultatif)",
  "Anything we should know about this payment":
    "Tout ce que nous devons savoir sur ce paiement",
  "Your receipt — JPG, PNG or PDF (max 8 MB) *":
    "Votre reçu — JPG, PNG ou PDF (max 8 Mo) *",
  "We attach the original file to the email, exactly as you upload it.":
    "Nous joignons le fichier original à l'e-mail, exactement tel que vous l'envoyez.",
  "Send receipt to Jaura Store": "Envoyer le reçu à Jaura Store",
  "Your details are used only to match your payment to your order.":
    "Vos coordonnées servent uniquement à relier votre paiement à votre commande.",
  "Sending…": "Envoi…",
  "Payment receipt sent": "Reçu de paiement envoyé",
  "Choose your receipt file first.": "Choisissez d'abord votre fichier de reçu.",
  "Enter a valid email address.": "Saisissez une adresse e-mail valide.",
  "Only JPG, PNG or PDF files can be sent.":
    "Seuls les fichiers JPG, PNG ou PDF peuvent être envoyés.",
  "Saved on your phone — it will send as soon as you are back online.":
    "Enregistré sur votre téléphone — l'envoi se fera dès que vous serez de nouveau en ligne.",
  "Saved": "Enregistré",
  "PDF": "PDF",
  "Your receipt": "Votre reçu",
  "Order ID copied: ": "Numéro de commande copié : ",

  // -------------------------------------------------------------- bank text
  "Name:": "Nom :",
  "Bank:": "Banque :",
  "MoMo:": "MoMo :",
  "Account number:": "Numéro de compte :",
  "Put your order ID in the transfer remark.":
    "Indiquez votre numéro de commande dans le libellé du transfert.",
  "Naira — UBA": "Naira — UBA",
  "CFA (Benin) — MTN MoMo": "CFA (Bénin) — MTN MoMo",
  "Togo Moov:": "Moov Togo :",
  "MTN MoMo Benin (F CFA)": "MTN MoMo Bénin (F CFA)",
  "Moov Money Togo (F CFA)": "Moov Money Togo (F CFA)",
  "UBA bank transfer (₦ Naira)": "Virement bancaire UBA (₦ Naira)",
  "Other bank transfer": "Autre virement bancaire",

  // -------------------------------------------------------------- categories
  "Clothings for men and women": "Vêtements pour hommes et femmes",
  "Household items": "Articles ménagers",
  "Ankara ready to wear": "Ankara prêt-à-porter",
  "Accessories": "Accessoires",
  "Beauty & skincare": "Beauté & soins de la peau",
  "Shoes": "Chaussures",
  "Gadgets / Electronics": "Gadgets / Électronique",
  "Packaging": "Emballages",
  "Bags": "Sacs",
  "Hair care": "Soins capillaires",
  "Nails": "Ongles",
  "Decor": "Décoration",
  "Gift set": "Coffret cadeau",
  "Children items": "Articles pour enfants",
  "All Products": "Tous les produits",

  // ------------------------------------------------------------------ toasts
  "Added to cart": "Ajouté au panier",
  "That PDF is over 8 MB. Please send a smaller file.":
    "Ce PDF dépasse 8 Mo. Envoyez un fichier plus petit.",

  // ------------------------------------------------------------ admin: login
  "Sign in": "Se connecter",
  "Signing in…": "Connexion…",
  "Sign out": "Se déconnecter",
  "Could not sign in.": "Impossible de se connecter.",
  "Forgot password? Reset it by email":
    "Mot de passe oublié ? Réinitialisez-le par e-mail",
  "Reset your password": "Réinitialiser votre mot de passe",
  "We email a 6-digit code to the admin address. Enter it below with a new password.":
    "Nous envoyons un code à 6 chiffres à l'adresse administrateur. Saisissez-le ci-dessous avec un nouveau mot de passe.",

  // ---------------------------------------------------------- admin: general
  "Admin · Jaura Store": "Admin · Jaura Store",
  "View store": "Voir la boutique",
  "Refresh": "Actualiser",
  "Refreshed.": "Actualisé.",
  "Reloading…": "Rechargement…",
  "Retrying…": "Nouvelle tentative…",
  "Delete": "Supprimer",
  "Deleted from the website.": "Supprimé du site.",
  "Tick the products to delete first.": "Cochez d'abord les produits à supprimer.",
  "Save settings": "Enregistrer les paramètres",
  "Settings saved.": "Paramètres enregistrés.",
  "Delete selected from website": "Supprimer la sélection du site",

  // ------------------------------------------------------- admin: categories
  "Manage Categories": "Gérer les catégories",
  "Add a category": "Ajouter une catégorie",
  "+ Add category": "+ Ajouter une catégorie",
  "Delete category": "Supprimer la catégorie",
  "Save categories": "Enregistrer les catégories",
  "Category added.": "Catégorie ajoutée.",
  "Category deleted.": "Catégorie supprimée.",
  "That category already exists.": "Cette catégorie existe déjà.",
  "Type a category name.": "Saisissez un nom de catégorie.",
  "Add or delete categories here. They show on the shop and in filters. Beauty and Skincare are now one category:":
    "Ajoutez ou supprimez des catégories ici. Elles apparaissent sur la boutique et dans les filtres. Beauty et Skincare forment désormais une seule catégorie :",
  "Category must be one of:": "La catégorie doit être l'une des suivantes :",
  "Photos can be attached afterwards by editing each piece — originals are never altered.":
    "Les photos peuvent être ajoutées ensuite en modifiant chaque article — les originaux ne sont jamais modifiés.",

  // --------------------------------------------------------- admin: products
  "+ Add a Product": "+ Ajouter un produit",
  "Edit products": "Modifier les produits",
  "Import products": "Importer des produits",
  "Bulk upload": "Import en masse",
  "Import products from a CSV. Columns:":
    "Importez des produits depuis un CSV. Colonnes :",
  "CSV file": "Fichier CSV",
  "Download CSV template": "Télécharger le modèle CSV",
  "Change photo": "Changer la photo",
  "Show on website": "Afficher sur le site",
  "In stock": "En stock",
  "Name (English)": "Nom (anglais)",
  "Name (French)": "Nom (français)",
  "French name": "Nom français",
  "Photo uploaded.": "Photo téléversée.",
  "Finishing the photo upload…": "Finalisation du téléversement de la photo…",
  "Please upload a photo from your gallery. We never replace it with AI.":
    "Téléversez une photo depuis votre galerie. Nous ne la remplaçons jamais par une image générée.",
  "Could not read that photo. Try another from your gallery.":
    "Impossible de lire cette photo. Essayez-en une autre depuis votre galerie.",
  "That photo did not upload. It will retry by itself.":
    "Cette photo n'a pas été téléversée. L'envoi sera réessayé automatiquement.",
  "Enter the ₦ price. CFA is converted on the website.":
    "Saisissez le prix en ₦. Le CFA est converti sur le site.",
  "Naira is the only price you enter on products. The website converts F CFA at":
    "Le Naira est le seul prix que vous saisissez pour les produits. Le site convertit le F CFA à",
  "Enter Naira only — F CFA is converted at 1 ₦ = 0.44.":
    "Saisissez uniquement le Naira — le F CFA est converti à 1 ₦ = 0,44.",
  "1 ₦ = 0.44 F CFA": "1 ₦ = 0,44 F CFA",

  // ----------------------------------------------------------- admin: orders
  "Orders": "Commandes",
  "Order": "Commande",
  "Status": "Statut",
  "Total": "Total",
  "Customer": "Client",
  "Email": "E-mail",
  "Name": "Nom",
  "Product": "Produit",
  "Products": "Produits",
  "Latest orders": "Dernières commandes",
  "Confirmed · ": "Confirmé · ",
  "Declined · ": "Refusé · ",
  "Sending receipt…": "Envoi du reçu…",
  "Saved on this device — it will sync when you are back online.":
    "Enregistré sur cet appareil — la synchronisation se fera dès que vous serez en ligne.",

  // --------------------------------------------------------- admin: insights
  "Store insights": "Statistiques de la boutique",
  "Insights": "Statistiques",
  "Total sales": "Ventes totales",
  "Average order value": "Valeur moyenne des commandes",
  "Pending orders": "Commandes en attente",
  "Top products": "Produits les plus vus",
  "Most visited pages": "Pages les plus visitées",
  "Recent activity": "Activité récente",
  "Visitor locations": "Localisation des visiteurs",
  "Visitors & page views": "Visiteurs et pages vues",
  "Page views": "Pages vues",
  "Views": "Vues",
  "Visitors": "Visiteurs",
  "Visits": "Visites",
  "Unique visitors": "Visiteurs uniques",
  "Conversion": "Conversion",
  "conversion rate": "taux de conversion",
  "Checkout attempts": "Tentatives de commande",
  "Visit → order": "Visite → commande",
  "Live now": "En direct",
  "Live on site": "En direct sur le site",
  "On the site right now": "Sur le site en ce moment",
  "Nobody is browsing right now.": "Personne ne navigue en ce moment.",
  "Nothing yet on this device.": "Rien pour l'instant sur cet appareil.",
  "sessions": "sessions",
  "on the site": "sur le site",
  "on": "le",
  "Page": "Page",
  "Location": "Localisation",
  "Traffic is counted on the server, so these numbers follow your store — not one phone or browser. This panel refreshes on its own every 30 seconds.":
    "Le trafic est compté sur le serveur : ces chiffres suivent votre boutique, et non un seul téléphone ou navigateur. Ce panneau s'actualise tout seul toutes les 30 secondes.",

  // ------------------------------------------------------ admin: misc labels
  "Account": "Compte",
  "My account": "Mon compte",
  "Categories": "Catégories",
  "Store settings": "Paramètres de la boutique",
  "Pay-in-CFA instructions": "Instructions de paiement en CFA",
  "Pay-in-Naira instructions": "Instructions de paiement en Naira",
  "WhatsApp (digits only)": "WhatsApp (chiffres uniquement)",
  "Unknown location": "Localisation inconnue",
  "Phone Benin": "Téléphone Bénin",
  "Phone Nigeria": "Téléphone Nigeria",
  "Password changed. Use it the next time you sign in.":
    "Mot de passe modifié. Utilisez-le lors de votre prochaine connexion.",
  "Password updated. Sign in with it now.":
    "Mot de passe mis à jour. Connectez-vous avec dès maintenant.",
  "The two new passwords do not match.":
    "Les deux nouveaux mots de passe ne correspondent pas.",
  "Review added.": "Avis ajouté.",
  "Maximum 6 options.": "Maximum 6 options.",
  "Cotonou, Benin": "Cotonou, Bénin",
  "confirmed": "confirmé",
  "pending": "en attente",

  // -------------------------------------------------- storefront: leftovers
  "No internet right now — your order and screenshot are saved on this phone and will reach us the moment you are back online. Keep your order ID.":
    "Pas d'internet pour le moment — votre commande et votre capture sont enregistrées sur ce téléphone et nous parviendront dès que vous serez de nouveau en ligne. Gardez votre numéro de commande.",
  "We could not read this order.": "Impossible de lire cette commande.",
  "Working…": "Traitement…",
  "No connection. Try again, or confirm from the admin portal.":
    "Pas de connexion. Réessayez, ou confirmez depuis le portail admin.",
  "That link did not work. Sign in to the admin portal instead.":
    "Ce lien n'a pas fonctionné. Connectez-vous plutôt au portail admin.",
  "Payment confirmed. The customer has been emailed a receipt.":
    "Paiement confirmé. Un reçu a été envoyé au client par e-mail.",
  "Order declined. The customer has been emailed about it.":
    "Commande refusée. Le client en a été informé par e-mail.",
  "Confirm payment": "Confirmer le paiement",
  "Decline order": "Refuser la commande",
  "Yes — payment received": "Oui — paiement reçu",
  "Decline this order": "Refuser cette commande",
  "That PDF is over 8 MB. Please send a smaller file.":
    "Ce PDF dépasse 8 Mo. Merci d'envoyer un fichier plus léger.",
  "Please upload an image screenshot.": "Veuillez téléverser une capture d'écran.",
  "Payment screenshot": "Capture du paiement",
  "Your payment receipt": "Votre reçu de paiement",
  "Your receipt has been emailed to": "Votre reçu a été envoyé par e-mail à",
  "with the original file attached. We will confirm your payment shortly.":
    "avec le fichier original en pièce jointe. Nous confirmerons votre paiement sous peu.",
  "Your receipt is saved with us. We will confirm your payment shortly.":
    "Votre reçu est bien enregistré chez nous. Nous confirmerons votre paiement sous peu.",
  "Session expired — sign in again.": "Session expirée — reconnectez-vous.",
  "Saved on this device; it will sync when you are back online.":
    "Enregistré sur cet appareil ; la synchronisation se fera dès que vous serez en ligne.",
  "No connection. Try again when you are back online.":
    "Pas de connexion. Réessayez quand vous serez de nouveau en ligne.",
  "Could not change the password.": "Impossible de modifier le mot de passe.",
  "Password updated.": "Mot de passe mis à jour.",
  "Checkout security could not be verified. Please try again.":
    "La vérification de sécurité a échoué. Veuillez réessayer.",
  "This site is protected by reCAPTCHA and the Google":
    "Ce site est protégé par reCAPTCHA ; les",
  "Privacy Policy": "Politique de confidentialité",
  "Terms of Service": "conditions d'utilisation",

  // ------------------------------------------------------------ legal pages
  "Last updated: 29 August 2026": "Dernière mise à jour : 29 août 2026",

  // privacy.html
  "What we collect": "Ce que nous collectons",
  "Name, phone number, email and delivery address you enter at checkout.":
    "Nom, numéro de téléphone, e-mail et adresse de livraison saisis lors de la commande.",
  "Order details: items, quantities, totals and chosen currency.":
    "Détails de la commande : articles, quantités, totaux et devise choisie.",
  "Payment screenshots you send to confirm a transfer.":
    "Captures de paiement envoyées pour confirmer un virement.",
  "Basic technical data (pages viewed, approximate city) used to improve the store.":
    "Données techniques de base (pages vues, ville approximative) servant à améliorer la boutique.",
  "How we use it": "Comment nous les utilisons",
  "To process and deliver your order, confirm payment, answer questions and keep you updated on that order. We do not sell your data.":
    "Pour traiter et livrer votre commande, confirmer le paiement, répondre à vos questions et vous tenir informé de cette commande. Nous ne vendons pas vos données.",
  "Who we share it with": "Avec qui nous les partageons",
  "Only the partners needed to fulfil your order (delivery partners) and to process communications (our email provider). Each is required to protect your data.":
    "Uniquement les partenaires nécessaires à votre commande (partenaires de livraison) et aux communications (notre fournisseur d'e-mail). Chacun est tenu de protéger vos données.",
  "How long we keep it": "Combien de temps nous les gardons",
  "Order records are kept for accounting and warranty purposes. Marketing messages are only sent if you asked for them, and you can opt out at any time.":
    "Les commandes sont conservées à des fins comptables et de garantie. Les messages marketing ne sont envoyés que si vous les avez demandés, et vous pouvez vous désinscrire à tout moment.",
  "Your rights": "Vos droits",
  "You may ask us for a copy of your data, ask us to correct it, or ask us to delete it where we are not required to keep it. Email":
    "Vous pouvez demander une copie de vos données, leur correction, ou leur suppression lorsque nous ne sommes pas tenus de les conserver. Écrivez à",
  "Cookies": "Cookies",
  "We use local storage to remember your cart, wishlist and currency choice. These are functional and required for the shop to work.":
    "Nous utilisons le stockage local pour retenir votre panier, votre liste d'envies et votre devise. Ces éléments sont fonctionnels et nécessaires au fonctionnement de la boutique.",

  // returns.html
  "Returns & Refunds": "Retours et remboursements",
  "Change of mind": "Changement d'avis",
  "Report any return request within": "Signalez toute demande de retour sous",
  "48 hours": "48 heures",
  "of delivery. Items must be unused, in original packaging and with all tags attached.":
    "après la livraison. Les articles doivent être neufs, dans leur emballage d'origine et avec toutes leurs étiquettes.",
  "Damaged or wrong item": "Article endommagé ou erroné",
  "If your parcel arrives damaged or contains the wrong item, contact us within":
    "Si votre colis arrive endommagé ou contient le mauvais article, contactez-nous sous",
  "with your order ID and photos. We will arrange an exchange or refund and cover the return transport where the error is ours.":
    "avec votre numéro de commande et des photos. Nous organiserons un échange ou un remboursement et prendrons en charge le transport retour si l'erreur vient de nous.",
  "Non-returnable items": "Articles non retournables",
  "Personal-care and cosmetics items once opened (for hygiene reasons).":
    "Produits d'hygiène et cosmétiques une fois ouverts (pour raisons d'hygiène).",
  "Hair products, wigs and extensions once the seal is broken.":
    "Produits capillaires, perruques et extensions une fois le sceau brisé.",
  "Clearance or final-sale items marked as such.":
    "Articles en déstockage ou en vente finale signalés comme tels.",
  "Gift sets once unsealed.": "Coffrets cadeaux une fois descellés.",
  "Refunds": "Remboursements",
  "Approved refunds are paid back to the account that made the original transfer, within 7–14 business days of the returned item being received and inspected.":
    "Les remboursements approuvés sont reversés sur le compte ayant effectué le virement d'origine, sous 7 à 14 jours ouvrés après réception et contrôle de l'article retourné.",
  "How to start a return": "Comment lancer un retour",
  "or message us on WhatsApp with your order ID, the item and the reason.":
    "ou écrivez-nous sur WhatsApp avec votre numéro de commande, l'article et la raison.",

  // shipping.html
  "Shipping & Delivery": "Expédition et livraison",
  "Where we deliver": "Où nous livrons",
  "Benin:": "Bénin :",
  "Cotonou, Calavi, Porto-Novo and surrounding areas — 6 to 14 business days.":
    "Cotonou, Calavi, Porto-Novo et environs — 6 à 14 jours ouvrés.",
  "Nigeria:": "Nigeria :",
  "Lagos Mainland, Lagos Island and other states — confirmed at checkout.":
    "Lagos Mainland, Lagos Island et autres États — confirmé lors de la commande.",
  "Neighbouring states:": "États voisins :",
  "Lomé and selected West African destinations.":
    "Lomé et certaines destinations d'Afrique de l'Ouest.",
  "Transport fare": "Frais de transport",
  "Fares depend on your delivery city and the weight of the parcel. The exact fare is confirmed with you after your order is placed, before dispatch.":
    "Les frais dépendent de votre ville de livraison et du poids du colis. Le tarif exact est confirmé avec vous après la commande, avant l'expédition.",
  "Tracking": "Suivi",
  "Once your order is confirmed you will receive an order ID. Use it on our":
    "Une fois votre commande confirmée, vous recevrez un numéro de commande. Utilisez-le sur notre page",
  "Track order": "Suivre la commande",
  "page, or message us on WhatsApp for a status update.":
    ", ou écrivez-nous sur WhatsApp pour connaître le statut.",
  "Delivery issues": "Problèmes de livraison",
  "If a parcel is delayed beyond the stated window, contact":
    "Si un colis dépasse le délai annoncé, contactez",
  "and we will follow it up with the dispatch partner.":
    "et nous ferons le suivi avec le partenaire de livraison.",

  // terms.html
  "Terms & Conditions": "Conditions générales",
  "1. About us": "1. Qui sommes-nous",
  "J Aura Store (\"we\", \"us\") is a boutique retailer of fashion, beauty and lifestyle goods, serving customers in Nigeria, Benin and neighbouring West African states. Contact:":
    "J Aura Store (« nous ») est une boutique de mode, beauté et art de vivre, au service des clients du Nigeria, du Bénin et des États voisins d'Afrique de l'Ouest. Contact :",
  "2. Prices and currency": "2. Prix et devise",
  "Naira is our base price. F CFA amounts shown on the site are converted at our house rate of":
    "Le Naira est notre prix de base. Les montants en F CFA affichés sur le site sont convertis à notre taux maison de",
  ". Where an item has no Naira price, it is listed in F CFA only. Prices may change without notice, but the price confirmed at checkout is the price you pay.":
    ". Lorsqu'un article n'a pas de prix en Naira, il est affiché uniquement en F CFA. Les prix peuvent changer sans préavis, mais le prix confirmé à la commande est celui que vous payez.",
  "3. Orders": "3. Commandes",
  "Placing an order creates an offer to buy. A contract is formed only when we confirm your payment. We may decline or cancel an order where an item is out of stock, mispriced, or where we suspect fraud.":
    "Passer commande constitue une offre d'achat. Le contrat n'est formé qu'à la confirmation de votre paiement. Nous pouvons refuser ou annuler une commande en cas de rupture de stock, d'erreur de prix ou de soupçon de fraude.",
  "4. Payment": "4. Paiement",
  "We accept direct bank transfer in Naira (UBA) and in F CFA (MTN MoMo Benin). Send your payment screenshot through the link shown after checkout, quoting your order ID in the transfer remark.":
    "Nous acceptons le virement bancaire direct en Naira (UBA) et en F CFA (MTN MoMo Bénin). Envoyez votre capture de paiement via le lien affiché après la commande, en indiquant votre numéro de commande dans le motif du virement.",
  "5. Delivery": "5. Livraison",
  "Delivery coverage and lead times are confirmed at checkout by city. Transport fare is quoted after your order is placed and depends on location and parcel weight.":
    "Les zones et délais de livraison sont confirmés à la commande selon la ville. Les frais de transport sont annoncés après la commande et dépendent de la localité et du poids du colis.",
  "6. Liability": "6. Responsabilité",
  "Nothing in these terms limits liability for death, personal injury or fraud. Otherwise our liability is limited to the value of the order.":
    "Rien dans ces conditions ne limite la responsabilité en cas de décès, de dommage corporel ou de fraude. Autrement, notre responsabilité est limitée à la valeur de la commande.",
  "7. Governing law": "7. Droit applicable",
  "These terms are governed by the laws of the Federal Republic of Nigeria, without affecting your statutory rights as a consumer.":
    "Ces conditions sont régies par les lois de la République fédérale du Nigeria, sans affecter vos droits légaux de consommateur.",
};

/* Counts are endless, so they are matched by shape instead of one by one.
 * "1 produit" stays singular, everything above one takes the plural. */
(function () {
  function one(num, word, many) {
    var n = String(num).replace(/[\s]/g, "");
    var single = n === "1" || n === "01";
    return num + " " + (single ? word : many || word + "s");
  }
  window.I18N_RULES = [
    [/^(\d[\d\s]*)\s+products?$/, function (m) { return one(m[1], "produit"); }],
    [/^(\d[\d\s]*)\s+items?$/, function (m) { return one(m[1], "article"); }],
    [/^(\d[\d\s]*)\s+orders?$/, function (m) { return one(m[1], "commande"); }],
    [/^(\d[\d\s]*)\s+views?$/, function (m) { return one(m[1], "vue"); }],
    [/^(\d[\d\s]*)\s+visits?$/, function (m) { return one(m[1], "visite"); }],
    [/^(\d[\d\s]*)\s+visitors?$/, function (m) { return one(m[1], "visiteur"); }],
    [/^(\d[\d\s]*)\s+sessions?$/, function (m) { return one(m[1], "session"); }],
    [/^(\d[\d\s]*)\s+in Stock$/, function (m) { return one(m[1], "en stock", "en stock"); }],
    [/^(\d[\d\s]*)\s+new$/, function (m) { return one(m[1], "nouveau", "nouveaux"); }],
    [/^(\d[\d\s]*)\s+days?$/, function (m) { return one(m[1], "jour"); }],
    [/^(\d[\d\s]*)\s+product views$/, function (m) { return one(m[1], "page de produit vue", "pages de produits vues"); }],
    [/^Products:\s*(\d[\d\s]*)$/, function (m) { return "Produits : " + m[1]; }],
    [/^(\d[\d.,]*)\s*%\s+completed$/, function (m) { return m[1].replace(".", ",") + " % terminé"; }],
    [/^confirmed:\s*(\d+)\s*·\s*pending:\s*(\d+)$/,
      function (m) { return "confirmé : " + m[1] + " · en attente : " + m[2]; }],
    /* the bank details are one line each, with the account number on the end */
    [/^Name:\s*(.+)$/, function (m) { return "Nom : " + m[1]; }],
    [/^Bank:\s*(.+)$/, function (m) { return "Banque : " + m[1]; }],
    [/^MoMo:\s*(.+)$/, function (m) { return "MoMo : " + m[1]; }],
    [/^Account number:\s*(.+)$/, function (m) { return "Numéro de compte : " + m[1]; }],
    [/^Order ID copied:\s*(.+)$/, function (m) { return "Numéro de commande copié : " + m[1]; }],
    [/^Togo Moov:\s*(.+)$/, function (m) { return "Moov Togo : " + m[1]; }],
  ];
})();
