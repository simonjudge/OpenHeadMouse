"""Tests for fitts_law/keyboard.py: on-screen keyboard layout generation."""

from __future__ import annotations

import random

import pytest

from fitts_law.keyboard import ROWS, build_keyboard_layout, key_at


def test_consistent_mode_uses_the_mean_width_for_every_key_as_a_square():
    layout = build_keyboard_layout(1920, 1080, "consistent", prompt_height_px=100, possible_widths=[30, 60])
    for rect in layout.values():
        assert rect.width == pytest.approx(45)
        assert rect.height == pytest.approx(45)  # square: width == height


def test_consistent_mode_spreads_keys_across_the_full_screen_with_gaps():
    layout = build_keyboard_layout(1920, 1080, "consistent", prompt_height_px=100, possible_widths=[50])
    top_row = ROWS[0]  # "QWERTYUIOP", 10 keys
    for letter in top_row:
        assert layout[letter].width == pytest.approx(50)

    first_key = layout[top_row[0]]
    last_key = layout[top_row[-1]]
    assert first_key.x > 0  # a gap before the first key
    assert last_key.x + last_key.width < 1920  # a gap after the last key
    assert last_key.x + last_key.width > 1920 / 2  # but the row still reaches well past the midpoint

    gap_before_first = first_key.x
    gap_after_last = 1920 - (last_key.x + last_key.width)
    assert gap_before_first == pytest.approx(gap_after_last)


def test_random_layout_keys_are_squares_drawn_from_the_possible_widths_list():
    widths = [30.0, 60.0]
    layout = build_keyboard_layout(1920, 1080, "random", prompt_height_px=100, possible_widths=widths, rng=random.Random(42))
    for rect in layout.values():
        assert rect.width == rect.height  # rounded squares
        assert rect.width in widths
    seen_widths = {layout[letter].width for row_letters in ROWS for letter in row_letters}
    assert seen_widths == set(widths)  # both sizes actually show up with this seed


def test_random_layout_is_fixed_once_generated_not_per_lookup():
    rng = random.Random(7)
    layout = build_keyboard_layout(1920, 1080, "random", possible_widths=[30, 60], rng=rng)
    # Re-reading the same layout dict gives identical rects every time.
    assert layout["Q"].width == layout["Q"].width
    assert layout["Q"] == layout["Q"]


def test_all_keys_lie_within_screen_bounds():
    layout = build_keyboard_layout(1920, 1080, "random", prompt_height_px=100, possible_widths=[30, 60], rng=random.Random(1))
    for rect in layout.values():
        assert rect.x >= 0
        assert rect.y >= 0
        assert rect.x + rect.width <= 1920 + 1e-6
        assert rect.y + rect.height <= 1080 - 100 + 1e-6  # keyboard sits above the bottom prompt strip


def test_no_space_bar_key_in_the_layout():
    layout = build_keyboard_layout(1920, 1080, "consistent", prompt_height_px=100, possible_widths=[50])
    assert " " not in layout
    assert set(layout.keys()) == set("".join(ROWS))


def test_key_at_finds_the_containing_key():
    layout = build_keyboard_layout(1920, 1080, "consistent", prompt_height_px=100, possible_widths=[50])
    q_rect = layout["Q"]
    cx, cy = q_rect.center
    assert key_at(layout, cx, cy) == "Q"
    assert key_at(layout, -10, -10) is None
