import os, json, datetime, requests, feedparser
from common import get_settings

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
CURRENCY = "eur"
STATE_FILE = "long_state.json"
PORTFOLIO_FILE = "portfolio.json"

ICONS = {"GARDER": "🟢", "RENFORCER": "🔵", "ALLEGER": "🟠", "VENDRE": "🔴"}

PROMPT = """Tu es un analyste crypto prudent spécialisé dans les investissements long terme.
Tu suis la position ci-dessous et tu dois dire si l'investisseur doit la garder, la renforcer,
l'alléger ou la vendre. Tu reviens sur ton analyse précédente : confirme-la ou corrige-la
en expliquant ce qui a changé. Sois honnête sur l'incertitude, ne promets rien.

Position : {name} ({symbol})
Thèse de l'investisseur : {thesis}
Prix d'achat moyen : {buy}
Données marché ({cur}) : prix {price}, 24h {c24}%, 7j {c7}%, 30j {c30}%,
distance au plus haut historique {ath}%, capitalisation {mcap}, volume 24h {vol}
Plus/moins-value latente : {pnl}
Actus récentes :
{news}

Analyse précédente : {prev}

Réponds UNIQUEMENT en JSON :
{{"verdict": "GARDER|RENFORCER|ALLEGER|VENDRE", "confiance": <0-10>,
"resume": "<2 phrases max en français>", "risques": "<1 phrase>",
"changement": "<ce qui a changé depuis la dernière analyse, ou 'rien de notable'>"}}"""


def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": msg, "disable_web_page_preview": True},
        timeout=30,
    )


def market(ids):
    r = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": CURRENCY, "ids": ",".join(ids),
                "price_change_percentage": "24h,7d,30d"},
        timeout=30,
    )
    r.raise_for_status()
    return {c["id"]: c for c in r.json()}


def news(name):
    url = f"https://news.google.com/rss/search?q={name}+crypto&hl=en-US&gl=US&ceid=US:en"
    try:
        return "\n".join("- " + e.title for e in feedparser.parse(url).entries[:6]) or "aucune"
    except Exception:
        return "indisponible"


def analyse(h, m, prev):
    price = m.get("current_price")
    buy = h.get("buy_price")
    pnl = f"{(price / buy - 1) * 100:+.1f}%" if buy and price else "inconnue"
    prompt = PROMPT.format(
        name=m.get("name", h["id"]), symbol=m.get("symbol", "").upper(),
        thesis=h.get("thesis", "non précisée"), buy=buy or "inconnu", cur=CURRENCY.upper(),
        price=price, c24=round(m.get("price_change_percentage_24h_in_currency") or 0, 1),
        c7=round(m.get("price_change_percentage_7d_in_currency") or 0, 1),
        c30=round(m.get("price_change_percentage_30d_in_currency") or 0, 1),
        ath=round(m.get("ath_change_percentage") or 0, 1),
        mcap=m.get("market_cap"), vol=m.get("total_volume"), pnl=pnl,
        news=news(m.get("name", h["id"])),
        prev=(f"{prev['verdict']} le {prev['date']} : {prev['resume']}" if prev else "aucune (première analyse)"),
    )
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=60,
    )
    r.raise_for_status()
    res = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    return res, price, pnl


def main():
    holdings = load_json(PORTFOLIO_FILE, [])
    state = load_json(STATE_FILE, {})
    cfg = get_settings()
    if not holdings:
        return
    hour = datetime.datetime.utcnow().hour
    recap = 5 <= hour < 9 or os.environ.get("FORCE") == "1"
    data = market([h["id"] for h in holdings])
    today = datetime.date.today().isoformat()

    for h in holdings:
        m = data.get(h["id"])
        if not m:
            print("Introuvable sur CoinGecko:", h["id"])
            continue
        hist = state.get(h["id"], [])
        prev = hist[-1] if hist else None
        try:
            res, price, pnl = analyse(h, m, prev)
        except Exception as ex:
            print("Erreur analyse", h["id"], ex)
            continue
        verdict = res.get("verdict", "GARDER").upper()
        changed = prev is not None and prev["verdict"] != verdict
        hist.append({"date": today, "verdict": verdict, "resume": res.get("resume", ""), "price": price})
        state[h["id"]] = hist[-15:]

        extra = ""
        if verdict == "RENFORCER" and cfg.get("solde") is not None:
            budget = cfg["solde"] * (100 - cfg["part"]) / 100
            extra = f"\n💰 Renfort suggéré : {round(budget * 0.10, 2)} € (10 % de ton budget long terme)"
        if changed or recap or prev is None:
            head = "⚠️ CHANGEMENT DE VERDICT" if changed else "📊 Suivi long terme"
            send(f"{head}\n{ICONS.get(verdict, '⚪')} {m['name']} : {verdict} "
                 f"(confiance {res.get('confiance', '?')}/10)\n"
                 f"Prix {price} {CURRENCY.upper()} | P/L {pnl} | 30j {round(m.get('price_change_percentage_30d_in_currency') or 0, 1)}%\n"
                 f"{res.get('resume', '')}\n"
                 f"Risque : {res.get('risques', '')}\n"
                 f"Évolution : {res.get('changement', '')}{extra}")
    json.dump(state, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
