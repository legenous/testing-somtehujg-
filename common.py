import os, json, requests

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = str(os.environ["TELEGRAM_CHAT_ID"])
API = f"https://api.telegram.org/bot{TG_TOKEN}"

# % de la bankroll memecoin misé selon le score de l'alerte
MISE_PCT = {7: 2, 8: 4, 9: 6, 10: 8}

HELP = (
    "Commandes :\n"
    "/solde 1000 : enregistre ton capital total (en €)\n"
    "/solde : affiche ton capital\n"
    "/part 20 : % du capital réservé aux memecoins (le reste = long terme)\n"
    "/mise : montre les mises par score d'alerte\n"
    "Le bot lit tes commandes à chaque passage (quelques minutes de délai)."
)


def tg(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=30)
        return r.json()
    except Exception:
        return {}


def send(msg):
    """Envoi en HTML (liens cliquables). Le texte dynamique doit être échappé."""
    return tg("sendMessage", chat_id=TG_CHAT, text=msg, parse_mode="HTML",
              disable_web_page_preview=True)


def reply(msg):
    return tg("sendMessage", chat_id=TG_CHAT, text=msg)


def get_settings():
    """Le solde est stocké dans le message épinglé du chat (rien dans le dépôt public)."""
    cfg = {"solde": None, "part": 20}
    pm = tg("getChat", chat_id=TG_CHAT).get("result", {}).get("pinned_message")
    if pm and "#cfg " in pm.get("text", ""):
        try:
            cfg.update(json.loads(pm["text"].split("#cfg ", 1)[1]))
            cfg["_msg_id"] = pm["message_id"]
        except Exception:
            pass
    return cfg


def save_settings(cfg):
    clean = {"solde": cfg.get("solde"), "part": cfg.get("part", 20)}
    solde = f"{clean['solde']:g} €" if clean["solde"] is not None else "non renseigné"
    text = f"💰 Solde : {solde} | Part memecoins : {clean['part']:g}%\n#cfg {json.dumps(clean)}"
    if cfg.get("_msg_id"):
        r = tg("editMessageText", chat_id=TG_CHAT, message_id=cfg["_msg_id"], text=text)
        if r.get("ok") or "not modified" in str(r):
            return
    r = tg("sendMessage", chat_id=TG_CHAT, text=text)
    mid = r.get("result", {}).get("message_id")
    if mid:
        cfg["_msg_id"] = mid
        tg("pinChatMessage", chat_id=TG_CHAT, message_id=mid, disable_notification=True)


def bankroll(cfg):
    return cfg["solde"] * cfg["part"] / 100 if cfg.get("solde") is not None else None


def mise_memecoin(cfg, score, factor=1.0):
    bank = bankroll(cfg)
    if bank is None:
        return None
    pct = MISE_PCT.get(int(score), 8 if score > 10 else 2)
    return round(bank * pct / 100 * factor, 2)


def num(s):
    try:
        v = float(s)
        return v if v >= 0 else None
    except Exception:
        return None


def process_commands(cfg):
    ups = tg("getUpdates", timeout=0).get("result", [])
    if not ups:
        return cfg
    changed = False
    for u in ups:
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != TG_CHAT:
            continue  # on n'obéit qu'à toi
        txt = (m.get("text") or "").strip()
        if not txt.startswith("/"):
            continue
        parts = txt.replace(",", ".").split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd in ("/start", "/aide", "/help"):
            reply(HELP)
        elif cmd == "/solde":
            if arg is None:
                s = cfg.get("solde")
                reply(f"Solde : {s:g} €" if s is not None else "Solde non renseigné. Ex : /solde 500")
            elif num(arg) is None:
                reply("Montant invalide. Ex : /solde 500")
            else:
                cfg["solde"] = num(arg)
                changed = True
                reply(f"✅ Solde enregistré : {cfg['solde']:g} €\n"
                      f"Bankroll memecoins ({cfg['part']:g}%) : {bankroll(cfg):.2f} €\n"
                      f"Budget long terme : {cfg['solde'] - bankroll(cfg):.2f} €")
        elif cmd == "/part":
            v = num(arg) if arg else None
            if v is None or v > 100:
                reply("Ex : /part 20 (20 % du capital pour les memecoins)")
            else:
                cfg["part"] = v
                changed = True
                reply(f"✅ Part memecoins : {v:g}%")
        elif cmd == "/mise":
            if cfg.get("solde") is None:
                reply("Renseigne d'abord ton solde. Ex : /solde 500")
            else:
                lines = [f"Score {s}/10 : {mise_memecoin(cfg, s):.2f} €" for s in (7, 8, 9, 10)]
                reply(f"Bankroll memecoins : {bankroll(cfg):.2f} €\n" + "\n".join(lines))
        else:
            reply("Commande inconnue. Tape /aide")

    tg("getUpdates", offset=ups[-1]["update_id"] + 1, timeout=0)  # confirme la lecture
    if changed:
        save_settings(cfg)
    return cfg
