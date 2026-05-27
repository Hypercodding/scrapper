"""Human-like timing and motion helpers.

The scraper already uses `random.uniform(min, max)` ranges for inter-action
delays, so the gain here is not "stop using fixed sleeps" — it's centralizing
the timing constants behind semantic names (`read`, `click`, `scroll`, `type`)
so they're tunable in one place and the call sites read like behavior instead
of magic numbers.

`human_pause` uses a triangular distribution biased toward the middle of the
range — closer to how real reaction-time samples cluster than uniform's flat
distribution. Cheap improvement that costs nothing at the call site.
"""
from __future__ import annotations

import random
from typing import Literal

# (min_ms, max_ms, mode_ms) for triangular distribution — mode skews the
# distribution toward typical human behavior rather than worst-case.
_TIMING = {
    "read":   (1200, 3500, 2000),   # reading a card / a paragraph
    "click":  (180,  420,  260),    # short pre-click pause
    "scroll": (400,  900,  550),    # between scroll steps
    "type":   (60,   180,  100),    # per-keystroke cadence
    "fetch":  (4000, 8000, 5500),   # between detail-page fetches
}

PauseKind = Literal["read", "click", "scroll", "type", "fetch"]


async def human_pause(page, kind: PauseKind = "read") -> None:
    """Wait for a `kind`-appropriate duration. Uses triangular distribution
    biased toward typical human timing rather than flat uniform."""
    lo, hi, mode = _TIMING.get(kind, _TIMING["read"])
    ms = random.triangular(lo, hi, mode)
    await page.wait_for_timeout(ms)


async def human_scroll(page, total_px: int = 0) -> None:
    """Scroll in 6–12 jittered steps. If `total_px=0`, scroll roughly one
    viewport-height; otherwise scroll exactly `total_px` total."""
    try:
        if total_px <= 0:
            total_px = await page.evaluate(
                "() => window.innerHeight * (1 + Math.random() * 0.6)"
            ) or 800
        steps = random.randint(6, 12)
        per_step = int(total_px / steps)
        for _ in range(steps):
            jitter = random.randint(-20, 20)
            await page.mouse.wheel(0, per_step + jitter)
            await human_pause(page, "scroll")
    except Exception:
        # Wheel can fail on some Chromium builds — fall back to JS scroll.
        try:
            await page.evaluate(f"window.scrollBy(0, {total_px or 800})")
        except Exception:
            pass


async def human_type(page, selector: str, text: str) -> None:
    """Type one character at a time with per-keystroke jitter. Used when a
    real search-box interaction is preferred over a direct URL navigation."""
    await page.click(selector)
    await human_pause(page, "click")
    for ch in text:
        await page.keyboard.type(ch)
        await human_pause(page, "type")
