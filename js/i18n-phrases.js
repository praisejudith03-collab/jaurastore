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
    [/^Togo Moov:\s*(.+)$/, function (m) { return "Moov Togo : " + m[1]; }],
  ];
})();
