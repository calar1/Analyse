"""
Génère docs/index.html : analyse approfondie des matchs à venir (sans cotes).
Prend en compte : force des équipes (domicile/extérieur), forme récente,
face-à-face sur 5 saisons, et avis du marché (qui intègre blessures et compos).
Données : football-data.co.uk (stats) + The Odds API (calendrier et avis du marché).
"""
import csv, difflib, html, io, json, os, re, unicodedata, urllib.request
from datetime import datetime, timezone
from math import exp, factorial
from zoneinfo import ZoneInfo

API_KEY = os.environ.get("ODDS_API_KEY", "")
REGIONS = "eu"
TZ = ZoneInfo("Europe/Paris")
MAX_BUTS = 10
JOURS_MAX = 3          # matchs des 3 prochains jours
POIDS_MARCHE = 0.6     # poids de l'avis du marché (il intègre blessures et compos)
SAISONS_H2H = 5
LIGUES = [  # (clé Odds API, nom, source stats, code)
    ("soccer_epl", "Premier League", "main", "E0"),
    ("soccer_spain_la_liga", "Liga", "main", "SP1"),
    ("soccer_germany_bundesliga", "Bundesliga", "main", "D1"),
    ("soccer_italy_serie_a", "Serie A", "main", "I1"),
    ("soccer_france_ligue_one", "Ligue 1", "main", "F1"),
    ("soccer_portugal_primeira_liga", "Primeira Liga", "main", "P1"),
    ("soccer_russia_premier_league", "Premier-Liga russe", "extra", "RUS"),
]
ALIAS = {
    "manchester united": "man united", "manchester city": "man city",
    "wolverhampton wanderers": "wolves", "tottenham hotspur": "tottenham",
    "newcastle united": "newcastle", "nottingham forest": "nottm forest",
    "brighton and hove albion": "brighton", "west ham united": "west ham",
    "leeds united": "leeds", "leicester city": "leicester",
    "paris saint germain": "paris sg", "borussia monchengladbach": "mgladbach",
    "bayer leverkusen": "leverkusen", "borussia dortmund": "dortmund",
    "eintracht frankfurt": "ein frankfurt", "atletico madrid": "ath madrid",
    "athletic bilbao": "ath bilbao", "real betis": "betis",
    "real sociedad": "sociedad", "celta vigo": "celta", "inter milan": "inter",
    "sporting cp": "sp lisbon", "sporting lisbon": "sp lisbon",
    "sporting braga": "sp braga", "olympique marseille": "marseille",
    "olympique lyonnais": "lyon", "as monaco": "monaco",
}


# ---------------------------------------------------------------- données
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", errors="replace").lstrip("\ufeff")


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\b(fc|cf|afc|ac|sc|fk|cd)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def saison_code(now, decalage):
    y = (now.year if now.month >= 8 else now.year - 1) - decalage
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def paire(r, a, b, ra=None, rb=None):
    x, y = num(r.get(a)), num(r.get(b))
    if x is None or y is None:
        return (None, None)
    if ra:
        x += num(r.get(ra)) or 0
        y += num(r.get(rb)) or 0
    return (x, y)


def ligne(h, a, g, ht=(None, None), co=(None, None), ca=(None, None), fo=(None, None)):
    return {"h": h, "a": a, "hn": norm(h), "an": norm(a), "g": g, "ht": ht, "co": co, "ca": ca, "fo": fo}


def lire_main(code, now):
    """Liste de saisons (la plus récente d'abord), chacune dans l'ordre chronologique."""
    saisons = []
    for dec in range(SAISONS_H2H):
        rows = []
        try:
            t = get(f"https://www.football-data.co.uk/mmz4281/{saison_code(now, dec)}/{code}.csv")
            for r in csv.DictReader(io.StringIO(t)):
                if r.get("FTHG") and r.get("HomeTeam"):
                    rows.append(ligne(r["HomeTeam"], r["AwayTeam"], (int(r["FTHG"]), int(r["FTAG"])),
                                      paire(r, "HTHG", "HTAG"), paire(r, "HC", "AC"),
                                      paire(r, "HY", "AY", "HR", "AR"), paire(r, "HF", "AF")))
        except Exception:
            pass
        saisons.append(rows)
    return saisons


def lire_extra(code):
    rows = [r for r in csv.DictReader(io.StringIO(get(f"https://www.football-data.co.uk/new/{code}.csv")))
            if r.get("HG") not in (None, "")]
    return [[ligne(r["Home"], r["Away"], (int(float(r["HG"])), int(float(r["AG"]))))
             for r in rows if r["Season"] == s] for s in sorted({r["Season"] for r in rows}, reverse=True)]


def forces(matchs):
    moy_d = max(sum(m[2] for m in matchs) / len(matchs), 0.01)
    moy_e = max(sum(m[3] for m in matchs) / len(matchs), 0.01)
    eq, K = {}, 6
    for h, a, gh, ga in matchs:
        for t in (h, a):
            eq.setdefault(t, [0, 0, 0, 0, 0, 0])
        eq[h][0] += 1; eq[h][1] += gh; eq[h][2] += ga
        eq[a][3] += 1; eq[a][4] += ga; eq[a][5] += gh
    f = {}
    for t, (dn, dm, de, en, em, ee) in eq.items():
        f[t] = {"att_d": ((dm + K * moy_d) / (dn + K)) / moy_d, "def_d": ((de + K * moy_e) / (dn + K)) / moy_e,
                "att_e": ((em + K * moy_e) / (en + K)) / moy_e, "def_e": ((ee + K * moy_d) / (en + K)) / moy_d}
    return moy_d, moy_e, f


def stat(rows, cle):
    m = [(r["hn"], r["an"], r[cle][0], r[cle][1]) for r in rows if r[cle][0] is not None]
    return m if len(m) >= 60 else None


def trouver(nom, f):
    n = norm(nom)
    n = ALIAS.get(n, n)
    if n in f:
        return n
    r = difflib.get_close_matches(n, list(f), n=1, cutoff=0.6)
    return r[0] if r else None


# ---------------------------------------------------------------- forme et face-à-face
def derniers(rows, cle, n):
    res = []
    for r in reversed(rows):
        if r["hn"] == cle:
            gf, ga = r["g"]
        elif r["an"] == cle:
            ga, gf = r["g"]
        else:
            continue
        res.append((gf, ga))
        if len(res) == n:
            break
    return res[::-1]


def facteurs_forme(rows, cle):
    """(attaque, défense, 5 derniers résultats) : forme récente vs moyenne de saison."""
    tout, rec = derniers(rows, cle, 10 ** 6), derniers(rows, cle, 5)
    lettres = "".join("V" if a > b else "N" if a == b else "D" for a, b in rec)
    if len(tout) < 6 or not rec:
        return 1.0, 1.0, lettres, len(tout)
    mf, mc = sum(a for a, _ in tout) / len(tout), sum(b for _, b in tout) / len(tout)
    rf, rc = sum(a for a, _ in rec) / len(rec), sum(b for _, b in rec) / len(rec)
    K = 5
    att = ((rf * len(rec) + K * mf) / (len(rec) + K)) / mf if mf else 1.0
    dfn = ((rc * len(rec) + K * mc) / (len(rec) + K)) / mc if mc else 1.0
    return min(max(att, 0.85), 1.15), min(max(dfn, 0.85), 1.15), lettres, len(tout)


def face_a_face(hist, hn, an):
    m = [r for r in hist if {r["hn"], r["an"]} == {hn, an}]
    v = nul = d = btts = o25 = buts = 0
    for r in m:
        gh, ga = r["g"]
        gf, gc = (gh, ga) if r["hn"] == hn else (ga, gh)
        v += gf > gc; nul += gf == gc; d += gf < gc
        btts += gh > 0 and ga > 0; o25 += gh + ga > 2; buts += gh + ga
    n = len(m)
    return {"n": n, "v": v, "nul": nul, "d": d, "buts": buts / n if n else 0,
            "btts": btts / n if n else 0, "o25": o25 / n if n else 0}


# ---------------------------------------------------------------- probabilités
def pois(k, lam):
    return exp(-lam) * lam ** k / factorial(k)


def matrice(ld, le):
    pd = [pois(i, ld) for i in range(MAX_BUTS + 1)]
    pe = [pois(j, le) for j in range(MAX_BUTS + 1)]
    return [[a * b for b in pe] for a in pd]


def probas(ld, le):
    p = dict.fromkeys(["1", "X", "2", "Over"], 0.0)
    for i, lg in enumerate(matrice(ld, le)):
        for j, pr in enumerate(lg):
            p["1" if i > j else "X" if i == j else "2"] += pr
            if i + j > 2:
                p["Over"] += pr
    return p


def ajuster(ld, le, mk):
    """Recale les buts attendus sur un mélange modèle + marché (le marché connaît les blessures)."""
    if not all(k in mk for k in ("1", "X", "2", "Over")):
        return ld, le, False
    pr = probas(ld, le)
    cible = {k: (1 - POIDS_MARCHE) * pr[k] + POIDS_MARCHE * mk[k] for k in ("1", "X", "2", "Over")}

    def err(a, b):
        p = probas(a, b)
        return sum((p[k] - cible[k]) ** 2 for k in cible)
    cur = (err(ld, le), ld, le)
    for pas, n in ((0.15, 6), (0.04, 5), (0.01, 5)):
        c0 = cur
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                a, b = c0[1] + i * pas, c0[2] + j * pas
                if a < 0.15 or b < 0.15:
                    continue
                e = err(a, b)
                if e < cur[0]:
                    cur = (e, a, b)
    return cur[1], cur[2], True


def marche(match):
    h2h, tot = [], []
    for bk in match.get("bookmakers", []):
        for mk in bk["markets"]:
            if mk["key"] == "h2h":
                d = {}
                for o in mk["outcomes"]:
                    k = "1" if o["name"] == match["home_team"] else "2" if o["name"] == match["away_team"] else "X"
                    d[k] = 1 / o["price"]
                if len(d) == 3:
                    t = sum(d.values()); h2h.append({k: v / t for k, v in d.items()})
            elif mk["key"] == "totals":
                d = {o["name"]: 1 / o["price"] for o in mk["outcomes"] if o.get("point") == 2.5}
                if len(d) == 2:
                    t = sum(d.values()); tot.append({k: v / t for k, v in d.items()})
    res = {}
    if len(h2h) >= 3:
        for k in "1X2":
            res[k] = sum(x[k] for x in h2h) / len(h2h)
    if len(tot) >= 3:
        res["Over"] = sum(x["Over"] for x in tot) / len(tot)
    return res


def sur(lam, lg):
    return 1 - sum(pois(i, lam) for i in range(int(lg) + 1))


NOMS_RES = {"1": "dom.", "X": "nul", "2": "ext."}


def mt_ft(ld, le, part):
    d1, e1, d2, e2 = ld * part, le * part, ld * (1 - part), le * (1 - part)
    P1 = [[pois(a, d1) * pois(b, e1) for b in range(6)] for a in range(6)]
    P2 = [[pois(c, d2) * pois(d, e2) for d in range(6)] for c in range(6)]
    res = {}
    r = lambda x, y: "1" if x > y else "X" if x == y else "2"
    for a in range(6):
        for b in range(6):
            for c in range(6):
                for d in range(6):
                    k = (r(a, b), r(a + c, b + d))
                    res[k] = res.get(k, 0) + P1[a][b] * P2[c][d]
    return res


def issues(ld, le, part, corners, cartons, fautes):
    """Liste de (catégorie, libellé, probabilité, code) ; le code sert à vérifier l'issue après le match."""
    M = matrice(ld, le)
    R = range(MAX_BUTS + 1)
    S = lambda f: sum(M[i][j] for i in R for j in R if f(i, j))
    L = []
    add = lambda cat, nom, p, code: L.append((cat, nom, p, code))
    p1, px, p2 = S(lambda i, j: i > j), S(lambda i, j: i == j), S(lambda i, j: i < j)
    add("Résultat", "Victoire domicile", p1, "R:1")
    add("Résultat", "Match nul", px, "R:X")
    add("Résultat", "Victoire extérieur", p2, "R:2")
    add("Résultat", "Double chance : domicile ou nul", p1 + px, "R:1X")
    add("Résultat", "Double chance : extérieur ou nul", px + p2, "R:X2")
    add("Résultat", "Double chance : pas de nul", p1 + p2, "R:12")
    add("Résultat", "Domicile (nul = remboursé)", p1 / (p1 + p2), "R:D1")
    add("Résultat", "Extérieur (nul = remboursé)", p2 / (p1 + p2), "R:D2")
    for lg in (0.5, 1.5, 2.5, 3.5, 4.5):
        o = S(lambda i, j: i + j > lg)
        add("Total de buts", f"Plus de {lg} buts", o, f"T:>{lg}")
        add("Total de buts", f"Moins de {lg} buts", 1 - o, f"T:<{lg}")
    b = S(lambda i, j: i > 0 and j > 0)
    add("Les 2 marquent", "Les 2 équipes marquent : oui", b, "B:1")
    add("Les 2 marquent", "Les 2 équipes marquent : non", 1 - b, "B:0")
    for lg in (0.5, 1.5, 2.5):
        add("Buts par équipe", f"Domicile : plus de {lg} buts", S(lambda i, j: i > lg), f"H:>{lg}")
        add("Buts par équipe", f"Extérieur : plus de {lg} buts", S(lambda i, j: j > lg), f"A:>{lg}")
    add("Buts par équipe", "Domicile ne marque pas", S(lambda i, j: i == 0), "H:0")
    add("Buts par équipe", "Extérieur ne marque pas", S(lambda i, j: j == 0), "A:0")
    add("Handicap", "Domicile gagne par 2 buts ou plus", S(lambda i, j: i - j >= 2), "G:H2")
    add("Handicap", "Extérieur gagne par 2 buts ou plus", S(lambda i, j: j - i >= 2), "G:A2")
    add("Handicap", "Domicile ne perd pas par 2 buts ou plus", S(lambda i, j: i - j > -2), "G:HN")
    add("Handicap", "Extérieur ne perd pas par 2 buts ou plus", S(lambda i, j: j - i > -2), "G:AN")
    for nom, c, f in (("Domicile", "1", lambda i, j: i > j), ("Nul", "X", lambda i, j: i == j),
                      ("Extérieur", "2", lambda i, j: i < j)):
        add("Résultat + total", f"{nom} et plus de 2.5 buts", S(lambda i, j: f(i, j) and i + j > 2.5), f"RT:{c}:>2.5")
        add("Résultat + total", f"{nom} et moins de 2.5 buts", S(lambda i, j: f(i, j) and i + j < 2.5), f"RT:{c}:<2.5")
    sc = sorted(((M[i][j], i, j) for i in range(5) for j in range(5)), reverse=True)[:3]
    for p, i, j in sc:
        add("Score exact", f"Score exact {i}-{j}", p, f"S:{i}-{j}")
    if part:
        d1, e1 = ld * part, le * part
        Mh = matrice(d1, e1)
        h1 = sum(Mh[i][j] for i in R for j in R if i > j)
        hx = sum(Mh[i][j] for i in R for j in R if i == j)
        add("Mi-temps", "Mi-temps : domicile mène", h1, "M:1")
        add("Mi-temps", "Mi-temps : nul", hx, "M:X")
        add("Mi-temps", "Mi-temps : extérieur mène", 1 - h1 - hx, "M:2")
        for lg in (0.5, 1.5):
            o = sur(d1 + e1, lg)
            add("Mi-temps", f"1re mi-temps : plus de {lg} buts", o, f"MT:>{lg}")
            add("Mi-temps", f"1re mi-temps : moins de {lg} buts", 1 - o, f"MT:<{lg}")
        for (a, b_), p in sorted(mt_ft(ld, le, part).items(), key=lambda x: -x[1])[:3]:
            add("Mi-temps", f"Mi-temps / fin : {NOMS_RES[a]} / {NOMS_RES[b_]}", p, f"MF:{a}{b_}")
    for nom, cle, lam, lignes in (("Corners", "co", corners, (7.5, 8.5, 9.5, 10.5, 11.5)),
                                  ("Cartons", "ca", cartons, (2.5, 3.5, 4.5, 5.5)),
                                  ("Fautes", "fo", fautes, (20.5, 22.5, 24.5, 26.5))):
        if lam:
            for lg in lignes:
                o = sur(lam, lg)
                add(nom, f"{nom} : plus de {lg}", o, f"C:{cle}:>{lg}")
                add(nom, f"{nom} : moins de {lg}", 1 - o, f"C:{cle}:<{lg}")
    return L


def evaluer(code, r):
    """True / False selon le résultat réel ; None si non vérifiable (donnée absente ou remboursé)."""
    gh, ga = r["g"]
    k, _, v = code.partition(":")
    res = "1" if gh > ga else "X" if gh == ga else "2"
    def seuil(x, t):
        return x > float(t[1:]) if t[0] == ">" else x < float(t[1:])
    if k == "R":
        if v in ("D1", "D2"):
            return None if res == "X" else (res == v[1])
        return res in v
    if k == "T":
        return seuil(gh + ga, v)
    if k == "B":
        return (gh > 0 and ga > 0) == (v == "1")
    if k == "H":
        return gh == 0 if v == "0" else seuil(gh, v)
    if k == "A":
        return ga == 0 if v == "0" else seuil(ga, v)
    if k == "G":
        return {"H2": gh - ga >= 2, "A2": ga - gh >= 2, "HN": gh - ga > -2, "AN": ga - gh > -2}[v]
    if k == "RT":
        c, _, t = v.partition(":")
        return res == c and seuil(gh + ga, t)
    if k == "S":
        return v == f"{gh}-{ga}"
    if k in ("M", "MT", "MF"):
        if r["ht"][0] is None:
            return None
        hh, ha = r["ht"]
        mt = "1" if hh > ha else "X" if hh == ha else "2"
        if k == "M":
            return mt == v
        if k == "MT":
            return seuil(hh + ha, v)
        return mt == v[0] and res == v[1]
    if k == "C":
        cle, _, t = v.partition(":")
        if r[cle][0] is None:
            return None
        return seuil(r[cle][0] + r[cle][1], t)
    return None


# ---------------------------------------------------------------- analyse
def analyser():
    now = datetime.now(timezone.utc)
    sortie, erreurs, rows_ligue = [], [], {}
    for sport, nom, source, code in LIGUES:
        try:
            saisons = lire_main(code, now) if source == "main" else lire_extra(code)
            hist = [r for s in saisons for r in s]
            base = saisons[0] if len(saisons[0]) >= 80 else (saisons[1] if len(saisons) > 1 else []) + saisons[0]
            if len(base) < 30:
                raise ValueError("pas assez de matchs joués")
            rows_ligue[nom] = saisons[0]
            moy_d, moy_e, f = forces([(r["hn"], r["an"], *r["g"]) for r in base])
            autres = {}
            for cle in ("co", "ca", "fo"):
                sm = stat(base, cle)
                autres[cle] = forces(sm) if sm else None
            ok = [r for r in base if r["ht"][0] is not None]
            tot_ft = sum(sum(r["g"]) for r in ok)
            part = sum(sum(r["ht"]) for r in ok) / tot_ft if len(ok) >= 60 and tot_ft else None
            data = json.loads(get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
                                  f"?apiKey={API_KEY}&regions={REGIONS}&markets=h2h,totals&oddsFormat=decimal"))
        except Exception as e:
            erreurs.append(f"{nom} : indisponible ({str(e)[:60]})")
            continue
        for m in data:
            debut = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
            if not (0 <= (debut - now).total_seconds() <= JOURS_MAX * 86400):
                continue
            h, a = trouver(m["home_team"], f), trouver(m["away_team"], f)
            if not h or not a:
                erreurs.append(f"{nom} : équipe non reconnue ({m['home_team']} - {m['away_team']})")
                continue
            ld = f[h]["att_d"] * f[a]["def_e"] * moy_d
            le = f[a]["att_e"] * f[h]["def_d"] * moy_e
            fh, fa = facteurs_forme(base, h), facteurs_forme(base, a)
            ld, le = ld * fh[0] * fa[1], le * fa[0] * fh[1]
            h2h = face_a_face(hist, h, a)
            if h2h["n"] >= 3 and ld + le > 0:
                w = h2h["n"] / (h2h["n"] + 10)
                fac = min(max(1 + w * (h2h["buts"] / (ld + le) - 1), 0.92), 1.08)
                ld, le = ld * fac, le * fac
            ld, le, avec_marche = ajuster(ld, le, marche(m))

            def total(cle):
                a_ = autres.get(cle)
                if not a_ or h not in a_[2] or a not in a_[2]:
                    return None
                md, me, ff = a_
                return ff[h]["att_d"] * ff[a]["def_e"] * md + ff[a]["att_e"] * ff[h]["def_d"] * me
            n_min = min(fh[3], fa[3])
            sortie.append({"id": f"{nom}|{h}|{a}|{debut:%Y-%m-%d}", "hk": h, "ak": a,
                           "ligue": nom, "h": m["home_team"], "a": m["away_team"], "date": debut,
                           "ld": ld, "le": le, "forme_h": fh[2], "forme_a": fa[2], "h2h": h2h,
                           "marche": avec_marche,
                           "fiab": "bonne" if n_min >= 15 else "moyenne" if n_min >= 8 else "faible",
                           "issues": issues(ld, le, part, total("co"), total("ca"), total("fo"))})
    return sortie, erreurs, rows_ligue


# ---------------------------------------------------------------- bilan
FICHIER_HIST = "docs/historique.json"


def hist_vide(now):
    return {"en_attente": {}, "bilan": {"depuis": now.strftime("%d/%m/%Y"), "matchs": 0,
                                         "all": {}, "top": {}, "cat": {}}}


def charger_hist(now):
    try:
        with open(FICHIER_HIST, encoding="utf-8") as fh:
            h = json.load(fh)
        h.setdefault("en_attente", {})
        h.setdefault("bilan", hist_vide(now)["bilan"])
        return h
    except Exception:
        return hist_vide(now)


def compter(bilan, cat, p, hit, top):
    tr = str(min(int(p * 10), 9))
    for cle in ("all", "top") if top else ("all",):
        d = bilan[cle].setdefault(tr, [0, 0, 0.0])
        d[0] += 1; d[1] += int(hit); d[2] += p
    c = bilan["cat"].setdefault(cat, [0, 0, 0.0])
    c[0] += 1; c[1] += int(hit); c[2] += p


def regler(hist, rows_ligue, now):
    """Vérifie les prévisions dont le match est terminé (résultat présent dans les données)."""
    for id_, e in list(hist["en_attente"].items()):
        rows = rows_ligue.get(e["ligue"])
        debut = datetime.fromisoformat(e["debut"])
        if rows is None or debut > now:
            continue
        r = next((x for x in reversed(rows) if x["hn"] == e["hk"] and x["an"] == e["ak"]), None)
        if r:
            for cat, code, pm, top in e["issues"]:
                hit = evaluer(code, r)
                if hit is not None:
                    compter(hist["bilan"], cat, pm / 1000, hit, top)
            hist["bilan"]["matchs"] += 1
            del hist["en_attente"][id_]
        elif (now - debut).days > 10:
            del hist["en_attente"][id_]  # match reporté ou résultat introuvable


def memoriser(hist, matchs, now):
    """Garde la dernière prévision avant le coup d'envoi (la plus informée)."""
    for m in matchs:
        if m["date"] > now:
            tops = {c for _, _, _, c in top_issues(m["issues"])}
            hist["en_attente"][m["id"]] = {
                "ligue": m["ligue"], "hk": m["hk"], "ak": m["ak"], "debut": m["date"].isoformat(),
                "issues": [[cat, code, int(p * 1000), int(code in tops)]
                           for cat, _, p, code in m["issues"] if p >= 0.5 and cat != "Score exact"]}


def sauver_hist(hist):
    os.makedirs("docs", exist_ok=True)
    tmp = FICHIER_HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, FICHIER_HIST)


def bloc_bilan(b):
    n_top = sum(v[0] for v in b["top"].values())
    h = ["<div class='card'><div class='t'>Bilan de fiabilité</div>"]
    if n_top < 30:
        h.append(f"<div class='lg'>En construction : {b['matchs']} match(s) vérifié(s) depuis le {b['depuis']}. "
                 "Les premiers chiffres fiables arrivent après quelques jours de matchs.</div></div>")
        return "".join(h)
    ok = sum(v[1] for v in b["top"].values())
    ann = sum(v[2] for v in b["top"].values()) / n_top
    h.append(f"<div class='box'><b>Issues les plus probables affichées</b>Annoncé en moyenne {pct(ann)} · "
             f"réalisé {pct(ok / n_top)} sur {n_top} prévisions ({b['matchs']} matchs, depuis le {b['depuis']}).</div>")
    h.append("<details><summary>Détail par niveau de probabilité</summary><table>"
             "<tr><td class='lg'>Annoncé</td><td class='lg'>Prévisions</td><td class='lg'>Réalisé</td></tr>")
    for tr in sorted(b["all"]):
        n, hits, sp = b["all"][tr]
        if n >= 10:
            h.append(f"<tr><td>{pct(sp / n)}</td><td>{n}</td><td>{pct(hits / n)}</td></tr>")
    h.append("</table><div class='cat'>Par catégorie (toutes prévisions ≥ 50 %)</div><table>")
    for cat, (n, hits, sp) in sorted(b["cat"].items()):
        if n >= 10:
            h.append(f"<tr><td>{html.escape(cat)}</td><td>{pct(sp / n)} annoncé</td><td>{pct(hits / n)} réalisé</td></tr>")
    h.append("</table></details>"
             "<div class='lg'>Plus l’annoncé et le réalisé sont proches, plus la page est fiable. "
             "Les issues d’un même match sont liées : il faut plusieurs semaines pour conclure.</div></div>")
    return "".join(h)

# ---------------------------------------------------------------- page
CSS = """:root{--bg:#EDF1F5;--card:#fff;--ink:#0F1B2D;--mute:#5B6878;--line:#D5DCE5;--good:#0F7A55;--goodbg:#DDF3EA}
@media(prefers-color-scheme:dark){:root{--bg:#0C141E;--card:#152230;--ink:#E8EEF5;--mute:#93A2B4;--line:#27384A;--good:#4FD1A0;--goodbg:#10392D}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:560px;margin:0 auto;padding:18px 14px 40px}h1{font-size:25px;margin:6px 0 2px}.sub,.note{color:var(--mute);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px;margin:12px 0}.lg{font-size:12px;color:var(--mute)}
.t{font-weight:700;font-size:17px;margin:2px 0}.box{background:var(--goodbg);border-radius:10px;padding:9px 10px;margin:8px 0}
.box b{display:block;margin-bottom:4px}table{width:100%;border-collapse:collapse;font-size:14px}
td{padding:6px 0;border-top:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}td:first-child{text-align:left}
.box td{border-color:rgba(127,127,127,.25)}summary{padding:8px 0;color:var(--mute);font-size:13px}.cat{font-weight:700;font-size:14px;margin-top:8px}"""


def pct(x):
    return f"{x * 100:.0f} %"


def top_issues(issues):
    par_cat = {}
    for cat, nom, p, code in issues:
        if cat in ("Score exact",) or not 0.60 <= p <= 0.90:
            continue
        par_cat.setdefault(cat, []).append((p, nom, code))
    choix = []
    for cat, lst in par_cat.items():
        for p, nom, code in sorted(lst, reverse=True)[:2]:
            choix.append((p, nom, cat, code))
    return sorted(choix, reverse=True)[:8]


def rendre(matchs, erreurs, bilan=None):
    matchs.sort(key=lambda m: m["date"])
    maj = datetime.now(TZ).strftime("%d/%m/%Y à %Hh%M")
    out = [f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Analyse des matchs</title><style>{CSS}</style></head><body><main><h1>Analyse des matchs</h1>"
           f"<p class='sub'>Mise à jour le {maj} · {len(matchs)} matchs des {JOURS_MAX} prochains jours</p>"]
    if bilan is not None:
        out.append(bloc_bilan(bilan))
    for m in matchs:
        d = m["date"].astimezone(TZ).strftime("%d/%m %Hh%M")
        f = m["h2h"]
        if f["n"] >= 2:
            fa = (f"Face-à-face ({f['n']} matchs) : {f['v']} victoire(s) dom., {f['nul']} nul(s), {f['d']} victoire(s) ext. · "
                  f"{f['buts']:.1f} buts/match · les 2 marquent {pct(f['btts'])}")
        else:
            fa = "Face-à-face : pas assez de matchs récents."
        out.append(f"<div class='card'><div class='lg'>{html.escape(m['ligue'])} · {d}</div>"
                   f"<div class='t'>{html.escape(m['h'])} – {html.escape(m['a'])}</div>"
                   f"<div class='lg'>Forme (5 derniers) : domicile {m['forme_h'] or '–'} · extérieur {m['forme_a'] or '–'}</div>"
                   f"<div class='lg'>{fa}</div>"
                   f"<div class='lg'>Buts attendus {m['ld']:.1f} – {m['le']:.1f} · Fiabilité des stats : {m['fiab']}"
                   f"{' · avis du marché pris en compte' if m['marche'] else ' · marché indisponible'}</div>")
        out.append("<div class='box'><b>Les issues les plus probables</b><table>")
        for p, nom, cat, _ in top_issues(m["issues"]):
            out.append(f"<tr><td>{html.escape(nom)}</td><td>{pct(p)}</td></tr>")
        out.append("</table></div><details><summary>Voir toutes les issues</summary>")
        cats = {}
        for cat, nom, p, _ in m["issues"]:
            cats.setdefault(cat, []).append((nom, p))
        for cat, lst in cats.items():
            out.append(f"<div class='cat'>{html.escape(cat)}</div><table>")
            for nom, p in lst:
                out.append(f"<tr><td>{html.escape(nom)}</td><td>{pct(p)}</td></tr>")
            out.append("</table>")
        out.append("</details></div>")
    if not matchs:
        out.append("<p>Aucun match à analyser pour le moment.</p>")
    if erreurs:
        out.append("<p class='note'>Remarques : " + html.escape(" ; ".join(sorted(set(erreurs))[:8])) + "</p>")
    out.append("<p class='note'>Probabilités estimées à partir de la force des équipes, de leur forme, des face-à-face et de l’avis du marché "
               "(qui intègre en partie blessures et compositions). Les blessures ne sont pas lues directement : vérifie les compos avant de miser. "
               "Probable ne veut pas dire rentable : compare toujours avec la cote 1xBet. Corners, cartons et fautes sont des estimations simples. "
               "Pas de données gratuites pour les touches, les joueurs ou les minutes de but. "
               "Ne mise que ce que tu peux te permettre de perdre. Aide : joueurs-info-service.fr (09 74 75 13 13).</p></main></body></html>")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write("".join(out))


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("Variable ODDS_API_KEY manquante.")
    maintenant = datetime.now(timezone.utc)
    sortie, erreurs, rows_ligue = analyser()
    hist = charger_hist(maintenant)
    regler(hist, rows_ligue, maintenant)
    memoriser(hist, sortie, maintenant)
    sauver_hist(hist)
    rendre(sortie, erreurs, hist["bilan"])
    print("docs/index.html généré")
