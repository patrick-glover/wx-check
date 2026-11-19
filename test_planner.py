"""Scenario tests. CI runs these before deploying; a failure keeps the last good page up.

Add a scenario whenever you add a place or a rule."""
import unittest
import planner as P

CALM = dict(river_peak=12.0, river_action=29, rain_in=0.4, gust_mph=18, wind_mph=10, thunder=False,
            wind_alert=False, armstrong_open=True, closed_hwys=[])


def run(dep="08:30", **kw):
    return P.plan(dict(CALM, **kw), dep)


def mins(t):  # "1:45p" -> minutes
    h, m = t[:-1].split(":"); h = int(h) % 12 + (12 if t[-1] == "p" else 0)
    return h * 60 + int(m)


class Venue(unittest.TestCase):
    def v(self, **kw):
        return P.venue(dict(dict(cabin_pop=10, cabin_wind=5, cabin_temp=45), **kw))["id"]

    def test_clear_calm_is_deck(self):
        self.assertEqual(self.v(), "DECK")

    def test_iffy_is_fireplace_maybe_deck(self):
        self.assertEqual(self.v(cabin_pop=30), "FIRE_MAYBE")

    def test_rain_is_fireplace(self):
        self.assertEqual(self.v(cabin_pop=80), "FIRE")

    def test_wind_is_fireplace_even_if_dry(self):
        self.assertEqual(self.v(cabin_wind=20), "FIRE")

    def test_unknown_is_fireplace(self):
        self.assertEqual(P.venue({})["id"], "FIRE")

    def test_facts_read_cabin_row(self):
        f = P.facts(dict(forecast=[dict(name="Cabin · Kenwood", pop=15, wind=6, tmin=44)]))
        self.assertEqual((f["cabin_pop"], f["cabin_wind"], f["cabin_temp"]), (15, 6, 44))


class Roads(unittest.TestCase):
    ONE_WAY = ("[IN THE CENTRAL CALIFORNIA AREA] 1-way controlled traffic at Cnopius Rd /in Edendale/ (Sonoma Co) "
               "24 hrs a day thru 2100 hrs on 11/30/26 - Due to construction")

    def test_one_way_control_is_not_a_closure(self):
        self.assertFalse(P.sonoma_closure(self.ONE_WAY))

    def test_sonoma_closure_detected(self):
        self.assertTrue(P.sonoma_closure("SR 116 is closed from Guerneville to Forestville (Sonoma Co) - Due to flooding"))

    def test_closure_elsewhere_doesnt_pair_with_sonoma_item(self):
        txt = ("I-80 is closed at Donner Summit (Placer Co) - Due to snow "
               "1-way controlled traffic near Cloverdale (Sonoma Co) - Due to slide repair")
        self.assertFalse(P.sonoma_closure(txt))

    def test_116_closed_slows_guerneville_to_tour(self):
        self.assertEqual(P.drive("BOON", "TOUR", dict(closed_hwys=["116"])), P.drive("BOON", "TOUR") + 10)
        self.assertEqual(P.drive("WILLOW", "TOUR", dict(closed_hwys=["116"])), P.drive("WILLOW", "TOUR"))


class Food(unittest.TestCase):
    def test_calm_lunch_is_a_named_guerneville_spot(self):
        p = run()
        lunch = next(s for s in p["steps"] if s["name"].startswith("Lunch"))
        self.assertIn("Guerneville", lunch["name"])
        self.assertTrue(any(n in lunch["name"] for n in ("boon", "Piknik")))

    def test_lunch_respects_closing_time(self):
        for k, lp in P.PLACES.items():
            if lp["kind"] != "lunch": continue
            for dep in ("07:30", "08:30", "10:00"):
                for s in run(dep)["steps"]:
                    if s["name"].startswith("Lunch") and lp["name"] in s["name"]:
                        self.assertLessEqual(mins(s["end"]), P.hm(lp["hours"][1]))

    def test_coffee_only_with_slack(self):
        for dep in ("07:30", "08:30", "09:15"):
            p = run(dep)
            cof = [s for s in p["steps"] if s["name"].startswith("Coffee")]
            if p["slack"] is not None and p["slack"] < P.COFFEE_MIN + 5:
                self.assertEqual(cof, [])
            for s in cof:
                self.assertLessEqual(mins(s["end"]), P.TOUR_ARRIVE_BY)

    def test_early_start_gets_coffee(self):
        # leaving at 7:30 on a gusty day: short paved walk, lots of slack
        p = run("07:30", gust_mph=40)
        self.assertTrue(any(s["name"].startswith("Coffee") for s in p["steps"]), p["steps"])


class Scenarios(unittest.TestCase):
    def test_calm_is_plan_a_full(self):
        p = run()
        self.assertEqual(p["id"], "A")
        self.assertIn("Guerneville", p["sub"])
        self.assertNotIn("min)", p["sub"])  # not shortened

    def test_river_high_plus_heavy_rain_is_paved_not_ragle(self):
        p = run(river_peak=31.0, rain_in=1.6)
        self.assertEqual(p["id"], "PAVED")
        self.assertNotIn("Guerneville", p["sub"])

    def test_river_high_light_rain_avoids_guerneville(self):
        p = run(river_peak=30.0, rain_in=0.2)
        self.assertNotEqual(p["id"], "A")
        self.assertNotIn("Guerneville", p["sub"])

    def test_gusts_avoid_all_tall_trees(self):
        p = run(gust_mph=40)
        self.assertNotIn(p["id"], ("A", "GROVE"))
        self.assertEqual(p["id"], "PAVED")

    def test_wind_alert_avoids_tall_trees(self):
        self.assertNotIn(run(wind_alert=True)["id"], ("A", "GROVE"))

    def test_armstrong_closed_calm_goes_to_grove(self):
        self.assertEqual(run(armstrong_open=False)["id"], "GROVE")

    def test_thunder_goes_indoors(self):
        self.assertEqual(run(thunder=True)["id"], "INDOOR")

    def test_worst_case_indoors(self):
        p = run(river_peak=33, rain_in=2.5, gust_mph=45, wind_alert=True, thunder=True)
        self.assertEqual(p["id"], "INDOOR")

    def test_moderate_rain_keeps_a(self):
        self.assertEqual(run(rain_in=0.9)["id"], "A")

    def test_heavy_rain_alone_is_paved(self):
        self.assertEqual(run(rain_in=1.2)["id"], "PAVED")

    def test_unknown_river_fails_safe(self):
        p = run(river_peak=None)
        self.assertNotEqual(p["id"], "A")
        self.assertTrue(any("unknown" in r for r in p["reasons"]))

    def test_unknown_gusts_fails_safe(self):
        self.assertNotIn(run(gust_mph=None)["id"], ("A", "GROVE"))

    def test_late_departure_shortens_but_makes_tour(self):
        p = run("10:15")
        tour = next(s for s in p["steps"] if s["name"] == "Chocolate tour")
        self.assertLessEqual(mins(tour["note"].split()[1]), P.TOUR_ARRIVE_BY)
        self.assertNotEqual(p["id"], "LATE")

    def test_very_late_departure_flags_tour(self):
        self.assertEqual(run("12:00")["id"], "LATE")

    def test_every_departure_reaches_tour_or_says_late(self):
        for m in range(7 * 60 + 30, 12 * 60 + 1, 15):
            p = P.plan(CALM, m)
            if p["id"] != "LATE":
                self.assertGreaterEqual(p["slack"], 0)

    def test_hwy12_closed_delays_cabin(self):
        a, b = run(), run(closed_hwys=["12"])
        self.assertGreater(mins(b["cabin"]), mins(a["cabin"]) - 1)
        self.assertIn("Bennett Valley", b["steps"][-1]["note"])

    def test_reasons_explain_rejections(self):
        p = run(gust_mph=40)
        self.assertTrue(any(r.startswith("Armstrong Redwoods out: gusts 40") for r in p["reasons"]))

    def test_flip_names_closest_threshold(self):
        f = dict(CALM, gust_mph=27)
        fl = P.flip(f, P.plan(f))
        self.assertIn("gusts", fl)
        self.assertIn("West County Trail", fl)


class Model(unittest.TestCase):
    def test_places_validate(self):
        P.validate()

    def test_missing_feature_is_rejected(self):
        bad = dict(P.PLACES)
        bad["X"] = dict(kind="walk", id="X", name="X", title="X", ll=(38.4, -122.9), outdoor=True,
                        surface="dirt", near_river=False, hours=("08:00", "17:00"), pref=1, dur=(30, 30), note="")
        with self.assertRaises(ValueError):
            P.validate(bad)  # no 'trees'

    def test_drive_calibration(self):
        self.assertTrue(125 <= P.drive("SAC", "ARM") <= 145, P.drive("SAC", "ARM"))
        self.assertTrue(115 <= P.drive("SAC", "TOUR") <= 130, P.drive("SAC", "TOUR"))
        self.assertTrue(12 <= P.drive("BOON", "TOUR") <= 25, P.drive("BOON", "TOUR"))

    def test_facts_from_checks(self):
        r = dict(river=dict(now=10.2, peak=14.0, action=29), rain_in=0.3, gusts=22,
                 forecast=[dict(name="Armstrong Redwoods", wind=12, thunder=False)],
                 alerts=[dict(event="Wind Advisory", headline="")], parks=dict(closure=[]),
                 roads={"12": "IS CLOSED ... in Sonoma County"})
        f = P.facts(r)
        self.assertEqual((f["river_peak"], f["wind_alert"], f["armstrong_open"], f["closed_hwys"]), (14.0, True, True, ["12"]))
        self.assertIsNone(P.facts(dict(alerts=[dict(event="(alert check failed)", headline="")]))["wind_alert"])

    def test_run_produces_departure_table(self):
        p = P.run(dict(river=dict(now=10, peak=12, action=29), rain_in=0.2, gusts=10,
                       forecast=[dict(name="Armstrong Redwoods", wind=8, thunder=False)], alerts=[], parks=dict(closure=[]), roads={}))
        self.assertEqual(p["id"], "A")
        self.assertEqual(p["by_departure"][0]["dep"], "07:30")
        self.assertEqual(p["by_departure"][-1]["id"], "LATE")


class TestRouteItems(unittest.TestCase):
    def test_drops_other_counties_and_joins_wrapped_lines(self):
        import autocheck as A
        lines = ["[IN THE CENTRAL CALIFORNIA AREA]",
                 "Is closed to southbound traffic from Brokaw Ave to the Jct of I 880 /in San Jose/",
                 "(Santa Clara Co) - Due to flooding - Motorists are advised to use an alternate route",
                 "The northbound connector to northbound SR 1 (Marin Co) is closed",
                 "- Due to flooding",
                 "Northbound traffic is reduced to 1 lane at Todd Rd /near Santa Rosa/",
                 "(Sonoma Co) - Due to a traffic collision",
                 "[IN THE NORTHERN CALIFORNIA AREA]",
                 "Northbound traffic is reduced to 1 lane at Pepperwood (Humboldt Co) - Due",
                 "to a traffic collision", "-->"]
        self.assertEqual(A.route_items("101", lines),
                         "Northbound traffic is reduced to 1 lane at Todd Rd /near Santa Rosa/ (Sonoma Co) - Due to a traffic collision")
        self.assertIn("Marin Co", A.route_items("37", lines))

    def test_nothing_on_route(self):
        import autocheck as A
        self.assertEqual(A.route_items("12", ["[IN THE CENTRAL CALIFORNIA AREA]", "No traffic restrictions are reported for this area."]),
                         "No restrictions reported in Solano/Napa/Sonoma Co.")
        self.assertTrue(P.sonoma_closure(A.route_items("101", ["[IN THE CENTRAL CALIFORNIA AREA]",
                        "Is closed from River Rd to Steele Ln (Sonoma Co) - Due to flooding"])))


if __name__ == "__main__":
    unittest.main()
