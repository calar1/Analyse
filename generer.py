"""
Génère docs/index.html : analyse automatique des matchs à venir.
Cotes : The Odds API (clé dans la variable ODDS_API_KEY)
Stats : football-data.co.uk
"""
import csv, difflib, html, io, json, os, re, unicodedata, urllib.request
from datetime import datetime, timezone
from math import exp, factorial
from zoneinfo import ZoneInfo

API_KEY = os.environ.get("ODDS_API_KEY", "")
PREFERE = os.environ.get("BOOKMAKER", "onexbet")   # bookmaker prioritaire si présent
REGIONS = "eu"
SEUIL = 0.04          # avantage minimum pour signaler un pari
EV_MAX = 0.10         # au-delà : écart suspect, jamais présenté comme un pari
COTE_MAX = 3.5        # on ne propose pas de pari au-dessus de cette cote
JOURS_MAX = 3         # seulement les matchs des 3 prochains jours
POIDS_MODELE = 0.30   # proba finale = 30 % modèle + 70 % consensus du marché
TZ = ZoneInfo("Europe/Paris")
MAX_BUTS = 10
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


def lire_main(code, now):
    res = []
    for dec in (0, 1):
        try:
            t = get(f"https://www.football-data.co.uk/mmz4281/{saison_code(now, dec)}/{code}.csv")
        except Exception:
            continue
        for r in csv.DictReader(io.StringIO(t)):
            if r.get("FTHG") and r.get("HomeTeam"):
                res.append((r["HomeTeam"], r["AwayTeam"], int(r["FTHG"]), int(r["FTAG"])))
        if dec == 0 and len(res) >= 80:
            break  # assez de matchs cette saison
    return res


def lire_extra(code):
    rows = [r for r in csv.DictReader(io.StringIO(get(f"https://www.football-data.co.uk/new/{code}.csv")))
            if r.get("HG") not in (None, "")]
    saisons = sorted({r["Season"] for r in rows})
    garde = set(saisons[-1:])
    cur = [r for r in rows if r["Season"] in garde]
    if len(cur) < 80 and len(saisons) > 1:
        garde.add(saisons[-2])
    return [(r["Home"], r["Away"], int(float(r["HG"])), int(float(r["AG"])))
            for r in rows if r["Season"] in garde]


def forces(matchs):
    moy_d = sum(m[2] for m in matchs) / len(matchs)
    moy_e = sum(m[3] for m in matchs) / len(matchs)
    eq, K = {}, 6
    for h, a, gh, ga in matchs:
        for t in (h, a):
            eq.setdefault(t, [0, 0, 0, 0, 0, 0])
        eq[h][0] += 1; eq[h][1] += gh; eq[h][2] += ga
        eq[a][3] += 1; eq[a][4] += ga; eq[a][5] += gh
    f = {}
    for t, (dn, dm, de, en, em, ee) in eq.items():
        f[norm(t)] = {
            "att_d": ((dm + K * moy_d) / (dn + K)) / moy_d,
            "def_d": ((de + K * moy_e) / (dn + K)) / moy_e,
            "att_e": ((em + K * moy_e) / (en + K)) / moy_e,
            "def_e": ((ee + K * moy_d) / (en + K)) / moy_d,
        }
    return moy_d, moy_e, f


def trouver(nom, f):
    n = norm(nom)
    n = ALIAS.get(n, n)
    if n in f:
        return n
    r = difflib.get_close_matches(n, list(f), n=1, cutoff=0.6)
    return r[0] if r else None


def pois(k, lam):
    return exp(-lam) * lam ** k / factorial(k)


def probas(ld, le):
    p = dict.fromkeys(["1", "X", "2", "Over", "Under", "BTTS"], 0.0)
    for i in range(MAX_BUTS + 1):
        for j in range(MAX_BUTS + 1):
            pr = pois(i, ld) * pois(j, le)
            p["1" if i > j else "X" if i == j else "2"] += pr
            p["Over" if i + j > 2 else "Under"] += pr
            if i and j:
                p["BTTS"] += pr
    return p


def cotes(match):
    pref, best = {}, {}
    for bk in match.get("bookmakers", []):
        for mk in bk["markets"]:
            for o in mk["outcomes"]:
                if mk["key"] == "h2h":
                    cle = "1" if o["name"] == match["home_team"] else "2" if o["name"] == match["away_team"] else "X"
                elif mk["key"] == "totals" and o.get("point") == 2.5:
                    cle = o["name"]
                else:
                    continue
                cible = pref if bk["key"] == PREFERE else best
                if o["price"] > cible.get(cle, (0, ""))[0]:
                    cible[cle] = (o["price"], bk["title"])
    return dict(pref)  # uniquement les cotes du bookmaker choisi (1xBet)


def marche(match):
    """Probabilités moyennes du marché, marge des bookmakers retirée."""
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
    for lst in (h2h, tot):
        if len(lst) >= 3:  # au moins 3 bookmakers pour un consensus fiable
            for k in lst[0]:
                res[k] = sum(x[k] for x in lst) / len(lst)
    return res


LABELS = {"1": "Victoire domicile", "X": "Match nul", "2": "Victoire extérieur",
          "Over": "Plus de 2.5 buts", "Under": "Moins de 2.5 buts"}


def analyser():
    now = datetime.now(timezone.utc)
    matchs_out, erreurs = [], []
    for sport, nom, source, code in LIGUES:
        try:
            stats = lire_main(code, now) if source == "main" else lire_extra(code)
            if len(stats) < 30:
                raise ValueError("pas assez de matchs joués")
            moy_d, moy_e, f = forces(stats)
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
            pr = probas(ld, le)
            lignes, mk = [], marche(m)
            for k, (cote, bk) in ((k, v) for k, v in cotes(m).items() if v):
                if k not in mk:
                    continue  # pas de consensus : on ne propose rien
                p = POIDS_MODELE * pr[k] + (1 - POIDS_MODELE) * mk[k]
                lignes.append({"k": k, "p": p, "c": cote, "bk": bk, "ev": p * cote - 1})
            lignes.sort(key=lambda x: -x["ev"])
            if not lignes:
                continue  # pas de cote 1xBet / pas de consensus
            matchs_out.append({"ligue": nom, "h": m["home_team"], "a": m["away_team"],
                               "date": datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00")),
                               "ld": ld, "le": le, "btts": pr["BTTS"], "lignes": lignes})
    return matchs_out, erreurs


CSS = """:root{--bg:#EDF1F5;--card:#fff;--ink:#0F1B2D;--mute:#5B6878;--line:#D5DCE5;--good:#0F7A55;--goodbg:#DDF3EA;--bad:#B93434}
@media(prefers-color-scheme:dark){:root{--bg:#0C141E;--card:#152230;--ink:#E8EEF5;--mute:#93A2B4;--line:#27384A;--good:#4FD1A0;--goodbg:#10392D;--bad:#F07878}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:560px;margin:0 auto;padding:18px 14px 40px}h1{font-size:25px;margin:6px 0 2px}.sub,.note{color:var(--mute);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px;margin:12px 0}.card.v{border:2px solid var(--good)}
.lg{font-size:12px;color:var(--mute)}.t{font-weight:700;font-size:17px;margin:2px 0}.pick{background:var(--goodbg);border-radius:10px;padding:9px 10px;margin:8px 0;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}td,th{padding:6px 0;border-top:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
th{font-size:11px;color:var(--mute);font-weight:600;border:0}td:first-child,th:first-child{text-align:left}.pos{color:var(--good);font-weight:800}.neg{color:var(--bad)}"""


def pct(x):
    return f"{x * 100:.0f} %".replace("-", "−")


def rendre(matchs, erreurs):
    for m in matchs:
        m["pari"] = next((l for l in m["lignes"] if SEUIL <= l["ev"] <= EV_MAX and l["c"] <= COTE_MAX), None)
    matchs.sort(key=lambda m: (0 if m["pari"] else 1, -(m["pari"]["ev"] if m["pari"] else 0), m["date"]))
    nb = sum(1 for m in matchs if m["pari"])
    maj = datetime.now(TZ).strftime("%d/%m/%Y à %Hh%M")
    out = [f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Analyse des matchs</title><style>{CSS}</style></head><body><main><h1>Analyse des matchs</h1>"
           f"<p class='sub'>Mise à jour le {maj} · {len(matchs)} matchs · {nb} pari(s) avec avantage</p>"]
    for m in matchs:
        best = m["pari"]
        v = best is not None
        d = m["date"].astimezone(TZ).strftime("%d/%m %Hh%M")
        out.append(f"<div class='card{' v' if v else ''}'><div class='lg'>{html.escape(m['ligue'])} · {d}</div>"
                   f"<div class='t'>{html.escape(m['h'])} – {html.escape(m['a'])}</div>"
                   f"<div class='lg'>Buts attendus {m['ld']:.1f} – {m['le']:.1f} · Les 2 marquent {pct(m['btts'])}</div>")
        if v:
            out.append(f"<div class='pick'>Meilleur pari : {LABELS[best['k']]} à {best['c']:.2f} "
                       f"({html.escape(best['bk'])}) · avantage +{pct(best['ev'])}</div>")
        else:
            out.append("<div class='lg' style='margin:6px 0'>Pas de pari intéressant sur ce match.</div>")
        out.append("<table><tr><th>Issue</th><th>Proba</th><th>Cote</th><th>Avantage</th></tr>")
        for l in m["lignes"]:
            cl = "pos" if m["pari"] is l else "neg" if l["ev"] < 0 else ""
            suspect = " ?" if l["ev"] > EV_MAX else ""
            out.append(f"<tr><td>{LABELS[l['k']]}</td><td>{pct(l['p'])}</td><td>{l['c']:.2f}</td>"
                       f"<td class='{cl}'>{'+' if l['ev'] > 0 else ''}{pct(l['ev'])}{suspect}</td></tr>")
        out.append("</table></div>")
    if not matchs:
        out.append("<p>Aucun match analysé pour le moment.</p>")
    if erreurs:
        out.append("<p class='note'>Remarques : " + html.escape(" ; ".join(sorted(set(erreurs))[:8])) + "</p>")
    out.append("<p class='note'>Estimation statistique, jamais une garantie de gain. Proba = 30 % modèle + 70 % marché. "
               "Un « ? » signale un écart suspect (probable info ignorée : blessures, rotations) : ce n’est pas un pari. "
               "Vérifie toujours les compos avant de miser. "
               "Ne mise que ce que tu peux te permettre de perdre. Aide : joueurs-info-service.fr (09 74 75 13 13).</p>"
               "</main></body></html>")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write("".join(out))


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("Variable ODDS_API_KEY manquante.")
    rendre(*analyser())
    print("docs/index.html généré")
