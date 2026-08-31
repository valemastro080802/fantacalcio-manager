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
    # Fantacalcio usa talvolta iniziali/punti: accetta solo un match univoco per token.
    toks=n.split()
    cand=[]
    for x in names:
        xt=norm(x).split()
        if toks and (all(t in xt for t in toks) or all(t in toks for t in xt)):
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
    name_pat=r"[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]*(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]*){0,3}"

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
                    subs=fetch_substitutions(day,url,names,{t:r.get("starters",[]) for t,r in parsed.items()})
                except Exception as sub_err:
                    subs={}
                    errors.append(f"G{day} cambi {url}: {sub_err}")
                for team,row in parsed.items():
                    row["substitutions"]=subs.get(team,[])
                    teams[team][str(day)]=row; updated+=1
            except Exception as e:
                errors.append(f"G{day} {url}: {e}")
            time.sleep(.25)
    available=[]
    for d in (1,2,3):
        count=sum(1 for t in TEAMS if isinstance(teams[t].get(str(d)),dict) and len(teams[t][str(d)].get("starters",[]))==11)
        if count: available.append(f"G{d} {count}/20")
    payload={
        "source":"Fantacalcio.it",
        "substitutions_source":"Sky Sport",
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
