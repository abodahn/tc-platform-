"""Arabic text on the generated PDFs.

Three things must all be true before one Arabic word appears correctly, and
pdf.py did none of them — which is why the Arabic on every PR, PO, RFQ and GRN
came out as boxes or as disconnected letters running the wrong way:

  1. **A font with Arabic glyphs.** pdf.py draws in reportlab's built-in
     Helvetica, a Type1 face whose glyph set is Latin-1. There is no Arabic in
     it to draw, so the viewer substitutes a box per character.
  2. **Shaping.** An Arabic letter has up to four forms depending on its
     neighbours (ب is بـ ـبـ ـب). Unicode stores the abstract letter; a PDF
     stores glyphs. `arabic_reshaper` performs that conversion.
  3. **Bidirectional reordering.** Arabic runs right-to-left, and reportlab lays
     glyphs left-to-right in the order given. Without `python-bidi` the words
     come out mirrored — readable backwards, which is worse than unreadable
     because nobody notices on a number.

**The font shipped here is Noto Naskh Arabic, under the SIL Open Font License**
(app/static/vendor/fonts/, licence alongside it). That choice is deliberate: the
Arabic faces on a Windows machine — arial, tahoma, segoeui — are
Microsoft-licensed and this repository is public, so none of them may be
committed. Render has no system Arabic font at all, which is why shipping one is
the only thing that makes Arabic work in production.

FONT_CANDIDATES is searched in order and the first readable file wins. The
vendored pair is first; the Windows paths at the end are a developer-machine
fallback only and must never be copied into the repository.

When no font is found, Arabic is passed through unshaped and a warning is
logged ONCE per process. That is a deliberate choice: silently drawing boxes is
how this defect survived to production in the first place.
"""
import logging
import os
import re

log = logging.getLogger(__name__)

# U+0600-06FF Arabic, U+0750-077F supplement, U+FB50-FDFF and U+FE70-FEFF forms.
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "..", "static", "vendor", "fonts")

# Searched in order; the first readable file wins. Add the OFL face to the
# vendor directory and every environment picks it up with no code change.
FONT_CANDIDATES = (
    (os.path.join(_VENDOR, "NotoNaskhArabic-Regular.ttf"),
     os.path.join(_VENDOR, "NotoNaskhArabic-Bold.ttf")),
    (os.path.join(_VENDOR, "Amiri-Regular.ttf"),
     os.path.join(_VENDOR, "Amiri-Bold.ttf")),
    # Linux distributions that ship the Noto family (Render's image may).
    ("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", None),
    # Developer machines only. NEVER vendored — Microsoft licence, public repo.
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
)

AR_FONT = "TCArabic"
AR_FONT_BOLD = "TCArabic-Bold"

_state = {"ready": None, "warned": False, "bold": False}


def has_arabic(s):
    return bool(s) and bool(_ARABIC_RE.search(str(s)))


def register():
    """Register the Arabic face with reportlab. Returns True once a font is in
    place. Idempotent and cheap after the first call."""
    if _state["ready"] is not None:
        return _state["ready"]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for regular, bold in FONT_CANDIDATES:
        if not (regular and os.path.exists(regular)):
            continue
        try:
            pdfmetrics.registerFont(TTFont(AR_FONT, regular))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("Arabic font %s unusable: %s", regular, exc)
            continue
        if bold and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont(AR_FONT_BOLD, bold))
                _state["bold"] = True
            except Exception:                                     # noqa: BLE001
                pass
        log.info("Arabic PDF font: %s", regular)
        _state["ready"] = True
        return True
    _state["ready"] = False
    return False


def shape(s):
    """Reshape and bidi-reorder an Arabic string for drawing. Returns non-Arabic
    strings untouched, so it is safe to call on everything."""
    if not has_arabic(s):
        return s
    s = str(s)
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(s))
    except Exception as exc:                                      # noqa: BLE001
        if not _state["warned"]:
            log.warning("Arabic shaping unavailable (%s); text drawn unshaped", exc)
            _state["warned"] = True
        return s


def _warn_once():
    if not _state["warned"]:
        log.warning(
            "No Arabic-capable font found — Arabic on generated PDFs will be "
            "boxes. Drop an OFL face (NotoNaskhArabic-Regular.ttf or "
            "Amiri-Regular.ttf) into app/static/vendor/fonts/.")
        _state["warned"] = True


class ArabicCanvas(object):
    """A reportlab canvas that draws Arabic correctly.

    Everything is delegated to the real canvas; only the four text calls are
    intercepted. Wrapping rather than editing 44 call sites means a draw added
    later is covered too — the previous shape of this bug was exactly a call
    site somebody forgot.

    The font swap is per-call and restored afterwards, so pdf.py's own setFont
    bookkeeping keeps working untouched.
    """

    def __init__(self, inner):
        self._c = inner
        self._font = ("Helvetica", 10)
        self._ok = register()
        if not self._ok:
            _warn_once()

    def __getattr__(self, name):              # everything not overridden below
        return getattr(self._c, name)

    def setFont(self, name, size, *a, **kw):
        self._font = (name, size)
        return self._c.setFont(name, size, *a, **kw)

    def _draw(self, fn, x, y, text, *a, **kw):
        if not has_arabic(text):
            return fn(x, y, text, *a, **kw)
        txt = shape(text)
        if not self._ok:
            return fn(x, y, txt, *a, **kw)
        name, size = self._font
        # Bold is honoured only when a bold Arabic face was found; falling back
        # to the regular face is correct, synthesising a fake bold is not.
        face = AR_FONT_BOLD if (_state["bold"] and "Bold" in name) else AR_FONT
        self._c.setFont(face, size)
        try:
            return fn(x, y, txt, *a, **kw)
        finally:
            self._c.setFont(name, size)

    def drawString(self, x, y, text, *a, **kw):
        return self._draw(self._c.drawString, x, y, text, *a, **kw)

    def drawRightString(self, x, y, text, *a, **kw):
        return self._draw(self._c.drawRightString, x, y, text, *a, **kw)

    def drawCentredString(self, x, y, text, *a, **kw):
        return self._draw(self._c.drawCentredString, x, y, text, *a, **kw)


def wrap(c):
    """Wrap a freshly created canvas. One call per generator in pdf.py."""
    return ArabicCanvas(c)
