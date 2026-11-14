"""Author-supplied colours reach SVG attributes, so only real colours pass."""

from __future__ import annotations

import pytest
from video_editor_core.paper import safe_css_color


@pytest.mark.parametrize(
    "value",
    ["#FF7A6B", "#abc", "#11223344", "coral", "rgba(5, 28, 26, 0.88)", "hsl(170, 60%, 40%)"],
)
def test_real_colours_pass_unchanged(value: str) -> None:
    assert safe_css_color(value, "leg") == value


@pytest.mark.parametrize(
    "value",
    [
        '#FFF" onload="alert(1)',
        "red;stroke:url(x)",
        "url(#evil)",
        "expression(alert(1))",
        "#GGGGGG",
        "rgb(1,2,3)>",
    ],
)
def test_anything_else_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="colour"):
        safe_css_color(value, "leg")


def test_a_journey_leg_with_an_injected_colour_is_refused() -> None:
    from video_editor_maps.journey import JourneyError, parse_journey

    with pytest.raises(JourneyError, match="colour"):
        parse_journey(
            {
                "journey": {
                    "stops": [
                        {"text": "A", "lon": 1, "lat": 1},
                        {"text": "B", "lon": 2, "lat": 2, "color": '#FFF" onload="x'},
                    ]
                }
            }
        )


def test_a_region_with_an_injected_colour_is_refused() -> None:
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import MapSceneError, render_map

    with pytest.raises(MapSceneError, match="colour"):
        render_map(
            {
                "id": "s",
                "type": "map",
                "props": {
                    "country": "India",
                    "regions": [{"state": "Kerala", "color": '"><script>'}],
                },
            },
            load_design("night_map"),
            index=1,
            duration=4.0,
        )
