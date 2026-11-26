# test_tally.py
import math
import unittest
from dataclasses import replace

from tally import CITIES, Params, model


class Invariants(unittest.TestCase):
    def setUp(self):
        self.p = Params(plates=350, price=45.0, rice_rs_kg=55.0, dal_rs_kg=160.0, oil_rs_l=140.0)
        self.r = model(self.p)

    def test_costs_have_exactly_seven_lines(self):
        self.assertEqual(
            set(self.r.costs),
            {"ingredients", "gas", "rent", "staff", "packaging", "utilities", "licences"},
        )

    def test_net_is_revenue_minus_costs(self):
        self.assertAlmostEqual(self.r.net, self.r.revenue - self.r.total_cost, places=2)

    def test_break_even_gives_zero_net(self):
        p = replace(self.p, plates=math.ceil(self.r.break_even_plates))
        self.assertAlmostEqual(model(p).net, 0.0, delta=self.p.price)

    def test_fewer_plates_lower_net(self):
        quiet = model(replace(self.p, plates=280))
        self.assertLess(quiet.net, self.r.net)

    def test_wastage_raises_cost(self):
        wasteful = model(replace(self.p, wastage_pct=0.12))
        self.assertGreater(wasteful.total_cost, self.r.total_cost)

    def test_owner_hourly_is_net_over_hours(self):
        self.assertAlmostEqual(self.r.owner_hourly, self.r.net / self.p.owner_hours, places=2)

    def test_month_after_owner_wage_subtracts_wage(self):
        self.assertAlmostEqual(
            self.r.month_after_owner_wage,
            self.r.month_net - self.p.owner_wage_month,
            places=2,
        )


class Pinned(unittest.TestCase):
    """These are the numbers the research speaks. If a param moves, research.md moves too."""

    def test_hyderabad(self):
        r = model(CITIES["Hyderabad"])
        self.assertAlmostEqual(r.net, 3762, delta=1)
        self.assertAlmostEqual(r.month_net, 112875, delta=1)
        self.assertAlmostEqual(r.month_after_owner_wage, 83875, delta=1)
        self.assertAlmostEqual(r.break_even_plates, 152, delta=0.5)
        self.assertAlmostEqual(model(replace(CITIES["Hyderabad"], plates=240)).net, 2236, delta=1)

    def test_chennai(self):
        r = model(CITIES["Chennai"])
        self.assertAlmostEqual(r.net, 3989, delta=1)
        self.assertAlmostEqual(r.month_net, 119663, delta=1)
        self.assertAlmostEqual(r.break_even_plates, 143, delta=0.5)

    def test_bengaluru(self):
        r = model(CITIES["Bengaluru"])
        self.assertAlmostEqual(r.net, 7848, delta=1)
        self.assertAlmostEqual(r.month_net, 235439, delta=1)
        self.assertAlmostEqual(r.break_even_plates, 106, delta=0.5)

    def test_bengaluru_at_forty_rupees(self):
        r = model(replace(CITIES["Bengaluru"], price=40.0))
        self.assertAlmostEqual(r.net, 3348, delta=1)

    def test_ingredients_per_plate(self):
        self.assertAlmostEqual(model(CITIES["Hyderabad"]).ingredients_per_plate, 14.06, places=2)


if __name__ == "__main__":
    unittest.main()
