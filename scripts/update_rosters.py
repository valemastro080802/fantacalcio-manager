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
    Usa gli aggiornamenti ufficiali di Calciomercato della Lega Serie A
    per correggere le rose quando le pagine squadra non sono ancora
    aggiornate.

    Gestisce:
    - giocatore già presente -> cambio squadra;
    - giocatore assente dalle rose -> inserimento, ma soltanto quando
      nome completo e ruolo possono essere ricavati con sufficiente
      sicurezza dal comunicato ufficiale.
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
            "ATTENZIONE: nessun aggiornamento Calciomercato "
            "riconosciuto."
        )
        return players

    corrections = []
    additions = []
    seen_operations = set()

    def clean_html(text):
        text = re.sub(r"<[^>]+>", " ", text)

        replacements = {
            "&nbsp;": " ",
            "&amp;": "&",
            "&#39;": "'",
            "&quot;": '"',
            "&rsquo;": "’",
            "&agrave;": "à",
            "&egrave;": "è",
            "&igrave;": "ì",
            "&ograve;": "ò",
            "&ugrave;": "ù",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return re.sub(r"\s+", " ", text).strip()

    def infer_role(text):
        """
        Restituisce P/D/C/A soltanto quando il testo contiene
        indicazioni sufficientemente chiare sul ruolo.
        """

        value = normalize(text)

        role_words = {
            "P": [
                "portiere",
                "goalkeeper",
            ],
            "D": [
                "difensore",
                "terzino",
                "centrale difensivo",
                "difensivo",
            ],
            "C": [
                "centrocampista",
                "centrocampo",
                "mediano",
                "mezzala",
                "regista",
            ],
            "A": [
                "attaccante",
                "centravanti",
                "punta",
                "esterno offensivo",
                "reparto offensivo",
            ],
        }

        matches = []

        for role, words in role_words.items():
            if any(word in value for word in words):
                matches.append(role)

        if len(set(matches)) == 1:
            return matches[0]

        return None

    def extract_full_name(block, surname):
        """
        Prova a ricavare il nome completo dal testo del comunicato.

        Prima cerca testi di link, molto affidabili per casi come
        <a ...>Andrea Pinamonti</a>.
        """

        surname_norm = normalize(surname)

        anchor_pattern = re.compile(
            r"<a\b[^>]*>(.*?)</a>",
            flags=re.IGNORECASE | re.DOTALL,
        )

        possible_names = []

        for match in anchor_pattern.finditer(block):
            anchor_text = clean_html(match.group(1))

            words = anchor_text.split()

            if not 2 <= len(words) <= 5:
                continue

            if surname_norm not in normalize(anchor_text).split():
                continue

            if all(
                re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", word)
                for word in words
            ):
                possible_names.append(anchor_text)

        unique_names = []

        seen_names = set()

        for name in possible_names:
            key = normalize(name)

            if key not in seen_names:
                seen_names.add(key)
                unique_names.append(name)

        if len(unique_names) == 1:
            return unique_names[0]

        # Fallback: cerca nel testo sequenze di 2-4 parole
        # che terminano con il cognome del titolo.
        plain_text = clean_html(block)

        name_pattern = re.compile(
            rf"\b("
            rf"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]+"
            rf"(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]+){{0,2}}"
            rf"\s+{re.escape(surname)}"
            rf")\b",
            flags=re.IGNORECASE,
        )

        possible_names = []

        for match in name_pattern.finditer(plain_text):
            candidate = match.group(1).strip()

            if normalize(candidate).endswith(surname_norm):
                possible_names.append(candidate)

        unique_names = []

        seen_names = set()

        for name in possible_names:
            key = normalize(name)

            if key not in seen_names:
                seen_names.add(key)
                unique_names.append(name)

        if len(unique_names) == 1:
            return unique_names[0]

        return None

    for index, heading in enumerate(headings):
        destination_raw = heading.group(1).strip()
        headline = clean_html(heading.group(2))

        destination = team_by_normalized.get(
            normalize(destination_raw)
        )

        if not destination:
            continue

        block_start = heading.end()

        if index + 1 < len(headings):
            block_end = headings[index + 1].start()
        else:
            block_end = min(
                len(page),
                block_start + 12000,
            )

        block = page[block_start:block_end]

        normalized_headline = normalize(headline)

        #
        # 1. CERCHIAMO PRIMA UN GIOCATORE GIÀ PRESENTE
        #
        existing_candidates = []

        for player in players:
            if normalize(player["team"]) == normalize(destination):
                continue

            normalized_name = normalize(player["name"])

            if not normalized_name:
                continue

            name_parts = normalized_name.split()

            if not name_parts:
                continue

            surname = name_parts[-1]

            if re.search(
                rf"\b{re.escape(surname)}\b",
                normalized_headline,
            ):
                existing_candidates.append(player)

        unique_existing = []
        existing_names = set()

        for player in existing_candidates:
            key = normalize(player["name"])

            if key not in existing_names:
                existing_names.add(key)
                unique_existing.append(player)

        if len(unique_existing) == 1:
            player = unique_existing[0]

            operation_key = (
                normalize(player["name"]),
                normalize(destination),
            )

            if operation_key in seen_operations:
                continue

            seen_operations.add(operation_key)

            old_team = player["team"]

            player["team"] = destination
            player["officialId"] = (
                f"{normalize(destination)}:"
                f"{normalize(player['name'])}"
            )

            corrections.append(
                (
                    player["name"],
                    old_team,
                    destination,
                )
            )

            continue

        #
        # 2. SE NON ESISTE NELLE ROSE, PROVIAMO AD AGGIUNGERLO
        #
        headline_words = normalized_headline.split()

        matched_surname = None

        # Cerchiamo quale parola del titolo potrebbe essere
        # il cognome del giocatore.
        for raw_word in headline.split():
            word = re.sub(
                r"[^A-Za-zÀ-ÖØ-öø-ÿ'’-]",
                "",
                raw_word,
            ).strip()

            if len(word) < 3:
                continue

            word_norm = normalize(word)

            if word_norm in {
                "ufficiale",
                "giallorosso",
                "neroverde",
                "biancoceleste",
                "rossoblu",
                "rossoblu",
                "qualita",
                "rinforzo",
                "nuovo",
                "arriva",
                "colpo",
            }:
                continue

            # Il cognome deve comparire anche nel testo
            # immediatamente successivo al titolo.
            if re.search(
                rf"\b{re.escape(word_norm)}\b",
                normalize(clean_html(block)),
            ):
                matched_surname = word
                break

        if not matched_surname:
            continue

        full_name = extract_full_name(
            block,
            matched_surname,
        )

        if not full_name:
            continue

        # Non inseriamo qualcuno che in realtà esiste già
        # con il nome completo.
        already_exists = any(
            normalize(player["name"]) == normalize(full_name)
            for player in players
        )

        if already_exists:
            continue

        role = infer_role(
            clean_html(block[:5000])
        )

        # Per i nuovi inserimenti il ruolo è obbligatorio:
        # se non siamo sicuri, non aggiungiamo nulla.
        if not role:
            print(
                "SKIP NUOVO GIOCATORE: "
                f"{full_name} -> {destination} "
                "(ruolo non determinabile con sicurezza)"
            )
            continue

        operation_key = (
            normalize(full_name),
            normalize(destination),
        )

        if operation_key in seen_operations:
            continue

        seen_operations.add(operation_key)

        new_player = {
            "name": full_name,
            "team": destination,
            "role": role,
            "officialId": (
                f"{normalize(destination)}:"
                f"{normalize(full_name)}"
            ),
        }

        players.append(new_player)

        additions.append(
            (
                full_name,
                destination,
                role,
            )
        )

    players = dedupe(players)

    print()
    print("CORREZIONI CALCIOMERCATO LEGA SERIE A")
    print("-------------------------------------")

    if not corrections:
        print("Nessun trasferimento interno da correggere.")
    else:
        for name, old_team, new_team in corrections:
            print(
                f"{name}: {old_team} -> {new_team}"
            )

    if not additions:
        print("Nessun nuovo giocatore da aggiungere.")
    else:
        for name, team, role in additions:
            print(
                f"NUOVO: {name} -> {team} ({role})"
            )

    print(
        f"Trasferimenti corretti: {len(corrections)}"
    )
    print(
        f"Nuovi giocatori aggiunti: {len(additions)}"
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
