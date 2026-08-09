"""Tiny shared helpers with no domain logic."""

from __future__ import annotations


def clamp(value: int, low: int, high: int) -> int:
    """Constrain an int between low and high (inclusive)."""
    return max(low, min(high, value))


def at_index(sequence, index):
    """Safe list access: return sequence[index] or None when unavailable."""
    if isinstance(sequence, list) and index < len(sequence):
        return sequence[index]
    return None
