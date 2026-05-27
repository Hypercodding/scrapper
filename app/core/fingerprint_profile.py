"""Per-session browser fingerprint profile.

Wraps the User-Agent string, Sec-CH-UA hints, viewport size, locale, timezone,
geolocation, and canvas/audio noise scripts into a single dataclass that's
deterministic per `session_id`. Two scrapes that share a session_id get the
same fingerprint (good — Cloudflare correlates by session); two scrapes that
don't share one get different fingerprints (also good — breaks correlation
across what should be independent users).

Wiring: `_launch_browser_with_context` calls `FingerprintProfile.for_session()`
to build a profile, then plumbs `viewport`, `user_agent`, `locale`,
`timezone_id`, `geolocation`, and the two init scripts into `new_context`
(or `launch_persistent_context` in Step 7).

Step 6 (`ProxySession`) will pass the proxy's egress country here so the
timezone and locale align with the IP — a mismatch between proxy country
and TZ/locale is a strong correlation signal.
"""
from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from typing import Tuple

# Last 3 stable Chrome majors at time of writing. Refresh quarterly — keeping
# this list updated is the single cheapest stealth maintenance task.
# Cloudflare flags UAs older than ~6 months as a high-confidence bot signal.
_UA_POOL: Tuple[Tuple[str, str], ...] = (
    ("131.0.0.0", "131"),
    ("130.0.0.0", "130"),
    ("129.0.0.0", "129"),
)

# Common real-desktop resolutions (Steam HW survey + StatCounter top entries).
# Mobile-phone sizes are intentionally excluded — Indeed serves a different
# layout for mobile, which would break the existing selectors.
_VIEWPORT_POOL: Tuple[Tuple[int, int], ...] = (
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1680, 1050),
    (1280, 800),
)

# country_code -> (timezone_id, locale, accept_language, lat, lon)
# Add entries as the proxy provider supports new countries.
_GEO_PROFILES = {
    "US": ("America/New_York", "en-US", "en-US,en;q=0.9", 40.7128, -74.0060),
    "GB": ("Europe/London",    "en-GB", "en-GB,en;q=0.9", 51.5074, -0.1278),
    "CA": ("America/Toronto",  "en-CA", "en-CA,en;q=0.9", 43.6532, -79.3832),
    "AU": ("Australia/Sydney", "en-AU", "en-AU,en;q=0.9", -33.8688, 151.2093),
    "DE": ("Europe/Berlin",    "de-DE", "de-DE,de;q=0.9,en;q=0.8", 52.52, 13.405),
    "FR": ("Europe/Paris",     "fr-FR", "fr-FR,fr;q=0.9,en;q=0.8", 48.8566, 2.3522),
    "NL": ("Europe/Amsterdam", "nl-NL", "nl-NL,nl;q=0.9,en;q=0.8", 52.3676, 4.9041),
    "IN": ("Asia/Kolkata",     "en-IN", "en-IN,en;q=0.9", 28.6139, 77.2090),
}


@dataclass(frozen=True)
class FingerprintProfile:
    """A frozen per-session fingerprint. Two sessions with the same
    `session_id` produce identical profiles; different ids produce
    statistically independent profiles."""

    session_id: str
    user_agent: str
    sec_ch_ua: str
    viewport: dict      # {"width": int, "height": int}
    locale: str
    timezone_id: str
    accept_language: str
    geolocation: dict   # {"latitude": float, "longitude": float, "accuracy": float}
    platform: str       # "Win32" | "MacIntel" | "Linux x86_64"
    dpr: float
    _seed: int          # deterministic canvas/audio noise seed

    @classmethod
    def for_session(
        cls,
        session_id: str = "",
        proxy_country: str = "US",
    ) -> "FingerprintProfile":
        """Build a deterministic profile from `session_id`.

        Pass the empty string to get a random one (uses uuid4 internally).
        Pass `proxy_country` as ISO-3166 alpha-2 to align timezone/locale/
        geolocation with the proxy's egress IP — Cloudflare cross-checks
        these and flags mismatches.
        """
        sid = session_id or uuid.uuid4().hex
        # SHA-256 of the session id seeds a Random — deterministic, so two
        # invocations on different worker replicas produce the same profile
        # for the same session id (matters once Step 7's storage_state
        # reuse spans worker restarts).
        rng = random.Random(int(hashlib.sha256(sid.encode()).hexdigest(), 16))

        ua_version, ua_major = rng.choice(_UA_POOL)
        vw, vh = rng.choice(_VIEWPORT_POOL)
        tz, locale, al, lat, lon = _GEO_PROFILES.get(
            proxy_country.upper() if proxy_country else "US",
            _GEO_PROFILES["US"],
        )
        platform = rng.choice(["Win32", "MacIntel"])

        if platform == "Win32":
            ua_platform = "Windows NT 10.0; Win64; x64"
            sec_ch_platform = "Windows"
        else:
            ua_platform = "Macintosh; Intel Mac OS X 10_15_7"
            sec_ch_platform = "macOS"

        ua = (
            f"Mozilla/5.0 ({ua_platform}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ua_version} Safari/537.36"
        )
        sec_ch_ua = (
            f'"Google Chrome";v="{ua_major}", '
            f'"Chromium";v="{ua_major}", '
            f'"Not?A_Brand";v="24"'
        )

        return cls(
            session_id=sid,
            user_agent=ua,
            sec_ch_ua=sec_ch_ua,
            viewport={"width": vw, "height": vh},
            locale=locale,
            timezone_id=tz,
            accept_language=al,
            geolocation={
                "latitude": lat + rng.uniform(-0.5, 0.5),
                "longitude": lon + rng.uniform(-0.5, 0.5),
                "accuracy": 50.0,
            },
            platform=sec_ch_platform,
            dpr=rng.choice([1.0, 1.25, 1.5, 2.0]),
            _seed=rng.randint(0, 2**31),
        )

    def extra_http_headers(self) -> dict:
        """Headers to install on the context, including Sec-CH-UA hints."""
        return {
            "Accept-Language": self.accept_language,
            "Sec-CH-UA": self.sec_ch_ua,
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": f'"{self.platform}"',
            "Upgrade-Insecure-Requests": "1",
        }

    def canvas_noise_script(self) -> str:
        """Deterministic per-session canvas noise.

        Same seed → same noise pattern across all `toDataURL` calls in the
        session, so the fingerprint hash is stable within a session but
        differs across sessions. Different from the typical "randomize
        every call" approach which itself becomes a detection signal.
        """
        return f"""
        (() => {{
            const seed = {self._seed};
            const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                try {{
                    const ctx = this.getContext('2d');
                    if (ctx && this.width > 0 && this.height > 0) {{
                        const img = ctx.getImageData(0, 0, this.width, this.height);
                        let s = seed;
                        for (let i = 0; i < img.data.length; i += 4) {{
                            s = (s * 16807) % 2147483647;
                            // Tiny ±3-LSB perturbation — imperceptible visually,
                            // big enough to change the canvas hash deterministically.
                            img.data[i]     = (img.data[i]     + (s & 3)) & 0xFF;
                            img.data[i + 1] = (img.data[i + 1] + ((s >> 2) & 3)) & 0xFF;
                        }}
                        ctx.putImageData(img, 0, 0);
                    }}
                }} catch (e) {{ /* SecurityError on cross-origin canvas — ignore */ }}
                return origToDataURL.apply(this, args);
            }};
        }})();
        """

    def audio_noise_script(self) -> str:
        """Deterministic per-session AudioBuffer noise.

        CreepJS audio fingerprinting hashes the float samples returned by
        `AudioBuffer.getChannelData`. ±1e-7 amplitude perturbation changes
        the hash without affecting any actual audio output.
        """
        return f"""
        (() => {{
            const seed = {self._seed};
            const origGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(...args) {{
                const data = origGetChannelData.apply(this, args);
                let s = seed;
                const n = Math.min(data.length, 1000);
                for (let i = 0; i < n; i++) {{
                    s = (s * 16807) % 2147483647;
                    data[i] += (s / 2147483647 - 0.5) * 1e-7;
                }}
                return data;
            }};
        }})();
        """
