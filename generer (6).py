"""
Génère docs/index.html : analyse approfondie des matchs à venir (sans cotes).
Prend en compte : force des équipes (domicile/extérieur), forme récente,
face-à-face sur 5 saisons, et avis du marché (qui intègre blessures et compos).
Données : football-data.co.uk (stats) + The Odds API (calendrier et avis du marché).
"""
import csv, difflib, html, io, json, os, re, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone
from math import exp, factorial, log, sqrt
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


def odds_json(sport, now):
    """Cotes des matchs des prochains jours. Ne consomme des crédits que s'il y a des matchs proches."""
    base = "https://api.the-odds-api.com/v4/sports/" + sport
    ev = json.loads(get(f"{base}/events?apiKey={API_KEY}"))
    limite = JOURS_MAX * 86400
    if not any(0 <= (datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - now).total_seconds() <= limite
               for e in ev):
        return []
    fin = (now + timedelta(days=JOURS_MAX)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.loads(get(f"{base}/odds/?apiKey={API_KEY}&regions={REGIONS}&markets=h2h,totals"
                          f"&oddsFormat=decimal&commenceTimeTo={fin}"))


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
                "att_e": ((em + K * moy_e) / (en + K)) / moy_e, "def_e": ((ee + K * moy_d) / (en + K)) / moy_d,
                "ng_att_d": dm + K * moy_d, "ng_def_d": de + K * moy_e,
                "ng_att_e": em + K * moy_e, "ng_def_e": ee + K * moy_d}
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


def alerte_marche(pre, mk, m):
    """Détecte un écart marché / statistiques : signe probable d'une info absente de nos données."""
    if not all(k in mk for k in ("1", "X", "2", "Over")):
        return None
    d = {k: mk[k] - pre[k] for k in ("1", "2", "Over")}
    k = max(d, key=lambda x: abs(d[x]))
    if abs(d[k]) < 0.10:
        return None
    pts = f"{abs(d[k]) * 100:.0f}"
    if k == "Over":
        return f"Le marché prévoit {'plus' if d[k] > 0 else 'moins'} de buts que les statistiques ({pts} points d’écart)."
    eq = m["home_team"] if k == "1" else m["away_team"]
    sens = "mieux placé" if d[k] > 0 else "moins bien placé"
    return (f"Le marché voit {eq} {sens} que les statistiques ({pts} points d’écart sur sa victoire) : "
            "une information absente de nos données (blessure, composition, enjeu) est probable.")


def fourchette(ld, le, sd, se):
    """Plage probable (±1 écart-type) due au nombre limité de buts observés."""
    vals = [probas(ld * exp(a * sd), le * exp(b * se)) for a in (-1, 1) for b in (-1, 1)]
    return {k: (min(v[k] for v in vals), max(v[k] for v in vals)) for k in ("1", "X", "2")}


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
    tot = ld + le
    add("Scénarios", "Le domicile marque le premier but", ld / tot * (1 - exp(-tot)), "N:1")
    add("Scénarios", "L’extérieur marque le premier but", le / tot * (1 - exp(-tot)), "N:2")
    add("Scénarios", "Au moins un but dans le premier quart d’heure (estimé)", 1 - exp(-tot * 0.14), "N:q1")
    add("Scénarios", "Au moins un but dans le dernier quart d’heure (estimé)", 1 - exp(-tot * 0.19), "N:q6")
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
        mf = mt_ft(ld, le, part)
        for (a, b_), p in sorted(mf.items(), key=lambda x: -x[1])[:3]:
            add("Mi-temps", f"Mi-temps / fin : {NOMS_RES[a]} / {NOMS_RES[b_]}", p, f"MF:{a}{b_}")
        add("Scénarios", "Le domicile gagne après avoir été mené à la mi-temps", mf.get(("2", "1"), 0), "MF:21")
        add("Scénarios", "L’extérieur gagne après avoir été mené à la mi-temps", mf.get(("1", "2"), 0), "MF:12")
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
            data = odds_json(sport, now)
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
            mk = marche(m)
            pre = probas(ld, le)
            ld, le, avec_marche = ajuster(ld, le, mk)
            alerte = alerte_marche(pre, mk, m)
            sd = sqrt(1 / f[h]["ng_att_d"] + 1 / f[a]["ng_def_e"])
            se_ = sqrt(1 / f[a]["ng_att_e"] + 1 / f[h]["ng_def_d"])
            inc = fourchette(ld, le, sd, se_)

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
                           "marche": avec_marche, "alerte": alerte, "inc": inc,
                           "fiab": "bonne" if n_min >= 15 else "moyenne" if n_min >= 8 else "faible",
                           "issues": issues(ld, le, part, total("co"), total("ca"), total("fo"))})
    return sortie, erreurs, rows_ligue


# ---------------------------------------------------------------- sélections nationales
ALIAS_SEL = {"usa": "united states", "united states of america": "united states", "korea republic": "south korea",
             "cote divoire": "ivory coast", "ir iran": "iran", "czechia": "czech republic", "turkiye": "turkey",
             "cape verde islands": "cape verde", "republic of korea": "south korea", "congo dr": "dr congo",
             "ireland": "republic of ireland", "china": "china pr", "north macedonia": "macedonia",
             "eswatini": "swaziland", "myanmar": "burma"}
MOTS_INTER = ("world_cup", "nations_league", "friendl", "qualif", "euro", "copa_america", "africa_cup",
              "asian_cup", "gold_cup", "international")
MOTS_EXCLUS = ("champs_league", "champions_league", "europa", "conference", "club", "women", "u20", "u21", "u23",
               "olympic", "mls", "league_cup", "_winner")
JEUNES = re.compile(r"\b(u\d\d|women|w|olympic)\b")


def lire_selections():
    t = get("https://raw.githubusercontent.com/martj42/international_results/master/results.csv")
    rows = []
    for r in csv.DictReader(io.StringIO(t)):
        if r["date"] < "2000-01-01":
            continue
        hs, as_ = num(r["home_score"]), num(r["away_score"])
        if hs is None or as_ is None:
            continue
        x = ligne(r["home_team"], r["away_team"], (int(hs), int(as_)))
        x.update({"d": r["date"], "t": r["tournament"], "n": r["neutral"].upper() == "TRUE"})
        rows.append(x)
    rows.sort(key=lambda x: x["d"])
    return rows


def coef_k(t):
    if "qualification" in t:
        return 40
    if t == "Friendly":
        return 20
    if t == "FIFA World Cup":
        return 60
    if any(w in t for w in ("UEFA Euro", "Copa Am", "African Cup", "AFC Asian", "Gold Cup", "Confederations")):
        return 50
    return 30


def calculer_elo(rows):
    """Classement Elo des sélections + données pour caler le lien écart Elo -> buts."""
    R, donnees = {}, []
    for r in rows:
        h, a = r["hn"], r["an"]
        rh, ra = R.get(h, 1500), R.get(a, 1500)
        d_elo = rh + (0 if r["n"] else 100) - ra
        gh, ga = r["g"]
        if r["d"] >= "2014-01-01":
            donnees.append((d_elo / 400, gh, ga))
        e = 1 / (1 + 10 ** (-d_elo / 400))
        w = 1 if gh > ga else 0.5 if gh == ga else 0
        dg = abs(gh - ga)
        g = 1 if dg <= 1 else 1.5 if dg == 2 else (11 + dg) / 8
        delta = coef_k(r["t"]) * g * (w - e)
        R[h], R[a] = rh + delta, ra - delta
    return R, donnees


def caler_modele(donnees):
    """Cherche (buts moyens par équipe G, sensibilité c) qui collent le mieux aux résultats passés."""
    best = None
    for G in [1.1 + 0.05 * i for i in range(11)]:
        for c in [0.2 + 0.05 * i for i in range(14)]:
            ll = 0.0
            for d, gh, ga in donnees:
                lh, la = G * exp(c * d), G * exp(-c * d)
                ll += gh * log(lh) - lh + ga * log(la) - la
            if best is None or ll > best[0]:
                best = (ll, G, c)
    return best[1], best[2]


def competitions_actives():
    liste = json.loads(get(f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"))
    res = []
    for sp in liste:
        txt = " ".join([sp.get("key", ""), sp.get("title", ""), sp.get("description", "")]).lower()
        if sp.get("key", "").startswith("soccer_") and not sp.get("has_outrights") \
                and any(w in txt for w in MOTS_INTER) and not any(w in txt for w in MOTS_EXCLUS):
            res.append((sp["key"], sp.get("title") or sp["key"]))
    return res


def trouver_sel(nom, R):
    if JEUNES.search(nom.lower()):
        return None
    n = norm(nom)
    n = ALIAS_SEL.get(n, n)
    if n in R:
        return n
    r = difflib.get_close_matches(n, list(R), n=1, cutoff=0.85)
    return r[0] if r else None


def analyser_selections(now, erreurs, rows_ligue):
    sortie = []
    try:
        rows = lire_selections()
        R, donnees = calculer_elo(rows)
        G, c = caler_modele(donnees)
        competitions = competitions_actives()
    except Exception as e:
        erreurs.append(f"Sélections nationales : indisponible ({str(e)[:60]})")
        return sortie
    rows_ligue["Sélections"] = rows
    base = [r for r in rows if r["d"] >= f"{now.year - 4}-01-01"]
    for cle, titre in competitions:
        try:
            data = odds_json(cle, now)
        except Exception as e:
            erreurs.append(f"{titre} : indisponible ({str(e)[:60]})")
            continue
        for m in data:
            debut = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
            if not (0 <= (debut - now).total_seconds() <= JOURS_MAX * 86400):
                continue
            h, a = trouver_sel(m["home_team"], R), trouver_sel(m["away_team"], R)
            if not h or not a:
                continue  # jeunes, féminines ou équipe inconnue : ignorés
            amical = "friendl" in (cle + titre).lower()
            ha = 100 if any(w in (cle + titre).lower() for w in ("qualif", "nations")) else 50
            d = (R[h] + ha - R[a]) / 400
            ld, le = G * exp(c * d), G * exp(-c * d)
            fh, fa = facteurs_forme(base, h), facteurs_forme(base, a)
            ld, le = ld * fh[0] * fa[1], le * fa[0] * fh[1]
            h2h = face_a_face(rows, h, a)
            if h2h["n"] >= 3 and ld + le > 0:
                w = h2h["n"] / (h2h["n"] + 10)
                fac = min(max(1 + w * (h2h["buts"] / (ld + le) - 1), 0.92), 1.08)
                ld, le = ld * fac, le * fac
            mk = marche(m)
            pre = probas(ld, le)
            ld, le, avec_marche = ajuster(ld, le, mk)
            alerte = alerte_marche(pre, mk, m)
            n_min = min(fh[3], fa[3])
            sortie.append({"id": f"{titre}|{h}|{a}|{debut:%Y-%m-%d}", "hk": h, "ak": a, "src": "Sélections",
                           "ligue": titre, "h": m["home_team"], "a": m["away_team"], "date": debut,
                           "ld": ld, "le": le, "forme_h": fh[2], "forme_a": fa[2], "h2h": h2h,
                           "marche": avec_marche, "alerte": alerte, "inc": None, "elo": (R[h], R[a]),
                           "fiab": "faible" if amical or n_min < 8 else "bonne" if n_min >= 15 else "moyenne",
                           "issues": issues(ld, le, None, None, None, None)})
    return sortie


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


def compter(bilan, cat, p, hit, top, ligue=None):
    tr = str(min(int(p * 10), 9))
    for cle in ("all", "top") if top else ("all",):
        d = bilan[cle].setdefault(tr, [0, 0, 0.0])
        d[0] += 1; d[1] += int(hit); d[2] += p
    c = bilan["cat"].setdefault(cat, [0, 0, 0.0])
    c[0] += 1; c[1] += int(hit); c[2] += p
    if ligue:
        l = bilan.setdefault("lig", {}).setdefault(ligue, [0, 0, 0.0])
        l[0] += 1; l[1] += int(hit); l[2] += p


def regler(hist, rows_ligue, now):
    """Vérifie les prévisions dont le match est terminé (résultat présent dans les données)."""
    for id_, e in list(hist["en_attente"].items()):
        rows = rows_ligue.get(e.get("src", e["ligue"]))
        debut = datetime.fromisoformat(e["debut"])
        if rows is None or debut > now:
            continue
        seuil = (debut - timedelta(days=1)).strftime("%Y-%m-%d")
        r = next((x for x in reversed(rows) if x["hn"] == e["hk"] and x["an"] == e["ak"]
                  and x.get("d", seuil) >= seuil), None)
        if r is None and e.get("src") == "Sélections":  # lieu neutre : équipes parfois inversées
            x = next((x for x in reversed(rows) if x["hn"] == e["ak"] and x["an"] == e["hk"] and x["d"] >= seuil), None)
            if x:
                r = dict(x, g=(x["g"][1], x["g"][0]), ht=(None, None))
        if r:
            for cat, code, pm, top in e["issues"]:
                hit = evaluer(code, r)
                if hit is not None:
                    compter(hist["bilan"], cat, pm / 1000, hit, top, e["ligue"])
            hist["bilan"]["matchs"] += 1
            del hist["en_attente"][id_]
        elif (now - debut).days > 10:
            del hist["en_attente"][id_]  # match reporté ou résultat introuvable


def memoriser(hist, matchs, now):
    """Garde la dernière prévision avant le coup d'envoi (la plus informée)."""
    for m in matchs:
        if m["date"] > now:
            tops = {c for _, _, _, c in top_issues(m.get("issues_aff") or m["issues"])}
            hist["en_attente"][m["id"]] = {
                "ligue": m["ligue"], "src": m.get("src", m["ligue"]), "hk": m["hk"], "ak": m["ak"], "debut": m["date"].isoformat(),
                "issues": [[cat, code, int(p * 1000), int(code in tops)]
                           for cat, _, p, code in m["issues"] if p >= 0.5 and cat != "Score exact"]}


def sauver_hist(hist):
    os.makedirs("docs", exist_ok=True)
    tmp = FICHIER_HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, FICHIER_HIST)


def calibrer(p, bilan):
    """Corrige une probabilité d'après l'écart annoncé / réalisé observé dans le bilan (si assez de données)."""
    def brut(q):
        d = bilan.get("all", {}).get(str(min(int(q * 10), 9)))
        if d and d[0] >= 150:
            shift = (d[1] / d[0] - d[2] / d[0]) * d[0] / (d[0] + 300)
            return min(max(q + shift, 0.01), 0.99)
        return q
    return brut(p) if p >= 0.5 else 1 - brut(1 - p)


def calibrer_issues(issues, bilan):
    """Applique la correction du bilan, puis remet les 3 issues du 1X2 à un total de 100 %."""
    res = [(c, n, calibrer(p, bilan), code) for c, n, p, code in issues]
    tri = [i for i, x in enumerate(res) if x[3] in ("R:1", "R:X", "R:2")]
    tot = sum(res[i][2] for i in tri)
    if tot > 0:
        for i in tri:
            c, n, p, code = res[i]
            res[i] = (c, n, p / tot, code)
    return res


def bloc_bilan(b):
    n_top = sum(v[0] for v in b["top"].values())
    h = ["<div class='card' style='--acc:#7C3AED'><span class='chip'>📈 Bilan de fiabilité</span>"]
    if n_top < 30:
        h.append(f"<p class='lg'>En construction : {b['matchs']} match(s) vérifié(s) depuis le {b['depuis']}. "
                 "Les premiers chiffres fiables arrivent après quelques jours de matchs.</p></div>")
        return "".join(h)
    ok = sum(v[1] for v in b["top"].values())
    ann = sum(v[2] for v in b["top"].values()) / n_top
    h.append(f"<div class='stats'><div><b>{pct(ann)}</b><span>annoncé</span></div><em>→</em>"
             f"<div><b>{pct(ok / n_top)}</b><span>réalisé</span></div></div>"
             f"<p class='lg' style='text-align:center'>sur {n_top} prévisions · {b['matchs']} matchs · depuis le {b['depuis']}</p>")
    h.append("<details><summary>Détail du bilan</summary><div class='cat'>Par niveau de probabilité</div><table>"
             "<tr><td class='lg'>Annoncé</td><td class='lg'>Prévisions</td><td class='lg'>Réalisé</td></tr>")
    for tr in sorted(b["all"]):
        n, hits, sp = b["all"][tr]
        if n >= 10:
            h.append(f"<tr><td>{pct(sp / n)}</td><td>{n}</td><td>{pct(hits / n)}</td></tr>")
    h.append("</table><div class='cat'>Par catégorie</div><table>")
    for cat, (n, hits, sp) in sorted(b["cat"].items()):
        if n >= 10:
            h.append(f"<tr><td>{html.escape(cat)}</td><td>{pct(sp / n)} annoncé</td><td>{pct(hits / n)} réalisé</td></tr>")
    h.append("</table>")
    if b.get("lig"):
        h.append("<div class='cat'>Par compétition</div><table>")
        for lg, (n, hits, sp) in sorted(b["lig"].items()):
            if n >= 10:
                h.append(f"<tr><td>{html.escape(lg)}</td><td>{pct(sp / n)} annoncé</td><td>{pct(hits / n)} réalisé</td></tr>")
        h.append("</table>")
    h.append("</details><p class='lg'>Les probabilités affichées sont corrigées automatiquement dès que le bilan montre un écart durable. "
             "Plus l’annoncé et le réalisé sont proches, plus la page est fiable. Les issues d’un même match sont liées : "
             "il faut plusieurs semaines pour conclure.</p></div>")
    return "".join(h)


# ---------------------------------------------------------------- page
CSS = """:root{--bg:#F3F7F1;--card:#fff;--ink:#10231A;--mute:#5E7167;--line:#E1E9E2;--g:#12B76A;--g2:#0A7F4B;--chip:#E5F6EC;--warn:#FFF1CC;--warnl:#FFB020;--shadow:0 8px 24px rgba(16,35,26,.09)}
:root[data-theme=dark]{--bg:#07140E;--card:#0F2218;--ink:#EAF6EE;--mute:#8FB09C;--line:#1D3A2B;--g2:#3DD17A;--chip:#143222;--warn:#3A2D08;--shadow:0 8px 24px rgba(0,0,0,.45)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 Outfit,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:560px;margin:0 auto;padding-bottom:40px}
.hero{position:relative;overflow:hidden;color:#fff;padding:26px 18px 30px;border-radius:0 0 30px 30px;
background:linear-gradient(135deg,#054F2E 0%,#0A9955 55%,#3DD17A 100%)}
.hero:before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,rgba(255,255,255,.06) 0 38px,transparent 38px 76px)}
.hero:after{content:"";position:absolute;right:-70px;top:-60px;width:230px;height:230px;border:3px solid rgba(255,255,255,.2);border-radius:50%}
.hero>*{position:relative;z-index:1}.hero h1{font-size:30px;line-height:1.1;margin:0 0 6px;font-weight:800;letter-spacing:-.02em}
.hero p{margin:0;opacity:.92;font-size:14px}.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.pill{background:rgba(255,255,255,.2);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:600}
#th{position:absolute;right:14px;top:14px;z-index:2;width:40px;height:40px;border-radius:50%;border:0;background:rgba(255,255,255,.22);font-size:18px;cursor:pointer}
.filters{display:flex;gap:8px;overflow-x:auto;padding:14px 12px 4px;scrollbar-width:none}.filters::-webkit-scrollbar{display:none}
.f{flex:none;border:0;border-radius:999px;padding:8px 14px;font:600 14px Outfit,system-ui,sans-serif;background:var(--card);color:var(--ink);box-shadow:var(--shadow);cursor:pointer;transition:transform .15s}
.f:active{transform:scale(.95)}.f.on{background:var(--g2);color:#fff}
.card{background:var(--card);border-radius:20px;box-shadow:var(--shadow);padding:15px;margin:14px 12px;border-top:6px solid var(--acc,#12B76A);animation:up .55s cubic-bezier(.2,.8,.2,1) both}
@keyframes up{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@keyframes reveal{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@media(prefers-reduced-motion:reduce){*{animation:none!important}}
.chip{display:inline-block;color:#fff;border-radius:999px;padding:3px 11px;font-size:12px;font-weight:700;background:var(--acc,#12B76A)}
.when{font-size:13px;color:var(--mute);margin-left:6px}
.t{font-weight:800;font-size:21px;line-height:1.2;margin:8px 0 10px;letter-spacing:-.01em}
.lg,.note,.sub{color:var(--mute);font-size:13px}.note{padding:6px 16px}
.bar{display:flex;border-radius:12px;overflow:hidden;font-size:14px;font-weight:700;color:#fff;animation:reveal 1s cubic-bezier(.2,.8,.2,1) both}
.bar div{padding:9px 0;text-align:center;min-width:0}
.leg{display:flex;justify-content:space-between;font-size:12px;color:var(--mute);margin:4px 0 6px;font-weight:600}
.box{border-radius:14px;padding:11px 13px;margin:10px 0;background:var(--chip);border-left:5px solid var(--g)}
.box h3{margin:0 0 6px;font-size:15px;font-weight:800}.box ul{margin:0;padding-left:18px;font-size:14.5px}.box li{margin:3px 0}
.warn{background:var(--warn);border-left-color:var(--warnl)}
.ir{display:grid;grid-template-columns:1fr auto;gap:3px 10px;padding:7px 0;border-top:1px solid rgba(127,127,127,.18);font-size:14.5px}
.ir:first-of-type{border-top:0}.ir b{font-weight:800}.tr{grid-column:1/-1;height:7px;border-radius:4px;background:rgba(127,127,127,.2);overflow:hidden}
.tr i{display:block;height:100%;border-radius:4px;animation:reveal 1s cubic-bezier(.2,.8,.2,1) both}
.stats{display:flex;align-items:center;justify-content:space-around;text-align:center;margin:6px 0}
.stats b{display:block;font-size:30px;font-weight:800;line-height:1.1;color:var(--g2)}.stats span{font-size:12px;color:var(--mute)}.stats em{font-size:24px;color:var(--mute);font-style:normal}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 0;border-top:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}td:first-child{text-align:left}
summary{list-style:none;cursor:pointer;display:inline-block;padding:8px 14px;border-radius:999px;background:var(--chip);color:var(--g2);font-weight:700;font-size:13px;margin:8px 6px 4px 0}
summary::-webkit-details-marker{display:none}details[open]>summary{background:var(--g2);color:#fff}
.cat{font-weight:800;font-size:14px;margin-top:10px}.hm td{text-align:center;font-size:12px;border:1px solid var(--line);padding:6px 0}
ul{margin:4px 0;padding-left:18px}"""


def pct(x):
    return f"{x * 100:.0f}\u00a0%"


def top_issues(issues):
    par_cat = {}
    for cat, nom, p, code in issues:
        if cat in ("Score exact", "Scénarios") or not 0.60 <= p <= 0.90:
            continue
        par_cat.setdefault(cat, []).append((p, nom, code))
    choix = []
    for cat, lst in par_cat.items():
        for p, nom, code in sorted(lst, reverse=True)[:2]:
            choix.append((p, nom, cat, code))
    return sorted(choix, reverse=True)[:8]


def previsibilite(p1, px, p2):
    H = -sum(p * log(p) for p in (p1, px, p2) if p > 0)
    return round(100 * (1 - H / log(3)))


def resume(m, P, pv):
    p1, p2 = P["R:1"], P["R:2"]
    h, a = m["h"], m["a"]
    L = []
    if p1 >= 0.60:
        L.append(f"{h} est nettement favori ({pct(p1)} de victoire).")
    elif p2 >= 0.60:
        L.append(f"{a} est nettement favori ({pct(p2)} de victoire).")
    elif p1 - p2 > 0.15:
        L.append(f"{h} est favori ({pct(p1)}), mais rien n’est joué.")
    elif p2 - p1 > 0.15:
        L.append(f"{a} est favori ({pct(p2)}), mais rien n’est joué.")
    elif p1 - p2 > 0.05:
        L.append(f"{h} part légèrement favori, sans écart décisif.")
    elif p2 - p1 > 0.05:
        L.append(f"{a} part légèrement favori, sans écart décisif.")
    else:
        L.append("Match équilibré : aucun favori net.")
    t = m["ld"] + m["le"]
    L.append(f"Buts attendus : {t:.1f}, soit un match {'ouvert' if t >= 3.0 else 'plutôt fermé' if t <= 2.2 else 'au rythme moyen'}.")
    vh, va = m["forme_h"].count("V"), m["forme_a"].count("V")
    if m["forme_h"] and m["forme_a"] and abs(vh - va) >= 2:
        L.append(f"Forme : {h if vh > va else a} est en meilleure dynamique ({m['forme_h']} contre {m['forme_a']}).")
    f = m["h2h"]
    if f["n"] >= 4 and max(f["v"], f["d"]) / f["n"] >= 0.6:
        L.append(f"Historique : {h if f['v'] > f['d'] else a} a dominé les face-à-face récents ({f['n']} matchs).")
    if m.get("alerte"):
        L.append(m["alerte"])
    return L


COULEURS = {"Premier League": "#7C3AED", "Liga": "#FF6A00", "Bundesliga": "#E4002B", "Serie A": "#0A74DA",
            "Ligue 1": "#1B4FD8", "Primeira Liga": "#0A8F3C", "Premier-Liga russe": "#C62828"}


def barre(m, P):
    p1, px, p2 = P["R:1"], P["R:X"], P["R:2"]
    h = (f"<div class='bar'><div style='width:{p1 * 100:.1f}%;background:#12B76A'>{pct(p1)}</div>"
         f"<div style='width:{px * 100:.1f}%;background:#F5A800'>{pct(px)}</div>"
         f"<div style='width:{p2 * 100:.1f}%;background:#7C3AED'>{pct(p2)}</div></div>"
         f"<div class='leg'><span>{html.escape(m['h'])}</span><span>Nul</span><span>{html.escape(m['a'])}</span></div>")
    if m.get("inc"):
        i = m["inc"]
        h += ("<div class='lg'>Fourchette probable (selon le nombre de buts observés) : "
              f"dom. {i['1'][0] * 100:.0f}–{pct(i['1'][1])} · nul {i['X'][0] * 100:.0f}–{pct(i['X'][1])} · "
              f"ext. {i['2'][0] * 100:.0f}–{pct(i['2'][1])}</div>")
    return h


def carte_scores(ld, le):
    M = matrice(ld, le)
    mx = max(M[i][j] for i in range(5) for j in range(5))
    h = ["<table class='hm'><tr><td class='lg'>dom. \\ ext.</td>" + "".join(f"<td class='lg'>{j}</td>" for j in range(5)) + "</tr>"]
    for i in range(5):
        h.append(f"<tr><td class='lg'>{i}</td>")
        for j in range(5):
            p = M[i][j]
            h.append(f"<td style='background:rgba(18,183,106,{0.07 + 0.85 * p / mx:.2f})'>{'<1' if p < 0.01 else f'{p * 100:.0f}'}</td>")
        h.append("</tr>")
    h.append("</table><div class='lg'>Probabilité (en %) de chaque score exact.</div>")
    return "".join(h)


def ligne_issue(nom, p):
    c = "#12B76A" if p >= 0.80 else "#7ACB2F" if p >= 0.70 else "#F5A800"
    return (f"<div class='ir'><span>{html.escape(nom)}</span><b>{pct(p)}</b>"
            f"<div class='tr'><i style='width:{p * 100:.0f}%;background:{c}'></i></div></div>")


SCRIPT = """<script>
(function(){var d=document.documentElement;
try{var t=localStorage.getItem("theme");if(t)d.setAttribute("data-theme",t)}catch(e){}
var b=document.getElementById("th");
function ic(){b.textContent=d.getAttribute("data-theme")==="dark"?"☀️":"🌙"}ic();
b.onclick=function(){var n=d.getAttribute("data-theme")==="dark"?"light":"dark";d.setAttribute("data-theme",n);ic();
try{localStorage.setItem("theme",n)}catch(e){}};
var fs=document.querySelectorAll(".f");
fs.forEach(function(f){f.onclick=function(){fs.forEach(function(x){x.classList.remove("on")});f.classList.add("on");
var l=f.getAttribute("data-l");document.querySelectorAll(".card[data-l]").forEach(function(c){
c.style.display=(l==="*"||c.getAttribute("data-l")===l)?"":"none"})}});
})();
</script>"""


def rendre(matchs, erreurs, bilan=None):
    matchs.sort(key=lambda m: m["date"])
    maj = datetime.now(TZ).strftime("%d/%m à %Hh%M")
    ligues = []
    for m in matchs:
        if m["ligue"] not in ligues:
            ligues.append(m["ligue"])
    signaux = [m for m in matchs if m.get("alerte")]
    out = ["<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<link rel='preconnect' href='https://fonts.googleapis.com'><link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
           "<link href='https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap' rel='stylesheet'>"
           f"<title>Analyse des matchs</title><style>{CSS}</style></head><body><main>"
           "<header class='hero'><button id='th' aria-label='Changer de thème'>🌙</button>"
           "<h1>⚽ Analyse<br>des matchs</h1><p>Probabilités, forme, face-à-face et avis du marché.</p>"
           f"<div class='pills'><span class='pill'>{len(matchs)} matchs</span><span class='pill'>{JOURS_MAX} prochains jours</span>"
           f"<span class='pill'>Mis à jour le {maj}</span></div></header>"]
    if len(ligues) > 1:
        out.append("<div class='filters'><button class='f on' data-l='*'>Tous</button>"
                   + "".join(f"<button class='f' data-l='{html.escape(l, quote=True)}'>{html.escape(l)}</button>" for l in ligues) + "</div>")
    if signaux:
        out.append("<div class='card' style='--acc:#FFB020'><span class='chip' style='background:#E58E00'>⚡ Signaux du jour</span>"
                   "<p class='lg'>Matchs où le marché voit autre chose que les statistiques : une info (blessure, compo) nous échappe peut-être.</p><ul>"
                   + "".join(f"<li><b>{html.escape(m['h'])} – {html.escape(m['a'])}</b></li>" for m in signaux) + "</ul></div>")
    if bilan is not None:
        out.append(bloc_bilan(bilan))
    for k, m in enumerate(matchs):
        iss = m.get("issues_aff") or m["issues"]
        P = {code: p for _, _, p, code in iss}
        d = m["date"].astimezone(TZ).strftime("%d/%m · %Hh%M")
        f = m["h2h"]
        if f["n"] >= 2:
            fa = (f"Face-à-face ({f['n']} matchs) : {f['v']} victoire(s) dom., {f['nul']} nul(s), {f['d']} victoire(s) ext. · "
                  f"{f['buts']:.1f} buts/match · les 2 marquent {pct(f['btts'])}")
        else:
            fa = "Face-à-face : pas assez de matchs récents."
        pv = previsibilite(P["R:1"], P["R:X"], P["R:2"])
        acc = COULEURS.get(m["ligue"], "#E58E00")
        out.append(f"<div class='card' data-l='{html.escape(m['ligue'], quote=True)}' style='--acc:{acc};animation-delay:{min(k, 8) * 70}ms'>"
                   f"<span class='chip'>{html.escape(m['ligue'])}</span><span class='when'>{d}</span>"
                   f"<div class='t'>{html.escape(m['h'])} – {html.escape(m['a'])}</div>" + barre(m, P) +
                   f"<div class='lg'>Prévisibilité du résultat : <b>{pv}/100</b> "
                   f"({'favori net' if pv >= 25 else 'issue assez incertaine' if pv >= 5 else 'match très ouvert'}) · "
                   f"fiabilité des stats : {m['fiab']}</div>")
        out.append("<div class='box'><h3>🧠 Ce que dit l’analyse</h3><ul>"
                   + "".join(f"<li>{html.escape(x)}</li>" for x in resume(m, P, pv)) + "</ul></div>")
        out.append("<div class='box' style='border-left-color:#F5A800'><h3>🎯 Les issues les plus probables</h3>"
                   + "".join(ligne_issue(nom, p) for p, nom, cat, _ in top_issues(iss)) + "</div>")
        out.append("<details><summary>📋 Forme, face-à-face, scores</summary>"
                   f"<div class='lg'>Forme (5 derniers) : domicile {m['forme_h'] or '–'} · extérieur {m['forme_a'] or '–'}</div>"
                   f"<div class='lg'>{fa}</div>"
                   + (f"<div class='lg'>Classement Elo : {m['elo'][0]:.0f} – {m['elo'][1]:.0f}</div>" if m.get("elo") else "") +
                   f"<div class='lg'>Buts attendus {m['ld']:.1f} – {m['le']:.1f}"
                   f"{' · avis du marché pris en compte' if m['marche'] else ' · marché indisponible'}</div>"
                   + carte_scores(m["ld"], m["le"]) + "</details>"
                   "<details><summary>📊 Toutes les issues</summary>")
        cats = {}
        for cat, nom, p, _ in iss:
            cats.setdefault(cat, []).append((nom, p))
        for cat, lst in cats.items():
            out.append(f"<div class='cat'>{html.escape(cat)}</div><table>")
            for nom, p in lst:
                out.append(f"<tr><td>{html.escape(nom)}</td><td>{pct(p)}</td></tr>")
            out.append("</table>")
        out.append("</details></div>")
    if not matchs:
        out.append("<div class='card'><div class='t'>Pas de match à analyser pour le moment</div>"
                   "<p class='lg'>Reviens bientôt : la page se met à jour chaque matin.</p></div>")
    if erreurs:
        out.append("<p class='note'>Remarques : " + html.escape(" ; ".join(sorted(set(erreurs))[:8])) + "</p>")
    out.append("<p class='note'>Probabilités estimées à partir de la force des équipes, de leur forme, des face-à-face et de l’avis du marché "
               "(qui intègre en partie blessures et compositions), puis corrigées par le bilan quand il est assez fourni. "
               "Les blessures ne sont pas lues directement : vérifie les compos avant de miser. "
               "Les scénarios (premier but, quarts d’heure) reposent sur des hypothèses simples et ne sont pas encore vérifiés par le bilan. "
               "Probable ne veut pas dire rentable : compare toujours avec la cote 1xBet. Corners, cartons et fautes sont des estimations simples. "
               "Matchs amicaux : compositions très changeantes, fiabilité réduite. Pas de données gratuites pour les touches, les joueurs ou les minutes de but. "
               "Ne mise que ce que tu peux te permettre de perdre. Aide : joueurs-info-service.fr (09 74 75 13 13).</p>"
               + SCRIPT + "</main></body></html>")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write("".join(out))


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("Variable ODDS_API_KEY manquante.")
    maintenant = datetime.now(timezone.utc)
    sortie, erreurs, rows_ligue = analyser()
    sortie += analyser_selections(maintenant, erreurs, rows_ligue)
    hist = charger_hist(maintenant)
    for m in sortie:
        m["issues_aff"] = calibrer_issues(m["issues"], hist["bilan"])
    regler(hist, rows_ligue, maintenant)
    memoriser(hist, sortie, maintenant)
    sauver_hist(hist)
    rendre(sortie, erreurs, hist["bilan"])
    print("docs/index.html généré")
