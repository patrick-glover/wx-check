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
        self.assertTrue(12 <= P.drive("GUE", "TOUR") <= 25, P.drive("GUE", "TOUR"))

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


if __name__ == "__main__":
    unittest.main()
