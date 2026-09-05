"""Folium helpers for the examples that draw.

The eight examples that already build maps each hand-roll their folium calls,
and the drift shows: one centres on a hardcoded `[9.93, -84.10]` that is wrong
for any round outside the central valley, one writes its HTML to the working
directory while the rest write beside themselves, and each picks its own
colours -- `compare_tsp` from a fixed list, `visualize_vrp` from
`random.randint`, so the same vehicle changes colour between runs of the same
script. None of that is what those examples are about.

This is the shared floor: a map fitted to the data rather than to a guess, one
stable qualitative palette, a convex hull for showing whether a group of stops
is a region or a scattering, a legend, and a save that reports where it went.

It is example infrastructure and deliberately not part of `vrp`. Nothing here
is a routing decision -- a hull is not a territory, it is a picture of one --
and an example importing map code from the platform would suggest the platform
draws, which it does not.

Sits beside `config` and `dataset`, the two modules every example already
imports, and is installed with them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import folium

Point = tuple[float, float]

# Qualitative, and fixed. Colour carries meaning on these maps -- which van,
# which territory -- so it has to survive a re-run: `visualize_vrp` seeds its
# vehicle colours from `random` and a reader comparing two runs is comparing
# nothing. Ordered so neighbours stay distinguishable in the common case of
# three or four groups.
COLOURS: tuple[str, ...] = (
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#17becf", "#e377c2", "#7f7f7f", "#bcbd22",
)

DEPOT_COLOUR = "#111111"


def colour(index: int) -> str:
    """The palette colour for group `index`, wrapping when there are more."""
    return COLOURS[index % len(COLOURS)]


def base_map(points: Sequence[Point], tiles: str = "cartodbpositron") -> folium.Map:
    """An empty map fitted to `points`.

    Args:
        points: `(latitude, longitude)` pairs the map must contain.
        tiles: Folium tile set. The default is deliberately pale, because
            everything these examples draw is coloured and OpenStreetMap's
            own colours compete with it.

    Returns:
        A `folium.Map` whose viewport already frames the data.

    Raises:
        ValueError: if `points` is empty. A map of nothing has no centre, and
            defaulting to one would put every empty round in the same wrong
            place rather than saying so.
    """
    if not points:
        raise ValueError("a map needs at least one point to frame")
    lats = [lat for lat, _ in points]
    lons = [lon for _, lon in points]
    canvas = folium.Map(tiles=tiles)
    canvas.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return canvas


def depot(target: Any, point: Point, label: str) -> None:
    """Mark a depot: a black square, so it never reads as one more stop."""
    folium.Marker(
        location=list(point), tooltip=label,
        icon=folium.Icon(color="black", icon="home", prefix="fa"),
    ).add_to(target)


def stop(target: Any, point: Point, fill: str, label: str,
         radius: int = 5) -> None:
    """Mark one stop as a filled circle in `fill`."""
    folium.CircleMarker(
        location=list(point), radius=radius, tooltip=label,
        color=fill, fill=True, fill_color=fill, fill_opacity=0.85, weight=1,
    ).add_to(target)


def _cross(o: Point, a: Point, b: Point) -> float:
    """Z of (a-o) x (b-o): positive when o->a->b turns left."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def hull(points: Iterable[Point]) -> list[Point]:
    """The convex hull of `points`, by Andrew's monotone chain.

    Written here rather than taken from a dependency: it is fifteen lines, and
    `shapely` would be the examples' first geometry dependency for one shape.

    Args:
        points: `(latitude, longitude)` pairs, in any order.

    Returns:
        The hull vertices anticlockwise, or the deduplicated input when there
        are fewer than three distinct points -- two stops make a line and one
        makes a dot, neither of which is a polygon, and the caller is expected
        to notice rather than be handed a degenerate ring.
    """
    unique = sorted(set(points))
    if len(unique) < 3:
        return unique
    lower, upper = _half(unique), _half(list(reversed(unique)))
    return lower[:-1] + upper[:-1]


def _half(ordered: list[Point]) -> list[Point]:
    """One chain of the hull. Both halves are the same walk, opposite ways.

    Building them in a single pass over the points and their reverse looks
    equivalent and is not: the second half must not pop vertices belonging to
    the first, so the two need separate stacks. That version returned three
    corners of a square and collapsed three collinear points to one.
    """
    chain: list[Point] = []
    for point in ordered:
        while len(chain) >= 2 and _cross(chain[-2], chain[-1], point) <= 0:
            chain.pop()
        chain.append(point)
    return chain


def area(ring: Sequence[Point]) -> float:
    """The area a hull encloses, by the shoelace formula.

    In squared degrees, which is nobody's idea of a unit. It is here so hulls
    can be *compared* -- the ratio of two is unitless and is what "this van
    covers a third of the round" means. Never report it as a size: a degree of
    longitude is not a degree of latitude anywhere but the equator.

    Returns:
        The enclosed area, or 0.0 for a ring too small to enclose anything.
    """
    if len(ring) < 3:
        return 0.0
    pairs = zip(ring, [*ring[1:], ring[0]], strict=True)
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in pairs)) / 2


def coverage(groups: Sequence[Sequence[Point]],
             everything: Sequence[Point]) -> int:
    """What share of the whole an average group's hull covers, in percent.

    The number behind a picture of grouped stops, and the one that says whether
    the grouping is geographic at all. A fleet split into territories gives
    small disjoint hulls and a low share; a fleet split by anything else --
    round-robin, or a clock -- gives hulls that each swell towards the whole
    round and sit on top of one another.

    Measured rather than asserted, because "three copies of the whole round" is
    the kind of claim that stays in a docstring long after the data stopped
    supporting it. It is a ratio of two hull areas, so the squared-degree unit
    cancels; see `area`.

    Args:
        groups: One sequence of points per group.
        everything: Every point, whichever group it fell in.

    Returns:
        Percent, or 0 when the points enclose no area at all.
    """
    whole = area(hull(everything))
    if whole <= 0 or not groups:
        return 0
    shares = [area(hull(group)) for group in groups]
    return round(sum(shares) / len(shares) / whole * 100)


def region(target: Any, points: Sequence[Point], edge: str, label: str,
           opacity: float = 0.12) -> bool:
    """Shade the convex hull of `points`, if they make one.

    Returns:
        Whether a polygon was drawn. False means fewer than three distinct
        stops, which is a fact about the group worth reporting rather than a
        silently missing shape.
    """
    ring = hull(points)
    if len(ring) < 3:
        return False
    folium.Polygon(
        locations=[list(p) for p in ring], tooltip=label, color=edge,
        weight=2, fill=True, fill_color=edge, fill_opacity=opacity,
    ).add_to(target)
    return True


def group(canvas: folium.Map, name: str, shown: bool = True) -> folium.FeatureGroup:
    """A named, toggleable layer. Call `controls` once every layer exists."""
    layer = folium.FeatureGroup(name=name, show=shown)
    layer.add_to(canvas)
    return layer


def controls(canvas: folium.Map) -> None:
    """Add the layer switcher. Only useful after `group` has been called."""
    folium.LayerControl(collapsed=False).add_to(canvas)


def legend(canvas: folium.Map, entries: dict[str, str], title: str) -> None:
    """A fixed swatch box, because a coloured map without one is a puzzle.

    Args:
        canvas: The map to draw on.
        entries: Label to colour, in the order they should be listed.
        title: Heading for the box.
    """
    rows = "".join(
        f'<div><span style="background:{value};width:11px;height:11px;'
        f'display:inline-block;margin-right:6px;border-radius:2px"></span>'
        f'{name}</div>'
        for name, value in entries.items())
    canvas.get_root().html.add_child(folium.Element(
        f'<div style="position:fixed;bottom:24px;left:24px;z-index:9999;'
        f'background:rgba(255,255,255,.92);padding:10px 13px;border-radius:6px;'
        f'font:12px/1.55 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)">'
        f'<b>{title}</b>{rows}</div>'))


def save(canvas: folium.Map, path: Path) -> Path:
    """Write the map and say where it went.

    Callers pass a path beside their own script -- `Path(__file__).parent /
    "name.html"` -- so the output lands with the example that made it. One
    script writes to the working directory instead, which is why the
    repository's `.gitignore` needs a rule naming that file specifically.

    Returns:
        The path written.
    """
    canvas.save(str(path))
    print(f"   map written to {path}")
    return path
