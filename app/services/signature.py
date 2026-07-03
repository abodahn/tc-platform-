"""
TC Platform — server-side signature generation.

Renders a person's name into an elegant handwritten-style signature (transparent
PNG data URL) using the vendored Great Vibes font (OFL). Used to auto-provision a
ready-to-use signature for every user so it can be stamped on any paper (PR/PO
PDFs, approvals) without each person having to draw one, and for the one-click
"quick signature" in the profile. Falls back to None if Pillow/font is missing.
"""
import base64
import io
import os

_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "vendor",
                          "fonts", "great-vibes.ttf")
_INK = (26, 42, 96)   # deep navy, reads as ink on white paper

DEFAULT_STYLE = "great-vibes"


def generate_png(name, size=96, color=_INK):
    """Return a data:image/png;base64 signature for `name`, or None on failure."""
    name = (name or "").strip()
    if not name:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    if not os.path.exists(_FONT_PATH):
        return None
    try:
        font = ImageFont.truetype(_FONT_PATH, size)
    except Exception:
        return None
    pad = 26
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bb = probe.textbbox((0, 0), name, font=font)
    w = (bb[2] - bb[0]) + pad * 2
    h = (bb[3] - bb[1]) + pad * 2
    img = Image.new("RGBA", (max(w, 60), max(h, 40)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - bb[0], pad - bb[1]), name, font=font, fill=tuple(color) + (255,))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
