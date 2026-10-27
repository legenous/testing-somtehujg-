import os, json, hashlib, html, requests, feedparser
from common import get_settings, process_commands, mise_memecoin, send

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
THRESHOLD = int(os.environ.get("THRESHOLD", "7"))
MAX_ALERTS = int(os.environ.get("MAX_ALERTS", "5"))
SEEN_FILE = "seen.json"

FEEDS = [
    "https://news.google.com/rss/search?q=celebrity+dies+OR+dead&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=viral+OR+trending+meme&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.reddit.com/r/news/new/.rss",
    "https://www.reddit.com/r/technology/new/.rss",
    "https://www.reddit.com/r/memes/hot/.rss",
    "https://www.reddit.com/r/CryptoCurrency/new/.rss",
]

PROMPT = """Tu aides un trader de memecoins (pump.fun / Solana).
Pour chaque actu ci-dessous, donne un score de 0 à 10 : probabilité qu'un memecoin
basé dessus devienne viral. Critères (par ordre d'importance) :
1. Audience mondiale et émotion forte (célébrité mondialement connue, décès, animal, scandale, meme)
2. Nom/ticker court, drôle et facile à retenir (max 6 lettres)
3. Fraîcheur : événement qui vient de se produire, pas une suite d'une vieille histoire
4. Lien crypto/tech/internet (Elon, IA, annonce de grande marque, tendance X/TikTok)
Pénalise : politique clivante locale, actu locale, sujet triste sans angle meme,
articles d'opinion, sujets déjà vieux de plusieurs jours, mort d'une personne peu connue.
Réponds UNIQUEMENT en JSON : une liste d'objets
{"i": <numéro>, "score": <0-10>, "ticker": "<ticker idée>", "cat": "<celebrite|deces|animal|tech|meme|autre>", "why": "<10 mots max en français>"}.

Actus :
"""


def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()


def save_seen(seen):
    json.dump(sorted(seen)[-3000:], open(SEEN_FILE, "w"))


def fetch_new(seen):
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
        except Exception:
            continue
        for e in feed.entries[:15]:
            uid = hashlib.md5((e.get("link", "") + e.get("title", "")).encode()).hexdigest()
            if uid in seen:
                continue
            items.append({"id": uid, "title": e.get("title", ""), "link": e.get("link", "")})
    return items


def score(items):
    text = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(items))
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": PROMPT + text}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=60,
    )
    r.raise_for_status()
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw)


def dex_check(ticker):
    """Vérifie sur DexScreener (Solana). Retourne (texte, facteur de mise, lien, libellé du lien)."""
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/search",
                         params={"q": ticker}, timeout=20)
        pairs = [p for p in r.json().get("pairs", [])
                 if p.get("chainId") == "solana"
                 and p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()]
    except Exception:
        return "⚠️ Vérif DexScreener indisponible", 1.0, None, None
    if not pairs:
        return "🟢 Aucun token Solana trouvé : terrain libre", 1.0, None, None
    best = max(pairs, key=lambda p: (p.get("volume", {}).get("h24") or 0))
    vol = best.get("volume", {}).get("h24") or 0
    liq = (best.get("liquidity") or {}).get("usd") or 0
    mint = best.get("baseToken", {}).get("address", "")
    if mint.endswith("pump"):
        link, label = f"https://pump.fun/coin/{mint}", "Ouvrir dans pump.fun"
    else:
        link, label = best.get("url"), "Voir sur DexScreener"
    state, factor = ("🔴 Déjà saturé", 0.0) if vol > 500_000 else ("🟠 Déjà lancé", 0.5)
    txt = (f"{state} : {len(pairs)} token(s) ${html.escape(ticker.upper())}\n"
           f"Meilleur : vol 24h ${vol:,.0f} | liq ${liq:,.0f}")
    return txt, factor, link, label


def main():
    cfg = get_settings()
    cfg = process_commands(cfg)  # /solde, /part, /mise, /aide

    seen = load_seen()
    items = fetch_new(seen)
    first_run = len(seen) == 0
    hits = []
    for start in range(0, len(items), 40):
        batch = items[start:start + 40]
        if not first_run:
            try:
                for r in score(batch):
                    if r.get("score", 0) >= THRESHOLD:
                        hits.append((r, batch[r["i"]]))
            except Exception as ex:
                print("Erreur scoring:", ex)
        for it in batch:
            seen.add(it["id"])
    hits.sort(key=lambda h: h[0]["score"], reverse=True)

    for r, it in hits[:MAX_ALERTS]:
        ticker = (r.get("ticker") or "?").lstrip("$")
        dex_txt, factor, link, label = dex_check(ticker)
        mise = mise_memecoin(cfg, r["score"], factor)
        if factor == 0:
            mise_line = "⛔ Pas de mise : trop saturé"
        elif mise is None:
            mise_line = "ℹ️ Renseigne ton solde : /solde 500"
        else:
            mise_line = f"💰 Mise suggérée : <b>{mise:.2f} €</b>"
        parts = [
            f"⚡ <b>MEMECOIN RAPIDE {r['score']}/10</b> [{html.escape(r.get('cat', 'autre'))}]",
            html.escape(it["title"]),
            f"💡 ${html.escape(ticker)} : {html.escape(r.get('why', ''))}",
            dex_txt,
            mise_line,
        ]
        if link:
            parts.append(f'👉 <a href="{html.escape(link, quote=True)}">{label}</a>')
        parts.append(f'📰 <a href="{html.escape(it["link"], quote=True)}">Source</a>')
        send("\n".join(parts))
    save_seen(seen)


if __name__ == "__main__":
    main()
