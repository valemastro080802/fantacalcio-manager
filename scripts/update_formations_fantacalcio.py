#!/usr/bin/env python3
"""Aggiorna data/seriea_formations.json con gli 11 titolari G1-G3 da Fantacalcio.it.

Pensato per GitHub Actions. Cerca le 10 partite della giornata, apre il
riepilogo di ogni match e salva solo formazioni complete da 11 giocatori.

Correzione v1.6.2:
Fantacalcio usa URL del tipo
/serie-a/calendario/1/2026-27/inter-monza/17959
Il vecchio parser eliminava l'ID numerico finale; senza quell'ID il sito
restituiva spesso la prima partita della giornata (da qui 2/20).
Questa versione conserva sempre il match ID quando è presente e rende\npiù tollerante il matching delle abbreviazioni dei nomi (es. Rodriguez Ju.).
"""

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

SEASON = "2026-27"
BASE = "https://www.fantacalcio.it"
OUTPUT = Path("data/seriea_formations.json")
ROSTERS = Path("data/seriea_rosters.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

TEAMS = [
    "Atalanta", "Bologna", "Cagliari", "Como", "Fiorentina", "Frosinone",
    "Genoa", "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Monza",
    "Napoli", "Parma", "Roma", "Sassuolo", "Torino", "Udinese", "Venezia",
]

TEAM_BY_NORM = {}


FORMATION_RE = re.compile(r"^(?:3|4|5)-(?:1|2|3|4|5)(?:-(?:1|2|3))?(?:-(?:1|2))?$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", re.I)
MATCH_ID_RE = re.compile(r"^\d+$")
TAIL_SECTIONS = {"riepilogo", "voti", "statistiche", "notizie", "pagelle", "assist", "articolo"}


def fetch(url, tries=3):
    err = None
    for n in range(tries):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            err = exc
            time.sleep(1.5 * (n + 1))
    raise err


def norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


TEAM_BY_NORM = {norm(t): t for t in TEAMS}


def roster_names():
    data = json.loads(ROSTERS.read_text(encoding="utf-8"))
    out = {team: [] for team in TEAMS}
    for player in data.get("players", []):
        team = player.get("team")
        name = player.get("name")
        if team in out and name:
            out[team].append(name)
    return out


def _match_parts_from_path(day, path):
    """Restituisce (slug, match_id) da un URL Fantacalcio.

    Forme accettate:
      /.../inter-monza/17959
      /.../inter-monza/17959/voti
      /.../inter-monza/17959/statistiche
      /.../inter-monza/riepilogo   (fallback, senza ID)
    """
    prefix = f"/serie-a/calendario/{day}/{SEASON}/"
    if not path.startswith(prefix):
        return None

    rest = path[len(prefix):].strip("/")
    if not rest:
        return None

    parts = rest.split("/")
    slug = parts[0].lower()
    if not SLUG_RE.fullmatch(slug):
        return None

    match_id = None
    for part in parts[1:]:
        if MATCH_ID_RE.fullmatch(part):
            match_id = part
            break

    return slug, match_id


def _extract_match_urls_from_html(day, html):
    """Estrae le partite mantenendo l'ID numerico Fantacalcio.

    L'ID è essenziale: senza /17959, /17960 ecc. Fantacalcio può servire
    un match diverso da quello indicato nello slug.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    # Preferiamo gli URL con ID. Quelli senza ID restano solo come fallback.
    with_id = []
    without_id = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].split("?", 1)[0].split("#", 1)[0]
        absolute = urljoin(BASE, href)
        parsed = _match_parts_from_path(day, urlparse(absolute).path)
        if not parsed:
            continue

        slug, match_id = parsed
        if match_id:
            canonical = f"{BASE}/serie-a/calendario/{day}/{SEASON}/{slug}/{match_id}"
            with_id.append(canonical)
        else:
            canonical = f"{BASE}/serie-a/calendario/{day}/{SEASON}/{slug}/riepilogo"
            without_id.append(canonical)

    # Dedupe preservando ordine.
    for url in with_id + without_id:
        # Se abbiamo già lo stesso slug con ID, scartiamo il fallback senza ID.
        path_parts = urlparse(url).path.strip("/").split("/")
        slug = path_parts[4] if len(path_parts) > 4 else url
        if "/riepilogo" in url:
            if any(f"/{slug}/" in x and not x.endswith("/riepilogo") for x in found):
                continue

        if url not in seen:
            seen.add(url)
            found.append(url)

    return found


def discover_matches(day, html=None):
    pages = []

    if html is not None:
        pages.append(html)
    else:
        # Pagelle espone l'elenco completo della giornata e, normalmente,
        # i link precisi ai 10 match.
        for url in (
            f"{BASE}/pagelle/{SEASON}/roma/{day}",
            f"{BASE}/serie-a/calendario/{day}/{SEASON}",
            f"{BASE}/serie-a/voti",
        ):
            try:
                pages.append(fetch(url))
            except Exception:
                pass

    found = []
    seen = set()
    for page in pages:
        for url in _extract_match_urls_from_html(day, page):
            if url not in seen:
                seen.add(url)
                found.append(url)

    # Una giornata di Serie A deve avere al massimo 10 partite.
    return found[:10]


def teams_from_url(url):
    parts = urlparse(url).path.strip("/").split("/")

    # .../calendario/<day>/<season>/<slug>/<id>[/sezione]
    try:
        idx = parts.index("calendario")
    except ValueError:
        return None

    if len(parts) <= idx + 3:
        return None

    slug = parts[idx + 3]
    words = slug.split("-")

    # Lo slug contiene i nomi delle due squadre. Cerchiamo una divisione
    # univoca confrontando tutte le combinazioni possibili con le 20 squadre.
    candidates = []
    for cut in range(1, len(words)):
        left = norm(" ".join(words[:cut]))
        right = norm(" ".join(words[cut:]))
        home = TEAM_BY_NORM.get(left)
        away = TEAM_BY_NORM.get(right)
        if home and away:
            candidates.append((home, away))

    return candidates[0] if len(candidates) == 1 else None


def _token_compatible(a, b):
    """Confronto tollerante per iniziali/abbreviazioni Fantacalcio.

    Esempi: "Ju." può corrispondere a "J."; "C." a "Christian";
    le parti di almeno 3 caratteri devono invece coincidere davvero.
    """
    if a == b:
        return True
    if len(a) <= 2 or len(b) <= 2:
        return a.startswith(b) or b.startswith(a)
    return False


def _tokens_cover(shorter, longer):
    """Ogni token di shorter deve trovare un token compatibile in longer."""
    used = set()
    for token in shorter:
        hit = None
        for i, other in enumerate(longer):
            if i in used:
                continue
            if _token_compatible(token, other):
                hit = i
                break
        if hit is None:
            return False
        used.add(hit)
    return True


def name_match(text, names):
    normalized = norm(text)
    if not normalized:
        return None

    exact = [name for name in names if norm(name) == normalized]
    if len(exact) == 1:
        return exact[0]

    tokens = normalized.split()
    candidates = []

    for name in names:
        ntokens = norm(name).split()
        if not ntokens:
            continue

        # 1) confronto token tradizionale
        if _tokens_cover(tokens, ntokens) or _tokens_cover(ntokens, tokens):
            candidates.append(name)
            continue

        # 2) fallback sul cognome + iniziale, utile per etichette come
        #    "Rodriguez Ju.", "Ordonez C.", "Mendy P.", "Tourè E."
        if tokens[0] == ntokens[0]:
            tail_a = tokens[1:]
            tail_b = ntokens[1:]
            if not tail_a or not tail_b or _tokens_cover(
                tail_a if len(tail_a) <= len(tail_b) else tail_b,
                tail_b if len(tail_a) <= len(tail_b) else tail_a,
            ):
                candidates.append(name)

    # Dedupe mantenendo ordine.
    candidates = list(dict.fromkeys(candidates))
    return candidates[0] if len(candidates) == 1 else None


def extract_one(strings, start, names):
    players = []
    seen = set()
    end = start

    for i in range(start, min(len(strings), start + 90)):
        text = strings[i].strip()
        end = i

        if i > start and FORMATION_RE.fullmatch(text) and players:
            break

        player = name_match(text, names)
        if player and player not in seen:
            seen.add(player)
            players.append(player)
            if len(players) == 11:
                return players, end

    return [], end


def parse_match(url, names_by_team, html=None):
    pair = teams_from_url(url)
    if not pair:
        return {}

    home, away = pair
    page = html if html is not None else fetch(url)
    soup = BeautifulSoup(page, "html.parser")
    strings = list(soup.stripped_strings)

    result = {}
    cursor = 0

    for team in (home, away):
        success = False

        for i in range(cursor, len(strings)):
            formation = strings[i].strip()
            if not FORMATION_RE.fullmatch(formation):
                continue

            players, end = extract_one(strings, i + 1, names_by_team[team])
            if len(players) == 11:
                result[team] = {
                    "formation": formation,
                    "starters": players,
                    "source": url,
                }
                cursor = end + 1
                success = True
                break

        if not success:
            # Alcuni tabellini Fantacalcio risultano temporaneamente non
            # disponibili: non salviamo dati parziali.
            continue

    return result


def main():
    names = roster_names()
    previous = {"teams": {}}

    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except Exception:
            pass

    teams = previous.get("teams") if isinstance(previous.get("teams"), dict) else {}
    for team in TEAMS:
        teams.setdefault(team, {})

    errors = []
    updated = 0
    discovered = {}

    for day in (1, 2, 3):
        try:
            urls = discover_matches(day)
            discovered[str(day)] = urls
        except Exception as exc:
            errors.append(f"G{day}: calendario non leggibile: {exc}")
            continue

        if not urls:
            errors.append(f"G{day}: nessun link partita trovato")
            continue

        if len(urls) < 10:
            errors.append(f"G{day}: trovate solo {len(urls)}/10 partite")

        for url in urls:
            try:
                parsed = parse_match(url, names)

                if len(parsed) < 2:
                    errors.append(
                        f"G{day} {url}: formazione incompleta "
                        f"({len(parsed)}/2 squadre)"
                    )

                for team, row in parsed.items():
                    teams[team][str(day)] = row
                    updated += 1

            except Exception as exc:
                errors.append(f"G{day} {url}: {exc}")

            time.sleep(0.20)

    available = []

    for day in (1, 2, 3):
        count = sum(
            1
            for team in TEAMS
            if isinstance(teams[team].get(str(day)), dict)
            and len(teams[team][str(day)].get("starters", [])) == 11
        )

        if count:
            available.append(f"G{day} {count}/20")

    payload = {
        "source": "Fantacalcio.it",
        "season": SEASON,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available_matchdays": available,
        "teams": teams,
        "errors": errors[-80:],
        "updated_lineups_this_run": updated,
        "discovered_match_urls": discovered,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Aggiornate {updated} formazioni. "
        f"Disponibilita': {', '.join(available) or 'nessuna'}"
    )

    for day in ("1", "2", "3"):
        print(f"G{day}: {len(discovered.get(day, []))}/10 URL trovati")

    if errors:
        print("Avvisi:")
        for error in errors[-15:]:
            print("-", error)


if __name__ == "__main__":
    main()
}

TEAMS = [
    "Atalanta", "Bologna", "Cagliari", "Como", "Fiorentina", "Frosinone",
    "Genoa", "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Monza",
    "Napoli", "Parma", "Roma", "Sassuolo", "Torino", "Udinese", "Venezia",
]

TEAM_BY_NORM = {}


FORMATION_RE = re.compile(r"^(?:3|4|5)-(?:1|2|3|4|5)(?:-(?:1|2|3))?(?:-(?:1|2))?$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", re.I)
MATCH_ID_RE = re.compile(r"^\d+$")
TAIL_SECTIONS = {"riepilogo", "voti", "statistiche", "notizie", "pagelle", "assist", "articolo"}


def fetch(url, tries=3):
    err = None
    for n in range(tries):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            err = exc
            time.sleep(1.5 * (n + 1))
    raise err


def norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


TEAM_BY_NORM = {norm(t): t for t in TEAMS}


def roster_names():
    data = json.loads(ROSTERS.read_text(encoding="utf-8"))
    out = {team: [] for team in TEAMS}
    for player in data.get("players", []):
        team = player.get("team")
        name = player.get("name")
        if team in out and name:
            out[team].append(name)
    return out


def _match_parts_from_path(day, path):
    """Restituisce (slug, match_id) da un URL Fantacalcio.

    Forme accettate:
      /.../inter-monza/17959
      /.../inter-monza/17959/voti
      /.../inter-monza/17959/statistiche
      /.../inter-monza/riepilogo   (fallback, senza ID)
    """
    prefix = f"/serie-a/calendario/{day}/{SEASON}/"
    if not path.startswith(prefix):
        return None

    rest = path[len(prefix):].strip("/")
    if not rest:
        return None

    parts = rest.split("/")
    slug = parts[0].lower()
    if not SLUG_RE.fullmatch(slug):
        return None

    match_id = None
    for part in parts[1:]:
        if MATCH_ID_RE.fullmatch(part):
            match_id = part
            break

    return slug, match_id


def _extract_match_urls_from_html(day, html):
    """Estrae le partite mantenendo l'ID numerico Fantacalcio.

    L'ID è essenziale: senza /17959, /17960 ecc. Fantacalcio può servire
    un match diverso da quello indicato nello slug.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    # Preferiamo gli URL con ID. Quelli senza ID restano solo come fallback.
    with_id = []
    without_id = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].split("?", 1)[0].split("#", 1)[0]
        absolute = urljoin(BASE, href)
        parsed = _match_parts_from_path(day, urlparse(absolute).path)
        if not parsed:
            continue

        slug, match_id = parsed
        if match_id:
            canonical = f"{BASE}/serie-a/calendario/{day}/{SEASON}/{slug}/{match_id}"
            with_id.append(canonical)
        else:
            canonical = f"{BASE}/serie-a/calendario/{day}/{SEASON}/{slug}/riepilogo"
            without_id.append(canonical)

    # Dedupe preservando ordine.
    for url in with_id + without_id:
        # Se abbiamo già lo stesso slug con ID, scartiamo il fallback senza ID.
        path_parts = urlparse(url).path.strip("/").split("/")
        slug = path_parts[4] if len(path_parts) > 4 else url
        if "/riepilogo" in url:
            if any(f"/{slug}/" in x and not x.endswith("/riepilogo") for x in found):
                continue

        if url not in seen:
            seen.add(url)
            found.append(url)

    return found


def discover_matches(day, html=None):
    pages = []

    if html is not None:
        pages.append(html)
    else:
        # Pagelle espone l'elenco completo della giornata e, normalmente,
        # i link precisi ai 10 match.
        for url in (
            f"{BASE}/pagelle/{SEASON}/roma/{day}",
            f"{BASE}/serie-a/calendario/{day}/{SEASON}",
            f"{BASE}/serie-a/voti",
        ):
            try:
                pages.append(fetch(url))
            except Exception:
                pass

    found = []
    seen = set()
    for page in pages:
        for url in _extract_match_urls_from_html(day, page):
            if url not in seen:
                seen.add(url)
                found.append(url)

    # Una giornata di Serie A deve avere al massimo 10 partite.
    return found[:10]


def teams_from_url(url):
    parts = urlparse(url).path.strip("/").split("/")

    # .../calendario/<day>/<season>/<slug>/<id>[/sezione]
    try:
        idx = parts.index("calendario")
    except ValueError:
        return None

    if len(parts) <= idx + 3:
        return None

    slug = parts[idx + 3]
    words = slug.split("-")

    # Lo slug contiene i nomi delle due squadre. Cerchiamo una divisione
    # univoca confrontando tutte le combinazioni possibili con le 20 squadre.
    candidates = []
    for cut in range(1, len(words)):
        left = norm(" ".join(words[:cut]))
        right = norm(" ".join(words[cut:]))
        home = TEAM_BY_NORM.get(left)
        away = TEAM_BY_NORM.get(right)
        if home and away:
            candidates.append((home, away))

    return candidates[0] if len(candidates) == 1 else None


def name_match(text, names):
    normalized = norm(text)
    if not normalized:
        return None

    exact = [name for name in names if norm(name) == normalized]
    if len(exact) == 1:
        return exact[0]

    tokens = normalized.split()
    candidates = []
    for name in names:
        ntokens = norm(name).split()
        if tokens and (
            all(t in ntokens for t in tokens)
            or all(t in tokens for t in ntokens)
        ):
            candidates.append(name)

    return candidates[0] if len(candidates) == 1 else None


def extract_one(strings, start, names):
    players = []
    seen = set()
    end = start

    for i in range(start, min(len(strings), start + 90)):
        text = strings[i].strip()
        end = i

        if i > start and FORMATION_RE.fullmatch(text) and players:
            break

        player = name_match(text, names)
        if player and player not in seen:
            seen.add(player)
            players.append(player)
            if len(players) == 11:
                return players, end

    return [], end


def parse_match(url, names_by_team, html=None):
    pair = teams_from_url(url)
    if not pair:
        return {}

    home, away = pair
    page = html if html is not None else fetch(url)
    soup = BeautifulSoup(page, "html.parser")
    strings = list(soup.stripped_strings)

    result = {}
    cursor = 0

    for team in (home, away):
        success = False

        for i in range(cursor, len(strings)):
            formation = strings[i].strip()
            if not FORMATION_RE.fullmatch(formation):
                continue

            players, end = extract_one(strings, i + 1, names_by_team[team])
            if len(players) == 11:
                result[team] = {
                    "formation": formation,
                    "starters": players,
                    "source": url,
                }
                cursor = end + 1
                success = True
                break

        if not success:
            # Alcuni tabellini Fantacalcio risultano temporaneamente non
            # disponibili: non salviamo dati parziali.
            continue

    return result


def main():
    names = roster_names()
    previous = {"teams": {}}

    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except Exception:
            pass

    teams = previous.get("teams") if isinstance(previous.get("teams"), dict) else {}
    for team in TEAMS:
        teams.setdefault(team, {})

    errors = []
    updated = 0
    discovered = {}

    for day in (1, 2, 3):
        try:
            urls = discover_matches(day)
            discovered[str(day)] = urls
        except Exception as exc:
            errors.append(f"G{day}: calendario non leggibile: {exc}")
            continue

        if not urls:
            errors.append(f"G{day}: nessun link partita trovato")
            continue

        if len(urls) < 10:
            errors.append(f"G{day}: trovate solo {len(urls)}/10 partite")

        for url in urls:
            try:
                parsed = parse_match(url, names)

                if len(parsed) < 2:
                    errors.append(
                        f"G{day} {url}: formazione incompleta "
                        f"({len(parsed)}/2 squadre)"
                    )

                for team, row in parsed.items():
                    teams[team][str(day)] = row
                    updated += 1

            except Exception as exc:
                errors.append(f"G{day} {url}: {exc}")

            time.sleep(0.20)

    available = []

    for day in (1, 2, 3):
        count = sum(
            1
            for team in TEAMS
            if isinstance(teams[team].get(str(day)), dict)
            and len(teams[team][str(day)].get("starters", [])) == 11
        )

        if count:
            available.append(f"G{day} {count}/20")

    payload = {
        "source": "Fantacalcio.it",
        "season": SEASON,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available_matchdays": available,
        "teams": teams,
        "errors": errors[-80:],
        "updated_lineups_this_run": updated,
        "discovered_match_urls": discovered,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Aggiornate {updated} formazioni. "
        f"Disponibilita': {', '.join(available) or 'nessuna'}"
    )

    for day in ("1", "2", "3"):
        print(f"G{day}: {len(discovered.get(day, []))}/10 URL trovati")

    if errors:
        print("Avvisi:")
        for error in errors[-15:]:
            print("-", error)


if __name__ == "__main__":
    main()
