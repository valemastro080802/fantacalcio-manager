#!/usr/bin/env python3

import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from html.parser import HTMLParser


TEAMS = {
    "Atalanta": "atalanta",
    "Bologna": "bologna",
    "Cagliari": "cagliari",
    "Como": "como",
    "Fiorentina": "fiorentina",
    "Frosinone": "frosinone",
    "Genoa": "genoa",
    "Inter": "inter",
    "Juventus": "juventus",
    "Lazio": "lazio",
    "Lecce": "lecce",
    "Milan": "milan",
    "Monza": "monza",
    "Napoli": "napoli",
    "Parma": "parma",
    "Roma": "roma",
    "Sassuolo": "sassuolo",
    "Torino": "torino",
    "Udinese": "udinese",
    "Venezia": "venezia",
}


ROLE_MAP = {
    "portiere": "P",
    "portieri": "P",
    "difensore": "D",
    "difensori": "D",
    "centrocampista": "C",
    "centrocampisti": "C",
    "attaccante": "A",
    "attaccanti": "A",
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
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
            time.sleep(2 * (attempt + 1))

    raise last_error


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )

    return re.sub(r"\s+", " ", text).strip().casefold()


def plausible_player_name(name):
    if not name:
        return False

    name = re.sub(r"\s+", " ", name).strip()

    if len(name) < 4 or len(name) > 90:
        return False

    low = normalize(name)

    # Elementi della pagina che non sono giocatori.
    banned = (
        "nationality logo",
        "serie a",
        "coppa italia",
        "supercoppa",
        "primavera",
        "enilive",
        "sponsor",
        "fantacalcio",
        "website",
        "tickets",
        "shop",
        "youtube",
        "facebook",
        "instagram",
        "twitter",
        "tiktok",
    )

    if any(word in low for word in banned):
        return False

    # Sigle tipo ATA, ITA, BRA ecc.
    if re.fullmatch(r"[A-Z]{2,4}", name):
        return False

    # Numero di maglia.
    if re.fullmatch(r"\d+", name):
        return False

    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", name):
        return False

    return True


class SquadParser(HTMLParser):

    def __init__(self, team):
        super().__init__()

        self.team = team
        self.players = []

        self.current_role = None
        self.heading_level = None
        self.heading_text = []

        self.roster_started = False
        self.roster_finished = False


    def handle_starttag(self, tag, attrs):

        tag = tag.lower()
        attrs = dict(attrs)

        # Una volta raggiunto il footer la rosa è terminata.
        if tag == "footer":
            self.current_role = None
            self.roster_finished = True
            return

        if self.roster_finished:
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = tag
            self.heading_text = []
            return

        if tag != "img":
            return

        # Prima di "Portiere" non leggiamo alcuna immagine.
        if not self.roster_started:
            return

        if not self.current_role:
            return

        alt = (attrs.get("alt") or "").strip()

        src = (
            attrs.get("src")
            or attrs.get("data-src")
            or attrs.get("data-lazy-src")
            or ""
        ).strip()

        # Le foto dei giocatori sono sul dominio immagini
        # ufficiale della Lega.
        if "images.legaseriea.it" not in src:
            return

        # Le bandiere nazionali usano lo stesso dominio:
        # vanno quindi escluse espressamente.
        if normalize(alt) == "nationality logo":
            return

        if not plausible_player_name(alt):
            return

        self.players.append({
            "name": alt,
            "team": self.team,
            "role": self.current_role,
        })


    def handle_data(self, data):

        if self.heading_level:
            self.heading_text.append(data)


    def handle_endtag(self, tag):

        tag = tag.lower()

        if not self.heading_level:
            return

        if tag != self.heading_level:
            return

        heading = normalize(
            " ".join(self.heading_text)
        )

        # I quattro titoli ufficiali aprono le sezioni
        # della rosa.
        if heading in ROLE_MAP:
            self.current_role = ROLE_MAP[heading]
            self.roster_started = True

        # Se, dopo l'inizio della rosa, incontriamo un altro
        # titolo non appartenente ai quattro ruoli,
        # smettiamo di considerare immagini come giocatori.
        elif self.roster_started:
            self.current_role = None
            self.roster_finished = True

        self.heading_level = None
        self.heading_text = []


def dedupe(players):

    output = []
    seen = set()

    for player in players:

        key = (
            normalize(player["team"]),
            normalize(player["name"]),
        )

        if key in seen:
            continue

        seen.add(key)

        # Identificatore utile al database.
        # Se un giocatore cambia squadra, il sito ha comunque
        # il confronto per nome per conservare i dati personali.
        player["officialId"] = (
            f"{normalize(player['team'])}:"
            f"{normalize(player['name'])}"
        )

        output.append(player)

    return output


def scrape_team(team, slug):

    url = (
        f"https://www.legaseriea.it/"
        f"team/{slug}/squad"
    )

    page = fetch(url)

    parser = SquadParser(team)
    parser.feed(page)

    players = dedupe(parser.players)

    roles = {
        "P": 0,
        "D": 0,
        "C": 0,
        "A": 0,
    }

    for player in players:
        roles[player["role"]] += 1

    print(
        f"{team}: {len(players)} giocatori "
        f"(P {roles['P']} | "
        f"D {roles['D']} | "
        f"C {roles['C']} | "
        f"A {roles['A']})"
    )

    # -----------------------------
    # CONTROLLI DI SICUREZZA
    # -----------------------------

    if len(players) < 15:
        raise RuntimeError(
            f"{team}: soltanto "
            f"{len(players)} giocatori"
        )

    if len(players) > 40:
        raise RuntimeError(
            f"{team}: numero sospetto "
            f"({len(players)} giocatori)"
        )

    if not 1 <= roles["P"] <= 7:
        raise RuntimeError(
            f"{team}: portieri sospetti "
            f"({roles['P']})"
        )

    if not 3 <= roles["D"] <= 18:
        raise RuntimeError(
            f"{team}: difensori sospetti "
            f"({roles['D']})"
        )

    if not 3 <= roles["C"] <= 18:
        raise RuntimeError(
            f"{team}: centrocampisti sospetti "
            f"({roles['C']})"
        )

    if not 1 <= roles["A"] <= 15:
        raise RuntimeError(
            f"{team}: attaccanti sospetti "
            f"({roles['A']})"
        )

    return players

def test_market_page():
    url = "https://www.legaseriea.it/serie-a"
    page = fetch(url)    
    pos = normalize(page).find("pinamonti")
    if pos != -1:
        print(page[max(0, pos - 1000):pos + 2000])

    print()
    print("TEST CALCIOMERCATO LEGA SERIE A")
    print("--------------------------------")

    checks = [
        "PINAMONTI",
        "LAZIO",
        "SASSUOLO",
        "Calciomercato",
    ]

    for text in checks:
        found = normalize(text) in normalize(page)
        print(f"{text}: {'TROVATO' if found else 'NON TROVATO'}")
def main():
    test_market_page()
    all_players = []
    errors = []

    for team, slug in TEAMS.items():

        try:
            players = scrape_team(team, slug)
            all_players.extend(players)

        except Exception as exc:
            errors.append(str(exc))


    teams_found = {
        player["team"]
        for player in all_players
    }

    print()
    print(
        f"Totale: {len(all_players)} giocatori "
        f"in {len(teams_found)} squadre"
    )

    # Se anche UNA SOLA squadra dà un risultato anomalo,
    # il JSON NON viene aggiornato.
    if errors:

        print(
            "\nCONTROLLO DI SICUREZZA FALLITO:",
            file=sys.stderr,
        )

        for error in errors:
            print(
                f"- {error}",
                file=sys.stderr,
            )

        sys.exit(1)


    if len(teams_found) != 20:
        print(
            f"ERRORE: trovate "
            f"{len(teams_found)}/20 squadre",
            file=sys.stderr,
        )
        sys.exit(1)


    # Range generale abbastanza ampio da consentire
    # normali variazioni delle rose, ma impedire risultati
    # evidentemente errati come i precedenti 745/845.
    if len(all_players) < 300:
        print(
            f"ERRORE: soltanto "
            f"{len(all_players)} giocatori",
            file=sys.stderr,
        )
        sys.exit(1)


    if len(all_players) > 700:
        print(
            f"ERRORE: {len(all_players)} giocatori "
            f"è un numero sospetto",
            file=sys.stderr,
        )
        sys.exit(1)


    payload = {
        "source": (
            "Lega Serie A - "
            "pagine ufficiali delle rose dei club"
        ),
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "teams": 20,
        "players_count": len(all_players),
        "players": sorted(
            all_players,
            key=lambda player: (
                player["team"],
                player["role"],
                player["name"],
            )
        ),
    }


    output = Path(
        "data/seriea_rosters.json"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


    print()
    print(
        f"OK: salvati {len(all_players)} "
        f"giocatori in {output}"
    )


if __name__ == "__main__":
    main()
