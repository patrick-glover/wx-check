#!/usr/bin/env python3
"""Monitor wrapper: run every check, diff against the last published status.json,
append to a change log, write out/, and push to ntfy when something matters."""
import hashlib, json, os, sys, time, urllib.request
from datetime import datetime
import autocheck as ac

PAGE_URL = os.environ.get("PAGE_URL", "")
TOPIC = os.environ.get("NTFY_TOPIC")
CRON = os.environ.get("CRON", "")
EVENT = os.environ.get("EVENT", "")
KEY_CRONS = {"0 4 18,19,20 11 *", "0 4 21 11 *", "30 14 21 11 *", "0 18 21 11 *", "30 20 21 11 *"}
OUT = sys.argv[1] if len(sys.argv) > 1 else "out"


def previous():
    try:
        req = urllib.request.Request(PAGE_URL + "status.json?t=" + str(int(time.time())), headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:
        return {}


def main():
    prev = previous()
    r, errors = {}, {}
    for key, fn in ac.CHECKS:
        try:
            r[key] = fn()
        except Exception as e:
            errors[key] = str(e)[:200]
    r["errors"] = errors
    detail, reasons = ac.decide(r)
    plan = detail["id"]
    if errors:
        reasons.append("Some checks failed (" + ", ".join(errors) + "). Use the links.")
    now = datetime.now(ac.PST)
    stamp = now.strftime("%a %b %-d, %-I:%M %p")

    log = prev.get("log", [])[-300:]
    new = []
    def add(level, msg): new.append(dict(t=now.isoformat(), level=level, msg=msg))

    if not prev:
        add("info", f"Monitoring started. Plan {plan}.")
    else:
        if prev.get("plan") != plan:
            add("crit", f"PLAN CHANGE {prev.get('plan')} → {plan} ({detail['title']}): {reasons[0]}")
        elif (prev.get("detail") or {}).get("sub") not in (None, detail["sub"]):
            add("info", f"Itinerary adjusted: {detail['sub']}")
        pa = {a["event"] + "|" + a["headline"] for a in prev.get("alerts", [])}
        na = {a["event"] + "|" + a["headline"] for a in r.get("alerts", [])}
        for a in sorted(na - pa): add("warn", "NEW NWS alert: " + a.split("|")[0] + ". " + a.split("|")[1][:140])
        for a in sorted(pa - na): add("info", "Alert ended: " + a.split("|")[0])
        pp = set((prev.get("parks") or {}).get("all", []))
        for a in (r.get("parks") or {}).get("all", []):
            if a not in pp: add("warn", "State Parks page changed: " + a[:180])
        for hw, txt in (r.get("roads") or {}).items():
            old = (prev.get("roads") or {}).get(hw)
            if old is not None and old != txt and "check failed" not in txt:
                add("warn" if "closed" in txt.lower() else "info", f"Caltrans Hwy {hw} update: {txt[:180]}")
        if r.get("gusts") is not None and prev.get("gusts") is not None and abs(r["gusts"] - prev["gusts"]) >= 5:
            add("warn" if r["gusts"] >= 25 else "info", f"Sat gust forecast {prev['gusts']} → {r['gusts']} mph")
        if r.get("rain_in") is not None and prev.get("rain_in") is not None and abs(r["rain_in"] - prev["rain_in"]) >= 0.1:
            add("info", f"Sat rain forecast {prev['rain_in']}\" → {r['rain_in']}\"")
        rv, prv = r.get("river") or {}, prev.get("river") or {}
        if rv.get("peak") is not None and prv.get("peak") is not None and abs(rv["peak"] - prv["peak"]) >= 1:
            add("crit" if rv["peak"] >= rv.get("action", 29) else "info", f"River forecast peak {prv['peak']} → {rv['peak']} ft")
        for k, v in errors.items(): add("warn", f"Check failed: {k}")
        if not new: add("info", f"Check OK, no changes. Plan {plan}, rain {r.get('rain_in')}\", river {rv.get('now')} ft.")
    log += new

    os.makedirs(OUT, exist_ok=True)
    out = dict(r, plan=plan, detail=detail, reasons=reasons, stamp=stamp, iso=now.isoformat(), log=log)
    json.dump(out, open(os.path.join(OUT, "status.json"), "w"), default=str)
    open(os.path.join(OUT, "status.html"), "w").write(ac.render_html(r, detail, reasons, stamp))
    txt = ac.render_txt(r, detail, reasons, stamp)
    print(txt); [print(" ", e["level"], e["msg"]) for e in new]

    important = [e for e in new if e["level"] in ("warn", "crit") and not e["msg"].startswith("Check failed")]
    if EVENT == "workflow_dispatch" or CRON in KEY_CRONS or important or not prev:
        body = txt + ("\n\nChanges:\n" + "\n".join("• " + e["msg"] for e in important) if important else "")
        json.dump(dict(plan=plan, title=detail["title"], body=body), open("notify.json", "w"))  # sent after the page deploys


if __name__ == "__main__":
    main()
