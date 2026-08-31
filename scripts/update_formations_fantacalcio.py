#!/usr/bin/env python3
"""Aggiorna data/seriea_formations.json con gli 11 titolari G1-G3 da Fantacalcio.it.

Pensato per GitHub Actions: se una partita/giornata non e' ancora disponibile,
conserva l'ultimo dato valido e non inventa ne' cancella formazioni.
"""
import json, re, time, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

SEASON="2026-27"
BASE="https://www.fantacalcio.it"
OUTPUT=Path("data/seriea_formations.json")
ROSTERS=Path("data/seriea_rosters.json")
HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept-Language":"it-IT,it;q=0.9"}
TEAMS=["Atalanta","Bologna","Cagliari","Como","Fiorentina","Frosinone","Genoa","Inter","Juventus","Lazio","Lecce","Milan","Monza","Napoli","Parma","Roma","Sassuolo","Torino","Udinese","Venezia"]
SLUG={re.sub(r'[^a-z0-9]+','-',t.lower()).strip('-'):t for t in TEAMS}
FORMATION_RE=re.compile(r"^(?:3|4|5)-(?:1|2|3|4|5)(?:-(?:1|2|3))?(?:-(?:1|2))?$")


def fetch(url, tries=3):
    err=None
    for n in range(tries):
        try:
            req=Request(url,headers=HEADERS)
            with urlopen(req,timeout=30) as r:
                return r.read().decode("utf-8","replace")
        except Exception as e:
            err=e; time.sleep(1.5*(n+1))
    raise err


def norm(s):
    s=unicodedata.normalize("NFKD",s or "")
    s="".join(c for c in s if not unicodedata.combining(c)).casefold()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()


def roster_names():
    data=json.loads(ROSTERS.read_text())
    out={t:[] for t in TEAMS}
    for p in data.get("players",[]):
        if p.get("team") in out and p.get("name"):
            out[p["team"]].append(p["name"])
    return out


def discover_matches(day, html=None):
    url=f"{BASE}/serie-a/calendario/{day}/{SEASON}"
    soup=BeautifulSoup(html if html is not None else fetch(url),"html.parser")
    pat=re.compile(rf"/serie-a/calendario/{day}/{re.escape(SEASON)}/[^/?#]+/\d+")
    found=[]
    for a in soup.find_all("a",href=True):
        href=a["href"]
        m=pat.search(href)
        if m:
            full=urljoin(BASE,m.group(0))
            if full not in found: found.append(full)
    return found


def teams_from_url(url):
    parts=urlparse(url).path.strip('/').split('/')
    try: slug=parts[-2]
    except Exception: return None
    # prova tutte le coppie note, evitando ambiguita' da nomi con trattino
    for hs,home in SLUG.items():
        prefix=hs+'-'
        if slug.startswith(prefix):
            away=SLUG.get(slug[len(prefix):])
            if away: return home,away
    return None


def name_match(text, names):
    n=norm(text)
    if not n:return None
    exact=[x for x in names if norm(x)==n]
    if len(exact)==1:return exact[0]

    # Le cronache Sky usano spesso "Cognome I." (es. "González N."),
    # mentre il listone contiene il nome completo (es. "Nico Gonzalez").
    # Un token di una sola lettera vale quindi come iniziale di uno dei token
    # del nome completo. Manteniamo comunque il requisito di match univoco.
    toks=n.split()
    def token_fits(t, full_tokens):
        if t in full_tokens:
            return True
        return len(t)==1 and any(ft.startswith(t) for ft in full_tokens)

    cand=[]
    for x in names:
        xt=norm(x).split()
        if not toks or not xt:
            continue
        query_in_player=all(token_fits(t,xt) for t in toks)
        player_in_query=all(token_fits(t,toks) for t in xt)
        if query_in_player or player_in_query:
            cand.append(x)
    return cand[0] if len(cand)==1 else None


def extract_one(strings,start,names):
    players=[]; seen=set(); end=start
    for i in range(start,min(len(strings),start+100)):
        txt=strings[i].strip(); end=i
        if i>start and FORMATION_RE.fullmatch(txt) and players:
            break
        p=name_match(txt,names)
        if p and p not in seen:
            seen.add(p); players.append(p)
            if len(players)==11:return players,end
    return [],end


def parse_match(url, names_by_team, html=None):
    pair=teams_from_url(url)
    if not pair:return {}
    home,away=pair
    soup=BeautifulSoup(html if html is not None else fetch(url),"html.parser")
    strings=list(soup.stripped_strings)
    result={}; cursor=0
    for team in (home,away):
        success=False
        for i in range(cursor,len(strings)):
            form=strings[i].strip()
            if not FORMATION_RE.fullmatch(form):continue
            players,end=extract_one(strings,i+1,names_by_team[team])
            if len(players)==11:
                result[team]={"formation":form,"starters":players,"source":url}
                cursor=end+1; success=True; break
        if not success:
            # Non pubblicato o markup cambiato: non produrre dato parziale.
            continue
    return result



def parse_substitution_text(text, names_by_team, starters_by_team=None):
    """Estrae coppie out/in dalla cronaca Sky senza dipendere da una sola frase fissa.

    Sky usa formule diverse ("prende il posto di", "sostituisce", "X per Y",
    doppi cambi, "staffetta tra..."). Per la staffetta, che non esplicita la
    direzione, usiamo gli 11 titolari: il titolare e' l'uscente.
    """
    out={team:[] for team in names_by_team}
    starters_by_team=starters_by_team or {}
    source=text or ""
    name_pat=r"[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]*\.?(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]*\.?){0,3}"

    def add_pair(in_raw,out_raw):
        in_raw=(in_raw or "").strip(" .,:;!-–—")
        out_raw=(out_raw or "").strip(" .,:;!-–—")
        for team,names in names_by_team.items():
            incoming=name_match(in_raw,names)
            outgoing=name_match(out_raw,names)
            if incoming and outgoing and incoming!=outgoing:
                row={"out":outgoing,"in":incoming}
                if row not in out[team]: out[team].append(row)
                return True
        return False

    def edge_name(fragment,names,side):
        # Le pagine Sky concatenano gli eventi: dopo il nome puo' iniziare subito
        # la frase dell'evento successivo. Cerchiamo quindi solo 1-4 token sul
        # bordo vicino alla formula del cambio, invece di far diventare il regex
        # del nome troppo avido.
        toks=re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+\.?",fragment or "")
        if side=="left":
            spans=(" ".join(toks[-n:]) for n in range(1,min(4,len(toks))+1))
        else:
            spans=(" ".join(toks[:n]) for n in range(1,min(4,len(toks))+1))
        for candidate in spans:
            m=name_match(candidate,names)
            if m:return m
        return None

    # Caso principale Sky: segmentiamo su "Sostituzione!". In questo modo una
    # sostituzione adiacente non viene inglobata nel nome del giocatore uscente.
    chunks=re.split(r"(?i)\bSostituzione!\s*",source)
    for chunk in chunks[1:]:
        m=re.search(r"(?i)(.*?)\s+prende\s+il\s+posto\s+di\s+(.*)",chunk,re.S)
        if not m:continue
        for team,names in names_by_team.items():
            incoming=edge_name(m.group(1),names,"left")
            outgoing=edge_name(m.group(2),names,"right")
            if incoming and outgoing and incoming!=outgoing:
                row={"out":outgoing,"in":incoming}
                if row not in out[team]:out[team].append(row)
                break

    # Formule esplicite uno-a-uno.
    patterns=[
        rf"({name_pat})\s+prende\s+il\s+posto\s+di\s+({name_pat})",
        rf"({name_pat})\s+sostituisce\s+({name_pat})",
        rf"({name_pat})\s+al\s+posto\s+di\s+({name_pat})",
        rf"(?:c['’][eè]\s+anche|dentro|entra)\s+({name_pat})\s+per\s+({name_pat})",
    ]
    for pat in patterns:
        for m in re.finditer(pat,source,re.I):
            add_pair(m.group(1),m.group(2))

    # Doppi cambi: entranti prima, uscenti dopo (es. "Adzic e Kostic ... escono Yildiz e Cambiaso").
    double_patterns=[
        rf"({name_pat})\s+e\s+({name_pat}).{{0,90}}?\bescono\s+({name_pat})\s+e\s+({name_pat})",
        rf"({name_pat})\s+e\s+({name_pat}).{{0,90}}?\bfuori\s+({name_pat})\s+e\s+({name_pat})",
        rf"(?:dentro|entrano)\s+({name_pat})\s+e\s+({name_pat}).{{0,50}}?\bper\s+({name_pat})\s+e\s+({name_pat})",
    ]
    for pat in double_patterns:
        for m in re.finditer(pat,source,re.I|re.S):
            add_pair(m.group(1),m.group(3))
            add_pair(m.group(2),m.group(4))

    # "Staffetta tra X e Y": determina la direzione solo se uno dei due era titolare e l'altro no.
    staffetta_pat=rf"staffetta\s+tra\s+({name_pat})\s+e\s+({name_pat})"
    for m in re.finditer(staffetta_pat,source,re.I):
        a_raw,b_raw=m.group(1),m.group(2)
        for team,names in names_by_team.items():
            a=name_match(a_raw,names); b=name_match(b_raw,names)
            if not a or not b: continue
            starters=set(starters_by_team.get(team,[]) or [])
            if a in starters and b not in starters:
                add_pair(b,a)
            elif b in starters and a not in starters:
                add_pair(a,b)
            break
    return out

def sky_match_url(day, match_url):
    pair=teams_from_url(match_url)
    if not pair:return None
    home,away=pair
    def slug(t):return re.sub(r"[^a-z0-9]+","-",norm(t)).strip("-")
    return f"https://sport.sky.it/calcio/serie-a/partite/2026/giornata-{day}/{slug(home)}-{slug(away)}/risultato-gol"

def fetch_substitutions(day, match_url, names_by_team, starters_by_team=None, html=None):
    url=sky_match_url(day,match_url)
    if not url:return {}
    soup=BeautifulSoup(html if html is not None else fetch(url),"html.parser")
    text=soup.get_text(" ",strip=True)
    return parse_substitution_text(text,names_by_team,starters_by_team)



def backfill_substitutions(teams, names_by_team, fetcher=None, errors=None):
    """Aggiorna i cambi anche quando il parser della formazione non riesce a rileggere il match.

    Usa le righe gia' presenti nello snapshot (con 11 titolari e source URL) come base,
    cosi' i cambi possono essere recuperati indipendentemente dal nuovo parsing della formazione.
    """
    errors = errors if errors is not None else []
    if fetcher is None: fetcher=fetch_substitutions_hybrid
    seen=set()
    for team,days in (teams or {}).items():
        if not isinstance(days,dict):
            continue
        for day,row in days.items():
            if day not in {"1","2","3"} or not isinstance(row,dict):
                continue
            url=row.get("source")
            if not url or len(row.get("starters",[]) or [])!=11:
                continue
            key=(day,url)
            if key in seen:
                continue
            seen.add(key)
            pair=teams_from_url(url)
            if not pair:
                continue
            home,away=pair
            starters={}
            for t in (home,away):
                r=(teams.get(t,{}) or {}).get(day,{})
                starters[t]=r.get("starters",[]) if isinstance(r,dict) else []
            try:
                subs=fetcher(int(day),url,names_by_team,starters) or {}
            except Exception as e:
                errors.append(f"G{day} cambi {url}: {e}")
                continue
            for t in (home,away):
                r=(teams.get(t,{}) or {}).get(day)
                if isinstance(r,dict) and len(r.get("starters",[]) or [])==11:
                    r["substitutions"]=subs.get(t,[]) if isinstance(subs.get(t,[]),list) else []
    return teams

ESPN_BASE="https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1"
ESPN_MATCHDAY_DATES={1:"20260821-20260824",2:"20260828-20260831",3:"20260904-20260907"}
ESPN_TEAM_ALIASES={
    "Inter":["Inter Milan","Internazionale"], "Milan":["AC Milan"],
    "Roma":["AS Roma"], "Como":["Como 1907"], "Parma":["Parma Calcio 1913"],
}

def fetch_json(url):
    return json.loads(fetch(url))

def _all_text_values(obj):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in {"text","shortText","description"} and isinstance(v,str):
                yield v
            yield from _all_text_values(v)
    elif isinstance(obj,list):
        for v in obj: yield from _all_text_values(v)

def espn_name_match(text,names):
    found=name_match(text,names)
    if found:return found
    q=set(norm(text).split())
    # Fallback per abbreviazioni Fantacalcio tipo "Rodriguez Je.":
    # richiede un token di almeno 4 lettere condiviso e un match univoco nella rosa.
    cand=[]
    for x in names:
        shared={t for t in q.intersection(norm(x).split()) if len(t)>=4}
        if shared:cand.append(x)
    return cand[0] if len(cand)==1 else None

def parse_espn_substitutions(summary,names_by_team):
    """Legge le sostituzioni dal JSON ESPN. Restituisce nomi canonici del listone."""
    out={team:[] for team in names_by_team}
    seen_text=set()
    patterns=[
        r"Substitution,\s*[^.]+\.\s*(.+?)\s+replaces\s+(.+?)(?:\.|$)",
        r"Sostituzione,\s*[^.]+\.\s*(.+?)\s+sostituisce\s+(.+?)(?:\.|$)",
    ]
    for text in _all_text_values(summary):
        if text in seen_text: continue
        seen_text.add(text)
        for pat in patterns:
            m=re.search(pat,text,re.I)
            if not m: continue
            incoming_raw,outgoing_raw=m.group(1).strip(),m.group(2).strip()
            for team,names in names_by_team.items():
                incoming=espn_name_match(incoming_raw,names); outgoing=espn_name_match(outgoing_raw,names)
                if incoming and outgoing and incoming!=outgoing:
                    row={"out":outgoing,"in":incoming}
                    if row not in out[team]: out[team].append(row)
                    break
            break
    return out

def _espn_event_teams(event):
    comps=event.get("competitions") or []
    if not comps:return []
    vals=[]
    for c in comps[0].get("competitors") or []:
        t=c.get("team") or {}
        vals.extend([t.get("displayName"),t.get("shortDisplayName"),t.get("name")])
    return [x for x in vals if x]

def _team_matches_espn(team,labels):
    wanted=[team]+ESPN_TEAM_ALIASES.get(team,[])
    nl=[norm(x) for x in labels]
    return any(norm(w)==x or norm(w) in x or x in norm(w) for w in wanted for x in nl)

def espn_event_id(day,home,away,scoreboard=None):
    if scoreboard is None:
        dates=ESPN_MATCHDAY_DATES.get(int(day))
        if not dates:return None
        scoreboard=fetch_json(f"{ESPN_BASE}/scoreboard?dates={dates}&limit=100")
    for event in scoreboard.get("events") or []:
        labels=_espn_event_teams(event)
        if _team_matches_espn(home,labels) and _team_matches_espn(away,labels):
            return str(event.get("id")) if event.get("id") is not None else None
    return None

def fetch_substitutions_espn(day,match_url,names_by_team,starters_by_team=None,scoreboard=None,summary=None):
    pair=teams_from_url(match_url)
    if not pair:return {}
    home,away=pair
    event_id=espn_event_id(day,home,away,scoreboard)
    if not event_id:return {home:[],away:[]}
    if summary is None: summary=fetch_json(f"{ESPN_BASE}/summary?event={event_id}")
    parsed=parse_espn_substitutions(summary,{home:names_by_team.get(home,[]),away:names_by_team.get(away,[])})
    return parsed

def fetch_substitutions_hybrid(day,match_url,names_by_team,starters_by_team=None):
    """ESPN e' la fonte primaria; Sky resta fallback solo se ESPN non produce cambi."""
    try:
        espn=fetch_substitutions_espn(day,match_url,names_by_team,starters_by_team)
        if any(espn.get(t) for t in espn): return espn
    except Exception:
        pass
    return fetch_substitutions(day,match_url,names_by_team,starters_by_team)

def main():
    names=roster_names()
    previous={"teams":{}}
    if OUTPUT.exists():
        try: previous=json.loads(OUTPUT.read_text())
        except Exception: pass
    teams=previous.get("teams") if isinstance(previous.get("teams"),dict) else {}
    for t in TEAMS: teams.setdefault(t,{})
    errors=[]; updated=0
    for day in (1,2,3):
        try: urls=discover_matches(day)
        except Exception as e:
            errors.append(f"G{day}: calendario non leggibile: {e}"); continue
        if not urls:
            errors.append(f"G{day}: nessun link partita trovato"); continue
        for url in urls:
            try:
                parsed=parse_match(url,names)
                try:
                    subs=fetch_substitutions_hybrid(day,url,names,{t:r.get("starters",[]) for t,r in parsed.items()})
                except Exception as sub_err:
                    subs={}
                    errors.append(f"G{day} cambi {url}: {sub_err}")
                for team,row in parsed.items():
                    row["substitutions"]=subs.get(team,[])
                    teams[team][str(day)]=row; updated+=1
            except Exception as e:
                errors.append(f"G{day} {url}: {e}")
            time.sleep(.25)

    # Recupera i cambi anche per le formazioni conservate dallo snapshot precedente.
    # Questo rende i cambi indipendenti dal fatto che Fantacalcio renda ancora leggibile
    # il markup degli 11 titolari nelle pagine gia' giocate.
    backfill_substitutions(teams,names,errors=errors)

    available=[]
    for d in (1,2,3):
        count=sum(1 for t in TEAMS if isinstance(teams[t].get(str(d)),dict) and len(teams[t][str(d)].get("starters",[]))==11)
        if count: available.append(f"G{d} {count}/20")
    payload={
        "source":"Fantacalcio.it",
        "substitutions_source":"ESPN (fallback Sky Sport)",
        "season":SEASON,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "available_matchdays":available,
        "teams":teams,
        "errors":errors[-40:],
        "updated_lineups_this_run":updated,
    }
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print(f"Aggiornate {updated} formazioni. Disponibilita': {', '.join(available) or 'nessuna'}")
    if errors:
        print("Avvisi:")
        for e in errors[-10:]: print("-",e)

if __name__=="__main__": main()
