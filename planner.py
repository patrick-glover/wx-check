#!/usr/bin/env python3
"""Saturday itinerary planner.

Places are data: each one says what it *is* (outdoor? tall trees? surface? by the
river? hours?). Rules turn those features into requirements on the weather/road
facts. The planner enumerates every walk x duration x lunch combination, simulates
the timeline from where you are, drops anything that breaks a rule or misses the
2:00 tour, and ranks the rest.

To add a contingency: add a place to PLACES. validate() refuses it if a feature is
missing, so a new place can't silently skip the wind or mud rules.

Unknown facts (a check failed) FAIL the rules that depend on them. The reasons
say so, and the next 30-min run usually clears it.
"""
import math

# ---------------------------------------------------------------- thresholds
GUST_MAX = 30        # mph, tall trees on saturated ground
WIND_MAX = 25        # mph sustained
RAIN_PACKED = 1.0    # in, Sat daytime: packed/boardwalk trail still fine below this
RAIN_DIRT = 0.5      # in, dirt trails turn to mud above this
TOUR_AT = 14 * 60    # 2:00 pm chocolate tour, hard anchor
TOUR_ARRIVE_BY = TOUR_AT - 5
TOUR_LEN = 30
CABIN_TARGET = 17 * 60
BARLOW_MAX = 90

# ---------------------------------------------------------------- places
# kind: walk | lunch | fixed | origin
# trees: tall | low | none     surface: paved | packed | dirt | indoor
PLACES = {
    "SAC":    dict(kind="origin", name="Sacramento", ll=(38.5816, -121.4944)),
    "ARM":    dict(kind="walk", id="A", name="Armstrong Redwoods", title="Plan A · Armstrong Redwoods",
                   ll=(38.5394, -123.0080), outdoor=True, trees="tall", surface="packed", near_river=True,
                   hours=("08:00", "17:30"), pref=100, dur=(90, 45), pair="GUE", requires=["armstrong_open"],
                   note="Pioneer Trail loop · $10 parking"),
    "GROVE":  dict(kind="walk", id="GROVE", name="Grove of Old Trees", title="Backup · Grove of Old Trees, Occidental",
                   ll=(38.3980, -122.9600), outdoor=True, trees="tall", surface="dirt", near_river=False,
                   hours=("08:00", "16:55"), pref=70, dur=(60, 30), pair="OCC",
                   note="17599 Fitzpatrick Ln · ~1 mi of loops"),
    "WCT":    dict(kind="walk", id="PAVED", name="West County Trail", title="Backup · West County Trail (paved)",
                   ll=(38.4741, -122.8902), outdoor=True, trees="none", surface="paved", near_river=False,
                   hours=("07:00", "16:55"), pref=50, dur=(60, 30), pair="GRATON",
                   note="Forestville → Graton, paved, open sky"),
    "RAGLE":  dict(kind="walk", id="RAGLE", name="Ragle Ranch", title="Backup · Ragle Ranch, Sebastopol",
                   ll=(38.4045, -122.8460), outdoor=True, trees="low", surface="dirt", near_river=False,
                   hours=("07:00", "16:55"), pref=45, dur=(60, 30), pair="SEB",
                   note="Oak woodland loops"),
    "SCHULZ": dict(kind="walk", id="INDOOR", name="Schulz Museum", title="Backup · Indoors: Schulz Museum, Santa Rosa",
                   ll=(38.4596, -122.7366), outdoor=False, trees="none", surface="indoor", near_river=False,
                   hours=("10:00", "17:00"), pref=30, dur=(90, 60),
                   note="Right off 101. Weekend hours 10–5, verify Fri"),
    "GUE":    dict(kind="lunch", name="Guerneville", ll=(38.5019, -122.9961), near_river=True, hours=("11:00", "20:00"), pref=10),
    "OCC":    dict(kind="lunch", name="Occidental", ll=(38.4071, -122.9488), near_river=False, hours=("11:00", "20:00"), pref=8),
    "GRATON": dict(kind="lunch", name="Graton", ll=(38.4366, -122.8689), near_river=False, hours=("11:00", "20:00"), pref=8),
    "FORL":   dict(kind="lunch", name="Forestville", ll=(38.4738, -122.8903), near_river=False, hours=("11:00", "20:00"), pref=7),
    "SEB":    dict(kind="lunch", name="Sebastopol", ll=(38.4021, -122.8239), near_river=False, hours=("11:00", "20:00"), pref=7),
    "TOUR":   dict(kind="fixed", name="Chocolate tour", ll=(38.4935, -122.8850), note="6492 Mirabel Rd. Approach from Hwy 116, not River Rd"),
    "BARLOW": dict(kind="fixed", name="The Barlow", ll=(38.4040, -122.8190), note="Sebastopol · indoors · flex"),
    "KEN":    dict(kind="fixed", name="Cabin · Kenwood", ll=(38.4130, -122.5460)),
}
NO_WALK = dict(kind="walk", id="NO_WALK", name="No walk", title="Short day · skip the walk", pref=0, dur=(0, 0))

WALK_KEYS = {"id", "name", "title", "ll", "outdoor", "trees", "surface", "near_river", "hours", "pref", "dur", "note"}
LUNCH_KEYS = {"name", "ll", "near_river", "hours", "pref"}


def validate(places=PLACES):
    for k, p in places.items():
        need = WALK_KEYS if p["kind"] == "walk" else LUNCH_KEYS if p["kind"] == "lunch" else {"name", "ll"}
        missing = need - p.keys()
        if missing:
            raise ValueError(f"place {k} missing {sorted(missing)}")
        if p["kind"] == "walk":
            if p["trees"] not in ("tall", "low", "none"): raise ValueError(f"{k}: trees={p['trees']!r}")
            if p["surface"] not in ("paved", "packed", "dirt", "indoor"): raise ValueError(f"{k}: surface={p['surface']!r}")
            if p["outdoor"] != (p["surface"] != "indoor"): raise ValueError(f"{k}: outdoor/surface disagree")
            if p.get("pair") and p["pair"] not in places: raise ValueError(f"{k}: pair {p['pair']} unknown")


validate()

# ---------------------------------------------------------------- time helpers

def hm(s):
    h, m = map(int, s.split(":")); return h * 60 + m


def fmt(m):
    m = int(round(m)); h, mm = divmod(m, 60); ap = "p" if h >= 12 else "a"
    return f"{h % 12 or 12}:{mm:02d}{ap}"

# ---------------------------------------------------------------- drive times
# Every Sacramento trip goes through 101 at Santa Rosa (~100 min in the wet).
# Local legs: 5 min + 1.2 min per straight-line km, calibrated to the known legs
# (Sac→Armstrong ~2h15, Sac→Forestville ~2h05). Override legs where that's off.
HUB = (38.4700, -122.7300)
SAC_TO_HUB = 100
OVERRIDES = {frozenset(("BARLOW", "KEN")): 45, frozenset(("TOUR", "KEN")): 45}


def km(a, b):
    x = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    y = math.radians(b[0] - a[0])
    return 6371 * math.hypot(x, y)


def local(a, b):
    return 5 + 1.2 * km(a, b)


def drive(a, b, f=None):
    if a == b: return 0
    f = f or {}
    if a == "SAC":
        t = SAC_TO_HUB + local(HUB, PLACES[b]["ll"])
    else:
        t = OVERRIDES.get(frozenset((a, b))) or local(PLACES[a]["ll"], PLACES[b]["ll"])
    if "KEN" in (a, b) and "12" in f.get("closed_hwys", ()):
        t += 15  # Bennett Valley Rd → Glen Ellen detour
    if "101" in f.get("closed_hwys", ()) and a == "SAC":
        t += 30
    return round(t)

# ---------------------------------------------------------------- rules

LABEL = dict(gust_mph="gusts", wind_mph="sustained wind", rain_in="Sat rain", river_peak="river forecast",
             thunder="thunderstorms", wind_alert="NWS wind alert", armstrong_open="Armstrong open")
UNIT = dict(gust_mph=" mph", wind_mph=" mph", rain_in="\"", river_peak=" ft")


def requirements(p, f):
    """(fact, op, threshold) list for a place, derived from its features."""
    req = []
    if p.get("outdoor"):
        req.append(("thunder", "is", False))
    if p.get("trees") == "tall":
        req += [("gust_mph", "<", GUST_MAX), ("wind_mph", "<", WIND_MAX), ("wind_alert", "is", False)]
    if p.get("surface") == "packed":
        req.append(("rain_in", "<", RAIN_PACKED))
    if p.get("surface") == "dirt":
        req.append(("rain_in", "<", RAIN_DIRT))
    if p.get("near_river"):
        req.append(("river_peak", "<", f.get("river_action") or 29))
    for fact in p.get("requires", []):
        req.append((fact, "is", True))
    return req


def check(p, f):
    """None if every requirement passes, else every failure, joined."""
    out = []
    for fact, op, thr in requirements(p, f):
        v = f.get(fact)
        if v is None:
            out.append(f"{LABEL.get(fact, fact)} unknown (check failed)")
        elif op == "<" and not v < thr:
            out.append(f"{LABEL.get(fact, fact)} {v}{UNIT.get(fact, '')} (limit {thr}{UNIT.get(fact, '')})")
        elif op == "is" and v is not thr:
            out.append({"thunder": "thunderstorms forecast", "wind_alert": "NWS wind alert in effect",
                        "armstrong_open": "State Parks page reports a closure"}.get(fact, f"{fact} is {v}"))
    return "; ".join(out) or None

# ---------------------------------------------------------------- planner

def walks():
    return [p for p in PLACES.values() if p["kind"] == "walk"] + [NO_WALK]


def key_of(p):
    return next((k for k, v in PLACES.items() if v is p), None)


def durations(w):
    full, short = w["dur"]
    ds = list(range(full, short - 1, -15))
    if short not in ds: ds.append(short)
    return ds


def plan(f, dep="08:30"):
    dep_m = hm(dep) if isinstance(dep, str) else dep
    cands, rejected, timing = [], {}, {}
    lunches = [(k, p) for k, p in PLACES.items() if p["kind"] == "lunch"]
    for w in walks():
        wk = key_of(w) or "SAC"
        why = check(w, f)
        if why:
            rejected[w["id"]] = why; continue
        for dur in durations(w):
            if dur:
                arr = dep_m + drive("SAC", wk, f)
                start = max(arr, hm(w["hours"][0]))
                end = start + dur
                if end > hm(w["hours"][1]): continue
            else:
                arr = start = end = dep_m
            options = [(lk, lp, style) for lk, lp in lunches for style in (60, 30)] + [("AFTER", None, 45)]
            for lk, lp, style in options:
                if lp is not None:
                    if check(lp, f): continue
                    l_start = max(end + drive(wk, lk, f), hm(lp["hours"][0]))
                    l_end = l_start + style
                    if l_end > hm(lp["hours"][1]): continue
                    tour_arr = l_end + drive(lk, "TOUR", f)
                else:
                    l_start = l_end = None
                    tour_arr = end + drive(wk, "TOUR", f)
                if tour_arr > TOUR_ARRIVE_BY:
                    timing[w["id"]] = f"can't fit before the 2:00 tour (leave Sac {fmt(dep_m)})"; continue
                slack = TOUR_ARRIVE_BY - tour_arr
                score = w["pref"] - (w["dur"][0] - dur) * 0.4
                score += (lp["pref"] + (6 if style == 60 else 0)) if lp else -4
                score += 5 if w.get("pair") == lk else 0
                score += min(slack, 30) * 0.1 - max(0, 10 - slack)
                score -= (tour_arr - dep_m - c_dur(dur, l_start, l_end)) * 0.15  # minutes in the car
                cands.append(dict(score=score, w=w, wk=wk, dur=dur, arr=arr, start=start, end=end,
                                  lk=lk, lp=lp, style=style, l_start=l_start, l_end=l_end, tour_arr=tour_arr, slack=slack))
    if not cands:
        return late_plan(f, dep_m)
    cands.sort(key=lambda c: -c["score"])
    best = cands[0]
    out = describe(best, f, dep_m)
    ru = next((c for c in cands if c["w"]["id"] != best["w"]["id"]), None)
    out["runner_up"] = dict(id=ru["w"]["id"], title=ru["w"]["title"], sub=sub_of(ru)) if ru else None
    out["reasons"] = [f"{w['name']} out: {rejected.get(w['id']) or timing.get(w['id'])}"
                      for w in walks() if w["pref"] > best["w"]["pref"] and (w["id"] in rejected or w["id"] in timing)]
    return out


def c_dur(dur, l_start, l_end):
    return dur + ((l_end - l_start) if l_start is not None else 0)


def sub_of(c):
    w = c["w"]
    s = f"{w['name']} {fmt(c['start'])}–{fmt(c['end'])}" + (f" ({c['dur']} min)" if c["dur"] != w["dur"][0] else "") + ", " if c["dur"] else ""
    s += (f"lunch in {c['lp']['name']} ({'sit-down' if c['style'] == 60 else 'grab-and-go'}), " if c["lp"] else "lunch after the tour, ")
    return s + "tour at 2:00."


def evening(f, after_lunch):
    b_start = TOUR_AT + TOUR_LEN + drive("TOUR", "BARLOW", f)
    leg = drive("BARLOW", "KEN", f)
    b_end = min(b_start + BARLOW_MAX, max(CABIN_TARGET - leg, b_start + (45 if after_lunch else 0)))
    return b_start, b_end, b_end + leg


def describe(c, f, dep_m):
    w = c["w"]
    steps = [dict(t=fmt(dep_m), name="Leave Sacramento", note="80 → 12 → 101" + (" (+30 min, 101 closure)" if "101" in f.get("closed_hwys", ()) else ""))]
    if c["dur"]:
        steps.append(dict(t=fmt(c["start"]), end=fmt(c["end"]), name=w["name"], note=w["note"]))
    if c["lp"]:
        steps.append(dict(t=fmt(c["l_start"]), end=fmt(c["l_end"]), name="Lunch · " + c["lp"]["name"],
                          note="sit-down" if c["style"] == 60 else "grab-and-go"))
    steps.append(dict(t=fmt(TOUR_AT), end=fmt(TOUR_AT + TOUR_LEN), name="Chocolate tour",
                      note=f"arrive {fmt(c['tour_arr'])} · {PLACES['TOUR']['note']}"))
    b0, b1, cabin = evening(f, not c["lp"])
    steps.append(dict(t=fmt(b0), end=fmt(b1), name="The Barlow" + (" + late lunch" if not c["lp"] else ""), note="flex block, cut this first"))
    steps.append(dict(t=fmt(cabin), name="Cabin · Kenwood", note=("Hwy 12 closed: Bennett Valley Rd → Glen Ellen" if "12" in f.get("closed_hwys", ()) else "Hwy 12 via Santa Rosa")))
    return dict(id=w["id"], title=w["title"], sub=sub_of(c), steps=steps, slack=c["slack"], cabin=fmt(cabin))


def late_plan(f, dep_m):
    arr = dep_m + drive("SAC", "TOUR", f)
    b0, b1, cabin = evening(f, True)
    return dict(id="LATE", title="Can't make the 2:00 tour", sub=f"Forestville ~{fmt(arr)}. Call the chocolate place now to push or join late; otherwise straight to The Barlow.",
                steps=[dict(t=fmt(dep_m), name="Leave Sacramento", note=""),
                       dict(t=fmt(arr), name="Forestville / call the tour", note="ask to join late or a later slot"),
                       dict(t=fmt(b1 - 60), end=fmt(b1), name="The Barlow + lunch", note=""),
                       dict(t=fmt(cabin), name="Cabin · Kenwood", note="")],
                slack=None, cabin=fmt(cabin), runner_up=None,
                reasons=[f"Leaving at {fmt(dep_m)} puts you in Forestville at {fmt(arr)}, past 1:55."])

# ---------------------------------------------------------------- flip condition

def flip(f, result, dep="08:30"):
    """The numeric requirement of the chosen plan closest to failing, and what you'd switch to."""
    p = next((w for w in walks() if w["id"] == result["id"]), None)
    if not p: return None
    best = None
    for fact, op, thr in requirements(p, f):
        v = f.get(fact)
        if op != "<" or v is None: continue
        margin = (thr - v) / thr
        if best is None or margin < best[0]:
            best = (margin, fact, thr, v)
    if not best: return None
    _, fact, thr, v = best
    alt = plan(dict(f, **{fact: thr}), dep)
    u = UNIT.get(fact, "")
    return f"Flips if {LABEL[fact]} ≥ {thr}{u} (now {v}{u}) → {alt['title']}"

# ---------------------------------------------------------------- facts from checks

def facts(r):
    f = {}
    rv = r.get("river") or {}
    vals = [x for x in (rv.get("peak"), rv.get("now")) if x is not None]
    f["river_peak"] = round(max(vals), 1) if vals else None
    f["river_action"] = rv.get("action") or 29
    f["rain_in"] = r.get("rain_in")
    f["gust_mph"] = r.get("gusts")
    rows = [x for x in (r.get("forecast") or []) if "wind" in x]
    arm = next((x for x in rows if x["name"] == "Armstrong Redwoods"), None)
    f["wind_mph"] = arm["wind"] if arm else None
    west = [x for x in rows if x["name"] in ("Armstrong Redwoods", "Lunch · Guerneville", "Chocolate tour · Forestville")]
    f["thunder"] = any(x.get("thunder") for x in west) if west else None
    alerts = r.get("alerts")
    if alerts is None or any(a["event"] == "(alert check failed)" for a in alerts):
        f["wind_alert"] = None
    else:
        f["wind_alert"] = any("Wind" in a["event"] for a in alerts)
    parks = r.get("parks")
    f["armstrong_open"] = None if parks is None else not parks.get("closure")
    import re
    f["closed_hwys"] = sorted(hw for hw, txt in (r.get("roads") or {}).items()
                              if re.search(r"\bclosed\b", txt, re.I) and "Sonoma" in txt)
    return f


def run(r):
    """Everything the page and the push need: plan, reasons, flip, departure table."""
    f = facts(r)
    p = plan(f)
    p["flip"] = flip(f, p)
    p["facts"] = f
    p["by_departure"] = [dict(dep=f"{m // 60:02d}:{m % 60:02d}", **{k: q[k] for k in ("id", "title", "sub")})
                         for m in range(7 * 60 + 30, 12 * 60 + 1, 15) for q in [plan(f, m)]]
    return p
