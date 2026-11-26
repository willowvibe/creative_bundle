# tally.py
"""The tiffin-centre model. Every rupee on screen comes from here."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Params:
    plates: int = 300                         # R9
    price: float = 40.0                       # R7
    days: int = 30
    rice_g: float = 60.0
    rice_rs_kg: float = 50.0                  # R2
    dal_g: float = 20.0
    dal_rs_kg: float = 120.0                  # R3
    oil_ml: float = 12.0
    oil_rs_l: float = 135.0                   # R4
    garnish_rs_plate: float = 6.0
    wastage_pct: float = 0.08
    gas_cylinders_month: float = 12.0
    cylinder_rs: float = 3081.0               # R1
    rent_rs_month: float = 17500.0            # R5
    staff_rs_month: float = 57000.0           # R6
    packaging_rs_plate: float = 2.0
    packaging_share: float = 0.25
    utilities_rs_day: float = 150.0
    licences_rs_year: float = 1200.0          # R8
    owner_hours: float = 12.0
    owner_wage_month: float = 29000.0


@dataclass(frozen=True)
class Result:
    revenue: float
    costs: dict[str, float] = field(default_factory=dict)
    ingredients_per_plate: float = 0.0
    per_plate_margin: float = 0.0
    days: int = 30
    owner_hours: float = 12.0
    owner_wage_month: float = 0.0

    @property
    def total_cost(self) -> float:
        return sum(self.costs.values())

    @property
    def net(self) -> float:
        return self.revenue - self.total_cost

    @property
    def fixed_daily(self) -> float:
        return (
            self.costs["gas"]
            + self.costs["rent"]
            + self.costs["staff"]
            + self.costs["utilities"]
            + self.costs["licences"]
        )

    @property
    def break_even_plates(self) -> float:
        return self.fixed_daily / self.per_plate_margin if self.per_plate_margin > 0 else math.inf

    @property
    def month_net(self) -> float:
        return self.net * self.days

    @property
    def month_after_owner_wage(self) -> float:
        return self.month_net - self.owner_wage_month

    @property
    def owner_hourly(self) -> float:
        return self.net / self.owner_hours


def model(p: Params) -> Result:
    ingredients_per_plate = (
        p.rice_g / 1000 * p.rice_rs_kg
        + p.dal_g / 1000 * p.dal_rs_kg
        + p.oil_ml / 1000 * p.oil_rs_l
        + p.garnish_rs_plate
    ) * (1 + p.wastage_pct)
    costs = {
        "ingredients": ingredients_per_plate * p.plates,
        "gas": p.gas_cylinders_month * p.cylinder_rs / p.days,
        "rent": p.rent_rs_month / p.days,
        "staff": p.staff_rs_month / p.days,
        "packaging": p.packaging_rs_plate * p.packaging_share * p.plates,
        "utilities": p.utilities_rs_day,
        "licences": p.licences_rs_year / 365,
    }
    packaging_per_plate = p.packaging_rs_plate * p.packaging_share
    return Result(
        revenue=p.plates * p.price,
        costs=costs,
        ingredients_per_plate=ingredients_per_plate,
        per_plate_margin=p.price - ingredients_per_plate - packaging_per_plate,
        days=p.days,
        owner_hours=p.owner_hours,
        owner_wage_month=p.owner_wage_month,
    )


CITIES = {
    "Hyderabad": Params(),
    "Chennai": replace(
        Params(),
        cylinder_rs=2994.5,
        rent_rs_month=18750.0,
        staff_rs_month=50000.0,
        owner_wage_month=26000.0,
        price=40.0,
    ),
    "Bengaluru": replace(
        Params(),
        cylinder_rs=2909.0,
        rent_rs_month=15000.0,
        staff_rs_month=74000.0,
        owner_wage_month=35000.0,
        price=55.0,
    ),
}


def table(p: Params) -> str:
    r = model(p)
    quiet = model(replace(p, plates=240))
    lines = [f"{'item':<20}{'₹':>12}"]
    for name, value in r.costs.items():
        lines.append(f"{name:<20}{value:>12,.0f}")
    lines.append(f"{'out':<20}{r.total_cost:>12,.0f}")
    lines.append(f"{'in':<20}{r.revenue:>12,.0f}")
    lines.append(f"{'kept':<20}{r.net:>12,.0f}")
    lines.append(f"{'ingredients/plate':<20}{r.ingredients_per_plate:>12,.2f}")
    lines.append(f"{'break-even plates':<20}{r.break_even_plates:>12,.0f}")
    lines.append(f"{'owner ₹/hour':<20}{r.owner_hourly:>12,.1f}")
    lines.append(f"{'month net':<20}{r.month_net:>12,.0f}")
    lines.append(f"{'month after wage':<20}{r.month_after_owner_wage:>12,.0f}")
    lines.append(f"{'kept at 240 plates':<20}{quiet.net:>12,.0f}")
    return "\n".join(lines)


def city_rows() -> str:
    header = (
        f"{'city':<18}{'revenue/day':>13}{'costs/day':>12}{'kept/day':>11}"
        f"{'kept/month':>13}{'after wage':>12}{'break-even':>12}{'kept@240':>11}"
    )
    lines = [header]
    for name, p in CITIES.items():
        r = model(p)
        quiet = model(replace(p, plates=240))
        lines.append(
            f"{name:<18}{r.revenue:>13,.0f}{r.total_cost:>12,.0f}{r.net:>11,.0f}"
            f"{r.month_net:>13,.0f}{r.month_after_owner_wage:>12,.0f}"
            f"{r.break_even_plates:>12,.0f}{quiet.net:>11,.0f}"
        )
    p = replace(CITIES["Bengaluru"], price=40.0)
    r = model(p)
    quiet = model(replace(p, plates=240))
    lines.append(
        f"{'Bengaluru @ ₹40':<18}{r.revenue:>13,.0f}{r.total_cost:>12,.0f}{r.net:>11,.0f}"
        f"{r.month_net:>13,.0f}{r.month_after_owner_wage:>12,.0f}"
        f"{r.break_even_plates:>12,.0f}{quiet.net:>11,.0f}"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--cities" in sys.argv:
        print(city_rows())
    elif "--table" in sys.argv:
        print(table(Params()))
