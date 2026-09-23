"""ISO 9241-411 task generation: multi-directional circular layout + extremity targets.

The circular task and the click-order sequence follow the ISO 9241-9/9241-411
"multi-directional tapping" convention: targets are placed evenly around a
circle of radius A (the amplitude), and are visited in an order that alternates
across the circle (roughly opposite each time) rather than around its rim, so
each movement is a full-amplitude reach rather than a short hop to a neighbour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Condition:
    """One (amplitude, width) pair to be repeated for a block of trials."""

    amplitude_px: float
    width_px: float
    label: str


def generate_circle_positions(
    center: tuple[float, float], num_targets: int, amplitude: float
) -> list[tuple[float, float]]:
    """Evenly spaced target centers around a circle of radius `amplitude`."""
    cx, cy = center
    positions = []
    for i in range(num_targets):
        angle = 2 * math.pi * i / num_targets
        positions.append((cx + amplitude * math.cos(angle), cy + amplitude * math.sin(angle)))
    return positions


def multi_directional_sequence(num_targets: int) -> list[int]:
    """ISO tapping order: index k visits target (k * step) % n.

    For odd `num_targets` (the ISO-recommended case, e.g. 9 or 13), step = n // 2
    is coprime with n, so this produces a full permutation of range(n) - every
    target is visited exactly once, each hop crossing (close to) the full
    diameter, before any target repeats.

    For even `num_targets`, n // 2 shares a factor with n, so that step alone
    would only cycle through half the targets. Instead we search outward from
    n // 2 for the nearest step that *is* coprime with n, preserving the
    near-diameter hop distance as closely as possible. Highly composite small
    n (e.g. 6, whose only units mod 6 are 1 and 5) degrade to a sequential
    walk - an inherent number-theory limit, not a bug.
    """
    if num_targets < 3:
        raise ValueError("num_targets must be at least 3")
    center = num_targets // 2
    step = center
    if math.gcd(step, num_targets) != 1:
        for delta in range(1, num_targets):
            for candidate in (center - delta, center + delta):
                if 1 <= candidate < num_targets and math.gcd(candidate, num_targets) == 1:
                    step = candidate
                    break
            else:
                continue
            break
    return [(k * step) % num_targets for k in range(num_targets)]


def generate_edge_extremity_targets(
    screen_w: float, screen_h: float, inset_px: float = 40.0, num_targets: int = 8
) -> list[tuple[float, float]]:
    """Generate `num_targets` points evenly spaced around the screen perimeter,
    inset from the true screen boundary so they sit outside the default
    corner-failsafe region in core/headmouse.py rather than colliding with it.
    When num_targets=8, falls back to the original 4 edge midpoints + 4 corners.
    """
    left = inset_px
    right = screen_w - inset_px
    top = inset_px
    bottom = screen_h - inset_px

    if num_targets == 8:
        # Original hardcoded 8-point layout
        mid_x = screen_w / 2
        mid_y = screen_h / 2
        return [
            (mid_x, top),
            (right, top),
            (right, mid_y),
            (right, bottom),
            (mid_x, bottom),
            (left, bottom),
            (left, mid_y),
            (left, top),
        ]

    # For other counts, distribute points evenly around the perimeter
    width = right - left
    height = bottom - top
    perimeter = 2 * (width + height)
    points: list[tuple[float, float]] = []
    segment_length = perimeter / num_targets

    for i in range(num_targets):
        distance = i * segment_length
        # Traverse: right edge (top to bottom), bottom edge (right to left),
        # left edge (bottom to top), top edge (left to right)
        if distance < height:
            # Right edge
            x = right
            y = top + distance
        elif distance < width + height:
            # Bottom edge
            x = right - (distance - height)
            y = bottom
        elif distance < 2 * width + height:
            # Left edge
            x = left
            y = bottom - (distance - width - height)
        else:
            # Top edge
            x = left + (distance - 2 * width - height)
            y = top
        points.append((x, y))

    return points


def max_safe_circle_radius(screen_w: float, screen_h: float, width_px: float, margin_px: float = 20.0) -> float:
    """Largest amplitude whose full multi-directional circle (plus stimulus
    radius and a safety margin) still fits within the screen bounds.

    Prevents targets from being plotted off-screen when a requested amplitude
    is large relative to a short screen dimension (e.g. amplitude=600 on a
    1080-tall screen would otherwise place vertical targets above y=0).
    """
    return max(0.0, min(screen_w, screen_h) / 2 - width_px / 2 - margin_px)


def build_condition_set(
    amplitudes: list[float], widths: list[float]
) -> list[Condition]:
    """Cartesian product of amplitude/width values, one Condition per pair."""
    return [
        Condition(amplitude_px=a, width_px=w, label=f"A={a:.0f} W={w:.0f}")
        for a, w in product(amplitudes, widths)
    ]
