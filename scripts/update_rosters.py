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

def apply_market_corrections(players):
    """
    Usa la pagina ufficiale Calciomercato della Lega Serie A
    come correttivo delle rose dei club.

    Per sicurezza corregge soltanto trasferimenti tra squadre
    di Serie A quando il giocatore è già presente nelle rose
    ufficiali ma risulta ancora assegnato alla vecchia squadra.
    """

    url = (
        "https://www.legaseriea.it/serie-a/news/"
        "calciomercato-gli-aggiornamenti-in-serie-a-enilive"
    )

    try:
        page = fetch(url)
    except Exception as exc:
        print()
        print(
            "ATTENZIONE: impossibile leggere il Calciomercato "
            f"({exc}). Uso soltanto le rose ufficiali."
        )
        return players

    team_by_normalized = {
        normalize(team): team
        for team in TEAMS
    }

    # Titoli come:
    # <p><strong>Lazio - Pinamonti è ufficiale!</strong></p>
    # <p><strong>Lecce - Ilić è giallorosso!</strong></p>
    heading_pattern = re.compile(
        r"<p>\s*<strong>\s*"
        r"([^<]+?)\s*[-–—]\s*([^<]+?)"
        r"\s*</strong>\s*</p>",
        flags=re.IGNORECASE,
    )

    headings = list(heading_pattern.finditer(page))

    if not headings:
        print()
        print(
            "ATTENZIONE: nessun blocco Calciomercato "
            "riconosciuto. Nessuna correzione applicata."
        )
        return players

    corrections = []
    seen_corrections = set()

    for index, heading in enumerate(headings):
        destination_raw = heading.group(1).strip()
        headline = heading.group(2).strip()

        destination = team_by_normalized.get(
            normalize(destination_raw)
        )

        # Ci interessano soltanto club dell'attuale Serie A.
        if not destination:
            continue

        block_start = heading.end()

        if index + 1 < len(headings):
            block_end = headings[index + 1].start()
        else:
            block_end = min(
                len(page),
                block_start + 10000,
            )

        block = page[block_start:block_end]

        # Rimuoviamo i tag HTML per facilitare il confronto
        # con i nomi dei giocatori.
        block_text = re.sub(
            r"<[^>]+>",
            " ",
            block,
        )

        block_text = (
            block_text
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&#39;", "'")
            .replace("&quot;", '"')
        )

        normalized_headline = normalize(headline)
        normalized_block = normalize(block_text)

        candidates = []

        for player in players:
            # Se è già nella squadra corretta non dobbiamo fare nulla.
            if normalize(player["team"]) == normalize(destination):
                continue

            player_name = normalize(player["name"])

            if not player_name:
                continue

            name_parts = player_name.split()

            if not name_parts:
                continue

            surname = name_parts[-1]

            # Regola di sicurezza:
            # 1. il cognome deve comparire nel titolo dell'operazione;
            # 2. il nome completo deve comparire nel testo del blocco.
            surname_in_headline = re.search(
                rf"\b{re.escape(surname)}\b",
                normalized_headline,
            )

            full_name_in_block = re.search(
                rf"\b{re.escape(player_name)}\b",
                normalized_block,
            )

            if surname_in_headline and full_name_in_block:
                candidates.append(player)

        # Applichiamo la correzione soltanto se abbiamo
        # identificato un singolo giocatore senza ambiguità.
        if len(candidates) != 1:
            continue

        player = candidates[0]

        correction_key = (
            normalize(player["name"]),
            normalize(destination),
        )

        # La pagina può contenere lo stesso articolo più volte
        # nel codice HTML: evitiamo correzioni duplicate.
        if correction_key in seen_corrections:
            continue

        seen_corrections.add(correction_key)

        old_team = player["team"]
        player["team"] = destination

        corrections.append(
            (
                player["name"],
                old_team,
                destination,
            )
        )

    # Ricalcola officialId ed elimina eventuali duplicati nel caso
    # in cui la nuova squadra abbia già aggiornato la propria rosa
    # mentre la vecchia squadra non lo abbia ancora fatto.
    players = dedupe(players)

    print()
    print("CORREZIONI CALCIOMERCATO LEGA SERIE A")
    print("-------------------------------------")

    if not corrections:
        print("Nessuna correzione necessaria.")
    else:
        for name, old_team, new_team in corrections:
            print(
                f"{name}: {old_team} -> {new_team}"
            )

        print(
            f"Totale correzioni: {len(corrections)}"
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

    print()
    print(
        f"Totale iniziale: {len(all_players)} giocatori"
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

    teams_found = {
        player["team"]
        for player in all_players
    }

    if len(teams_found) != 20:
        print(
            f"ERRORE: trovate "
            f"{len(teams_found)}/20 squadre",
            file=sys.stderr,
        )
        sys.exit(1)

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

    # Dopo aver verificato che le rose ufficiali siano sane,
    # usiamo il Calciomercato Lega Serie A per correggere
    # eventuali trasferimenti non ancora recepiti dalle rose.
    all_players = apply_market_corrections(
        all_players
    )

    # Nuovo controllo di sicurezza dopo le correzioni.
    teams_found = {
        player["team"]
        for player in all_players
    }

    if len(teams_found) != 20:
        print(
            "ERRORE DOPO CALCIOMERCATO: "
            f"trovate {len(teams_found)}/20 squadre",
            file=sys.stderr,
        )
        sys.exit(1)

    if not 300 <= len(all_players) <= 700:
        print(
            "ERRORE DOPO CALCIOMERCATO: "
            f"{len(all_players)} giocatori",
            file=sys.stderr,
        )
        sys.exit(1)

    print()
    print(
        f"Totale finale: {len(all_players)} giocatori "
        f"in {len(teams_found)} squadre"
    )

    payload = {
        "source": (
            "Lega Serie A - rose ufficiali dei club "
            "+ aggiornamenti ufficiali Calciomercato"
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
