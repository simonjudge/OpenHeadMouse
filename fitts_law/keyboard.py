"""On-screen QWERTY keyboard layout for the Typing Test mode.

Lays out standard letter rows across the full screen width/height (above a
prompt strip pinned to the bottom of the screen), in either a 'consistent'
(uniform key size) or 'random' (per-key discrete-width square, fixed once per
test run unless re-randomized per word) mode. Each key is a square: in
'random' mode its width/height is drawn from `possible_widths` (the same
discrete list used by the ISO Standard/Edge modes' Widths field); in
'consistent' mode every key uses the mean of `possible_widths`. Any space
left over after sizing the keys is spread out as gaps between/around them, so
a small key size on a large screen forces reaches across a large chunk of the
screen rather than tiny neighbour-to-neighbour hops. Hit-testing here is
rectangular (a real key's shape), which is an intentional, documented
deviation from stimuli.py's shared circular hit-test - on-screen keyboard
keys are rectangles, not circles.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
TYPING_WORDS = [
    "THE", "QUICK", "BROWN", "FOX", "JUMPS", "OVER", "LAZY", "DOG",
    "ZEBRA", "PIZZA", "JAZZY", "MIX", "VOLT", "WAXY", "GYM", "QUIZ",
]

PROMPT_HEIGHT_PX = 100.0
DEFAULT_KEY_WIDTH_PX = 90.0


@dataclass(frozen=True)
class KeyRect:
    letter: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height


def _pick_sizes(count: int, mode: str, possible_widths: list[float], rng: random.Random) -> list[float]:
    """Returns `count` key sizes (each used as both width and height, i.e. a
    square). In 'random' mode each key independently picks one of
    `possible_widths`; otherwise every key gets the mean of `possible_widths`,
    a single stable size."""
    if mode == "random":
        return [rng.choice(possible_widths) for _ in range(count)]
    size = statistics.mean(possible_widths)
    return [size] * count


def _spread(sizes: list[float], total_span: float) -> list[float]:
    """Returns the start position of each item in `sizes`, spread with equal
    gaps before/between/after so they span the full `total_span` edge to
    edge instead of clustering to one side."""
    used = sum(sizes)
    gap = max(0.0, (total_span - used) / (len(sizes) + 1))
    positions: list[float] = []
    cursor = gap
    for size in sizes:
        positions.append(cursor)
        cursor += size + gap
    return positions


def build_keyboard_layout(
    screen_w: float,
    screen_h: float,
    key_size_mode: str = "consistent",
    prompt_height_px: float = PROMPT_HEIGHT_PX,
    possible_widths: list[float] | None = None,
    rng: random.Random | None = None,
) -> dict[str, KeyRect]:
    """Returns a dict mapping each uppercase letter to its KeyRect. The
    keyboard spans the full width and the top `screen_h - prompt_height_px`
    of the screen, leaving the bottom `prompt_height_px` strip for the
    prompt. Every key is a square (see `_pick_sizes`); any space left over
    after sizing the keys becomes gaps around/between them (see `_spread`),
    so small key sizes make reaches span a large part of the screen even
    though the keyboard itself still fills it.
    """
    rng = rng or random.Random()
    widths_pool = list(possible_widths) if possible_widths else [DEFAULT_KEY_WIDTH_PX]
    available_height = screen_h - prompt_height_px

    row_sizes = [_pick_sizes(len(letters), key_size_mode, widths_pool, rng) for letters in ROWS]
    row_heights = [max(sizes) for sizes in row_sizes]

    total_row_height = sum(row_heights)
    if total_row_height > available_height and total_row_height > 0:
        # Degenerate case: the picked sizes don't fit vertically. Scale every
        # picked size down by one global factor so keys stay square.
        scale = available_height / total_row_height
        row_sizes = [[s * scale for s in sizes] for sizes in row_sizes]
        row_heights = [h * scale for h in row_heights]

    row_ys = _spread(row_heights, available_height)

    layout: dict[str, KeyRect] = {}
    for letters, sizes, row_height, row_y in zip(ROWS, row_sizes, row_heights, row_ys):
        col_xs = _spread(sizes, screen_w)
        for letter, size, x in zip(letters, sizes, col_xs):
            y = row_y + (row_height - size) / 2
            layout[letter] = KeyRect(letter=letter, x=x, y=y, width=size, height=size)
    return layout


def key_at(layout: dict[str, KeyRect], x: float, y: float) -> str | None:
    for letter, rect in layout.items():
        if rect.contains(x, y):
            return letter
    return None
