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
        char for char in text
        if not unicodedata.combining(char)
    )

    return re.sub(r"\s+", " ", text).strip().casefold()


def plausible_player_name(name):
    if not name:
        return False

    name = re.sub(r"\s+", " ", name).strip()

    if len(name) < 4 or len(name) > 80:
        return False

    low = normalize(name)

    banned_exact = {
        "nationality logo",
        "atalanta",
        "bologna",
        "cagliari",
        "como",
        "fiorentina",
        "frosinone",
        "genoa",
        "inter",
        "juventus",
        "lazio",
        "lecce",
        "milan",
        "monza",
        "napoli",
        "parma",
        "roma",
        "sassuolo",
        "torino",
        "udinese",
        "venezia",
    }

    if low in banned_exact:
        return False

    banned_words = (
        "logo",
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
    )

    if any(word in low for word in banned_words):
        return False

    # Sigle squadra/nazione tipo ATA, ITA, BRA ecc.
    if re.fullmatch(r"[A-Z]{2,4}", name):
        return False

    # Numeri di maglia o altri valori numerici.
    if re.fullmatch(r"\d+", name):
        return False

    # Un nome di calciatore deve contenere almeno una lettera.
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


    def handle_starttag(self, tag, attrs):

        tag = tag.lower()
        attrs = dict(attrs)

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = tag
            self.heading_text = []
            return

        if tag != "img":
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

        # Le foto dei giocatori ufficiali sono servite da
        # images.legaseriea.it.
        if "images.legaseriea.it" not in src:
            return

        # Le bandiere/nazionalità usano lo stesso dominio,
        # ma hanno alt "Nationality Logo".
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

        if self.heading_level and tag == self.heading_level:

            heading = normalize(
                " ".join(self.heading_text)
            )

            if heading in ROLE_MAP:
                self.current_role = ROLE_MAP[heading]

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

        player["officialId"] = (
            normalize(player["name"])
            .replace(" ", "-")
        )

        output.append(player)

    return output


def scrape_team(team, slug):

    url = f"https://www.legaseriea.it/team/{slug}/squad"

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

    # CONTROLLI DI SICUREZZA PER SINGOLA SQUADRA

    if len(players) < 15:
        raise RuntimeError(
            f"{team}: trovati soltanto {len(players)} giocatori"
        )

    if len(players) > 45:
        raise RuntimeError(
            f"{team}: trovati troppi giocatori ({len(players)})"
        )

    if roles["P"] < 1:
        raise RuntimeError(
            f"{team}: nessun portiere trovato"
        )

    if roles["D"] < 3:
        raise RuntimeError(
            f"{team}: numero difensori sospetto"
        )

    if roles["C"] < 3:
        raise RuntimeError(
            f"{team}: numero centrocampisti sospetto"
        )

    if roles["A"] < 1:
        raise RuntimeError(
            f"{team}: nessun attaccante trovato"
        )

    return players


def main():

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


    # CONTROLLO DI SICUREZZA GENERALE

    if errors:
        print(
            "\nERRORI:",
            file=sys.stderr
        )

        for error in errors:
            print(
                f"- {error}",
                file=sys.stderr
            )

        sys.exit(1)


    if len(teams_found) != 20:
        print(
            f"ERRORE: trovate {len(teams_found)}/20 squadre",
            file=sys.stderr
        )
        sys.exit(1)


    if len(all_players) < 300:
        print(
            f"ERRORE: soltanto {len(all_players)} giocatori",
            file=sys.stderr
        )
        sys.exit(1)


    if len(all_players) > 700:
        print(
            f"ERRORE: {len(all_players)} giocatori è un numero sospetto",
            file=sys.stderr
        )
        sys.exit(1)


    payload = {
        "source": (
            "Lega Serie A - pagine ufficiali "
            "delle rose dei club"
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
