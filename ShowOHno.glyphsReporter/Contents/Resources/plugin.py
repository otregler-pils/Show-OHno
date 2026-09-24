# encoding: utf-8
"""
Show OHno — Reporter plugin for Glyphs 3/4 (View > Show OHno).

Draws a small spacing proof card (e.g. HHxHnxnn) directly above the active
glyph in the Edit View, centred on the glyph's body. The card fades in and
out when it doesn't fit the visible area or the zoom is out of range, and
follows the stylistic sets active in the Edit View (suffix-named alternates).

Settings are in the Edit View context menu (right-click) while the reporter
is switched on: theme, kerning, highlight, position, size, zoom range and
proof string.

MIT License — see LICENSE in the repository.
"""

import time
import objc
from AppKit import (
    NSColor, NSBezierPath, NSGraphicsContext, NSAffineTransform, NSShadow,
    NSMakeRect, NSMakeSize,
)
from GlyphsApp import Glyphs, ONSTATE, OFFSTATE
from GlyphsApp.plugins import ReporterPlugin

try:
    from Quartz import (
        CGContextSetAlpha, CGContextBeginTransparencyLayer, CGContextEndTransparencyLayer,
    )
    HAS_QUARTZ = True
except Exception:
    HAS_QUARTZ = False

PLUGIN_ID = "com.pilstype.ShowOHno"
KEY_DARK = PLUGIN_ID + ".darkTheme"
KEY_KERNING = PLUGIN_ID + ".kerningOn"
KEY_STRING = PLUGIN_ID + ".primaryString"
KEY_SIZE = PLUGIN_ID + ".size"
KEY_POSITION = PLUGIN_ID + ".position"      # "above" / "below"
KEY_HIGHLIGHT = PLUGIN_ID + ".highlight"
KEY_MINZOOM = PLUGIN_ID + ".hideBelow"    # hide below this Edit View size (pt), 0 = never
KEY_MAXZOOM = PLUGIN_ID + ".hideAbove"    # hide above this Edit View size (pt), 0 = never

# {g} = glyph selected in the Edit View
TEMPLATE_LINES = [
    ["H", "H", "{g}", "H", "n", "{g}", "n", "n"],
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
]

MIN_ZOOMS = [("Never", 0), ("50 pt", 50), ("100 pt", 100), ("150 pt", 150), ("250 pt", 250)]
MAX_ZOOMS = [("Never", 0), ("400 pt", 400), ("500 pt", 500), ("700 pt", 700), ("1000 pt", 1000)]

SIZES = [("Small", 48), ("Medium", 72), ("Large", 110)]   # card em size in pt

PAD_FACTOR = 0.55   # side padding, as a fraction of cap height
PAD_FACTOR_Y = 0.22 # top/bottom padding outside the body (ascender..descender), fraction of cap height
GAP = 22.0          # distance between the glyph and the card
MARGIN = 8.0        # minimal distance from the edge of the visible area
CORNER = 10.0
FADE_DURATION = 0.18  # seconds
FRAME = 1.0 / 60.0


def _get_default(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        value = None
    return fallback if value is None else value


def _kerning(font, master_id, left_glyph, right_glyph):
    """Effective LTR kerning: exceptions first, then groups."""
    if left_glyph is None or right_glyph is None:
        return 0.0
    left_name, right_name = left_glyph.name, right_glyph.name
    left_group = left_glyph.rightKerningKey or left_name
    right_group = right_glyph.leftKerningKey or right_name
    seen = set()
    for pair in ((left_name, right_name), (left_name, right_group),
                 (left_group, right_name), (left_group, right_group)):
        if pair in seen or not pair[0] or not pair[1]:
            continue
        seen.add(pair)
        try:
            value = font.kerningForPair(master_id, pair[0], pair[1])
        except Exception:
            value = None
        # Some builds return a huge sentinel (NSNotFound) for "no kerning".
        if value is not None and abs(value) < 10000:
            return float(value)
    return 0.0


def _active_features(tab):
    """OpenType features switched on in the Edit View (e.g. ['ss01'])."""
    feats = None
    try:
        feats = tab.features
    except Exception:
        feats = None
    if not feats:
        try:
            feats = tab.graphicView().features()
        except Exception:
            feats = None
    if not feats:
        return []
    result = []
    for f in feats:
        try:
            tag = str(f.name) if hasattr(f, "name") else str(f)
        except Exception:
            continue
        if tag:
            result.append(tag)
    return result


def _substituted_name(font, name, features):
    """Apply active features by Glyphs naming convention: n -> n.ss01 -> n.ss01.ss03 …

    Works for features whose alternates use the usual suffix naming
    (ss01–ss20, salt, cv01…). Feature code itself is not interpreted.
    """
    current = name
    for tag in features:
        candidate = current + "." + tag
        g = font.glyphs[candidate]
        if g is None and current != name:
            # also allow n.ss03 when n.ss01 is already applied but n.ss01.ss03 doesn't exist
            candidate = name + "." + tag
            g = font.glyphs[candidate]
        if g is not None and g.export:
            current = candidate
    return current


class ShowOHno(ReporterPlugin):

    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "OHno", "cs": "OHno"})
        Glyphs.registerDefaults({
            KEY_DARK: False,
            KEY_KERNING: True,
            KEY_STRING: 0,
            KEY_SIZE: 72,
            KEY_POSITION: "above",
            KEY_HIGHLIGHT: False,
            KEY_MINZOOM: 50,
            KEY_MAXZOOM: 500,
        })

    # ------------------------------------------------------------------ drawing

    @objc.python_method
    def start(self):
        self._alpha = 0.0
        self._lastTime = None
        self._lastCard = None
        self._lastView = None
        self._tickPending = False

    @objc.python_method
    def foregroundInViewCoords(self, layer=None):
        try:
            self._fadeAndDraw(layer)
        except Exception as e:
            import traceback
            print("Show OHno error:", e)
            print(traceback.format_exc())

    @objc.python_method
    def _fadeAndDraw(self, layer):
        if not hasattr(self, "_alpha"):
            self.start()

        font = Glyphs.font
        tab = font.currentTab if font is not None else None
        view = None
        try:
            view = tab.graphicView() if tab is not None else None
        except Exception:
            view = None
        if view is not self._lastView:
            # Different tab/window: never fade out a card from somewhere else.
            self._lastCard = None
            self._alpha = 0.0
            self._lastView = view

        card, visible = self._buildCard(layer)
        if card is not None:
            self._lastCard = card
        else:
            visible = False
            card = self._lastCard

        now = time.time()
        dt = 0.0 if self._lastTime is None else min(now - self._lastTime, 0.1)
        self._lastTime = now
        step = dt / FADE_DURATION if dt > 0 else FRAME / FADE_DURATION
        target = 1.0 if visible else 0.0
        if self._alpha < target:
            self._alpha = min(target, self._alpha + step)
        elif self._alpha > target:
            self._alpha = max(target, self._alpha - step)

        if card is not None and self._alpha > 0.001:
            self._renderCard(card, self._alpha)
        if self._alpha == 0.0 and not visible:
            self._lastCard = None

        if self._alpha != target:
            self._scheduleTick()
        else:
            self._lastTime = None

    @objc.python_method
    def _scheduleTick(self):
        if self._tickPending:
            return
        self._tickPending = True
        self.performSelector_withObject_afterDelay_("fadeTick:", None, FRAME)

    def fadeTick_(self, sender):
        self._tickPending = False
        try:
            Glyphs.redraw()
        except Exception:
            pass

    @objc.python_method
    def _buildCard(self, activeLayer):
        """Return (card, visible). card is None when nothing can be built."""
        card, visible = self._buildCardInner(activeLayer)
        return card, visible

    @objc.python_method
    def _buildCardInner(self, activeLayer):
        font = Glyphs.font
        if font is None:
            return None, False
        tab = font.currentTab
        if tab is None:
            return None, False
        if activeLayer is None:
            if not font.selectedLayers:
                return None, False
            activeLayer = font.selectedLayers[0]
        glyph = activeLayer.parent
        if glyph is None:
            return None, False

        origin = tab.selectedLayerOrigin
        if origin is None:
            return None, False
        viewScale = float(tab.scale)
        viewPort = tab.viewPort

        masterId = activeLayer.associatedMasterId or font.selectedFontMaster.id
        master = font.masters[masterId] or font.selectedFontMaster
        ascender = float(master.ascender or 800)
        descender = float(master.descender or -200)
        upm = float(font.upm or 1000)
        capHeight = float(master.capHeight or ascender * 0.7)

        # --- build the proof line
        index = int(_get_default(KEY_STRING, 0))
        if not 0 <= index < len(TEMPLATE_LINES):
            index = 0
        kerningOn = bool(_get_default(KEY_KERNING, True))

        features = _active_features(tab)

        items = []
        for token in TEMPLATE_LINES[index]:
            if token == "{g}":
                items.append((glyph, activeLayer, True))
                continue
            g = font.glyphs[_substituted_name(font, token, features)]
            if g is None:
                continue
            l = g.layers[masterId]
            if l is not None:
                items.append((g, l, False))
        if not items:
            return None, False

        positions = []
        penX = 0.0
        for i, (g, l, isSel) in enumerate(items):
            positions.append(penX)
            penX += float(l.width or 0)
            if kerningOn and i < len(items) - 1:
                penX += _kerning(font, masterId, g, items[i + 1][0])
        lineWidth = max(penX, 1.0)

        # --- card geometry (view coordinates)
        # Vertically the text sits on the body (descender..ascender) plus padding.
        s = float(_get_default(KEY_SIZE, 72)) / upm
        maxCardWidth = viewPort.size.width - 2 * MARGIN
        needed = (lineWidth + 2 * PAD_FACTOR * capHeight) * s
        if needed > maxCardWidth:
            s = max(maxCardWidth / (lineWidth + 2 * PAD_FACTOR * capHeight), 0.005)
        PAD_X = PAD_FACTOR * capHeight * s
        PAD_Y = PAD_FACTOR_Y * capHeight * s
        cardW = lineWidth * s + 2 * PAD_X
        cardH = (ascender - descender) * s + 2 * PAD_Y   # centred on the body

        # Horizontal: centre the whole card on the selected glyph's body
        # (its advance-width box in the Edit View).
        selCenterView = origin.x + float(activeLayer.width or 0) * viewScale / 2.0
        cardX = selCenterView - cardW / 2.0

        # Vertical: above the ascender or the glyph's own top (accents),
        # below the descender or the glyph's own bottom.
        glyphTop, glyphBottom = ascender, descender
        try:
            b = activeLayer.bounds
            if b.size.height > 0:
                glyphTop = max(ascender, b.origin.y + b.size.height)
                glyphBottom = min(descender, b.origin.y)
        except Exception:
            pass
        topY = origin.y + glyphTop * viewScale + GAP
        bottomY = origin.y + glyphBottom * viewScale - GAP - cardH
        vpMinY = viewPort.origin.y + MARGIN
        vpMaxY = viewPort.origin.y + viewPort.size.height - MARGIN
        # No flipping: if the card doesn't fit on its side, just hide it.
        if _get_default(KEY_POSITION, "above") == "below":
            cardY = bottomY
            fits = cardY >= vpMinY
        else:
            cardY = topY
            fits = cardY + cardH <= vpMaxY

        # Hide when zoomed out: Edit View font size (the "pt" value bottom right)
        # below the threshold.
        viewSize = upm * viewScale
        minZoom = float(_get_default(KEY_MINZOOM, 50))
        maxZoom = float(_get_default(KEY_MAXZOOM, 500))
        if minZoom > 0 and viewSize < minZoom:
            fits = False
        if maxZoom > 0 and viewSize > maxZoom:
            fits = False


        baselineY = cardY + PAD_Y - descender * s
        glyphPaths = []
        for (g, l, isSel), x in zip(items, positions):
            path = l.completeBezierPath
            if path is None:
                continue
            t = NSAffineTransform.transform()
            t.translateXBy_yBy_(cardX + PAD_X + x * s, baselineY)
            t.scaleBy_(s)
            p = path.copy()
            p.transformUsingAffineTransform_(t)
            glyphPaths.append((p, isSel))

        card = {"rect": NSMakeRect(cardX, cardY, cardW, cardH), "paths": glyphPaths}
        return card, fits

    @objc.python_method
    def _renderCard(self, cardData, alpha):
        ctx = NSGraphicsContext.currentContext()
        cg = None
        if HAS_QUARTZ and alpha < 1.0:
            try:
                cg = ctx.CGContext()
            except Exception:
                try:
                    cg = ctx.graphicsPort()
                except Exception:
                    cg = None
        NSGraphicsContext.saveGraphicsState()
        if cg is not None:
            CGContextSetAlpha(cg, alpha)
            CGContextBeginTransparencyLayer(cg, None)

        # --- colours
        # Low-contrast palette so the card doesn't fight with the Edit View.
        dark = bool(_get_default(KEY_DARK, False))
        if dark:
            # exact mirror of the light theme
            bg = NSColor.colorWithCalibratedWhite_alpha_(0.035, 0.98)
            fg = NSColor.colorWithCalibratedWhite_alpha_(0.90, 1.0)   # 90 % white
            highlight = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.62, 0.35, 1.0)
        else:
            bg = NSColor.colorWithCalibratedWhite_alpha_(0.965, 0.98)
            fg = NSColor.colorWithCalibratedWhite_alpha_(0.10, 1.0)   # 90 % black
            highlight = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.80, 0.42, 0.15, 1.0)
        accent = highlight if bool(_get_default(KEY_HIGHLIGHT, False)) else fg

        # --- card background with shadow
        rect = cardData["rect"]
        card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, CORNER, CORNER)

        NSGraphicsContext.saveGraphicsState()
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.12))
        shadow.setShadowBlurRadius_(10.0)
        shadow.setShadowOffset_(NSMakeSize(0, -2))
        shadow.set()
        bg.set()
        card.fill()
        NSGraphicsContext.restoreGraphicsState()


        # --- glyphs
        NSGraphicsContext.saveGraphicsState()
        card.addClip()
        for p, isSel in cardData["paths"]:
            (accent if isSel else fg).set()
            p.fill()
        NSGraphicsContext.restoreGraphicsState()

        if cg is not None:
            CGContextEndTransparencyLayer(cg)
        NSGraphicsContext.restoreGraphicsState()

    # ------------------------------------------------------------- context menu

    @objc.python_method
    def conditionalContextMenus(self):
        dark = bool(_get_default(KEY_DARK, False))
        kern = bool(_get_default(KEY_KERNING, True))
        high = bool(_get_default(KEY_HIGHLIGHT, False))
        size = int(_get_default(KEY_SIZE, 72))
        pos = _get_default(KEY_POSITION, "above")
        idx = int(_get_default(KEY_STRING, 0))

        menus = [
            {"name": "OHno: Dark Theme", "action": self.toggleTheme_,
             "state": ONSTATE if dark else OFFSTATE},
            {"name": "OHno: Kerning", "action": self.toggleKerning_,
             "state": ONSTATE if kern else OFFSTATE},
            {"name": "OHno: Highlight Selected Glyph", "action": self.toggleHighlight_,
             "state": ONSTATE if high else OFFSTATE},
            {"name": "OHno: Show Below Glyph", "action": self.togglePosition_,
             "state": ONSTATE if pos == "below" else OFFSTATE},
        ]
        for label, pt in SIZES:
            menus.append({"name": "OHno Size: %s (%d pt)" % (label, pt),
                          "action": self.setSize_,
                          "state": ONSTATE if size == pt else OFFSTATE})
        minZoom = int(_get_default(KEY_MINZOOM, 50))
        for label, value in MIN_ZOOMS:
            menus.append({"name": "OHno Hide When Zoomed Below: %s" % label,
                          "action": self.setMinZoom_,
                          "state": ONSTATE if minZoom == value else OFFSTATE})
        maxZoom = int(_get_default(KEY_MAXZOOM, 500))
        for label, value in MAX_ZOOMS:
            menus.append({"name": "OHno Hide When Zoomed Above: %s" % label,
                          "action": self.setMaxZoom_,
                          "state": ONSTATE if maxZoom == value else OFFSTATE})
        for i, template in enumerate(TEMPLATE_LINES):
            text = "".join("x" if t == "{g}" else t for t in template)
            menus.append({"name": "OHno String %d: %s" % (i + 1, text),
                          "action": self.setString_,
                          "state": ONSTATE if i == idx else OFFSTATE})
        return menus

    def toggleTheme_(self, sender):
        Glyphs.defaults[KEY_DARK] = not bool(_get_default(KEY_DARK, False))
        Glyphs.redraw()

    def toggleKerning_(self, sender):
        Glyphs.defaults[KEY_KERNING] = not bool(_get_default(KEY_KERNING, True))
        Glyphs.redraw()

    def toggleHighlight_(self, sender):
        Glyphs.defaults[KEY_HIGHLIGHT] = not bool(_get_default(KEY_HIGHLIGHT, False))
        Glyphs.redraw()

    def togglePosition_(self, sender):
        current = _get_default(KEY_POSITION, "above")
        Glyphs.defaults[KEY_POSITION] = "above" if current == "below" else "below"
        Glyphs.redraw()

    def setSize_(self, sender):
        title = str(sender.title())
        for label, pt in SIZES:
            if title.startswith("OHno Size: %s" % label):
                Glyphs.defaults[KEY_SIZE] = pt
        Glyphs.redraw()

    def setMinZoom_(self, sender):
        title = str(sender.title())
        for label, value in MIN_ZOOMS:
            if title.endswith(": " + label):
                Glyphs.defaults[KEY_MINZOOM] = value
        Glyphs.redraw()

    def setMaxZoom_(self, sender):
        title = str(sender.title())
        for label, value in MAX_ZOOMS:
            if title.endswith(": " + label):
                Glyphs.defaults[KEY_MAXZOOM] = value
        Glyphs.redraw()

    def setString_(self, sender):
        title = str(sender.title())
        try:
            number = int(title.split("String ")[1].split(":")[0])
            Glyphs.defaults[KEY_STRING] = number - 1
        except Exception:
            pass
        Glyphs.redraw()

    @objc.python_method
    def __file__(self):
        return __file__
