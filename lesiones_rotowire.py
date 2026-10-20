#!/usr/bin/env python3
"""
Lesiones según RotoWire
=======================

Busca en RotoWire a los jugadores que Biwenger marca como lesionados (tu
plantilla, el mercado libre y las plantillas rivales) y muestra su estado
(Out, Questionable, Probable...), la fecha estimada de vuelta y la última
noticia.

Uso:
    python biwenger_nba.py          # antes, para tener datos/raw/ al día
    python lesiones_rotowire.py

El resultado también se guarda en datos/lesiones.json. La fecha de vuelta es
la estimación de RotoWire ("Est. return"), no un dato oficial del equipo.
Solo usa la biblioteca estándar de Python (3.8 o superior).
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "datos", "raw")
OUTPUT_PATH = os.path.join(HERE, "datos", "lesiones.json")

ROTOWIRE = "https://www.rotowire.com"
SEARCH_URL = ROTOWIRE + "/frontend/ajax/search-players.php?nba=1&searchTerm={q}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh)"}

# Nombres que Biwenger y RotoWire escriben distinto
ALIAS = {
    "Gregory Jackson II": "GG Jackson",
}


def load(name):
    with open(os.path.join(RAW_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def injured_players():
    """Devuelve {nombre: [dónde está]} de los jugadores no disponibles en Biwenger."""
    players = load("publico.json")["data"]["players"]
    me = load("yo.json")["data"]
    market = load("mercado.json")["data"]
    users = load("usuarios.json")
    where = {}

    def add(tag, ids):
        for pid in ids:
            p = players.get(str(pid))
            if p and p.get("status") not in (None, "ok"):
                where.setdefault(p["name"], []).append(tag)

    add("Mi equipo", [x["id"] for x in me.get("players", [])])
    add("Mercado", [s["player"]["id"] for s in market.get("sales", []) if not s.get("user")])
    for u in users.values():
        d = u["data"]
        if d["id"] != me["id"]:
            add(d["name"], [x["id"] for x in d.get("players", [])])
    return where


def rotowire(name):
    """Estado, vuelta estimada y última noticia de un jugador en RotoWire."""
    found = json.loads(get(SEARCH_URL.format(q=urllib.parse.quote(ALIAS.get(name, name)))))
    hits = [x for x in found.get("players", []) if x["link"].startswith("/basketball/")]
    if not hits:
        return {"error": "no encontrado en RotoWire"}
    page = get(ROTOWIRE + hits[0]["link"])
    info = {"equipo": hits[0]["text"], "url": ROTOWIRE + hits[0]["link"]}

    m = re.search(r'pp2-injury-info">(.*?)</div>', page, re.S)
    if m:
        block = m.group(1)
        status = re.search(r'pp2-injury-status">(.*?)</span>', block, re.S)
        injury = re.search(r"<b>(.*?)</b>", block, re.S)
        back = re.search(r"Est\. return\s*<b>(.*?)</b>", block, re.S)
        info["estado"] = text(status.group(1)) if status else ""
        info["lesion"] = text(injury.group(1)) if injury else ""
        info["vuelta"] = text(back.group(1)) if back else ""
    else:
        info["estado"] = "No aparece en el parte"

    m = re.search(r'news-update__headline">(.*?)</div>.*?news-update__timestamp">(.*?)</div>'
                  r'\s*<div class="news-update__news">(.*?)</div>', page, re.S)
    if m:
        info["titular"], info["fecha"], info["noticia"] = (text(g) for g in m.groups())
    return info


def update():
    """Consulta RotoWire y guarda datos/lesiones.json. Lo usa también biwenger_nba.py."""
    result = []
    for name, tags in injured_players().items():
        try:
            info = rotowire(name)
        except Exception as e:
            info = {"error": str(e)}
        result.append({"jugador": name, "donde": tags, **info})
        time.sleep(0.5)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    try:
        result = update()
    except FileNotFoundError as e:
        sys.exit(f"Falta {e.filename}. Ejecuta antes: python biwenger_nba.py")

    for r in result:
        if "error" in r:
            print(f"{r['jugador']} ({', '.join(r['donde'])}): {r['error']}")
            continue
        estado = " · ".join(x for x in (r.get("estado"), r.get("lesion"),
                                        r.get("vuelta") and "vuelta " + r["vuelta"]) if x)
        print(f"{r['jugador']} [{r['equipo']}] ({', '.join(r['donde'])}): {estado}")
        if r.get("noticia"):
            print(f"    {r['fecha']}: {r['titular']}. {r['noticia']}")
    print(f"\nGuardado en {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
