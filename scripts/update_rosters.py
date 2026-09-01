#!/usr/bin/env python3

import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin

SOURCE_URL = "https://www.fantacalcio.it/quotazioni-fantacalcio/2026-27"
STATS_URL = "https://www.fantacalcio.it/statistiche-serie-a/2026-27/italia"
OUTPUT = Path("data/seriea_rosters.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}

TEAM_BY_SLUG = {
    "atalanta": "Atalanta",
    "bologna": "Bologna",
    "cagliari": "Cagliari",
    "como": "Como",
    "fiorentina": "Fiorentina",
    "frosinone": "Frosinone",
    "genoa": "Genoa",
    "inter": "Inter",
    "juventus": "Juventus",
    "lazio": "Lazio",
    "lecce": "Lecce",
    "milan": "Milan",
    "monza": "Monza",
    "napoli": "Napoli",
    "parma": "Parma",
    "roma": "Roma",
    "sassuolo": "Sassuolo",
    "torino": "Torino",
    "udinese": "Udinese",
    "venezia": "Venezia",
}

ROLE_WORDS = {
    "P": ("p", "por", "portiere", "portieri", "goalkeeper"),
    "D": ("d", "dif", "difensore", "difensori", "defender"),
    "C": ("c", "cen", "centrocampista", "centrocampisti", "midfielder"),
    "A": ("a", "att", "attaccante", "attaccanti", "forward", "striker"),
}


def fetch(url, tries=3):
    last_error = None
    for attempt in range(tries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < tries:
                time.sleep(2 * (attempt + 1))
    raise last_error


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def clean_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("\xa0", " ").strip()


def plausible_name(value):
    value = clean_text(value)
    if not value or len(value) < 2 or len(value) > 90:
        return False
    low = normalize(value)
    banned = (
        "calciatore", "quotazione", "fantacalcio", "campioncino",
        "logo", "squadra", "classic", "mantra", "fvm", "cerca",
    )
    if any(item in low for item in banned):
        return False
    if re.fullmatch(r"[A-Z]{2,4}", value):
        return False
    return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", value))


class QuotesParser(HTMLParser):
    """Estrae le righe della tabella quotazioni senza dipendere da classi CSS specifiche."""

    PROFILE_RE = re.compile(
        r"/serie-a/squadre/([^/]+)/([^/]+)/([0-9]+)(?:[/?#]|$)",
        re.IGNORECASE,
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.row_depth = 0
        self.row_text = []
        self.row_attrs = []
        self.row_links = []
        self.active_link = None
        self.active_link_text = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)

        if tag == "tr":
            self.in_row = True
            self.row_depth = 1
            self.row_text = []
            self.row_attrs = []
            self.row_links = []
            self.active_link = None
            self.active_link_text = []
            self.row_attrs.extend(attrs)
            return

        if not self.in_row:
            return

        if tag == "tr":
            self.row_depth += 1

        self.row_attrs.extend(attrs)

        if tag == "a":
            href = attrs_dict.get("href") or ""
            if self.PROFILE_RE.search(href):
                self.active_link = {
                    "href": href,
                    "attrs": attrs,
                    "text": [],
                }
                self.row_links.append(self.active_link)

    def handle_data(self, data):
        if not self.in_row:
            return
        value = clean_text(data)
        if not value:
            return
        self.row_text.append(value)
        if self.active_link is not None:
            self.active_link["text"].append(value)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.in_row:
            return

        if tag == "a":
            self.active_link = None
            return

        if tag == "tr":
            self.row_depth -= 1
            if self.row_depth <= 0:
                if self.row_links:
                    self.rows.append({
                        "text": self.row_text[:],
                        "attrs": self.row_attrs[:],
                        "links": self.row_links[:],
                    })
                self.in_row = False



class StatsTableParser(HTMLParser):
    """Estrae intestazioni/celle della tabella statistiche e l'ID Fantacalcio del calciatore."""

    PROFILE_RE = QuotesParser.PROFILE_RE

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cell_tag = None
        self.cell_text = []
        self.cells = []
        self.row_links = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.cells = []
            self.row_links = []
            return
        if not self.in_row:
            return
        if tag in {"th", "td"}:
            self.in_cell = True
            self.cell_tag = tag
            self.cell_text = []
        if tag == "a":
            href = attrs_dict.get("href") or ""
            if self.PROFILE_RE.search(href):
                self.row_links.append({"href": href, "attrs": attrs})

    def handle_data(self, data):
        if self.in_row and self.in_cell:
            value = clean_text(data)
            if value:
                self.cell_text.append(value)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.in_row:
            return
        if tag in {"th", "td"} and self.in_cell:
            self.cells.append({
                "tag": self.cell_tag,
                "text": clean_text(" ".join(self.cell_text)),
            })
            self.in_cell = False
            self.cell_tag = None
            self.cell_text = []
            return
        if tag == "tr":
            if self.cells:
                self.rows.append({"cells": self.cells[:], "links": self.row_links[:]})
            self.in_row = False


def parse_decimal(value):
    value = clean_text(value).replace(",", ".")
    if not value or value in {"-", "—"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def scrape_fantamedia():
    page = fetch(STATS_URL)
    parser = StatsTableParser()
    parser.feed(page)

    fm_index = None
    for row in parser.rows:
        headers = [normalize(cell["text"]) for cell in row["cells"] if cell["tag"] == "th"]
        if not headers:
            continue
        for index, header in enumerate(headers):
            if header in {"fm", "fantamedia", "media fantavoto"}:
                fm_index = index
                break
        if fm_index is not None:
            break

    if fm_index is None:
        raise RuntimeError("colonna FM/Fantamedia non trovata nella pagina statistiche")

    by_id = {}
    recognized_rows = 0
    for row in parser.rows:
        if not row["links"]:
            continue
        cells = [cell["text"] for cell in row["cells"] if cell["tag"] == "td"]
        if fm_index >= len(cells):
            continue
        href = row["links"][0].get("href") or ""
        match = StatsTableParser.PROFILE_RE.search(href)
        if not match:
            continue
        player_id = match.group(3)
        recognized_rows += 1
        by_id[player_id] = parse_decimal(cells[fm_index])

    if recognized_rows < 250:
        raise RuntimeError(
            f"soltanto {recognized_rows} righe statistiche riconosciute: probabile cambio HTML"
        )

    return by_id, recognized_rows

def role_from_row(row):
    values = []
    values.extend(row["text"])
    for key, value in row["attrs"]:
        if value:
            values.append(value)
        if key:
            values.append(key)

    # Prima: parole intere chiare come "Attaccante" o "role-a".
    joined = " ".join(values)
    normalized = normalize(joined)
    for role, words in ROLE_WORDS.items():
        for word in words[2:]:
            if re.search(rf"\b{re.escape(word)}\b", normalized):
                return role

    # Poi: token CSS/attributi comuni (role-a, ruolo_a, player-role-p...).
    tokens = re.split(r"[^a-zA-Z]+", joined.casefold())
    for index, token in enumerate(tokens):
        if token not in {"p", "d", "c", "a", "por", "dif", "cen", "att"}:
            continue
        nearby = " ".join(tokens[max(0, index - 2): index + 3])
        if not any(marker in nearby for marker in ("role", "ruolo", "position", "posizione")):
            continue
        if token in {"p", "por"}:
            return "P"
        if token in {"d", "dif"}:
            return "D"
        if token in {"c", "cen"}:
            return "C"
        if token in {"a", "att"}:
            return "A"

    return None


def best_name_from_row(row, link):
    candidates = []

    # Il testo del link è il candidato base.
    link_text = clean_text(" ".join(link.get("text") or []))
    if plausible_name(link_text):
        candidates.append(link_text)

    # Fantacalcio spesso mette il nome esteso in title/aria-label/alt.
    for key, value in link.get("attrs") or []:
        if key.lower() in {"title", "aria-label", "data-name", "data-player-name"}:
            if plausible_name(value):
                candidates.append(clean_text(value))

    for key, value in row["attrs"]:
        if key.lower() in {"title", "aria-label", "alt", "data-name", "data-player-name"}:
            if plausible_name(value):
                candidates.append(clean_text(value))

    if not candidates:
        return None

    # Preferiamo il candidato più informativo, evitando etichette generiche.
    unique = []
    seen = set()
    for candidate in candidates:
        key = normalize(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    return max(unique, key=lambda value: (len(value.split()), len(value)))


def load_previous_players():
    if not OUTPUT.exists():
        return []
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        players = payload.get("players") or []
        return players if isinstance(players, list) else []
    except Exception:
        return []


def previous_role_index(players):
    by_name = {}
    for player in players:
        name = normalize(player.get("name"))
        role = player.get("role")
        if name and role in {"P", "D", "C", "A"}:
            by_name.setdefault(name, set()).add(role)
    return by_name


def resolve_previous_role(name, index):
    roles = index.get(normalize(name), set())
    if len(roles) == 1:
        return next(iter(roles))
    return None


def scrape_fantacalcio():
    page = fetch(SOURCE_URL)
    parser = QuotesParser()
    parser.feed(page)

    previous = load_previous_players()
    old_roles = previous_role_index(previous)

    players = []
    unresolved_roles = []
    seen_ids = set()

    for row in parser.rows:
        for link in row["links"]:
            href = link.get("href") or ""
            match = QuotesParser.PROFILE_RE.search(href)
            if not match:
                continue

            team_slug, _player_slug, player_id = match.groups()
            team = TEAM_BY_SLUG.get(normalize(team_slug))
            if not team:
                continue

            if player_id in seen_ids:
                continue

            name = best_name_from_row(row, link)
            if not name:
                continue

            role = role_from_row(row)
            if role is None:
                role = resolve_previous_role(name, old_roles)

            if role is None:
                unresolved_roles.append((name, team, player_id))
                continue

            seen_ids.add(player_id)
            players.append({
                "name": name,
                "team": team,
                "role": role,
                # ID stabile Fantacalcio: non cambia quando il giocatore cambia club.
                "officialId": f"fantacalcio:{player_id}",
                "fantacalcioId": int(player_id),
                "profileUrl": urljoin(SOURCE_URL, href),
            })

    # Fantamedia ufficiale: stessa identità stabile Fantacalcio usata per le rose.
    try:
        fantamedia_by_id, stats_rows = scrape_fantamedia()
    except Exception as exc:
        raise RuntimeError(f"Fantamedia non aggiornata: {exc}") from exc

    for player in players:
        player_id = str(player.get("fantacalcioId") or "")
        player["fantamedia"] = fantamedia_by_id.get(player_id)

    if unresolved_roles:
        print("ATTENZIONE: giocatori senza ruolo riconoscibile:", file=sys.stderr)
        for name, team, player_id in unresolved_roles[:30]:
            print(f"- {name} | {team} | id {player_id}", file=sys.stderr)
        if len(unresolved_roles) > 30:
            print(f"- ... altri {len(unresolved_roles) - 30}", file=sys.stderr)

    return players, unresolved_roles


def safety_checks(players, unresolved_roles):
    teams = {player["team"] for player in players}

    if len(players) < 300:
        raise RuntimeError(f"soltanto {len(players)} giocatori riconosciuti")
    if len(players) > 750:
        raise RuntimeError(f"numero sospetto di giocatori: {len(players)}")
    if len(teams) != 20:
        raise RuntimeError(f"trovate {len(teams)}/20 squadre")

    # Se il parser perde troppi ruoli, non sovrascriviamo il JSON sano.
    if len(unresolved_roles) > 15:
        raise RuntimeError(
            f"troppi giocatori senza ruolo ({len(unresolved_roles)}): "
            "probabile cambio HTML di Fantacalcio.it"
        )

    roles = {"P": 0, "D": 0, "C": 0, "A": 0}
    for player in players:
        roles[player["role"]] += 1

    for role, count in roles.items():
        if count < 20:
            raise RuntimeError(f"ruolo {role}: soltanto {count} giocatori")

    return teams, roles


def main():
    print("Fonte rose/quotazioni: Fantacalcio.it — Quotazioni ufficiali 2026/27")
    print(SOURCE_URL)
    print("Fonte Fantamedia: Fantacalcio.it — Statistiche ufficiali 2026/27")
    print(STATS_URL)
    print()

    try:
        players, unresolved_roles = scrape_fantacalcio()
        teams, roles = safety_checks(players, unresolved_roles)
    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        print("Il file JSON esistente NON è stato modificato.", file=sys.stderr)
        sys.exit(1)

    players.sort(key=lambda p: (p["team"], p["role"], normalize(p["name"])))

    payload = {
        "source": "Fantacalcio.it - Quotazioni + Statistiche ufficiali Serie A 2026/27",
        "source_url": SOURCE_URL,
        "stats_source_url": STATS_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "teams": len(teams),
        "players_count": len(players),
        "players": players,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"OK: {len(players)} giocatori | {len(teams)} squadre | "
        f"P {roles['P']} D {roles['D']} C {roles['C']} A {roles['A']} | "
        f"Fantamedia disponibili {sum(p.get('fantamedia') is not None for p in players)}"
    )
    if unresolved_roles:
        print(f"Saltati per ruolo non riconosciuto: {len(unresolved_roles)}")
    print(f"Salvato: {OUTPUT}")


if __name__ == "__main__":
    main()
