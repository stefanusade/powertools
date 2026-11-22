"""Konversi warna murni Python (tanpa GTK).

Dipisah dari ``ui.py`` supaya bisa diuji tanpa display dan dipakai ulang oleh
jendela hasil color picker.
"""

from __future__ import annotations

RGB = tuple[int, int, int]
HSL = tuple[float, float, float]
CMYK = tuple[int, int, int, int]


def clamp8(value: float) -> int:
    return max(0, min(255, int(round(value))))


def normalize_rgb(rgb) -> RGB:
    r, g, b = (clamp8(float(v)) for v in rgb)
    return r, g, b


def hex_to_rgb(hex_color: str) -> RGB:
    value = str(hex_color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Warna hex tidak valid: {hex_color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = normalize_rgb(rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_to_hsl(r: int, g: int, b: int) -> HSL:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    high, low = max(rf, gf, bf), min(rf, gf, bf)
    lightness = (high + low) / 2.0
    if high == low:
        return 0.0, 0.0, lightness * 100.0
    delta = high - low
    saturation = delta / (2.0 - high - low) if lightness > 0.5 else delta / (high + low)
    if high == rf:
        hue = ((gf - bf) / delta) % 6
    elif high == gf:
        hue = (bf - rf) / delta + 2
    else:
        hue = (rf - gf) / delta + 4
    return hue * 60.0, saturation * 100.0, lightness * 100.0


def hsl_to_rgb(h: float, s: float, l: float) -> RGB:
    h = float(h) % 360.0
    s = max(0.0, min(100.0, float(s))) / 100.0
    l = max(0.0, min(100.0, float(l))) / 100.0
    if s == 0:
        value = clamp8(l * 255)
        return value, value, value

    def channel(n: int) -> float:
        k = (n + h / 30.0) % 12.0
        a = s * min(l, 1.0 - l)
        return l - a * max(-1.0, min(k - 3.0, 9.0 - k, 1.0))

    return clamp8(channel(0) * 255), clamp8(channel(8) * 255), clamp8(channel(4) * 255)


def rgb_to_cmyk(r: int, g: int, b: int) -> CMYK:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    k = 1.0 - max(rf, gf, bf)
    if k >= 1.0:
        return 0, 0, 0, 100
    c = (1.0 - rf - k) / (1.0 - k)
    m = (1.0 - gf - k) / (1.0 - k)
    y = (1.0 - bf - k) / (1.0 - k)
    return (
        clamp8(c * 100),
        clamp8(m * 100),
        clamp8(y * 100),
        clamp8(k * 100),
    )


def format_color(rgb, fmt: str) -> str:
    """Format CSS untuk sebuah warna: hex, rgb, hsl, atau cmyk."""
    fmt = (fmt or "hex").lower()
    r, g, b = normalize_rgb(rgb)
    if fmt == "rgb":
        return f"rgb({r}, {g}, {b})"
    if fmt == "hsl":
        h, s, l = rgb_to_hsl(r, g, b)
        return f"hsl({round(h)}, {round(s)}%, {round(l)}%)"
    if fmt == "cmyk":
        c, m, y, k = rgb_to_cmyk(r, g, b)
        # device-cmyk() adalah sintaks CMYK resmi di CSS Color 5.
        return f"device-cmyk({c}% {m}% {y}% {k}%)"
    return rgb_to_hex((r, g, b))


def shade_color(rgb, amount: float) -> RGB:
    """Campur warna ke arah putih (positif) atau hitam (negatif).

    ``amount`` 0.0–1.0. Nilai 0.3 berarti 30% lebih terang/gelap.
    """
    r, g, b = normalize_rgb(rgb)
    amount = max(-1.0, min(1.0, float(amount)))
    target = 255.0 if amount >= 0 else 0.0
    factor = abs(amount)
    return tuple(  # type: ignore[return-value]
        clamp8(value + (target - value) * factor) for value in (r, g, b)
    )


def contrast_color(rgb) -> str:
    """Warna teks yang tetap terbaca di atas warna latar ``rgb``."""
    r, g, b = normalize_rgb(rgb)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance > 150 else "#ffffff"


def color_shades(rgb, steps=(10, 20, 30)) -> list[tuple[str, RGB]]:
    """Deret gelap → terang: −30%, −20%, −10%, asli, +10%, +20%, +30%."""
    base = normalize_rgb(rgb)
    result: list[tuple[str, RGB]] = []
    for pct in reversed(steps):
        result.append((f"\u2212{pct}%", shade_color(base, -pct / 100.0)))
    result.append(("Asli", base))
    for pct in steps:
        result.append((f"+{pct}%", shade_color(base, pct / 100.0)))
    return result


def color_palette(rgb) -> list[tuple[str, RGB]]:
    """Palet rekomendasi: harmoni hue + variasi terang/gelap/netral."""
    r, g, b = normalize_rgb(rgb)
    h, s, l = rgb_to_hsl(r, g, b)
    return [
        ("Asli", (r, g, b)),
        ("Komplementer", hsl_to_rgb(h + 180.0, s, l)),
        ("Analog \u221230\u00b0", hsl_to_rgb(h - 30.0, s, l)),
        ("Analog +30\u00b0", hsl_to_rgb(h + 30.0, s, l)),
        ("Triadik +120\u00b0", hsl_to_rgb(h + 120.0, s, l)),
        ("Triadik +240\u00b0", hsl_to_rgb(h + 240.0, s, l)),
        ("Versi terang", shade_color((r, g, b), 0.22)),
        ("Versi gelap", shade_color((r, g, b), -0.22)),
        ("Netral", hsl_to_rgb(h, max(6.0, s * 0.15), l)),
    ]