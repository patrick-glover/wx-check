#!/usr/bin/env python3
"""Big Q weekend autocheck.

Pulls NWS hourly forecasts, weekend alerts, Saturday rainfall, the Russian River
gauge, Armstrong Redwoods' State Parks alerts and Caltrans highway conditions.
It then picks a go/no-go call for Saturday morning and writes:

  status.html  - a static, phone-friendly page with no JavaScript
  status.txt   - a short text summary (for a message / notification)
  status.json  - the raw results

Usage:  python3 autocheck.py [--out DIR] [--baseline]
  --baseline   record the current State Parks alerts as "normal" (run once now),
               so later runs only flag alerts that are NEW.
"""
import argparse, hashlib, html, json, os, re, sys, urllib.request
import planner
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "bigq-weekend-autocheck (personal script)"
PST = timezone(timedelta(hours=-8))

STOPS = [
    ("2026-11-20", 12, 18, "Fri 12–6p",     "Brew bike · Sacramento",      "38.5816,-121.4944"),
    ("2026-11-21",  8, 10, "Sat 8–10a",     "Drive out · Sacramento",      "38.5816,-121.4944"),
    ("2026-11-21", 10, 12, "10:45a–12:15p", "Armstrong Redwoods",          "38.5394,-123.0080"),
    ("2026-11-21", 12, 14, "12:30–1:35p",   "Lunch · Guerneville",         "38.5019,-122.9961"),
    ("2026-11-21", 14, 15, "2:00–2:30p",    "Chocolate tour · Forestville", "38.4935,-122.8850"),
    ("2026-11-21", 15, 17, "2:45–4:15p",    "The Barlow · Sebastopol",     "38.4040,-122.8190"),
    ("2026-11-21", 17, 20, "5–8p",          "Cabin · Kenwood",             "38.4130,-122.5460"),
    ("2026-11-21", 20, 23, "8:30p",         "Golden Bear Station",         "38.4180,-122.5530"),
]
WEEKEND_START = datetime(2026, 11, 20, 0, 0, tzinfo=PST)
WEEKEND_END = datetime(2026, 11, 23, 0, 0, tzinfo=PST)
HIGHWAYS = ["80", "12", "101", "116", "37"]
PARKS_URL = "https://www.parks.ca.gov/?page_id=450"
BASELINE = os.path.join(HERE, ".autocheck-parks-baseline.json")


def fetch(url, accept="application/geo+json, application/json, text/html"):
    req = urllib.request.Request(url, headers={"User-Agent": UA if "weather.gov" in url else "Mozilla/5.0", "Accept": accept})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def getj(url, _c={}):
    if url not in _c:
        _c[url] = json.loads(fetch(url))
    return _c[url]


def point(ll):
    return getj("https://api.weather.gov/points/" + ll)["properties"]


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- checks

def check_forecast():
    rows = []
    for day, h0, h1, when, name, ll in STOPS:
        try:
            periods = getj(point(ll)["forecastHourly"])["properties"]["periods"]
            hrs = [p for p in periods if p["startTime"][:10] == day and h0 <= int(p["startTime"][11:13]) < h1]
            if not hrs:
                rows.append(dict(when=when, name=name, note="not in forecast yet")); continue
            pops = [(p.get("probabilityOfPrecipitation") or {}).get("value") or 0 for p in hrs]
            winds = [max(map(int, re.findall(r"\d+", p["windSpeed"])) or [0]) for p in hrs]
            temps = [p["temperature"] for p in hrs]
            sky = max(set(p["shortForecast"] for p in hrs), key=[p["shortForecast"] for p in hrs].count)
            rows.append(dict(when=when, name=name, tmin=min(temps), tmax=max(temps), pop=max(pops),
                             hours=pops, wind=max(winds), sky=sky,
                             thunder=any("thunder" in p["shortForecast"].lower() for p in hrs)))
        except Exception as e:
            rows.append(dict(when=when, name=name, note=f"error: {e}"))
    return rows


def check_alerts():
    seen, out = set(), []
    for ll in ["38.5816,-121.4944", "38.5394,-123.0080", "38.4040,-122.8190", "38.4130,-122.5460"]:
        try:
            for f in getj("https://api.weather.gov/alerts/active?point=" + ll)["features"]:
                a = f["properties"]
                if a["id"] in seen: continue
                seen.add(a["id"])
                start = parse_iso(a.get("onset") or a.get("effective") or a["sent"])
                end = parse_iso(a["ends"] or a["expires"]) if (a.get("ends") or a.get("expires")) else WEEKEND_END
                if end < WEEKEND_START or start > WEEKEND_END:
                    continue  # doesn't touch the weekend
                out.append(dict(event=a["event"], severity=a.get("severity"), headline=a.get("headline") or ""))
        except Exception as e:
            out.append(dict(event="(alert check failed)", severity="Unknown", headline=str(e)))
    return out


def check_rain():
    gd = getj(point("38.5019,-122.9961")["forecastGridData"])["properties"]["quantitativePrecipitation"]["values"]
    a, b = datetime(2026, 11, 21, 6, tzinfo=PST), datetime(2026, 11, 21, 22, tzinfo=PST)
    mm = 0.0
    for v in gd:
        start, dur = v["validTime"].split("/")
        t0 = parse_iso(start)
        m = re.match(r"P(?:(\d+)D)?(?:T(\d+)H)?", dur)
        hours = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) or 1
        t1 = t0 + timedelta(hours=hours)
        ov = (min(t1, b) - max(t0, a)).total_seconds()
        if ov > 0 and v["value"]:
            mm += v["value"] * ov / (t1 - t0).total_seconds()
    return round(mm / 25.4, 2)


def check_gusts():
    """Max forecast gust (mph) Sat 9a-4p at Armstrong and Sebastopol, from NWS gridData."""
    a, b = datetime(2026, 11, 21, 9, tzinfo=PST), datetime(2026, 11, 21, 16, tzinfo=PST)
    best = None
    for ll in ("38.5394,-123.0080", "38.4040,-122.8190"):
        g = getj(point(ll)["forecastGridData"])["properties"]["windGust"]
        k = 0.621371 if "km_h" in g.get("uom", "km_h") else 1.0  # NWS gridData reports km/h
        for v in g["values"]:
            start, dur = v["validTime"].split("/")
            t0 = parse_iso(start)
            m = re.match(r"P(?:(\d+)D)?(?:T(\d+)H)?", dur)
            t1 = t0 + timedelta(hours=(int(m.group(1) or 0) * 24 + int(m.group(2) or 0)) or 1)
            if t1 > a and t0 < b and v["value"] is not None:
                best = max(best or 0, round(v["value"] * k))
    if best is None:
        raise ValueError("no gust data for Sat")
    return best


def check_river():
    g = getj("https://api.water.noaa.gov/nwps/v1/gauges/GUEC1")
    sf = getj("https://api.water.noaa.gov/nwps/v1/gauges/GUEC1/stageflow")
    data = (sf.get("forecast") or {}).get("data") or []
    peak = max(data, key=lambda x: x["primary"]) if data else None
    return dict(now=g["status"]["observed"]["primary"],
                action=g["flood"]["categories"]["action"]["stage"],
                flood=g["flood"]["categories"]["minor"]["stage"],
                peak=peak["primary"] if peak else None,
                peak_time=peak["validTime"] if peak else None)


def parks_alerts():
    t = fetch(PARKS_URL)
    out = []
    for m in re.finditer(r'<div class="alert alert-(\w+)[^"]*"[^>]*>(.*?)</div>', t, re.S):
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if txt:
            out.append(txt)
    return out


def check_parks():
    alerts = parks_alerts()
    base = set(json.load(open(BASELINE))) if os.path.exists(BASELINE) else set()
    h = lambda s: hashlib.sha1(s.encode()).hexdigest()
    new = [a for a in alerts if h(a) not in base]
    closed_re = re.compile(r"\b(reserve|park|armstrong)\b[^.]{0,40}\b(is|are|will be|remains?)\s+closed|closed (today|due to|until)|"
                           r"pioneer trail[^.]{0,40}closed|armstrong woods road[^.]{0,40}closed|storm (closure|damage)", re.I)
    closure = [a for a in new if closed_re.search(a)]  # standing alerts are in the baseline
    return dict(new=new, closure=closure, baseline=bool(base), all=alerts)


def check_roads():
    out = {}
    for r in HIGHWAYS:
        try:
            t = fetch(f"https://roads.dot.ca.gov/roadscell.php?roadnumber={r}")
            t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
            lines = [l.strip() for l in t.splitlines() if l.strip()]
            i = next((k for k, l in enumerate(lines) if l.startswith("SR ") or l.startswith("I ") or l.startswith("US ")), None)
            body = " ".join(lines[i + 1:]) if i is not None else ""
            body = body.split("-->")[0]
            # keep only the Bay Area / central section(s)
            parts = re.split(r"(\[IN [^\]]+\])", body)
            keep = []
            for k in range(1, len(parts) - 1, 2):
                if ("CENTRAL" in parts[k] or "SAN FRANCISCO" in parts[k]) and "SIERRA" not in parts[k]:
                    keep.append(parts[k] + " " + parts[k + 1].strip())
            out[r] = " ".join(keep).strip() or body.strip()[:800]
        except Exception as e:
            out[r] = f"(check failed: {e})"
    return out


CHECKS = [("forecast", check_forecast), ("alerts", check_alerts), ("rain_in", check_rain), ("gusts", check_gusts),
          ("river", check_river), ("parks", check_parks), ("roads", check_roads)]

# ---------------------------------------------------------------- decision

def decide(r):
    """Returns (plan dict, reasons). The plan comes from planner.py; reasons add
    the context the planner doesn't act on (alerts, road notes)."""
    p = planner.run(r)
    reasons = list(p["reasons"])
    f = p["facts"]
    for hw in f["closed_hwys"]:
        reasons.append(f"Hwy {hw} reports a closure in Sonoma Co. Check QuickMap."
                       + (" Guerneville ↔ Forestville goes around on River Rd (+10 min)." if hw == "116" else ""))
    if f.get("river_peak") is not None and f["river_peak"] >= f["river_flood"]:
        reasons.append(f"River at flood stage ({f['river_peak']} ft): River Rd near Mirabel can go under. "
                       "Reach the tour from Forestville / 116 and call Rainy Day Chocolate before you drive over."
                       + (" 116 is ALSO closed: call them now about a late or later slot." if "116" in f["closed_hwys"] else ""))
    for a in r.get("alerts") or []:
        if re.search(r"Flood|Wind|Storm", a["event"]):
            reasons.append(f"NWS {a['event']} touches the weekend.")
    if not reasons:
        reasons.append("No closures, no flooding, no wind. Rain jackets and go.")
    return p, reasons


# ---------------------------------------------------------------- output

def render_html(r, p, reasons, stamp):
    e = html.escape
    title, sub = p["title"], p["sub"]
    steps = "".join(f"<tr><td class=n>{e(x['t'])}</td><td><b>{e(x['name'])}</b><br><span class=m>{e(x.get('note',''))}</span></td></tr>" for x in p["steps"])
    v = p.get("venue")
    extra = (f"<div style='margin-top:10px'><b>{e(v['title'])}</b><br><span class=m>{e(v['note'])}</span></div>" if v else "") + \
            (f"<div class=m style='margin-top:8px'>{e(p['flip'])}</div>" if p.get("flip") else "") + \
            (f"<div class=m>Runner-up: {e(p['runner_up']['title'])}</div>" if p.get("runner_up") else "")
    rows = ""
    for x in r.get("forecast", []):
        if "note" in x:
            rows += f"<tr><td>{e(x['when'])}</td><td>{e(x['name'])}</td><td colspan=1 class=m>{e(x['note'])}</td></tr>"
        else:
            bars = "".join(f"<i><b style='height:{p}%'></b></i>" for p in x["hours"])
            rows += (f"<tr><td class='n w'>{e(x['when'])}</td><td><b>{e(x['name'])}</b><br><span class=m>{e(x['sky'])} · {x['tmin']}–{x['tmax']}° · ≤{x['wind']} mph</span></td>"
                     f"<td class=n>{x['pop']}%<div class=h>{bars}</div></td></tr>")
    alerts = "".join(f"<div class=al><b>{e(a['event'])}</b><br><span class=m>{e(a['headline'])}</span></div>" for a in r.get("alerts", [])) \
        or "<div class='al ok'>No NWS alerts touching Fri–Sun.</div>"
    river = r.get("river") or {}
    parks = r.get("parks") or {}
    parks_html = ("".join(f"<div class=al><b>NEW</b> {e(a[:400])}</div>" for a in parks.get("new", [])) if parks.get("new")
                  else "<div class='al ok'>No new alerts on the Armstrong State Parks page.</div>")
    roads = "".join(f"<div class=rd><b>Hwy {k}</b> <span class=m>{e(v[:600])}</span></div>" for k, v in (r.get("roads") or {}).items())
    errs = "".join(f"<div class=al><b>Check failed:</b> {e(k)}: {e(v)}</div>" for k, v in r.get("errors", {}).items())
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Big Q · Status</title>
<style>
:root{{--bg:#F8F7F4;--bg2:#EFEDE7;--fg:#17140F;--m:#6E675B;--r:#D9D5CB;--a:#A07723;--w:#9A6B24;--g:#4A6B4E;--f:#4E6472}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14120E;--bg2:#1E1B15;--fg:#EDE9E0;--m:#8D8578;--r:#332E25;--a:#D6B266;--w:#D0A055;--g:#8CB08F;--f:#8FA8B8}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;padding:16px}}
.wrap{{max-width:40rem;margin:0 auto}}h1{{font:400 1.9rem/1.1 ui-serif,Georgia,serif;margin:.2rem 0 0}}
.ey{{font:.65rem ui-monospace,Menlo,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--m)}}
.call{{border:1px solid var(--a);background:var(--bg2);padding:14px 16px;margin:16px 0}}.call h2{{font:400 1.35rem ui-serif,Georgia,serif;margin:.2rem 0;color:var(--a)}}
.call ul{{margin:.5rem 0 0;padding-left:1.1rem}}h3{{font:600 .72rem system-ui;letter-spacing:.1em;text-transform:uppercase;color:var(--m);margin:22px 0 8px;border-top:1px solid var(--r);padding-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}td{{padding:7px 4px;border-bottom:1px solid var(--r);vertical-align:top}}
.n{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}.w{{white-space:normal;width:5.2rem;font-size:.75rem}}td{{overflow-wrap:anywhere}}.m{{color:var(--m);font-size:.82rem}}
.h{{display:flex;gap:2px;margin-top:3px}}.h i{{width:7px;height:14px;background:var(--bg2);position:relative;display:block}}.h b{{position:absolute;left:0;right:0;bottom:0;background:var(--f)}}
.al{{border-left:2px solid var(--w);background:var(--bg2);padding:8px 12px;margin-bottom:6px;font-size:.88rem}}.al.ok{{border-color:var(--g)}}
.st{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}}.st div{{border:1px solid var(--r);padding:10px}}.st b{{display:block;font:400 1.3rem ui-serif,Georgia,serif}}
.rd{{font-size:.82rem;padding:6px 0;border-bottom:1px solid var(--r)}}a{{color:var(--a)}}
.lk a{{display:block;padding:9px 0;border-bottom:1px solid var(--r);text-decoration:none}}
</style></head><body><div class=wrap>
<div class=ey>Big Q · autocheck · {e(stamp)}</div>
<h1>Saturday call</h1>
<div class=call><div class=ey>Recommended</div><h2>{e(title)}</h2><div>{e(sub)}</div>
<ul>{''.join(f'<li>{e(x)}</li>' for x in reasons)}</ul>{extra}</div>
<h3>Itinerary</h3><table>{steps}</table>
<div class=st>
<div><span class=ey>Sat rain · Guerneville</span><b>{r.get('rain_in','n/a')} in</b><span class=m>6 am–10 pm</span></div>
<div><span class=ey>Russian River</span><b>{river.get('now','n/a')} ft</b><span class=m>peak fcst {river.get('peak','n/a')} ft · flood {river.get('flood',32)}</span></div>
</div>
<h3>By stop</h3><table>{rows}</table>
<h3>NWS alerts (Fri–Sun)</h3>{alerts}
<h3>Armstrong Redwoods · State Parks</h3>{parks_html}
<h3>Highways (Caltrans)</h3>{roads}
{('<h3>Problems</h3>' + errs) if errs else ''}
<h3>Links</h3><div class=lk>
<a href="{PARKS_URL}">Armstrong Redwoods · State Parks</a>
<a href="https://quickmap.dot.ca.gov/">Caltrans QuickMap</a>
<a href="https://socoemergency.org/">Sonoma County emergency / road closures</a>
<a href="https://water.noaa.gov/gauges/GUEC1">Russian River gauge</a>
<a href="https://forecast.weather.gov/MapClick.php?lat=38.5394&lon=-123.008&FcstType=graphical">Hourly graph · Armstrong</a>
<a href="https://forecast.weather.gov/MapClick.php?lat=38.413&lon=-122.546&FcstType=graphical">Hourly graph · Kenwood</a>
</div></div></body></html>"""


def render_txt(r, p, reasons, stamp):
    title, sub = p["title"], p["sub"]
    arm = next((x for x in r.get("forecast", []) if x["name"] == "Armstrong Redwoods" and "pop" in x), None)
    lines = [f"Big Q check {stamp}", f"→ {title}", sub] + [f"• {x}" for x in reasons]
    if p.get("flip"): lines.append(p["flip"])
    if p.get("venue"): lines.append(f"♥ {p['venue']['title']}: {p['venue']['note']}")
    if arm: lines.append(f"Armstrong 11–12: {arm['tmin']}–{arm['tmax']}°, rain {arm['pop']}%, wind ≤{arm['wind']} mph")
    lines.append(f"Sat rain {r.get('rain_in','?')}\" · river {(r.get('river') or {}).get('now','?')} ft")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--ntfy", default=os.environ.get("NTFY_TOPIC"), help="ntfy.sh topic to push the summary to")
    ap.add_argument("--page-url", default=os.environ.get("PAGE_URL"), help="link opened when the notification is tapped")
    a = ap.parse_args()

    if a.baseline:
        alerts = parks_alerts()
        json.dump([hashlib.sha1(x.encode()).hexdigest() for x in alerts], open(BASELINE, "w"))
        print(f"Saved {len(alerts)} State Parks alerts as baseline."); return

    r, errors = {}, {}
    for key, fn in CHECKS:
        try:
            r[key] = fn()
        except Exception as e:
            errors[key] = str(e)
    r["errors"] = errors
    p, reasons = decide(r)
    if errors:
        reasons.append("Some checks failed (" + ", ".join(errors) + "). Use the links.")
    stamp = datetime.now(PST).strftime("%a %b %-d, %-I:%M %p")
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "status.html"), "w").write(render_html(r, p, reasons, stamp))
    txt = render_txt(r, p, reasons, stamp)
    open(os.path.join(a.out, "status.txt"), "w").write(txt)
    json.dump(dict(r, plan=p["id"], detail=p, reasons=reasons, stamp=stamp), open(os.path.join(a.out, "status.json"), "w"), indent=1, default=str)
    print(txt)
    if a.ntfy:
        notify(a.ntfy, p["id"], txt, a.page_url, p["title"])


def notify(topic, plan, body, url=None, title=None):
    short = (title or plan).replace("·", "-")
    headers = {"Title": "Saturday call: " + short, "Tags": "evergreen_tree" if plan == "A" else "warning",
               "Priority": "default" if plan == "A" else "high"}
    if url:
        headers["Click"] = url
        headers["Actions"] = "view, Open status, " + url
    req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        print("ntfy:", r.status)


if __name__ == "__main__":
    main()
