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


def best_name_from_row(row, links, player_slug):
    """Sceglie il nome fantacalcistico della riga, evitando link/etichette di posizione."""

    candidates = []

    # In una stessa riga Fantacalcio può avere più link allo stesso profilo
    # (es. ruolo/posizione + nome). Li valutiamo tutti prima di scegliere.
    for link in links:
        link_text = clean_text(" ".join(link.get("text") or []))
        if plausible_name(link_text):
            candidates.append(link_text)

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

    # Rimuoviamo duplicati e descrizioni tattiche che talvolta sono anch'esse
    # cliccabili verso il profilo del calciatore.
    position_terms = {
        "portiere", "estremo difensore",
        "difensore", "difensore centrale", "centrale difensivo",
        "terzino destro", "terzino sinistro", "terzino",
        "braccetto destro", "braccetto sinistro",
        "centrocampista", "centrocampista centrale", "mediano",
        "mezzala", "regista", "trequartista",
        "esterno destro", "esterno sinistro", "esterno di centrocampo",
        "attaccante", "punta centrale", "seconda punta",
        "ala destra", "ala sinistra", "esterno offensivo",
    }

    unique = []
    seen = set()
    for candidate in candidates:
        key = normalize(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in position_terms:
            continue
        unique.append(candidate)

    if not unique:
        return None

    slug_norm = normalize(player_slug.replace("-", " "))
    slug_tokens = set(slug_norm.split())

    def score(candidate):
        cand_norm = normalize(candidate)
        # Tolgo la sola punteggiatura, così "Martinez L." combacia con
        # lo slug "martinez-l".
        cand_compact = normalize(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]+", " ", candidate))
        cand_tokens = set(cand_compact.split())

        exact_slug = 1 if cand_compact == slug_norm else 0
        overlap = len(cand_tokens & slug_tokens)
        extra = len(cand_tokens - slug_tokens)

        # Prima il candidato che rappresenta davvero lo slug del giocatore;
        # a parità preferiamo il nome mostrato in tabella, non descrizioni lunghe.
        return (exact_slug, overlap, -extra, -len(candidate.split()), -len(candidate))

    best = max(unique, key=score)

    # Se nessun candidato ha alcun legame con lo slug, meglio non inventare.
    best_tokens = set(
        normalize(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]+", " ", best)).split()
    )
    if slug_tokens and not (best_tokens & slug_tokens):
        return None

    return best


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

            same_player_links = []
            for candidate_link in row["links"]:
                candidate_match = QuotesParser.PROFILE_RE.search(
                    candidate_link.get("href") or ""
                )
                if candidate_match and candidate_match.group(3) == player_id:
                    same_player_links.append(candidate_link)

            name = best_name_from_row(
                row,
                same_player_links,
                _player_slug,
            )
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
    print("Fonte: Fantacalcio.it — Quotazioni ufficiali 2026/27")
    print(SOURCE_URL)
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
        "source": "Fantacalcio.it - Quotazioni ufficiali Serie A 2026/27",
        "source_url": SOURCE_URL,
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
        f"P {roles['P']} D {roles['D']} C {roles['C']} A {roles['A']}"
    )
    if unresolved_roles:
        print(f"Saltati per ruolo non riconosciuto: {len(unresolved_roles)}")
    print(f"Salvato: {OUTPUT}")


if __name__ == "__main__":
    main()
