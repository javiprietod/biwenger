#!/usr/bin/env python3
"""
Panel Fantasy NBA para Biwenger
================================

Descarga los datos de tu liga de Biwenger (NBA) con tu propia sesión y genera
un panel HTML (panel.html) con:

  * Jugadores en el mercado (precio pedido frente a valor real)
  * Saldo de cada usuario (el tuyo exacto, el del resto estimado)
  * Valor del equipo de cada usuario y cuánto ha subido o bajado
  * Plantillas de todos los usuarios
  * Evolución diaria del valor de los equipos
  * Últimos fichajes y ventas de la liga

Uso:
    python biwenger_nba.py            # descarga datos y genera panel.html
    python biwenger_nba.py --abrir    # igual, y abre el panel en el navegador
    python biwenger_nba.py --offline  # regenera el panel con lo último descargado

Solo usa la biblioteca estándar de Python (3.8 o superior). Tus credenciales se
leen de config.json y nunca salen de tu ordenador salvo hacia Biwenger.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

BASE = "https://biwenger.as.com/api/v2"
PUBLIC_URL = "https://cf.biwenger.com/api/v2/competitions/{comp}/data?lang=es&score={score}"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "datos")
RAW_DIR = os.path.join(DATA_DIR, "raw")
HIST_DIR = os.path.join(DATA_DIR, "historial")
START_PATH = os.path.join(DATA_DIR, "inicio.json")
INJURIES_PATH = os.path.join(DATA_DIR, "lesiones.json")
CONFIG_PATH = os.path.join(HERE, "config.json")
OUTPUT_PATH = os.path.join(HERE, "panel.html")

DEFAULT_CONFIG = {
    "email": "",
    "password": "",
    "token": "",
    "liga_id": None,
    "competicion": "nba",
    "puntuacion": 1,
    "x_version": "630",
    "saldo_inicial": None,
    "max_movimientos": 3000,
    "posiciones": {"1": "Base", "2": "Alero", "3": "Pívot", "4": "Pívot", "5": "Entrenador"},
}

STATUS_ES = {
    "ok": "Disponible",
    "injured": "Lesionado",
    "doubt": "Duda",
    "sanctioned": "Sancionado",
    "warned": "Apercibido",
    "unknown": "Desconocido",
}


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Configuración
# --------------------------------------------------------------------------- #

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    # Las variables de entorno tienen prioridad (útil para no guardar la contraseña)
    for key, env in (("email", "BIWENGER_EMAIL"), ("password", "BIWENGER_PASSWORD"),
                     ("token", "BIWENGER_TOKEN")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


# --------------------------------------------------------------------------- #
#  Cliente de la API
# --------------------------------------------------------------------------- #

class Biwenger:
    def __init__(self, cfg):
        self.cfg = cfg
        token = (cfg.get("token") or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.token = token or None
        self.league_id = None
        self.user_id = None

    def _request(self, url, method="GET", body=None, auth=True):
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Mozilla/5.0 (PanelFantasyNBA)",
            "X-Lang": "es",
            "X-Version": str(self.cfg.get("x_version") or "630"),
        }
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        if auth and self.league_id:
            headers["X-League"] = str(self.league_id)
        if auth and self.user_id:
            headers["X-User"] = str(self.user_id)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        short = url.split("?")[0]
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise ApiError("HTTP %s en %s: %s" % (e.code, short, detail))
        except urllib.error.URLError as e:
            raise ApiError("Sin conexión con %s (%s)" % (short, e.reason))
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError("Respuesta no válida de %s" % short)

    def get(self, path, auth=True):
        url = path if path.startswith("http") else BASE + path
        time.sleep(0.25)  # no saturar el servidor
        return self._request(url, auth=auth)

    def login(self):
        if self.token:
            return
        email, password = self.cfg.get("email"), self.cfg.get("password")
        if not email or not password:
            raise ApiError("Falta el token o el email/contraseña en config.json")
        resp = self._request(BASE + "/auth/login", "POST",
                             {"email": email, "password": password}, auth=False)
        token = resp.get("token") or (resp.get("data") or {}).get("token")
        if not token:
            raise ApiError("El inicio de sesión no devolvió token. Revisa email y contraseña.")
        self.token = token

    def pick_league(self, account):
        leagues = as_list(D(account).get("leagues"))
        if not leagues:
            raise ApiError("Tu cuenta no tiene ligas.")
        wanted = self.cfg.get("liga_id")
        comp = (self.cfg.get("competicion") or "nba").lower()
        chosen = None
        if wanted:
            chosen = next((l for l in leagues if str(l.get("id")) == str(wanted)), None)
        if chosen is None:
            chosen = next((l for l in leagues
                           if str(l.get("competition", "")).lower() == comp), None)
        if chosen is None:
            chosen = leagues[0]
        self.league_id = chosen.get("id")
        self.user_id = uid_of(chosen.get("user"))
        return chosen, leagues


# --------------------------------------------------------------------------- #
#  Utilidades
# --------------------------------------------------------------------------- #

def D(resp):
    """Devuelve el bloque 'data' de una respuesta de Biwenger."""
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"] if resp["data"] is not None else {}
    return resp if resp is not None else {}


def as_list(x):
    if x is None:
        return []
    if isinstance(x, dict):
        return list(x.values())
    if isinstance(x, list):
        return x
    return [x]


def uid_of(x):
    if isinstance(x, dict):
        x = x.get("id")
    if x in (None, "", 0):
        return None
    return str(x)


def num(x, default=0):
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
#  Descarga
# --------------------------------------------------------------------------- #

def download(cfg, warnings):
    api = Biwenger(cfg)
    raw = {}

    def safe(name, fn):
        try:
            raw[name] = fn()
            print("  ✓ %s" % name)
        except ApiError as e:
            warnings.append("No se pudo descargar «%s»: %s" % (name, e))
            print("  ✗ %s: %s" % (name, e))
            raw[name] = None

    print("Datos públicos de la competición…")
    safe("publico", lambda: api.get(PUBLIC_URL.format(
        comp=cfg.get("competicion") or "nba", score=cfg.get("puntuacion") or 1), auth=False))

    print("Iniciando sesión…")
    api.login()
    account = api.get("/account")
    league, _ = api.pick_league(account)
    raw["cuenta"] = {"data": {"leagues": [
        {"id": l.get("id"), "name": l.get("name"), "competition": l.get("competition"),
         "user": {"id": uid_of(l.get("user")), "name": (l.get("user") or {}).get("name")}}
        for l in as_list(D(account).get("leagues"))]}}
    print("  ✓ Liga: %s (id %s)" % (league.get("name"), api.league_id))

    safe("liga", lambda: api.get("/league?include=all&fields=*,standings,tournaments,group,"
                                 "settings(description)"))
    safe("mercado", lambda: api.get("/market"))
    safe("yo", lambda: api.get("/user?fields=*,lineup(type,playersID,reservesID,captain,"
                               "striker,coach,date),players(id,owner),market(*,-userID),"
                               "offers,-trophies"))

    standings = as_list(D(raw.get("liga") or {}).get("standings"))
    raw["usuarios"] = {}
    for s in standings:
        uid = uid_of(s)
        if not uid:
            continue
        if uid == api.user_id and raw.get("yo"):
            raw["usuarios"][uid] = raw["yo"]
            continue
        try:
            raw["usuarios"][uid] = api.get("/user/%s?fields=*,players(id,owner)" % uid)
        except ApiError as e:
            warnings.append("Plantilla de %s no disponible: %s" % (s.get("name"), e))
    print("  ✓ plantillas (%d usuarios)" % len(raw["usuarios"]))

    # Tablón de la liga: fichajes, ventas y primas por jornada (para estimar saldos)
    board, offset, limit = [], 0, 50
    max_items = int(cfg.get("max_movimientos") or 3000)
    try:
        while offset < max_items:
            page = as_list(D(api.get("/league/%s/board?offset=%d&limit=%d"
                                     % (api.league_id, offset, limit))))
            board.extend(page)
            if len(page) < limit:
                break
            offset += limit
        else:
            warnings.append("El tablón tiene más de %d entradas; el saldo estimado de los "
                            "rivales puede ser incompleto." % max_items)
        print("  ✓ tablón (%d entradas)" % len(board))
    except ApiError as e:
        warnings.append("No se pudo leer el tablón de la liga: %s" % e)
    raw["tablon"] = {"data": board}
    raw["meta"] = {"liga_id": api.league_id, "usuario_id": api.user_id,
                   "liga_nombre": league.get("name"),
                   "descargado": dt.datetime.now().isoformat(timespec="seconds")}
    return raw


def save_raw(raw):
    os.makedirs(RAW_DIR, exist_ok=True)
    for name, obj in raw.items():
        save_json(os.path.join(RAW_DIR, name + ".json"), obj)


def load_raw():
    raw = {}
    if not os.path.isdir(RAW_DIR):
        raise SystemExit("No hay datos guardados. Ejecuta primero sin --offline.")
    for fn in os.listdir(RAW_DIR):
        if fn.endswith(".json"):
            raw[fn[:-5]] = load_json(os.path.join(RAW_DIR, fn))
    return raw


# --------------------------------------------------------------------------- #
#  Procesado
# --------------------------------------------------------------------------- #

def build_players(raw, cfg):
    pub = D(raw.get("publico") or {})
    teams = {}
    for t in as_list(pub.get("teams")):
        if isinstance(t, dict) and t.get("id") is not None:
            teams[str(t["id"])] = t.get("name") or t.get("slug") or ""
    positions = {str(k): v for k, v in (cfg.get("posiciones") or {}).items()}
    players = {}
    for p in as_list(pub.get("players")):
        if not isinstance(p, dict) or p.get("id") is None:
            continue
        players[str(p["id"])] = normalize_player(p, teams, positions)
    return players, teams, positions


def normalize_player(p, teams, positions):
    games = int(num(p.get("playedHome")) + num(p.get("playedAway")))
    points = num(p.get("points"))
    fitness = []
    for f in as_list(p.get("fitness"))[:5]:
        fitness.append(f if isinstance(f, (int, float)) or f is None else str(f))
    pos = p.get("position")
    return {
        "id": str(p.get("id")),
        "name": p.get("name") or "Jugador %s" % p.get("id"),
        "team": teams.get(str(p.get("teamID")), ""),
        "pos": positions.get(str(pos), "P%s" % pos if pos is not None else ""),
        "price": num(p.get("price")),
        "inc": num(p.get("priceIncrement")),
        "status": p.get("status") or "ok",
        "statusEs": STATUS_ES.get(p.get("status") or "ok", p.get("status") or ""),
        "statusInfo": p.get("statusInfo") or "",
        "points": points,
        "games": games,
        "avg": round(points / games, 1) if games else 0,
        "fitness": fitness,
    }


def ensure_player(players, pobj, teams, positions):
    """Añade un jugador que aparece en la liga pero no en los datos públicos."""
    pid = uid_of(pobj)
    if not pid:
        return None
    if pid not in players:
        base = pobj if isinstance(pobj, dict) else {"id": pid}
        players[pid] = normalize_player(base, teams, positions)
    return pid


def parse_board(items):
    """Convierte el tablón en movimientos de dinero por usuario.

    Solo cuenta lo posterior al último reinicio de la liga (leagueReset). Devuelve
    también la fecha de ese reinicio y las primas del primer día, que forman parte
    del patrimonio con el que todos arrancan."""
    moves, net, start_bonus = [], {}, {}
    items = [it for it in as_list(items) if isinstance(it, dict)]
    resets = [num(it.get("date")) for it in items if it.get("type") == "leagueReset"]
    reset = max(resets) if resets else None

    def add(uid, amount):
        if uid:
            net[uid] = net.get(uid, 0) + amount
            if reset is not None and num(date) < reset + 86400 and typ == "bonus":
                start_bonus[uid] = start_bonus.get(uid, 0) + amount

    for it in items:
        typ, date, content = it.get("type"), it.get("date"), it.get("content")
        if reset is not None and num(date) < reset:
            continue
        if typ in ("transfer", "market", "exchange", "loan", "adminTransfer", "loanReturn"):
            for c in as_list(content):
                if not isinstance(c, dict):
                    continue
                amount = num(c.get("amount"))
                frm, to = uid_of(c.get("from")), uid_of(c.get("to"))
                if amount:
                    add(frm, amount)
                    add(to, -amount)
                moves.append({"date": date, "type": typ, "pid": uid_of(c.get("player")),
                              "from": frm, "to": to, "amount": amount})
        elif typ == "clauseIncrement":
            for c in as_list(content):
                if isinstance(c, dict):
                    add(uid_of(c.get("user")), -num(c.get("amount")))
        elif typ in ("roundFinished", "bonus"):
            results = content.get("results") if isinstance(content, dict) else content
            for r in as_list(results):
                if isinstance(r, dict):
                    add(uid_of(r.get("user")), num(r.get("bonus") if typ == "roundFinished"
                                                   else r.get("amount", r.get("bonus"))))
    moves.sort(key=lambda m: m.get("date") or 0, reverse=True)
    return moves, net, reset, start_bonus


def start_values(reset, users, players):
    """Valor de cada equipo al reiniciarse la liga.

    Se congela en datos/inicio.json la primera vez que se ve un reinicio, porque
    los precios cambian cada noche. Cuenta solo los jugadores que siguen en la
    plantilla desde el reinicio."""
    saved = load_json(START_PATH) or {}
    if saved.get("reset") == reset and saved.get("valores"):
        return saved["valores"]
    vals = {}
    for u in users:
        vals[u["id"]] = sum(players[r["pid"]]["price"] for r in u["roster"]
                            if num(r["since"]) < reset + 3600)
    save_json(START_PATH, {"reset": reset, "valores": vals})
    return vals


def build_model(raw, cfg, warnings):
    meta = raw.get("meta") or {}
    me = str(meta.get("usuario_id") or "")
    players, teams, positions = build_players(raw, cfg)
    if not players:
        warnings.append("No hay datos públicos de jugadores: los valores salen de la API de tu liga.")

    league = D(raw.get("liga") or {})
    if isinstance(league, list):
        league = league[0] if league else {}
    standings = as_list(league.get("standings"))

    users, owner_of = [], {}
    for s in standings:
        uid = uid_of(s)
        if not uid:
            continue
        udata = D((raw.get("usuarios") or {}).get(uid) or {})
        roster = []
        for entry in as_list(udata.get("players")):
            pid = ensure_player(players, entry, teams, positions)
            if not pid:
                continue
            owner = (entry.get("owner") if isinstance(entry, dict) else None) or {}
            roster.append({"pid": pid, "buy": num(owner.get("price"), None),
                           "clause": num(owner.get("clause"), None),
                           "since": owner.get("date")})
            owner_of[pid] = uid
        value = sum(players[r["pid"]]["price"] for r in roster) if roster else num(s.get("teamValue"))
        inc = sum(players[r["pid"]]["inc"] for r in roster)
        users.append({
            "id": uid, "name": s.get("name") or "Usuario %s" % uid,
            "points": num(s.get("points")), "position": s.get("position"),
            "value": value, "valueApi": num(s.get("teamValue"), None),
            "inc": inc, "size": len(roster) or s.get("teamSize"),
            "roster": roster, "me": uid == me,
        })
    users.sort(key=lambda u: (u["position"] if u["position"] is not None else 999, -u["points"]))

    # ---- saldos -----------------------------------------------------------
    moves, net, reset, start_bonus = parse_board(D(raw.get("tablon") or {}))
    my_balance = D(raw.get("yo") or {}).get("balance") if raw.get("yo") else None
    initial = cfg.get("saldo_inicial")
    if reset is not None:
        # Tras un reinicio cada uno recibe un equipo distinto: lo común es el
        # patrimonio (saldo + equipo), no el saldo. saldo_inicial es ese patrimonio,
        # contando las primas del primer día.
        v0 = start_values(reset, users, players)
        base = None
        if my_balance is not None:
            base = num(my_balance) - net.get(me, 0) + v0.get(me, 0)
            if initial is not None and abs(base + start_bonus.get(me, 0) - num(initial)) > 500000:
                warnings.append("Tu saldo indica un patrimonio inicial de %.2fM, no los %.2fM de "
                                "saldo_inicial. Se usa el calculado con tu saldo."
                                % ((base + start_bonus.get(me, 0)) / 1e6, num(initial) / 1e6))
        elif initial is not None:
            bonus = start_bonus.values()
            base = num(initial) - (sum(bonus) / len(bonus) if bonus else 0)
        initial = {u["id"]: base - v0.get(u["id"], 0) for u in users} if base is not None else None
    elif initial is None and my_balance is not None:
        initial = num(my_balance) - net.get(me, 0)
    for u in users:
        start = initial.get(u["id"]) if isinstance(initial, dict) else initial
        if u["me"] and my_balance is not None:
            u["balance"], u["balanceExact"] = num(my_balance), True
        elif start is not None:
            u["balance"], u["balanceExact"] = num(start) + net.get(u["id"], 0), False
        else:
            u["balance"], u["balanceExact"] = None, False

    # ---- mercado ----------------------------------------------------------
    market = []
    mdata = D(raw.get("mercado") or {})
    for sale in as_list(mdata.get("sales") if isinstance(mdata, dict) else mdata):
        if not isinstance(sale, dict):
            continue
        pid = ensure_player(players, sale.get("player"), teams, positions)
        if not pid:
            continue
        seller = sale.get("user")
        price = num(sale.get("price"), players[pid]["price"])
        market.append({
            "pid": pid, "price": price,
            "sellerId": uid_of(seller),
            "seller": (seller or {}).get("name") if isinstance(seller, dict) else None,
            "until": sale.get("until"), "date": sale.get("date"),
        })

    names = {u["id"]: u["name"] for u in users}
    recent = []
    for m in moves[:25]:
        if m.get("pid"):
            ensure_player(players, m["pid"], teams, positions)
        recent.append(dict(m, fromName=names.get(m["from"]) if m["from"] else None,
                           toName=names.get(m["to"]) if m["to"] else None))

    # Solo se incrustan en el panel los jugadores que se usan, más los que más se mueven
    used = set(owner_of) | {m["pid"] for m in market} | {m["pid"] for m in recent if m["pid"]}
    movers = sorted(players.values(), key=lambda p: p["inc"])
    used |= {p["id"] for p in movers[:15]} | {p["id"] for p in movers[-15:]}
    top_avg = sorted((p for p in players.values() if p["id"] not in owner_of),
                     key=lambda p: -p["avg"])[:15]
    used |= {p["id"] for p in top_avg}

    return {
        "league": meta.get("liga_nombre") or league.get("name") or "Mi liga",
        "updated": meta.get("descargado"),
        "me": me,
        "users": users,
        "market": market,
        "moves": recent,
        "players": {pid: players[pid] for pid in used if pid in players},
        "owner": owner_of,
        "freeTop": [p["id"] for p in top_avg],
        "balanceNote": ("Tu saldo es exacto. El de los rivales se estima con los fichajes, "
                        "ventas y primas del tablón de la liga."
                        if my_balance is not None else
                        "Saldos estimados a partir del tablón de la liga."),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
#  Lesiones (RotoWire)
# --------------------------------------------------------------------------- #

MONTHS_ES = {"Jan": "ene", "Feb": "feb", "Mar": "mar", "Apr": "abr", "May": "may", "Jun": "jun",
             "Jul": "jul", "Aug": "ago", "Sep": "sep", "Oct": "oct", "Nov": "nov", "Dec": "dic"}


def date_es(s):
    """'Dec 1' -> '1 dic', 'Apr 2, 2027' -> '2 abr 2027'."""
    parts = (s or "").replace(",", "").split()
    if len(parts) < 2 or parts[0] not in MONTHS_ES:
        return s or ""
    return " ".join([parts[1], MONTHS_ES[parts[0]]] + parts[2:])


def update_injuries(warnings):
    try:
        import lesiones_rotowire
        lesiones_rotowire.update()
    except Exception as e:
        warnings.append("No se pudieron consultar las lesiones en RotoWire: %s" % e)


def attach_injuries(model, injuries):
    """Añade a cada jugador del panel el estado de RotoWire (campo rw)."""
    by_name = {r.get("jugador"): r for r in injuries or [] if "error" not in r}
    for p in model["players"].values():
        r = by_name.get(p["name"])
        if not r:
            continue
        news = " ".join(x for x in (r.get("fecha"), r.get("titular") and r["titular"] + ".",
                                    r.get("noticia")) if x)
        p["rw"] = {
            "status": r.get("estado") or "",
            "injury": r.get("lesion") or "",
            "back": date_es(r.get("vuelta")),
            "news": news,
            "url": r.get("url") or "",
        }


# --------------------------------------------------------------------------- #
#  Historial
# --------------------------------------------------------------------------- #

def save_snapshot(model):
    today = dt.date.today().isoformat()
    snap = {
        "fecha": today,
        "usuarios": {u["id"]: {"nombre": u["name"], "valor": u["value"],
                               "saldo": u["balance"], "puntos": u["points"]}
                     for u in model["users"]},
    }
    save_json(os.path.join(HIST_DIR, today + ".json"), snap)


def load_history():
    hist = []
    if os.path.isdir(HIST_DIR):
        for fn in sorted(os.listdir(HIST_DIR)):
            if fn.endswith(".json"):
                snap = load_json(os.path.join(HIST_DIR, fn))
                if snap and snap.get("fecha"):
                    hist.append(snap)
    return hist


def attach_history(model, hist):
    today = dt.date.today()
    prev = [h for h in hist if h["fecha"] < today.isoformat()]
    week_ref = None
    for h in hist:
        if h["fecha"] <= (today - dt.timedelta(days=7)).isoformat():
            week_ref = h
    if week_ref is None and prev:
        week_ref = prev[0]
    last = prev[-1] if prev else None
    for u in model["users"]:
        u["chgPrev"] = (u["value"] - last["usuarios"][u["id"]]["valor"]
                        if last and u["id"] in last["usuarios"] else None)
        u["chgWeek"] = (u["value"] - week_ref["usuarios"][u["id"]]["valor"]
                        if week_ref and u["id"] in week_ref["usuarios"] else None)
    model["weekRef"] = week_ref["fecha"] if week_ref else None
    model["history"] = [{"fecha": h["fecha"],
                         "valores": {k: v.get("valor") for k, v in h["usuarios"].items()}}
                        for h in hist[-120:]]


# --------------------------------------------------------------------------- #
#  HTML
# --------------------------------------------------------------------------- #

def render(model):
    payload = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    page = HTML_TEMPLATE.replace("__DATA__", payload)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel Fantasy NBA</title>
<style>
:root{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --surface-2:#f2f1ed;
  --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --line:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --accent:#2a78d6; --accent-soft:#e3eefb;
  --up:#006300; --down:#c23232;
  --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
  --other:#c3c2b7;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --surface-2:#232321;
    --ink:#f0efec; --ink-2:#c3c2b7; --muted:#898781;
    --line:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --accent:#3987e5; --accent-soft:#1d2a3b;
    --up:#0ca30c; --down:#e66767; --other:#4a4a46;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --surface-2:#232321;
  --ink:#f0efec; --ink-2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --accent:#3987e5; --accent-soft:#1d2a3b;
  --up:#0ca30c; --down:#e66767; --other:#4a4a46;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:1180px;margin:0 auto;display:flex;
  justify-content:space-between;align-items:flex-start;gap:12px}
h1{margin:0;font-size:22px;letter-spacing:-.01em}
.sub{color:var(--ink-2);font-size:13px;margin-top:2px}
nav{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--line)}
nav .in{max-width:1180px;margin:0 auto;padding:0 16px;display:flex;gap:4px;overflow-x:auto}
nav a{color:var(--ink-2);text-decoration:none;padding:10px 10px;font-size:14px;white-space:nowrap;
  border-bottom:2px solid transparent}
nav a:hover{color:var(--ink)}
nav a.on{color:var(--ink);border-bottom-color:var(--accent)}
main{max-width:1180px;margin:0 auto;padding:12px 16px 60px}
section{margin-top:28px;scroll-margin-top:56px}
h2{font-size:17px;margin:0 0 4px}
.note{color:var(--muted);font-size:12.5px;margin:0 0 10px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.kpi{padding:14px 14px 12px}
.kpi .l{color:var(--ink-2);font-size:12.5px}
.kpi .v{font-size:22px;font-weight:650;margin-top:2px}
.kpi .d{font-size:12.5px;margin-top:2px;color:var(--ink-2)}
.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 10px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}
th{color:var(--ink-2);font-weight:600;font-size:12.5px;cursor:pointer;user-select:none;
  position:sticky;top:0;background:var(--surface)}
th.sorted::after{content:" ▾";color:var(--muted)}
th.sorted.asc::after{content:" ▴"}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tr.me td{background:var(--accent-soft)}
tbody tr:hover td{background:var(--surface-2)}
tr.me:hover td{background:var(--accent-soft)}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--muted)}
.tag{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:999px;
  background:var(--surface-2);color:var(--ink-2);margin-left:4px}
.st{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;color:var(--ink-2)}
.st i{width:8px;height:8px;border-radius:50%;display:inline-block}
.pl small{color:var(--muted);display:block;font-size:11.5px}
.rw{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;
  text-decoration:none;border:1px solid currentColor}
.rw span{font-weight:400}
.rw-out{color:var(--crit);background:color-mix(in srgb,var(--crit) 12%,transparent)}
.rw-doubt{color:var(--serious);background:color-mix(in srgb,var(--serious) 14%,transparent)}
.rw-q{color:#b07a00;background:color-mix(in srgb,var(--warn) 18%,transparent)}
.rw-prob{color:var(--good);background:color-mix(in srgb,var(--good) 12%,transparent)}
.rw-none{color:var(--muted);background:var(--surface-2)}
.rw-out-name{color:var(--crit)} .rw-doubt-name{color:var(--serious)} .rw-q-name{color:#b07a00}
.fit{display:inline-flex;gap:3px}
.fit b{font-weight:500;font-size:11px;min-width:22px;text-align:center;padding:1px 3px;
  border-radius:4px;background:var(--surface-2);color:var(--ink-2)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 10px}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
  padding:5px 11px;border-radius:999px;font:inherit;font-size:13px;cursor:pointer}
.chip.on{border-color:var(--accent);color:var(--ink);background:var(--accent-soft)}
select,button.tg{font:inherit;font-size:13.5px;padding:6px 10px;border-radius:8px;
  border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.warn{border-left:3px solid var(--warn);padding:10px 14px;margin-top:14px;font-size:13px}
.warn ul{margin:4px 0 0;padding-left:18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:760px){.grid2{grid-template-columns:1fr}}
.chart{position:relative;padding:12px 12px 6px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--ring);
  border-radius:8px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.12);
  display:none;min-width:170px;z-index:3}
.tip .r{display:flex;justify-content:space-between;gap:12px}
.tip .r.hl{font-weight:650}
.legend{display:flex;gap:14px;font-size:12.5px;color:var(--ink-2);padding:2px 4px 8px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:14px;height:2px;display:inline-block}
.empty{padding:22px;color:var(--muted);text-align:center;font-size:13.5px}
.mini{padding:6px 0}
.mini h3{font-size:13.5px;margin:6px 12px 4px;color:var(--ink-2);font-weight:600}
</style>
</head>
<body>
<header>
  <div>
    <h1 id="title">Panel Fantasy NBA</h1>
    <div class="sub" id="updated"></div>
  </div>
  <button class="tg" id="theme" type="button" aria-label="Cambiar tema claro u oscuro">Tema</button>
</header>
<nav><div class="in">
  <a href="#resumen" class="on">Resumen</a><a href="#usuarios">Usuarios</a>
  <a href="#mercado">Mercado</a><a href="#plantillas">Plantillas</a>
  <a href="#evolucion">Evolución</a><a href="#jugadores">Subidas y bajadas</a>
  <a href="#movimientos">Movimientos</a>
</div></nav>
<main>
  <div id="warnings"></div>

  <section id="resumen">
    <h2>Resumen</h2>
    <div class="kpis" id="kpis"></div>
  </section>

  <section id="usuarios">
    <h2>Usuarios de la liga</h2>
    <p class="note" id="balanceNote"></p>
    <div class="card tbl" id="usersTbl"></div>
  </section>

  <section id="mercado">
    <h2>Jugadores en el mercado</h2>
    <p class="note">Diferencia = precio pedido menos valor actual. Negativo es más barato que su valor.</p>
    <div class="bar" id="mktFilters"></div>
    <div class="card tbl" id="mktTbl"></div>
  </section>

  <section id="plantillas">
    <h2>Plantillas</h2>
    <div class="bar"><select id="rosterSel" aria-label="Usuario"></select>
      <span class="note" id="rosterSum" style="margin:0"></span></div>
    <div class="card tbl" id="rosterTbl"></div>
  </section>

  <section id="evolucion">
    <h2>Evolución del valor de los equipos</h2>
    <div class="bar"><label class="note" style="margin:0" for="hlSel">Destacar:</label>
      <select id="hlSel"></select></div>
    <div class="card chart" id="chart"></div>
  </section>

  <section id="jugadores">
    <h2>Subidas y bajadas de hoy</h2>
    <p class="note">Todos los jugadores de la competición. «Libre» = no pertenece a nadie de tu liga.</p>
    <div class="grid2">
      <div class="card tbl mini"><h3>▲ Más suben</h3><div id="upTbl"></div></div>
      <div class="card tbl mini"><h3>▼ Más bajan</h3><div id="downTbl"></div></div>
    </div>
    <h2 style="margin-top:22px">Mejores medias sin dueño en tu liga</h2>
    <p class="note">Para vigilar cuando salgan al mercado.</p>
    <div class="card tbl" id="freeTbl"></div>
  </section>

  <section id="movimientos">
    <h2>Últimos movimientos</h2>
    <div class="card tbl" id="movesTbl"></div>
  </section>
</main>

<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
const M = JSON.parse(document.getElementById("data").textContent);
const P = M.players || {};
const users = M.users || [];
const byId = Object.fromEntries(users.map(u => [u.id, u]));
const me = byId[M.me];

/* ---------- tema ---------- */
const root = document.documentElement;
try { const t = localStorage.getItem("pfn-theme"); if (t) root.dataset.theme = t; } catch(e) {}
document.getElementById("theme").onclick = () => {
  const dark = root.dataset.theme ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("pfn-theme", root.dataset.theme); } catch(e) {}
  drawChart();
};

/* ---------- formato ---------- */
const nf = (d) => new Intl.NumberFormat("es-ES", {maximumFractionDigits:d, minimumFractionDigits:d});
function money(v, signed){
  if (v === null || v === undefined || isNaN(v)) return "—";
  const a = Math.abs(v), s = v < 0 ? "−" : (signed && v > 0 ? "+" : "");
  if (a >= 1e6) return s + nf(a >= 1e8 ? 0 : 2).format(a/1e6) + " M€";
  if (a >= 1e3) return s + nf(0).format(a/1e3) + " k€";
  return s + nf(0).format(a) + " €";
}
function delta(v){
  if (v === null || v === undefined || isNaN(v)) return '<span class="flat">—</span>';
  if (v === 0) return '<span class="flat">= 0 €</span>';
  return v > 0 ? `<span class="up">▲ ${money(v,true)}</span>` : `<span class="down">▼ ${money(v)}</span>`;
}
function date(ts, withTime){
  if (!ts) return "—";
  const d = typeof ts === "number" ? new Date(ts < 1e12 ? ts*1000 : ts) : new Date(ts);
  if (isNaN(d)) return "—";
  return d.toLocaleDateString("es-ES", withTime ? {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"} : {day:"numeric",month:"short"});
}
function left(ts){
  if (!ts) return "—";
  const ms = (ts < 1e12 ? ts*1000 : ts) - Date.now();
  if (ms <= 0) return "cerrado";
  const h = Math.floor(ms/36e5), m = Math.floor(ms%36e5/6e4);
  return h >= 24 ? Math.floor(h/24) + " d " + (h%24) + " h" : h + " h " + m + " min";
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const STC = {ok:"var(--good)", doubt:"var(--warn)", injured:"var(--crit)", sanctioned:"var(--serious)", warned:"var(--warn)"};
// Estado de RotoWire -> clase de color
function rwClass(s){
  s = (s || "").toLowerCase();
  if (s === "out" || s === "ofs") return "rw-out";
  if (s.startsWith("doubt")) return "rw-doubt";
  if (s.startsWith("quest") || s.startsWith("gtd")) return "rw-q";
  if (s.startsWith("prob")) return "rw-prob";
  return "rw-none";
}
function status(p){
  if (p.rw){
    const r = p.rw, cls = rwClass(r.status);
    const label = cls === "rw-none" ? "Sin parte RW" : r.status;
    const tip = [r.injury, r.back && "Vuelta estimada: " + r.back, r.news].filter(Boolean).join("\n");
    return `<a class="rw ${cls}" href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(tip)}">`
      + `${esc(label)}${r.back ? ` <span>· vuelve ${esc(r.back)}</span>` : ""}</a>`;
  }
  const title = p.statusInfo ? ` title="${esc(p.statusInfo)}"` : "";
  return `<span class="st"${title}><i style="background:${STC[p.status]||"var(--muted)"}"></i>${esc(p.statusEs)}</span>`;
}
function statusSort(p){
  const order = {"rw-out":0, "rw-doubt":1, "rw-q":2, "rw-prob":3, "rw-none":4};
  return p.rw ? order[rwClass(p.rw.status)] : p.status === "ok" ? 9 : 5;
}
function player(p){
  const cls = p.rw ? " " + rwClass(p.rw.status) + "-name" : "";
  return `<div class="pl${cls}">${esc(p.name)}<small>${esc(p.team)}${p.pos ? " · " + esc(p.pos) : ""}</small></div>`;
}
function fitness(p){
  if (!p.fitness || !p.fitness.length) return "—";
  return '<span class="fit">' + p.fitness.map(f => `<b>${f === null ? "–" : typeof f === "number" ? f : esc(String(f).slice(0,3))}</b>`).join("") + "</span>";
}
function ownerName(pid){ const u = byId[(M.owner||{})[pid]]; return u ? esc(u.name) + (u.me ? ' <span class="tag">tú</span>' : "") : '<span class="tag">Libre</span>'; }

/* ---------- tablas ordenables ---------- */
function table(el, cols, rows, sortKey, sortDir, rowClass){
  let key = sortKey, dir = sortDir || -1;
  function draw(){
    const col = cols.find(c => c.key === key);
    const sorted = col ? rows.slice().sort((a,b) => {
      let x = col.sort ? col.sort(a) : a[key], y = col.sort ? col.sort(b) : b[key];
      if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
      return (typeof x === "string" ? x.localeCompare(y, "es") : x - y) * dir;
    }) : rows;
    el.innerHTML = rows.length ? `<table><thead><tr>${cols.map(c =>
      `<th data-k="${c.key}" class="${c.num ? "n " : ""}${c.key === key ? "sorted" + (dir > 0 ? " asc" : "") : ""}">${c.label}</th>`).join("")}</tr></thead><tbody>${
      sorted.map(r => `<tr class="${rowClass ? rowClass(r) : ""}">${cols.map(c =>
        `<td class="${c.num ? "n" : ""}">${c.fmt ? c.fmt(r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table>`
      : '<div class="empty">Sin datos</div>';
    el.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k; if (k === key) dir = -dir; else { key = k; dir = cols.find(c=>c.key===k).num ? -1 : 1; }
      draw();
    });
  }
  draw();
}

/* ---------- cabecera y avisos ---------- */
document.getElementById("title").textContent = M.league || "Panel Fantasy NBA";
document.getElementById("updated").textContent = "Actualizado " + (M.updated ? new Date(M.updated).toLocaleString("es-ES", {weekday:"long", day:"numeric", month:"long", hour:"2-digit", minute:"2-digit"}) : "—");
if (M.warnings && M.warnings.length) {
  document.getElementById("warnings").innerHTML = `<div class="card warn"><b>Avisos de la última descarga</b><ul>${M.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
}
document.getElementById("balanceNote").textContent = M.balanceNote + (M.weekRef ? ` Variación semanal desde el ${date(M.weekRef)}.` : "");

/* ---------- resumen ---------- */
(function(){
  const k = [];
  if (me) {
    const worth = users.map(u => ({id:u.id, w:(u.balance||0)+u.value})).sort((a,b)=>b.w-a.w);
    const valRank = users.slice().sort((a,b)=>b.value-a.value).findIndex(u=>u.me)+1;
    k.push(["Tu saldo", money(me.balance), me.balanceExact ? "Dato exacto de tu cuenta" : "Estimado"]);
    k.push(["Valor de tu equipo", money(me.value), `${valRank}º de ${users.length} en valor`]);
    k.push(["Variación hoy", delta(me.inc), me.chgWeek !== null && me.chgWeek !== undefined ? "7 días: " + delta(me.chgWeek) : "Semana: falta historial"]);
    k.push(["Clasificación", (me.position ?? "—") + "º", nf(0).format(me.points) + " puntos"]);
    k.push(["Patrimonio", money((me.balance||0)+me.value), `${worth.findIndex(w=>w.id===me.id)+1}º de la liga (saldo + equipo)`]);
  }
  const free = (M.market||[]).filter(m => !m.sellerId).length;
  k.push(["En el mercado", String((M.market||[]).length), `${free} del mercado, ${(M.market||[]).length - free} de usuarios`]);
  document.getElementById("kpis").innerHTML = k.map(([l,v,d]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");
})();

/* ---------- usuarios ---------- */
table(document.getElementById("usersTbl"), [
  {key:"position", label:"#", num:true, fmt:r => r.position ?? "—", sort:r => -(r.position ?? 999)},
  {key:"name", label:"Usuario", fmt:r => esc(r.name) + (r.me ? ' <span class="tag">tú</span>' : "")},
  {key:"points", label:"Puntos", num:true, fmt:r => nf(0).format(r.points)},
  {key:"value", label:"Valor equipo", num:true, fmt:r => money(r.value)},
  {key:"inc", label:"Var. hoy", num:true, fmt:r => delta(r.inc)},
  {key:"chgWeek", label:"Var. 7 días", num:true, fmt:r => delta(r.chgWeek)},
  {key:"balance", label:"Saldo", num:true, fmt:r => money(r.balance) + (r.balance !== null && !r.balanceExact ? ' <span class="tag">est.</span>' : "")},
  {key:"worth", label:"Patrimonio", num:true, fmt:r => money(r.worth), sort:r => r.worth},
  {key:"size", label:"Jug.", num:true, fmt:r => r.size ?? "—"},
], users.map(u => Object.assign(u, {worth: (u.balance||0) + u.value})), "position", -1, r => r.me ? "me" : "");

/* ---------- mercado ---------- */
(function(){
  const rows = (M.market||[]).filter(m => P[m.pid]).map(m => {
    const p = P[m.pid];
    return Object.assign({}, m, {p, name:p.name, value:p.price, inc:p.inc, avg:p.avg, points:p.points,
      diff: m.price - p.price, seller: m.sellerId ? (byId[m.sellerId]?.name || m.seller || "Usuario") : null});
  });
  const pos = [...new Set(rows.map(r => r.p.pos).filter(Boolean))];
  const state = {pos:null, who:"all"};
  const bar = document.getElementById("mktFilters");
  function chips(){
    bar.innerHTML = [["all","Todos"],["free","Del mercado"],["users","De usuarios"]].map(([k,l]) =>
      `<button class="chip ${state.who===k?"on":""}" data-w="${k}">${l}</button>`).join("") +
      '<span style="width:8px"></span>' +
      [null, ...pos].map(p => `<button class="chip ${state.pos===p?"on":""}" data-p="${p ?? ""}">${p ?? "Todas"}</button>`).join("");
    bar.querySelectorAll("[data-w]").forEach(b => b.onclick = () => { state.who = b.dataset.w; chips(); draw(); });
    bar.querySelectorAll("[data-p]").forEach(b => b.onclick = () => { state.pos = b.dataset.p || null; chips(); draw(); });
  }
  function draw(){
    const f = rows.filter(r => (!state.pos || r.p.pos === state.pos) &&
      (state.who === "all" || (state.who === "free" ? !r.sellerId : !!r.sellerId)));
    table(document.getElementById("mktTbl"), [
      {key:"name", label:"Jugador", fmt:r => player(r.p)},
      {key:"status", label:"Estado", fmt:r => status(r.p), sort:r => statusSort(r.p)},
      {key:"seller", label:"Vende", fmt:r => r.sellerId ? esc(r.seller) + (r.sellerId === M.me ? ' <span class="tag">tú</span>' : "") : '<span class="tag">Mercado</span>', sort:r => r.seller || ""},
      {key:"price", label:"Precio", num:true, fmt:r => money(r.price)},
      {key:"value", label:"Valor", num:true, fmt:r => money(r.value)},
      {key:"diff", label:"Diferencia", num:true, fmt:r => r.diff === 0 ? '<span class="flat">0</span>' : `<span class="${r.diff < 0 ? "up" : "down"}">${money(r.diff, true)}</span>`},
      {key:"inc", label:"Var. hoy", num:true, fmt:r => delta(r.inc)},
      {key:"points", label:"Puntos", num:true, fmt:r => nf(0).format(r.points)},
      {key:"avg", label:"Media", num:true, fmt:r => nf(1).format(r.avg)},
      {key:"fit", label:"Últimos", fmt:r => fitness(r.p), sort:r => 0},
      {key:"until", label:"Cierra", fmt:r => left(r.until), sort:r => -(r.until||0)},
    ], f, "avg", -1);
  }
  chips(); draw();
})();

/* ---------- plantillas ---------- */
(function(){
  const sel = document.getElementById("rosterSel");
  sel.innerHTML = users.map(u => `<option value="${u.id}" ${u.me ? "selected" : ""}>${esc(u.name)}${u.me ? " (tú)" : ""}</option>`).join("");
  function draw(){
    const u = byId[sel.value]; if (!u) return;
    const rows = u.roster.filter(r => P[r.pid]).map(r => {
      const p = P[r.pid];
      return {p, name:p.name, value:p.price, inc:p.inc, buy:r.buy, profit: r.buy ? p.price - r.buy : null,
        clause:r.clause, points:p.points, avg:p.avg, since:r.since};
    });
    document.getElementById("rosterSum").innerHTML = `${rows.length} jugadores · ${money(u.value)} · hoy ${delta(u.inc)}`;
    table(document.getElementById("rosterTbl"), [
      {key:"name", label:"Jugador", fmt:r => player(r.p)},
      {key:"status", label:"Estado", fmt:r => status(r.p), sort:r => statusSort(r.p)},
      {key:"value", label:"Valor", num:true, fmt:r => money(r.value)},
      {key:"inc", label:"Var. hoy", num:true, fmt:r => delta(r.inc)},
      {key:"buy", label:"Compra", num:true, fmt:r => money(r.buy)},
      {key:"profit", label:"Beneficio", num:true, fmt:r => delta(r.profit)},
      {key:"clause", label:"Cláusula", num:true, fmt:r => money(r.clause)},
      {key:"points", label:"Puntos", num:true, fmt:r => nf(0).format(r.points)},
      {key:"avg", label:"Media", num:true, fmt:r => nf(1).format(r.avg)},
      {key:"fit", label:"Últimos", fmt:r => fitness(r.p), sort:r => 0},
    ], rows, "value", -1);
  }
  sel.onchange = draw; draw();
})();

/* ---------- gráfico de evolución ---------- */
const hlSel = document.getElementById("hlSel");
hlSel.innerHTML = users.map(u => `<option value="${u.id}" ${u.me ? "selected" : ""}>${esc(u.name)}${u.me ? " (tú)" : ""}</option>`).join("");
hlSel.onchange = drawChart;
function drawChart(){
  const box = document.getElementById("chart");
  const H = (M.history||[]).filter(h => h.valores);
  if (H.length < 2) { box.innerHTML = '<div class="empty">El gráfico aparece a partir del segundo día de datos. Ejecuta el script cada día y se irá llenando.</div>'; return; }
  const css = getComputedStyle(root);
  const c = n => css.getPropertyValue(n).trim();
  const W = Math.max(320, box.clientWidth - 24), Ht = W < 520 ? 240 : 300;
  const m = {l: 58, r: 12, t: 10, b: 26};
  const iw = W - m.l - m.r, ih = Ht - m.t - m.b;
  const hl = hlSel.value;
  const ids = users.map(u => u.id);
  let lo = Infinity, hi = -Infinity;
  H.forEach(h => ids.forEach(id => { const v = h.valores[id]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  const span = (hi - lo) || hi * 0.1 || 1;
  const raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const tstep = [1, 2, 2.5, 5, 10].map(f => f * mag).find(v => v >= raw);
  lo = Math.floor(lo / tstep) * tstep; hi = Math.ceil(hi / tstep) * tstep;
  const x = i => m.l + (H.length === 1 ? iw/2 : i * iw / (H.length - 1));
  const y = v => m.t + ih - (v - lo) / (hi - lo) * ih;
  const tv = []; for (let v = lo; v <= hi + tstep / 2; v += tstep) tv.push(v);
  const step = Math.max(1, Math.ceil(H.length / (W < 520 ? 4 : 7)));
  let s = `<svg viewBox="0 0 ${W} ${Ht}" role="img" aria-label="Valor de equipo por día">`;
  tv.forEach(v => s += `<line x1="${m.l}" x2="${W-m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${c("--line")}" stroke-width="1"/><text x="${m.l-8}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="${c("--muted")}">${money(v)}</text>`);
  H.forEach((h, i) => { if (i % step === 0 || i === H.length - 1) s += `<text x="${x(i)}" y="${Ht-6}" text-anchor="middle" font-size="11" fill="${c("--muted")}">${date(h.fecha + "T12:00")}</text>`; });
  const path = id => { let d = "", pen = false; H.forEach((h, i) => { const v = h.valores[id]; if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); pen = true; }); return d; };
  ids.filter(id => id !== hl).forEach(id => s += `<path d="${path(id)}" fill="none" stroke="${c("--other")}" stroke-width="1.5" stroke-linejoin="round"/>`);
  s += `<path d="${path(hl)}" fill="none" stroke="${c("--accent")}" stroke-width="2.5" stroke-linejoin="round"/>`;
  const li = H.length - 1, lv = H[li].valores[hl];
  if (lv != null) s += `<circle cx="${x(li)}" cy="${y(lv)}" r="4" fill="${c("--accent")}" stroke="${c("--surface")}" stroke-width="2"/>`;
  s += `<line id="xh" y1="${m.t}" y2="${m.t+ih}" stroke="${c("--axis")}" stroke-width="1" visibility="hidden"/>`;
  s += `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" id="hit"/></svg>`;
  const hu = byId[hl];
  box.innerHTML = `<div class="legend"><span><i style="background:${c("--accent")};height:3px"></i>${esc(hu ? hu.name : "")}</span><span><i style="background:${c("--other")}"></i>Resto de equipos</span></div>` + s + '<div class="tip" id="tip"></div>';
  const svg = box.querySelector("svg"), tip = box.querySelector("#tip"), xh = box.querySelector("#xh");
  function move(ev){
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(H.length - 1, Math.round((sx - m.l) / iw * (H.length - 1))));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
    const rows = ids.map(id => ({id, v:H[i].valores[id]})).filter(o => o.v != null).sort((a,b) => b.v - a.v);
    tip.innerHTML = `<div style="color:var(--muted);margin-bottom:4px">${date(H[i].fecha + "T12:00")}</div>` +
      rows.slice(0, 12).map(o => `<div class="r ${o.id === hl ? "hl" : ""}"><span>${esc(byId[o.id]?.name || o.id)}</span><span>${money(o.v)}</span></div>`).join("");
    tip.style.display = "block";
    const px = x(i) * r.width / W + 12, tw = tip.offsetWidth;
    tip.style.left = (px + tw > box.clientWidth ? px - tw - 24 : px) + "px";
    tip.style.top = "36px";
  }
  const hit = box.querySelector("#hit");
  hit.addEventListener("pointermove", move); hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => { tip.style.display = "none"; xh.setAttribute("visibility", "hidden"); });
}
drawChart();
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(drawChart, 150); });

/* ---------- subidas, bajadas y libres ---------- */
(function(){
  const all = Object.values(P);
  const mini = [
    {key:"name", label:"Jugador", fmt:r => player(r)},
    {key:"inc", label:"Var.", num:true, fmt:r => delta(r.inc)},
    {key:"price", label:"Valor", num:true, fmt:r => money(r.price)},
    {key:"own", label:"Dueño", fmt:r => ownerName(r.id), sort:r => byId[(M.owner||{})[r.id]]?.name || "~"},
  ];
  table(document.getElementById("upTbl"), mini, all.filter(p => p.inc > 0).sort((a,b) => b.inc - a.inc).slice(0, 12), "inc", -1);
  table(document.getElementById("downTbl"), mini, all.filter(p => p.inc < 0).sort((a,b) => a.inc - b.inc).slice(0, 12), "inc", 1);
  table(document.getElementById("freeTbl"), [
    {key:"name", label:"Jugador", fmt:r => player(r)},
    {key:"status", label:"Estado", fmt:r => status(r), sort:r => statusSort(r)},
    {key:"avg", label:"Media", num:true, fmt:r => nf(1).format(r.avg)},
    {key:"points", label:"Puntos", num:true, fmt:r => nf(0).format(r.points)},
    {key:"price", label:"Valor", num:true, fmt:r => money(r.price)},
    {key:"inc", label:"Var. hoy", num:true, fmt:r => delta(r.inc)},
    {key:"fit", label:"Últimos", fmt:r => fitness(r), sort:r => 0},
  ], (M.freeTop||[]).map(id => P[id]).filter(Boolean), "avg", -1);
})();

/* ---------- movimientos ---------- */
(function(){
  const TYPE = {transfer:"Traspaso", market:"Mercado", exchange:"Intercambio", loan:"Cesión", loanReturn:"Fin cesión", adminTransfer:"Admin"};
  const rows = (M.moves||[]).map(m => Object.assign({}, m, {p:P[m.pid], name:P[m.pid]?.name || "—",
    kind: !m.from && m.to ? "Compra al mercado" : m.from && !m.to ? "Venta al mercado" : (TYPE[m.type] || m.type)}));
  const who = (id, name) => id ? esc(name || byId[id]?.name || "Usuario") + (id === M.me ? ' <span class="tag">tú</span>' : "") : '<span class="tag">Mercado</span>';
  table(document.getElementById("movesTbl"), [
    {key:"date", label:"Fecha", fmt:r => date(r.date, true)},
    {key:"kind", label:"Tipo"},
    {key:"name", label:"Jugador", fmt:r => r.p ? player(r.p) : "—"},
    {key:"fromName", label:"De", fmt:r => who(r.from, r.fromName)},
    {key:"toName", label:"A", fmt:r => who(r.to, r.toName)},
    {key:"amount", label:"Importe", num:true, fmt:r => money(r.amount)},
  ], rows, "date", -1);
})();

/* ---------- navegación ---------- */
const links = [...document.querySelectorAll("nav a")];
const secs = links.map(a => document.querySelector(a.getAttribute("href")));
addEventListener("scroll", () => {
  let cur = 0; secs.forEach((s, i) => { if (s.getBoundingClientRect().top < 120) cur = i; });
  links.forEach((a, i) => a.classList.toggle("on", i === cur));
}, {passive:true});
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
#  Principal
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Panel Fantasy NBA para Biwenger")
    ap.add_argument("--offline", action="store_true",
                    help="no descargar; regenerar el panel con los últimos datos guardados")
    ap.add_argument("--abrir", action="store_true", help="abrir el panel al terminar")
    args = ap.parse_args()

    cfg = load_config()
    warnings = []
    if args.offline:
        raw = load_raw()
    else:
        if not os.path.exists(CONFIG_PATH) and not os.environ.get("BIWENGER_TOKEN") \
                and not os.environ.get("BIWENGER_EMAIL"):
            raise SystemExit("Falta config.json. Copia config.example.json como config.json "
                             "y rellena tus datos.")
        try:
            raw = download(cfg, warnings)
        except ApiError as e:
            msg = str(e)
            hint = ""
            if "401" in msg or "403" in msg:
                hint = ("\n→ La sesión no es válida. Si usas token, cópialo de nuevo desde el "
                        "navegador; si usas email y contraseña, revísalos.")
            elif "400" in msg or "426" in msg:
                hint = ("\n→ Puede que Biwenger pida una versión más nueva: sube «x_version» en "
                        "config.json (mira la cabecera X-Version en el navegador).")
            raise SystemExit("Error: " + msg + hint)
        save_raw(raw)
        print("Consultando lesiones en RotoWire…")
        update_injuries(warnings)

    model = build_model(raw, cfg, warnings)
    if not args.offline:
        save_snapshot(model)
    attach_history(model, load_history())
    attach_injuries(model, load_json(INJURIES_PATH))
    render(model)
    print("\nPanel generado: %s" % OUTPUT_PATH)
    for w in warnings:
        print("  ! " + w)
    if args.abrir:
        webbrowser.open("file://" + OUTPUT_PATH)


if __name__ == "__main__":
    main()
