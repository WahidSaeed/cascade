"""More artifact scenarios for the demo world (Northpeak Outfitters, all fictional): three `ml` and three `dl`.

Each scenario has prompt templates, a generator, and an oracle (the label an LLM that reads the text gives).
Phrase banks are written so the label is unambiguous: verification compares an artifact with the real LLM, and a
scenario only belongs here if a careful reader would always give the generator's label.

  ml  (TF-IDF + logistic regression):  review sentiment, catalog category, message language
  dl  (small transformer fine-tune):    return reason, review star rating, community post moderation
"""
from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .demo_world import quoted


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    task_type: str                    # classification | scoring
    kind: str                         # ml | dl: what the agent is asked to build
    prompts: list[str]                # instruction templates around the quoted text ({t})
    labels: list[str]
    make_text: Callable[[random.Random, str], str] = field(repr=False)

    def make(self, rng: random.Random, label: str | None = None) -> tuple[str, str]:
        """One (prompt, label). The label is chosen uniformly unless given."""
        lbl = label or rng.choice(self.labels)
        return rng.choice(self.prompts).format(t=self.make_text(rng, lbl)), lbl

    @property
    def instructions(self) -> str:
        return ("Fine-tune a small transformer (deep learning) on the attached labelled data"
                if self.kind == "dl" else "Go straight to ml: train on the attached labelled data")


def _cap(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[0].upper() + s[1:] if s else s


GEAR = ["tent", "sleeping bag", "rain jacket", "down jacket", "hiking boots", "trail shoes", "backpack",
        "camp stove", "headlamp", "trekking poles", "fleece", "sleeping mat", "water filter", "climbing harness"]


# ============================================================================ ml: review sentiment
SENT_POS = ["absolutely love this {p}", "the {p} exceeded every expectation", "best {p} I have ever owned",
            "the {p} is fantastic and worth every cent", "really happy with the {p}",
            "the {p} kept me warm and dry the whole trip", "brilliant {p}, would buy again",
            "excellent quality, the {p} feels built to last", "my new {p} is superb",
            "the {p} performed perfectly on a week in the Alps", "great value, the {p} is comfortable and light",
            "five weekends of use and the {p} still looks brand new", "delighted with this {p}"]
SENT_NEG = ["the {p} fell apart after two trips", "really disappointed with the {p}",
            "the {p} leaked on the first night", "worst {p} I have ever bought", "the {p} is cheaply made and flimsy",
            "would not recommend this {p} to anyone", "the {p} broke within a week", "the zip on the {p} failed immediately",
            "terrible quality, the {p} is going straight back", "the {p} is uncomfortable and far too heavy",
            "the {p} let me down badly in the rain", "complete waste of money, this {p}",
            "the stitching on the {p} came undone on day one"]
SENT_OPEN = ["", "", "", "Honest review: ", "Update after a month: ", "Quick review. ", "Bought this for my partner. "]
SENT_CLOSE = ["", "", ".", "!", ". Five stars from me.", ". Avoid.", ". Very pleased.", ". Not happy at all."]


def _sentiment_text(rng: random.Random, label: str) -> str:
    bank = SENT_POS if label == "positive" else SENT_NEG
    close = rng.choice([c for c in SENT_CLOSE if not (
        (label == "positive" and c in (". Avoid.", ". Not happy at all.")) or
        (label == "negative" and c in (". Five stars from me.", ". Very pleased.")))])
    first, second = rng.sample(bank, 2)
    body = first if rng.random() < 0.6 else f"{first} and {second}"
    return _cap(rng.choice(SENT_OPEN) + body.format(p=rng.choice(GEAR)) + close)


# ============================================================================ ml: catalog category
CATALOG = {
    "tents": ["2-person backpacking tent", "ultralight trekking pole tent", "4-season mountain tent",
              "family dome tent with porch", "freestanding solo tent", "tunnel tent with vestibule", "bivvy shelter tent"],
    "sleeping": ["800-fill down sleeping bag", "synthetic sleeping bag rated to -5°C", "self-inflating sleeping mat",
                 "insulated air sleeping pad", "silk sleeping bag liner", "inflatable camping pillow", "mummy sleeping bag"],
    "footwear": ["waterproof leather hiking boots", "lightweight trail running shoes", "approach shoes with sticky rubber",
                 "insulated winter boots", "leather walking shoes", "gore-tex mid hiking boots", "camp sandals"],
    "apparel": ["waterproof 3-layer rain jacket", "hooded down jacket", "merino base layer top", "softshell trekking trousers",
                "fleece mid layer", "windproof running jacket", "insulated hiking gloves"],
    "cooking": ["compact canister camp stove", "titanium cook pot 900 ml", "two-burner camping stove",
                "collapsible silicone bowl set", "stainless steel camping kettle", "spork and cutlery set",
                "freeze-dried meal pack"],
    "climbing": ["dynamic climbing rope 9.8 mm", "sport climbing harness", "locking carabiner set",
                 "bouldering crash pad", "climbing helmet", "belay device with guide mode", "set of climbing nuts"],
    "packs": ["45 L trekking backpack", "20 L daypack with hydration sleeve", "65 L expedition rucksack",
              "running vest pack 12 L", "waterproof dry bag 30 L", "hip pack for day hikes", "travel duffel bag 70 L"],
}
CAT_ADJ = ["Northpeak", "Ridgeline", "Summit", "Alpine Pro", "Trailhead", "Basecamp", "Highline", "Fjord"]
CAT_TAIL = ["", "", " – new season", " (blue)", " (graphite)", ", 2026 model", " – member price", " (green)"]


def _catalog_text(rng: random.Random, label: str) -> str:
    return f"{rng.choice(CAT_ADJ)} {rng.choice(CATALOG[label])}{rng.choice(CAT_TAIL)}"


# ============================================================================ ml: message language
LANG = {
    "en": (["Hello", "Hi there", "Good morning", "Dear team"],
           ["my order has not arrived yet", "I would like to return my jacket", "the boots are too small for me",
            "can you send me a new invoice", "the tent pole is broken", "when will my parcel be delivered",
            "I want to change my delivery address", "the sleeping bag is not warm enough", "please cancel my order",
            "how do I reset my password"],
           ["Thank you", "Best regards", "Kind regards", "Thanks a lot"]),
    "de": (["Hallo", "Guten Tag", "Guten Morgen", "Liebes Team"],
           ["meine Bestellung ist noch nicht angekommen", "ich möchte meine Jacke zurückgeben",
            "die Schuhe sind mir zu klein", "können Sie mir eine neue Rechnung schicken", "die Zeltstange ist gebrochen",
            "wann wird mein Paket geliefert", "ich möchte meine Lieferadresse ändern", "der Schlafsack ist nicht warm genug",
            "bitte stornieren Sie meine Bestellung", "wie setze ich mein Passwort zurück"],
           ["Vielen Dank", "Mit freundlichen Grüßen", "Beste Grüße", "Danke schön"]),
    "fr": (["Bonjour", "Salut", "Bonsoir", "Chère équipe"],
           ["ma commande n'est pas encore arrivée", "je voudrais retourner ma veste", "les chaussures sont trop petites pour moi",
            "pouvez-vous m'envoyer une nouvelle facture", "l'arceau de la tente est cassé", "quand mon colis sera-t-il livré",
            "je veux changer mon adresse de livraison", "le sac de couchage n'est pas assez chaud",
            "merci d'annuler ma commande", "comment réinitialiser mon mot de passe"],
           ["Merci beaucoup", "Cordialement", "Bien à vous", "Merci d'avance"]),
    "es": (["Hola", "Buenos días", "Buenas tardes", "Estimado equipo"],
           ["mi pedido todavía no ha llegado", "quiero devolver mi chaqueta", "las botas me quedan pequeñas",
            "pueden enviarme una nueva factura", "la varilla de la tienda está rota", "cuándo entregarán mi paquete",
            "quiero cambiar mi dirección de entrega", "el saco de dormir no abriga lo suficiente",
            "por favor cancelen mi pedido", "cómo restablezco mi contraseña"],
           ["Muchas gracias", "Saludos cordiales", "Un saludo", "Gracias de antemano"]),
    "it": (["Ciao", "Buongiorno", "Buonasera", "Gentile team"],
           ["il mio ordine non è ancora arrivato", "vorrei restituire la mia giacca", "gli scarponi sono troppo piccoli per me",
            "potete inviarmi una nuova fattura", "il palo della tenda è rotto", "quando verrà consegnato il mio pacco",
            "voglio cambiare il mio indirizzo di consegna", "il sacco a pelo non è abbastanza caldo",
            "per favore annullate il mio ordine", "come reimposto la mia password"],
           ["Grazie mille", "Cordiali saluti", "Distinti saluti", "Grazie in anticipo"]),
    "nl": (["Hallo", "Goedemorgen", "Goedemiddag", "Beste klantenservice"],
           ["mijn bestelling is nog niet aangekomen", "ik wil mijn jas retourneren", "de schoenen zijn te klein voor mij",
            "kunt u mij een nieuwe factuur sturen", "de tentstok is gebroken", "wanneer wordt mijn pakket bezorgd",
            "ik wil mijn bezorgadres wijzigen", "de slaapzak is niet warm genoeg", "annuleer alstublieft mijn bestelling",
            "hoe stel ik mijn wachtwoord opnieuw in"],
           ["Hartelijk dank", "Met vriendelijke groet", "Groetjes", "Alvast bedankt"]),
}


def _language_text(rng: random.Random, label: str) -> str:
    hello, bodies, bye = LANG[label]
    a, b = rng.sample(bodies, 2)
    body = a if rng.random() < 0.6 else f"{a}, {b}"
    return f"{rng.choice(hello)}, {body}. {rng.choice(bye)}"


# ============================================================================ dl: return reason
RETURN = {
    "too_small": ["it is a size too small", "it's far too small for me", "the size I ordered is too small",
                  "it came up much smaller than the size chart", "it's too small, I can't use it",
                  "it is too tight and too short", "I need one size up", "it runs small, way too snug"],
    "too_large": ["it is a size too big", "it's far too big for me", "the size I ordered is too large",
                  "it came up much bigger than the size chart", "it's too large, I can't use it",
                  "it is too loose and too long", "I need one size down", "it runs large, way too roomy"],
    "defective": ["it broke the first time I used it", "a seam split open on day one", "it arrived with a hole in it",
                  "a buckle snapped straight away", "a part is already coming off", "it arrived damaged and unusable",
                  "the stitching came apart after one use", "the fabric tore on the first outing"],
    "not_as_described": ["the colour is completely different from the photos", "it is not waterproof as the listing claimed",
                         "the listing said 800-fill but the label says 600", "it weighs twice what the page says",
                         "the material is polyester, not merino as advertised", "it looks nothing like the pictures",
                         "the product page promised pockets and there are none",
                         "it is a different model from the one described"],
    "changed_mind": ["I changed my mind and no longer need it", "I found one I like better elsewhere",
                     "my trip was cancelled so I don't need it", "I ordered it by accident",
                     "I decided to keep my old one after all", "it was a gift and they already have one",
                     "I bought two and only need one", "I simply don't want it any more"],
}
RET_LEAD = ["I'm returning the {p} because ", "Returning my {p}: ", "Please process a return for the {p}, ",
            "I'd like to send back the {p} as ", "Return request for {p}. Reason: ", "The {p} is going back - ",
            "Hi, sending the {p} back since ", "Can I return the {p}? "]
RET_TAIL = ["", "", ".", ". Thanks.", ". Order NP-{o}.", ". Please refund.", ". Cheers!"]


WEARABLE = ["rain jacket", "down jacket", "hiking boots", "trail shoes", "fleece", "climbing harness", "softshell trousers",
            "base layer", "gloves"]


def _return_text(rng: random.Random, label: str) -> str:
    sized = label in ("too_small", "too_large")          # a size reason only makes sense for something worn
    lead = rng.choice(RET_LEAD).format(p=rng.choice(WEARABLE if sized else GEAR))
    return _cap(lead + rng.choice(RETURN[label]) + rng.choice(RET_TAIL).format(o=rng.randint(10000, 99999)))


# ============================================================================ dl: review star rating (1-5)
STARS = {
    "1": ["it broke on the first day and support ignored me", "the worst purchase I have ever made",
          "it fell apart immediately, total junk", "dangerous, the buckle failed on a descent",
          "useless, it leaked through within an hour"],
    "2": ["disappointing, it wore out after a few trips", "below average, the zip keeps sticking",
          "not great, heavier and colder than advertised", "poor fit and mediocre materials",
          "it works but I regret buying it"],
    "3": ["it's okay, does the job but nothing special", "average quality for the price",
          "decent but the fit is a bit odd", "fine for occasional use, nothing more", "mixed feelings, some good, some bad"],
    "4": ["very good, just one small niggle with the straps", "really solid, only slightly heavy",
          "great performance, the colour faded a little", "comfortable and warm, minor stitching flaw",
          "I like it a lot, would be perfect with more pockets"],
    "5": ["outstanding, flawless on a two-week trek", "perfect in every way, I love it",
          "the best gear I own, absolutely brilliant", "superb quality, exceeded all expectations",
          "faultless, I've already bought a second one"],
}
STAR_LEAD = ["My {p}: ", "Review of the {p} - ", "After a season with the {p}, ", "The {p}? ", "Bought the {p}. ",
             "Used the {p} on the Kungsleden: ", "Honest take on the {p}: ", ""]


def _stars_text(rng: random.Random, label: str) -> str:
    return _cap(rng.choice(STAR_LEAD).format(p=rng.choice(GEAR)) + rng.choice(STARS[label]) + rng.choice(["", ".", "!"]))


# ============================================================================ dl: community post moderation
MOD_ALLOW = ["Has anyone used the {p} above 3000 m? How did it hold up?", "Tip: reproof your {p} every season, it makes a huge difference.",
             "What size {p} would you recommend for a 10-day hike?", "Loved the Dolomites trip report, thanks for sharing the route!",
             "Is the {p} warm enough for Scottish winters?", "Mine lasted five years of heavy use, really impressed with the {p}.",
             "Does the {p} fit in the 45 L pack with a bear canister?", "Great answer above, the warranty team sorted my {p} in a week.",
             "Which {p} is better for wet weather, the old or the new model?", "Here's my packing list for the GR20 if it helps anyone."]
MOD_FLAG = ["BUY CHEAP {p} NOW at cheap-gear-deals.example.net !!! 90% OFF", "Earn $500 a day from home, DM me for crypto signals",
            "You are an idiot and nobody wants your stupid {p} advice", "Click here for free gift cards: bit.ly/free-giftz",
            "Shut up, morons, this forum is garbage", "Use code SPAM50 at fake-outlet.example.org for a free {p}!!!",
            "Get rich quick with my forex course, link in bio", "Only losers buy the {p}, you're all pathetic",
            "WIN A FREE {p}!!! Just send your card number to claim", "Hot singles in your area want to go camping, click now"]


def _moderation_text(rng: random.Random, label: str) -> str:
    base = rng.choice(MOD_ALLOW if label == "allow" else MOD_FLAG).format(p=rng.choice(GEAR))
    who = rng.choice(["", "", "", "Posted by trailfan: ", "Re: ", "Reply: ", "New post - ", "Comment: "])
    tail = rng.choice(["", "", "", " #hiking", " #gear", " (edited)", " - sent from my phone", " #outdoors"])
    return who + base + tail


SCENARIOS: list[Scenario] = [
    Scenario("sentiment", "Review sentiment", "Is a product review positive or negative?", "classification", "ml",
             ["What is the sentiment of this product review? Answer positive or negative: '{t}'",
              "Is this review positive or negative? '{t}'"],
             ["positive", "negative"], _sentiment_text),
    Scenario("catalog", "Catalog category", "Which catalog category a product listing belongs to",
             "classification", "ml",
             ["Which catalog category fits this product - tents, sleeping, footwear, apparel, cooking, climbing or packs? '{t}'",
              "Categorise this product listing as tents, sleeping, footwear, apparel, cooking, climbing or packs: '{t}'"],
             list(CATALOG), _catalog_text),
    Scenario("language", "Message language", "Which language a customer message is written in", "classification", "ml",
             ["Which language is this customer message written in? Answer en, de, fr, es, it or nl: '{t}'",
              "Detect the language of this message (en, de, fr, es, it or nl): '{t}'"],
             list(LANG), _language_text),
    Scenario("return_reason", "Return reason", "Why a customer is returning an item", "classification", "dl",
             ["Why is this item being returned? Answer too_small, too_large, defective, not_as_described or changed_mind: '{t}'",
              "Classify the return reason as too_small, too_large, defective, not_as_described or changed_mind: '{t}'"],
             list(RETURN), _return_text),
    Scenario("stars", "Review star rating", "The 1-5 star rating a review would give", "scoring", "dl",
             ["Predict the star rating (1-5) this review would give: '{t}'",
              "How many stars (1 to 5) does this review deserve? '{t}'"],
             list(STARS), _stars_text),
    Scenario("moderation", "Community post moderation", "Allow a community Q&A post or flag it as spam / abuse",
             "classification", "dl",
             ["Moderate this community Q&A post: flag spam and abuse, allow the rest. Answer allow or flag: '{t}'",
              "Should this forum post be allowed, or flagged as spam or abuse? Answer allow or flag: '{t}'"],
             ["allow", "flag"], _moderation_text),
]
BY_KEY = {s.key: s for s in SCENARIOS}


# ============================================================================ oracle
def _bank_label(text: str, banks: dict[str, list[str]]) -> str | None:
    """The label whose phrase matches the most text (placeholders are wildcards)."""
    best, best_len = None, 0
    low = text.lower()
    for label, phrases in banks.items():
        for ph in phrases:
            rx = re.escape(ph.lower()).replace(r"\{p\}", ".+?").replace(r"\{o\}", r"\d+")
            if re.search(rx, low) and len(ph) > best_len:
                best, best_len = label, len(ph)
    return best


def detect(prompt: str) -> Scenario | None:
    p = prompt.strip().lower()
    for s in SCENARIOS:
        for tmpl in s.prompts:
            if p.startswith(tmpl.split("{t}")[0].strip().lower()[:40]):
                return s
    return None


def oracle(prompt: str) -> str | None:
    s = detect(prompt)
    if s is None:
        return None
    t = quoted(prompt)
    banks: dict[str, list[str]] = {
        "sentiment": {"positive": SENT_POS, "negative": SENT_NEG},
        "catalog": CATALOG,
        "language": {k: v[1] + v[0] + v[2] for k, v in LANG.items()},
        "return_reason": RETURN,
        "stars": STARS,
        "moderation": {"allow": MOD_ALLOW, "flag": MOD_FLAG},
    }[s.key]
    return _bank_label(t, banks)
