#!/usr/bin/env python3
"""build_holders.py - maakt holders.json: per aandeel de grootste kopers en verkopers
(grote beleggers) tussen twee kwartalen, uit de gratis, officiele SEC "Form 13F Data Sets".

Gebruik:
  python3 build_holders.py NIEUW.zip OUD.zip

Opties:
  --issuers 3000            aantal aandelen (grootste eerst) dat meegenomen wordt
  --top 10                  aantal kopers en verkopers per aandeel
  --ua "Naam email@adres"   (alleen voor het ophalen van de tickerlijst bij de SEC)
Alleen Python 3.9+ nodig, geen extra packages.
"""
import argparse, csv, datetime, io, json, os, re, sqlite3, sys, tempfile, urllib.request, zipfile

csv.field_size_limit(sys.maxsize)
SUFFIX = r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|HOLDINGS|GROUP|CL|CLASS|NEW|THE|SA|NV|AG)\b"


def norm(s):  # zelfde normalisatie als op de website
    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND "))
    return re.sub(r"\s+", " ", re.sub(SUFFIX, " ", s)).strip()


def rows(z, name):
    member = next((n for n in z.namelist() if n.upper().endswith(name.upper())), None)
    if not member:
        sys.exit(f"{name} niet gevonden in het zipbestand")
    with z.open(member) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""), delimiter="\t")


def iso(s):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s.strip()


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load(db, tag, path):
    z = zipfile.ZipFile(path)
    best = {}  # per belegger alleen het laatste 13F-HR in deze dataset
    for r in rows(z, "SUBMISSION.tsv"):
        if r.get("SUBMISSIONTYPE", "").strip() != "13F-HR":
            continue
        cik = r["CIK"].strip().lstrip("0")
        cur = (r["ACCESSION_NUMBER"], iso(r.get("FILING_DATE", "")), iso(r.get("PERIODOFREPORT", "")))
        if cik not in best or cur[1] > best[cik][1]:
            best[cik] = cur
    acc2cik = {v[0]: k for k, v in best.items()}
    names = {}
    for r in rows(z, "COVERPAGE.tsv"):
        if r["ACCESSION_NUMBER"] in acc2cik:
            names[r["ACCESSION_NUMBER"]] = r.get("FILINGMANAGER_NAME", "").strip()
    db.execute(f"create table filers_{tag}(cik text primary key, name text, filed text, period text)")
    db.executemany(f"insert into filers_{tag} values(?,?,?,?)", [(c, names.get(v[0], ""), v[1], v[2]) for c, v in best.items()])
    db.execute(f"create table pos_{tag}(cik text, cusip text, name text, shares real, value real)")
    batch, n = [], 0
    for r in rows(z, "INFOTABLE.tsv"):
        cik = acc2cik.get(r["ACCESSION_NUMBER"])
        if not cik or r.get("PUTCALL", "").strip():  # opties overslaan
            continue
        if r.get("SSHPRNAMTTYPE", "SH").strip() not in ("SH", ""):
            continue
        cusip = r["CUSIP"].strip().upper()
        if len(cusip) != 9 or cusip == "000000000":  # rommel zoals "N/A" overslaan
            continue
        batch.append((cik, cusip, r["NAMEOFISSUER"].strip(), num(r["SSHPRNAMT"]), num(r["VALUE"])))
        if len(batch) >= 50000:
            db.executemany(f"insert into pos_{tag} values(?,?,?,?,?)", batch); n += len(batch); batch = []
    db.executemany(f"insert into pos_{tag} values(?,?,?,?,?)", batch); n += len(batch)
    db.commit()
    print(f"{tag}: {len(best)} beleggers, {n} posities ingelezen")


def tickers(ua):
    try:
        req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": ua})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        m = {}
        for v in d.values():
            m.setdefault(norm(v["title"]), v["ticker"])
        return m
    except Exception as e:
        print("Tickerlijst niet opgehaald (niet erg, zoeken op naam werkt):", e)
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new"); ap.add_argument("old")
    ap.add_argument("--issuers", type=int, default=3000)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--ua", default="InvestorTool contact@example.com")
    a = ap.parse_args()
    path = os.path.join(tempfile.gettempdir(), "holders_tmp.db")
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    load(db, "new", a.new); load(db, "old", a.old)
    db.executescript("""
    create table an as select cik,cusip,max(name) name,sum(shares) shares,sum(value) value from pos_new group by cik,cusip;
    create table ao as select cik,cusip,max(name) name,sum(shares) shares,sum(value) value from pos_old group by cik,cusip;
    create index an_i on an(cik,cusip); create index ao_i on ao(cik,cusip);
    create table ch as
      select n.cik cik,n.cusip cusip,n.shares sn,coalesce(o.shares,0) so,n.value vn,coalesce(o.value,0) vo
      from an n left join ao o on o.cik=n.cik and o.cusip=n.cusip
      where n.cik in (select cik from filers_old)
      union all
      select o.cik,o.cusip,0,o.shares,0,o.value from ao o
      where o.cik in (select cik from filers_new)
        and not exists (select 1 from an n where n.cik=o.cik and n.cusip=o.cusip);
    create table ch2 as select cik,cusip,sn,so,(sn-so)*(case when sn>0 then vn/sn else vo/so end) cv from ch where sn<>so;
    create index ch2_i on ch2(cusip,cv);
    """)
    # Naam per aandeel = de naam die het VAAKST voorkomt (niet de alfabetisch laatste: dan wint rommel als "N/A")
    db.executescript("""
    create table nmc as select cusip,name,count(*) c from pos_new where trim(name)<>'' and upper(trim(name))<>'N/A' group by cusip,name;
    create table best as select n.cusip cusip,n.name name from nmc n
      join (select cusip,max(c) mc from nmc group by cusip) m on n.cusip=m.cusip and n.c=m.mc group by n.cusip;
    create index best_i on best(cusip);
    """)
    fl = {c: (n, f) for c, n, f in db.execute("select cik,name,filed from filers_new")}
    tk = tickers(a.ua)
    out = []
    for cusip, name, v in db.execute("select a.cusip,coalesce(b.name,'?'),sum(a.value) v from an a left join best b on b.cusip=a.cusip group by a.cusip order by v desc limit ?", (a.issuers,)).fetchall():
        def side(sql):
            return [[c, fl.get(c, ("", ""))[0], fl.get(c, ("", ""))[1], round(sn), round(so), round(cv)]
                    for c, sn, so, cv in db.execute(sql, (cusip, a.top))]
        b = side("select cik,sn,so,cv from ch2 where cusip=? and cv>0 order by cv desc limit ?")
        s = side("select cik,sn,so,cv from ch2 where cusip=? and cv<0 order by cv asc limit ?")
        if b or s:
            out.append({"c": cusip, "n": name, "t": tk.get(norm(name), ""), "v": round(v), "b": b, "s": s})
    per = lambda t: (db.execute(f"select period from filers_{t} group by period order by count(*) desc limit 1").fetchone() or [""])[0]
    data = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "period_new": per("new"), "period_old": per("old"), "issuers": out}
    with open("holders.json", "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    print(f"Klaar: holders.json met {len(out)} aandelen ({os.path.getsize('holders.json') / 1e6:.1f} MB)")
    print("Controle - de 8 grootste aandelen in het bestand (dit zouden bekende namen moeten zijn):")
    for i in out[:8]:
        print("   ", i["n"], "| ticker:", i["t"] or "-", "| kopers:", len(i["b"]), "| verkopers:", len(i["s"]))
    names = " ".join(i["n"] for i in out)
    missing = [x for x in ("APPLE", "MICROSOFT", "NVIDIA") if x not in names]
    if missing:
        print("LET OP: deze grote namen ontbreken:", ", ".join(missing), "- controleer of je de juiste, volledige zip-bestanden gebruikt.")


if __name__ == "__main__":
    main()
