# encoding: utf-8
"""
Show OHno — plugin for Glyphs 3/4.

Adds "Show OHno" to the instance pop-up menu of the preview bar, next to
"Show All Instances". Choosing a proof string there shows the selected glyph
in a spacing proof string such as HHxHnxnn in the preview panel of that tab,
at the preview's own size and in its own black/white colours. Choosing any
other entry of the menu (Show All Instances, an instance, "-") switches the
preview back.

Only public view methods are used: the preview (GSGlyphsPreview) is hidden
while the proof is shown, and the proof is drawn in its place.

Stylistic sets active in the Edit View are applied by suffix naming
(n -> n.ss01).

MIT License — see LICENSE in the repository.
"""

import objc
from AppKit import NSView, NSColor, NSAffineTransform, NSMenu, NSMenuItem
from Foundation import NSObject, NSNotificationCenter, NSTimer

try:
    from Quartz import CIFilter
except Exception:
    CIFilter = None
from GlyphsApp import Glyphs, ONSTATE, OFFSTATE, UPDATEINTERFACE, DRAWFOREGROUND
from GlyphsApp.plugins import GeneralPlugin

PLUGIN_ID = "com.pilstype.ShowOHno"
KEY_STRING = PLUGIN_ID + ".primaryString"
MENU_TAG = PLUGIN_ID + ".previewMenu"

# {g} = glyph selected in the Edit View
TEMPLATE_LINES = [
    ["H", "H", "{g}", "H", "n", "{g}", "n", "n"],
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
]

FIT_MARGIN = 0.94   # a line wider than the panel is shrunk to this share of its width
FLIP_VERTICAL = False   # the preview's F button: False = mirror left/right, True = upside down
SYNC_INTERVAL = 0.2     # seconds between checks of the preview bar settings


def _get_default(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        value = None
    return fallback if value is None else value


def _template_text(template):
    return "".join("x" if t == "{g}" else t for t in template)


# ------------------------------------------------------------------ proof line

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
    try:
        feats = tab.features
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
    """Apply active features by Glyphs naming convention: n -> n.ss01 -> n.ss01.ss03 …"""
    current = name
    for tag in features:
        candidate = current + "." + tag
        g = font.glyphs[candidate]
        if g is None and current != name:
            candidate = name + "." + tag
            g = font.glyphs[candidate]
        if g is not None and g.export:
            current = candidate
    return current


def _tab_selected_layer(tab):
    layer = None
    try:
        layers = tab.selectedLayers
        if layers:
            layer = layers[0]
    except Exception:
        layer = None
    if layer is None:
        try:
            layer = tab.graphicView().activeLayer()   # text cursor, no selection
        except Exception:
            layer = None
    if layer is None or layer.parent is None:
        return None
    return layer


def _build_line(tab):
    try:
        font = tab.parent
    except Exception:
        font = Glyphs.font
    active = _tab_selected_layer(tab)
    if font is None or active is None:
        return None
    masterId = active.associatedMasterId or font.selectedFontMaster.id
    master = font.masters[masterId] or font.selectedFontMaster
    features = _active_features(tab)
    index = int(_get_default(KEY_STRING, 0))
    if not 0 <= index < len(TEMPLATE_LINES):
        index = 0

    items = []
    for token in TEMPLATE_LINES[index]:
        if token == "{g}":
            items.append((active.parent, active))
            continue
        g = font.glyphs[_substituted_name(font, token, features)]
        if g is None:
            continue
        l = g.layers[masterId]
        if l is not None:
            items.append((g, l))
    if not items:
        return None

    positions = []
    penX = 0.0
    for i, (g, l) in enumerate(items):
        positions.append(penX)
        penX += float(l.width or 0)
        if i < len(items) - 1:
            penX += _kerning(font, masterId, g, items[i + 1][0])
    return {
        "layers": [l for g, l in items],
        "positions": positions,
        "width": max(penX, 1.0),
        "ascender": float(master.ascender or 800),
        "descender": float(master.descender or -200),
    }


# ------------------------------------------------------------- the proof view

class ShowOHnoProofView(NSView):
    """Takes the place of a tab's hidden preview and draws the proof line."""

    def initWithTab_preview_(self, tab, preview):
        self = objc.super(ShowOHnoProofView, self).initWithFrame_(preview.frame())
        if self is None:
            return None
        self.tab = tab
        self.preview = preview
        self.state = None
        self.setAutoresizingMask_(preview.autoresizingMask())
        self.setWantsLayer_(True)
        try:
            self.setLayerUsesCoreImageFilters_(True)
        except Exception:
            pass
        return self

    @objc.python_method
    def previewState(self):
        """The preview bar settings, read from the hidden preview."""
        pv = self.preview

        def read(name, fallback):
            try:
                return getattr(pv, name)() if pv.respondsToSelector_(name) else fallback
            except Exception:
                return fallback
        return (
            bool(read("black", True)),
            bool(read("flip", False)),
            round(float(read("radius", 0.0) or 0.0), 3),
            float(read("scale", 0.0) or 0.0),
            tuple(pv.frame().size),
        )

    @objc.python_method
    def sync(self, force=False):
        """Follow the preview bar: colours, flip, blur, size."""
        state = self.previewState()
        if not force and state == self.state:
            return
        self.state = state
        self.setFrame_(self.preview.frame())
        radius = state[2]
        layer = self.layer()
        if layer is not None:
            if radius > 0 and CIFilter is not None:
                blur = CIFilter.filterWithName_("CIGaussianBlur")
                blur.setDefaults()
                blur.setValue_forKey_(radius, "inputRadius")
                layer.setFilters_([blur])
            else:
                layer.setFilters_(None)
        self.setNeedsDisplay_(True)

    def isFlipped(self):
        return False

    def hitTest_(self, point):
        return None

    def drawRect_(self, rect):
        try:
            self._draw()
        except Exception as e:
            import traceback
            print("Show OHno error:", e)
            print(traceback.format_exc())

    @objc.python_method
    def _draw(self):
        line = _build_line(self.tab)
        if line is None:
            return
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height

        # the preview's own size, colours and flip
        black, flip, radius, scale, size = self.previewState()
        if scale <= 0:
            scale = h * 0.7 / 1000.0
        scale = min(scale, w * FIT_MARGIN / line["width"])

        mirror = NSAffineTransform.transform()
        if flip:
            if FLIP_VERTICAL:
                mirror.translateXBy_yBy_(0, h)
                mirror.scaleXBy_yBy_(1, -1)
            else:
                mirror.translateXBy_yBy_(w, 0)
                mirror.scaleXBy_yBy_(-1, 1)

        body = line["ascender"] - line["descender"]
        startX = (w - line["width"] * scale) / 2.0
        baseline = (h - body * scale) / 2.0 - line["descender"] * scale
        (NSColor.whiteColor() if black else NSColor.blackColor()).set()

        for layer, x in zip(line["layers"], line["positions"]):
            path = layer.completeBezierPath
            if path is None:
                continue
            t = NSAffineTransform.transform()
            t.translateXBy_yBy_(startX + x * scale, baseline)
            t.scaleBy_(scale)
            t.appendTransform_(mirror)
            p = path.copy()
            p.transformUsingAffineTransform_(t)
            p.fill()


# --------------------------------------------------------------- switching

def _current_tab():
    font = Glyphs.font
    return font.currentTab if font is not None else None


def _preview_of(tab):
    try:
        return tab.previewView()
    except Exception:
        return None


def _proof_view_of(tab):
    preview = _preview_of(tab)
    if preview is None or preview.superview() is None:
        return None
    for sub in preview.superview().subviews() or []:
        if isinstance(sub, ShowOHnoProofView):
            return sub
    return None


def _set_on(tab, on):
    preview = _preview_of(tab)
    if preview is None or preview.superview() is None:
        print("Show OHno: preview panel not found.")
        return
    proof = _proof_view_of(tab)
    if on:
        if proof is None:
            proof = ShowOHnoProofView.alloc().initWithTab_preview_(tab, preview)
            preview.superview().addSubview_positioned_relativeTo_(proof, 1, preview)  # NSWindowAbove
        preview.setHidden_(True)
        proof.sync(force=True)
        if _controller is not None:
            _controller.startSync()
    else:
        if proof is not None:
            proof.removeFromSuperview()
        preview.setHidden_(False)
        preview.setNeedsDisplay_(True)


def _refresh_current():
    tab = _current_tab()
    if tab is None:
        return
    proof = _proof_view_of(tab)
    if proof is not None:
        proof.sync()
        proof.setNeedsDisplay_(True)


class ShowOHnoController(NSObject):

    # --- following the preview bar (blur slider, F, black/white, size)

    @objc.python_method
    def startSync(self):
        if getattr(self, "timer", None) is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                SYNC_INTERVAL, self, "syncTick:", None, True)

    def syncTick_(self, timer):
        try:
            tab = _current_tab()
            proof = _proof_view_of(tab) if tab is not None else None
            if proof is not None:
                proof.sync()
        except Exception as e:
            print("Show OHno sync error:", e)

    # --- the menu

    def popUpWillShow_(self, notification):
        try:
            tab = _current_tab()
            if tab is None or notification.object() != tab.instancePopupButton():
                return
            self._inject(tab)
        except Exception as e:
            print("Show OHno menu error:", e)

    @objc.python_method
    def _inject(self, tab):
        menu = tab.instancePopupButton().menu()
        for item in list(menu.itemArray()):
            if item.representedObject() == MENU_TAG:
                menu.removeItem_(item)

        on = _proof_view_of(tab) is not None
        current = int(_get_default(KEY_STRING, 0))
        if not 0 <= current < len(TEMPLATE_LINES):
            current = 0

        # "Show OHno": one click, last used string
        show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show OHno", "showOHno:", "")
        show.setTarget_(self)
        show.setRepresentedObject_(MENU_TAG)
        show.setState_(ONSTATE if on else OFFSTATE)

        # "OHno String": pick another string (also switches OHno on)
        sub = NSMenu.alloc().initWithTitle_("OHno String")
        for i, template in enumerate(TEMPLATE_LINES):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                _template_text(template), "chooseString:", "")
            item.setTarget_(self)
            item.setTag_(i)
            item.setState_(ONSTATE if i == current else OFFSTATE)
            sub.addItem_(item)
        strings = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "OHno String: " + _template_text(TEMPLATE_LINES[current]), None, "")
        strings.setSubmenu_(sub)
        strings.setRepresentedObject_(MENU_TAG)

        separator = NSMenuItem.separatorItem()
        separator.setRepresentedObject_(MENU_TAG)
        menu.addItem_(separator)
        menu.addItem_(show)
        menu.addItem_(strings)
        if on:
            self._showTitle(tab)

    @objc.python_method
    def _showTitle(self, tab):
        # A pull-down pop-up shows the title of its first item.
        try:
            tab.instancePopupButton().itemAtIndex_(0).setTitle_("Show OHno")
        except Exception:
            pass

    # --- actions

    def showOHno_(self, sender):
        tab = _current_tab()
        if tab is None:
            return
        _set_on(tab, True)
        self._showTitle(tab)

    def chooseString_(self, sender):
        Glyphs.defaults[KEY_STRING] = int(sender.tag())
        self.showOHno_(sender)

    def menuSentAction_(self, notification):
        # Any of Glyphs' own entries (Show All Instances, "-", an instance)
        # switches the preview of that tab back to normal.
        try:
            tab = _current_tab()
            if tab is None or _proof_view_of(tab) is None:
                return
            if notification.object() != tab.instancePopupButton().menu():
                return
            item = notification.userInfo().get("MenuItem")
            if item is not None and item.representedObject() == MENU_TAG:
                return
            _set_on(tab, False)
            # Glyphs keeps the button title when the chosen mode did not change
            # (e.g. Show All Instances -> OHno -> Show All Instances), so put
            # the chosen entry's title back ourselves.
            if item is not None and item.title():
                tab.instancePopupButton().itemAtIndex_(0).setTitle_(item.title())
        except Exception as e:
            print("Show OHno menu error:", e)


_controller = None


class ShowOHno(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = "Show OHno"
        Glyphs.registerDefaults({KEY_STRING: 0})

    @objc.python_method
    def start(self):
        global _controller
        if _controller is not None:
            return
        _controller = ShowOHnoController.alloc().init()
        center = NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(
            _controller, "popUpWillShow:", "NSPopUpButtonWillPopUpNotification", None)
        center.addObserver_selector_name_object_(
            _controller, "menuSentAction:", "NSMenuDidSendActionNotification", None)
        # redraw on selection changes, edits and preview size/colour changes
        Glyphs.addCallback(self.interfaceChanged, UPDATEINTERFACE)
        Glyphs.addCallback(self.editViewDrawn, DRAWFOREGROUND)

    @objc.python_method
    def interfaceChanged(self, notification=None):
        _refresh_current()

    @objc.python_method
    def editViewDrawn(self, layer=None, info=None):
        _refresh_current()

    @objc.python_method
    def __file__(self):
        return __file__
