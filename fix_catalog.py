#!/usr/bin/env python3
"""Recategorize Wix products and add French names."""
import json
import re
from pathlib import Path

ROOT = Path("/home/user/jaura-store")
seed_path = ROOT / "data" / "seed.json"
products = json.loads(seed_path.read_text())

# Longer phrases first.
RULES = [
    ("ankara", r"ankara|adire|bubu|ashake"),
    ("nails", r"nail|manicure"),
    ("shoes", r"heel|sandal|(?<!shoe )shoes?(?! wipe)|kitten heel|burgundy heel|zara heel|cover heel|quality shoe"),
    ("gift-set", r"gift set|gift box"),
    ("hair-care", r"hair|wig|scrunchie|ponytail|frontal|fringe bob|full closure|birthmark|skull cap|hot comb|straightener|afro"),
    ("beauty", r"lipstick|lipgloss|lip gloss|lip gel|lip scrub|beard|clipper|mouth spray|mini mirror"),
    ("skincare", r"skincare|acne|toner|cleanser|serum|hand cream|body oil|deodorant|sun cream|face mask|peel|nicotinamide|niacinamide|bioaqua|rashel|collagen|argan|wipes"),
    ("accessories", r"watch|bracelet|choker|necklace|neckpiece|knuckles ring|jewelry|jewellery|key holder|key chain|deblve"),
    ("gadgets", r"power bank|earpod|headphone|flip phone|microphone|mircophone|mic |selfie|tripod|speaker|smartwatch|smart watch|bp monitor|phone holder|video making|influencer light|mini q light|led cube|tab\b|fan\b"),
    ("bags", r"\bbag\b|tote|cross bag"),
    ("children", r"children|baby"),
    ("packaging", r"mailer|sticker|thank you"),
    ("clothing", r"fur cap|gown|skirt|pants|shorts|two piece|corset|sundress"),
    ("decor", r"decor|glow in the dark|marble|flower bouquet|flower pot|potted flower|vogue frame|cactus|inflatable sofa|floating shelf|scented candle|work table"),
]

# Overrides when keyword rules would mis-fire.
OVERRIDE = {
    "100l-storage-bag": "household",
    "shoe-wipes-1600": "household",
    "big-wig-bag": "hair-care",
    "jelly-baby-bag": "children",
    "laptop-school-bag": "bags",
    "3-in-1-laptop-bag": "bags",
    "mailer-bag-25-pcs": "packaging",
    "pink-silk-pillowcase": "hair-care",
    "smoothing-solution-hair-removal-chair": "skincare",
    "menstrual-relief-belt-12000": "beauty",
    "wipes-5-packs-2500": "skincare",
    "big-baby-wipes": "children",
    "mini-fan-3850": "gadgets",
    "portable-rechargeable-fan": "gadgets",
    "owambe-turbo-fan": "gadgets",
    "ac-design-fan-13500": "gadgets",
    "electric-steam-iron-16000": "household",
    "electronic-personal-scale-9000": "household",
    "wrist-watch-box-2200": "accessories",
    "jewelry-case": "accessories",
    "transparent-jewelry-box": "accessories",
    "a58-smartwatch-with-jewelry-set": "gift-set",
    "rock-006-smartwatch-gift-set": "gift-set",
    "3-in-1-vacuum-flask-gift-set": "gift-set",
    "ceramic-mug": "gift-set",
    "ice-bottle": "gift-set",
    "white-gift-set": "gift-set",
    "diary-gift-set": "gift-set",
    "dark-brown-7-in-1-gift-set-for-men-2": "gift-set",
    "men-5-in-1-set": "gift-set",
    "men-7-in-1-gift-set": "gift-set",
    "fife-set": "gift-set",
    "beach-bag-and-hat": "bags",
    "foldable-water-bottle": "household",
    "car-diffuser": "household",
    "mini-diffuser": "decor",
    "july-diffuser": "decor",
    "talking-cactus": "decor",
    "essential-oil-for-humidifier": "decor",
    "blue-idea-rechargeable-clipper": "beauty",
    "alstow-professional-clipper": "beauty",
    "i18pro-flip-phone-26500": "gadgets",
    "i20-ultra-2-max-suit": "gadgets",
    "i60-suit-extreme-12-in-1-set": "gadgets",
    "s25-ultra-pro-tab": "gadgets",
    "s11-pro-max-smart-watch": "accessories",
    "smartwatch-with-game-pad": "gadgets",
    "poedagar-watch": "accessories",
    "valenzo-watch": "gift-set",
    "r-on-the-edge-watch": "gift-set",
    "aisy-watch": "accessories",
    "foldable-travelling-bag": "bags",
    "foldable-travelling-bag-with-wheels": "bags",
    "advanced-snail-cleanser-cosrx": "skincare",
    "advanced-snail-toner-cosrx": "skincare",
    "collagen-snail-serum-30ml": "skincare",
    "10000-mah-power-bank": "gadgets",
    "q8-wireless-mircophone-15700": "gadgets",
    "f15-wireless-mic-double-18000": "gadgets",
    "f15-wireless-mic-single-13000": "gadgets",
    "children-12lcd-writing-tablet": "children",
    "fur-cap": "clothing",
    "press-on-nail-2": "nails",
}

PHRASES = [
    ("power bank", "batterie externe"),
    ("storage bag", "sac de rangement"),
    ("sandwich maker", "appareil à sandwich"),
    ("pot set", "set de casseroles"),
    ("spice jar set", "set de pots à épices"),
    ("spice jar", "pot à épices"),
    ("plate rack", "égouttoir à assiettes"),
    ("soap holder", "porte-savon"),
    ("skincare set", "coffret soin"),
    ("cutlery set", "ménagère"),
    ("gift set", "coffret cadeau"),
    ("vacuum flask", "thermos"),
    ("bathroom rack", "étagère de salle de bain"),
    ("dish washer", "égouttoir"),
    ("lunch plate", "assiette compartimentée"),
    ("face mask", "masque visage"),
    ("maxi skirt", "jupe longue"),
    ("full closure bob", "carré full closure"),
    ("wrist watch", "montre"),
    ("smart watch", "montre connectée"),
    ("smartwatch", "montre connectée"),
    ("press on nail", "faux ongles"),
    ("manicure set", "set de manucure"),
    ("hair dryer", "sèche-cheveux"),
    ("hair straightener", "lisseur"),
    ("hair scrunchies", "chouchous"),
    ("hair brush", "brosse à cheveux"),
    ("hair clip", "pince à cheveux"),
    ("hair band", "bandeau"),
    ("lip gloss", "gloss"),
    ("lipgloss", "gloss"),
    ("lipstick", "rouge à lèvres"),
    ("lip gel", "gel lèvres"),
    ("lip scrub", "gommage lèvres"),
    ("beard balm", "baume barbe"),
    ("mouth spray", "spray buccal"),
    ("mini mirror", "mini miroir"),
    ("hand cream", "crème pour les mains"),
    ("body oil", "huile corps"),
    ("sun cream", "crème solaire"),
    ("face mask sheet", "masque en tissu"),
    ("peel off mask", "masque peel-off"),
    ("foot peel mask", "masque pieds"),
    ("center table", "table basse"),
    ("flower bouquet", "bouquet de fleurs"),
    ("flower pot", "pot de fleurs"),
    ("potted flower", "plante en pot"),
    ("floating shelf", "étagère murale"),
    ("scented candles", "bougies parfumées"),
    ("writing tablet", "tablette d'écriture"),
    ("selfie stick", "perche à selfie"),
    ("phone holder", "support téléphone"),
    ("bp monitor", "tensiomètre"),
    ("wireless mic", "micro sans fil"),
    ("steam iron", "fer à vapeur"),
    ("personal scale", "pèse-personne"),
    ("water bottle", "gourde"),
    ("tote bag", "cabas"),
    ("travelling bag", "sac de voyage"),
    ("school bag", "cartable"),
    ("laptop bag", "sac pour ordinateur"),
    ("cross bag", "sac banane"),
    ("wig bag", "sac à perruque"),
    ("mailer bag", "pochettes d'expédition"),
    ("key holder", "porte-clés"),
    ("key chain", "porte-clés"),
    ("jewelry case", "boîte à bijoux"),
    ("jewelry box", "boîte à bijoux"),
    ("choker set", "set de ras-de-cou"),
    ("flat sandals", "sandales plates"),
    ("kitten heels", "talons kitten"),
    ("cover heels", "escarpins fermés"),
    ("quality shoes", "chaussures"),
    ("menstrual relief belt", "ceinture menstruelle"),
    ("inflatable sofa", "canapé gonflable"),
    ("glow in the dark", "phosphorescent"),
    ("talking cactus", "cactus parlant"),
    ("thank you sticker", "sticker merci"),
    ("kitchen tissue", "essuie-tout"),
    ("clothes pegs", "pinces à linge"),
    ("shoe wipes", "lingettes chaussures"),
    ("baby wipes", "lingettes bébé"),
    ("silk pillowcase", "taie en soie"),
    ("hot comb", "peigne chauffant"),
    ("afro ponytail", "queue de cheval afro"),
    ("fringe bob", "carré à frange"),
    ("skull cap", "bonnet skull"),
    ("fur cap", "bonnet en fourrure"),
    ("ankara pants", "pantalon ankara"),
    ("ankara shorts", "short ankara"),
    ("ankara chic set", "ensemble ankara"),
    ("vacuum cup", "gobelet isotherme"),
    ("insulated mugs", "mugs isothermes"),
    ("foldable bag", "sac pliable"),
    ("beach bag and hat", "sac de plage et chapeau"),
    ("electric hand mixer", "batteur électrique"),
    ("dry grinder", "moulin"),
    ("vegetable peeler", "épluche-légumes"),
    ("veggies slicer", "coupe-légumes"),
    ("washing machine", "machine à laver"),
    ("wall suction hanger", "crochet ventouse"),
    ("wooden hanger", "cintre en bois"),
    ("underwear hanger", "cintre lingerie"),
    ("work table", "bureau"),
    ("led cube light", "cube lumineux LED"),
    ("video making kit", "kit vidéo"),
    ("wireless resonance speaker", "enceinte résonante"),
    ("portable rechargeable fan", "ventilateur rechargeable"),
    ("rechargeable clipper", "tondeuse rechargeable"),
    ("professional clipper", "tondeuse professionnelle"),
    ("flip phone", "téléphone à clapet"),
    ("selfie tripod", "trépied selfie"),
    ("tracking tripod", "trépied suiveur"),
    ("air freshener", "désodorisant"),
    ("car diffuser", "diffuseur voiture"),
    ("mini diffuser", "mini diffuseur"),
    ("essential oil", "huile essentielle"),
    ("gold cutlery", "ménagère dorée"),
    ("aluminum pot", "casseroles en aluminium"),
    ("sealed crisper", "boîtes hermétiques"),
    ("spice jar", "pot à épices"),
    ("plate rack", "égouttoir"),
    ("soap holder", "porte-savon"),
    ("cup set", "set de tasses"),
    ("flask", "thermos"),
    ("towel", "serviette"),
    ("mop", "serpillière"),
    ("decor", "décoration"),
    ("heels", "talons"),
    ("watch", "montre"),
    ("bracelet", "bracelet"),
    ("necklace", "collier"),
    ("bag", "sac"),
    ("fan", "ventilateur"),
    ("set", "coffret"),
]

WORDS = {
    "with": "avec",
    "and": "et",
    "for": "pour",
    "of": "de",
    "in": "en",
    "pcs": "pcs",
    "layers": "niveaux",
    "layer": "niveau",
    "step": "marche",
    "mini": "mini",
    "big": "grand",
    "small": "petit",
    "gold": "or",
    "white": "blanc",
    "black": "noir",
    "pink": "rose",
    "men": "homme",
    "women": "femme",
    "unisex": "unisexe",
    "professional": "professionnel",
    "electric": "électrique",
    "wireless": "sans fil",
    "rechargeable": "rechargeable",
    "portable": "portable",
    "foldable": "pliable",
    "double": "double",
    "single": "simple",
    "children": "enfants",
    "baby": "bébé",
}


def classify(p):
    slug = p.get("slug") or ""
    if slug in OVERRIDE:
        return OVERRIDE[slug]
    blob = (slug + " " + (p.get("name") or "")).lower()
    for cat, pat in RULES:
        if re.search(pat, blob, re.I):
            if cat == "bags" and re.search(r"storage bag|mailer|wig bag|water bottle", blob):
                continue
            if cat == "skincare" and re.search(r"lipstick|lipgloss|lip gel|lip scrub", blob):
                continue
            if cat == "gadgets" and re.search(r"watch box|jewelry", blob):
                continue
            return cat
    return "household"


def title_name(name):
    small = {"a", "an", "the", "and", "or", "of", "in", "on", "with", "for"}
    parts = str(name).split()
    out = []
    for i, w in enumerate(parts):
        if re.match(r"^\d", w) or w.upper() in {"LED", "USB", "SPF", "AG", "DD", "LV", "Q8", "F15", "M10", "AC", "BP"}:
            out.append(w.upper() if w.isalpha() and len(w) <= 3 else w)
        elif i != 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)


def to_fr(name):
    s = " " + name.lower().strip() + " "
    s = re.sub(r"\s+", " ", s)
    for en, fr in sorted(PHRASES, key=lambda x: -len(x[0])):
        s = re.sub(r"(?<![a-z])" + re.escape(en) + r"(?![a-z])", fr, s, flags=re.I)
    tokens = []
    for w in s.split():
        tokens.append(WORDS.get(w, w))
    s = " ".join(tokens).strip()
    # light capitalize first letter
    if s:
        s = s[0].upper() + s[1:]
    return s


from collections import Counter

for p in products:
    p["category"] = classify(p)
    p["name"] = title_name(p.get("name") or p.get("slug") or "")
    p["nameFr"] = to_fr(p["name"])
    p["description"] = p["name"]

c = Counter(p["category"] for p in products)
print("counts:")
for k, v in c.most_common():
    print(f"  {k}: {v}")
print("total", len(products))

seed_path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
js = ROOT / "js" / "products-data.js"
js.write_text("window.JA_SEED = " + json.dumps(products, ensure_ascii=False) + ";\n", encoding="utf-8")
print("wrote seed + products-data.js")
