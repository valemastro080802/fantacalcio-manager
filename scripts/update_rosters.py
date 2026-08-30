#!/usr/bin/env python3
import json, re, html as htmlmod, sys, time
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone

TEAMS = {
 "Atalanta":"atalanta","Bologna":"bologna","Cagliari":"cagliari","Como":"como",
 "Fiorentina":"fiorentina","Frosinone":"frosinone","Genoa":"genoa","Inter":"inter",
 "Juventus":"juventus","Lazio":"lazio","Lecce":"lecce","Milan":"milan",
 "Monza":"monza","Napoli":"napoli","Parma":"parma","Roma":"roma",
 "Sassuolo":"sassuolo","Torino":"torino","Udinese":"udinese","Venezia":"venezia"
}

ROLE_MAP={
 "portiere":"P","portieri":"P","goalkeeper":"P",
 "difensore":"D","difensori":"D","defender":"D",
 "centrocampista":"C","centrocampisti":"C","midfielder":"C",
 "attaccante":"A","attaccanti":"A","forward":"A","striker":"A"
}

HEADERS={
 "User-Agent":"Mozilla/5.0",
 "Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
 "Accept-Language":"it-IT,it;q=0.9,en;q=0.7"
}

def fetch(url, tries=3):
    last=None
    for i in range(tries):
        try:
            req=Request(url,headers=HEADERS)
            with urlopen(req,timeout=30) as r:
                return r.read().decode("utf-8","replace")
        except Exception as e:
            last=e
            time.sleep(2*(i+1))
    raise last

def clean(s):
    s=re.sub(r"<[^>]+>"," ",s or "")
    s=htmlmod.unescape(s)
    return re.sub(r"\s+"," ",s).strip()

def plausible_name(name):
    if not name or len(name)<3 or len(name)>80:
        return False
    low=name.casefold()
    banned=("logo","serie a","enilive","sponsor","nationality","club")
    return not any(x in low for x in banned)

def extract_from_html(page, team):
    players=[]
    heading_pat=re.compile(r'<h[1-6][^>]*>(.*?)</h[1-6]>',re.I|re.S)
    heads=[]

    for m in heading_pat.finditer(page):
        txt=clean(m.group(1)).casefold()
        if txt in ROLE_MAP:
            heads.append((m.start(),m.end(),ROLE_MAP[txt]))

    for i,(st,en,role) in enumerate(heads):
        end=heads[i+1][0] if i+1<len(heads) else len(page)
        seg=page[en:end]

        for tag in re.findall(r'<img\b[^>]*>',seg,re.I|re.S):
            am=re.search(r'\balt=["\']([^"\']+)["\']',tag,re.I)
            if not am:
                continue

            name=htmlmod.unescape(am.group(1)).strip()

            if not plausible_name(name):
                continue

            srcm=re.search(r'\bsrc=["\']([^"\']+)["\']',tag,re.I)
            src=htmlmod.unescape(srcm.group(1) if srcm else "")

            if "legaseriea" not in src:
                continue

            players.append({
                "name":name,
                "team":team,
                "role":role,
                "officialId":f"{team}:{name}".lower()
            })

    return players

def dedupe(rows):
    out=[]
    seen=set()

    for p in rows:
        key=(p["team"],re.sub(r"\W+","",p["name"].casefold()),p["role"])

        if key in seen:
            continue

        seen.add(key)
        out.append(p)

    return out

def scrape_team(team,slug):
    url=f"https://www.legaseriea.it/team/{slug}/squad"
    page=fetch(url)

    rows=dedupe(extract_from_html(page,team))

    if len(rows)<15:
        raise RuntimeError(f"{team}: trovati solo {len(rows)} giocatori")

    return rows

def main():
    all_players=[]
    errors=[]

    for team,slug in TEAMS.items():
        try:
            rows=scrape_team(team,slug)
            all_players.extend(rows)
            print(f"{team}: {len(rows)}")
        except Exception as e:
            errors.append(str(e))

    teams_found={p["team"] for p in all_players}

    if errors or len(teams_found)!=20 or len(all_players)<300:
        print("Controllo sicurezza FALLITO",file=sys.stderr)
        print(" | ".join(errors),file=sys.stderr)
        print(
            f"Giocatori={len(all_players)} Squadre={len(teams_found)}/20",
            file=sys.stderr
        )
        sys.exit(1)

    payload={
      "source":"Lega Serie A - pagine ufficiali delle rose dei club",
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "teams":20,
      "players_count":len(all_players),
      "players":sorted(
          all_players,
          key=lambda x:(x["team"],x["role"],x["name"])
      )
    }

    out=Path("data/seriea_rosters.json")
    out.parent.mkdir(parents=True,exist_ok=True)

    out.write_text(
        json.dumps(payload,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

    print(f"OK: {len(all_players)} giocatori in {out}")

if __name__=="__main__":
    main()
