# encoding: utf-8
"""
Show OHno — plugin for Glyphs 4.

Adds "Show OHno" to the instance pop-up menu of the preview bar. While it is
on, the preview shows the glyph selected in the Edit View in a spacing proof
string such as HHxHnxnn instead of the Edit View text.

The plugin registers a "DrawPreview" callback with Glyphs and draws through the
preview API (needsExtraMainOutlineDrawingInPreview / drawPreviewWithOptions:,
Glyphs 4 build 3855 and later): Glyphs' own preview text is switched off and
the proof is drawn in its place, inside preview.safeAreaRect().

Choosing Show All Instances, an instance or "-" in the same menu switches the
preview back. Another proof string can be picked in "OHno String".
Stylistic sets active in the Edit View are applied by suffix naming
(n -> n.ss01).

MIT License — see LICENSE in the repository.
"""

import time
import objc
from AppKit import NSColor, NSAffineTransform, NSMenu, NSMenuItem
from Foundation import NSObject, NSNotificationCenter
from GlyphsApp import Glyphs, ONSTATE, OFFSTATE
from GlyphsApp.plugins import GeneralPlugin

PLUGIN_ID = "com.pilstype.ShowOHno"
KEY_STRING = PLUGIN_ID + ".primaryString"
KEY_ON = PLUGIN_ID + ".showInPreview"
MENU_TAG = PLUGIN_ID + ".previewMenu"
PREVIEW_API_BUILD = 3855   # Glyphs 4 build that added the preview drawing callback
DRAWPREVIEW = "DrawPreview"   # callback type for drawing in the preview panel

# {g} = glyph selected in the Edit View
TEMPLATE_LINES = [
    ["H", "H", "{g}", "H", "n", "{g}", "n", "n"],
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
]

FIT_MARGIN = 0.94   # a line wider than the preview is shrunk to this share of its width
FIT_HEIGHT = 0.92   # the letters' full height never takes more than this share of the preview


def _glyphs_build():
    try:
        return int(Glyphs.buildNumber)
    except Exception:
        return 0


def _get_default(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        value = None
    return fallback if value is None else value


def _string_index():
    index = int(_get_default(KEY_STRING, 0))
    return index if 0 <= index < len(TEMPLATE_LINES) else 0


def _template_text(template):
    return "".join("x" if t == "{g}" else t for t in template)


def _current_tab():
    font = Glyphs.font
    return font.currentTab if font is not None else None


def _is_on():
    return bool(_get_default(KEY_ON, False))


def _set_on(on):
    Glyphs.defaults[KEY_ON] = bool(on)
    _refresh()


def _refresh():
    # Only repaint the preview. Rebuilding it (updatePreview) makes Glyphs
    # re-interpolate the instances and flash placeholders for a frame.
    try:
        _current_tab().previewView().setNeedsDisplay_(True)
    except Exception:
        pass


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
    result = []
    for f in feats or []:
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


def _selected_layer(tab):
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


_range_cache = {}
RANGE_CACHE_SECONDS = 30.0


def _vertical_range(font, master_id, ascender, descender):
    """Lowest and highest point of the letters in this master, accents included
    (e.g. caron on caps, ogonek). The same for every glyph of the font, so the
    baseline does not jump when another glyph is selected."""
    key = (objc.pyobjc_id(font), master_id)
    cached = _range_cache.get(key)
    now = time.time()
    if cached is not None and now - cached[0] < RANGE_CACHE_SECONDS:
        return cached[1], cached[2]
    bottom, top = descender, ascender
    try:
        for g in font.glyphs:
            if not g.export or g.category != "Letter":
                continue
            layer = g.layers[master_id]
            if layer is None:
                continue
            b = layer.bounds
            if b.size.height <= 0:
                continue
            bottom = min(bottom, b.origin.y)
            top = max(top, b.origin.y + b.size.height)
    except Exception:
        pass
    _range_cache[key] = (now, bottom, top)
    return bottom, top


def _build_line(tab):
    try:
        font = tab.parent
    except Exception:
        font = Glyphs.font
    active = _selected_layer(tab)
    if font is None or active is None:
        return None
    masterId = active.associatedMasterId or font.selectedFontMaster.id
    master = font.masters[masterId] or font.selectedFontMaster
    features = _active_features(tab)

    items = []
    for token in TEMPLATE_LINES[_string_index()]:
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
        "font": font,
        "masterId": masterId,
    }


def _draw_line_api(line, area, scale, black, flip=False):
    """Glyphs 4: centre everything letters can reach (accents included) in the safe area.
    The preview's flip button turns the line upside down (Glyphs does not do it for callbacks)."""
    bottom, top = _vertical_range(line["font"], line["masterId"], line["ascender"], line["descender"])
    x0, y0 = area.origin.x, area.origin.y
    w, h = area.size.width, area.size.height
    body = max(top - bottom, 1.0)
    scale = min(scale, w * FIT_MARGIN / line["width"], h * FIT_HEIGHT / body)
    startX = x0 + (w - line["width"] * scale) / 2.0
    baseline = y0 + (h - body * scale) / 2.0 - bottom * scale
    mirror = NSAffineTransform.transform()
    if flip:
        mirror.translateXBy_yBy_(0, 2 * y0 + h)
        mirror.scaleXBy_yBy_(1, -1)
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


def _show_title(tab):
    """A pull-down pop-up shows the title of its first item."""
    try:
        item = tab.instancePopupButton().itemAtIndex_(0)
        if item.title() != "Show OHno":
            item.setTitle_("Show OHno")
    except Exception:
        pass


# ------------------------------------------------------ the drawing callback

class ShowOHnoPreviewDrawer(NSObject):
    """Registered with Glyphs for the "DrawPreview" callback; draws only into the preview."""

    @objc.typedSelector(b'Z@:')
    def needsExtraMainOutlineDrawingInPreview(self):
        # Glyphs draws its normal preview text only while OHno is off.
        return not _is_on()

    @objc.typedSelector(b'v@:@')
    def drawPreviewWithOptions_(self, options):
        if not _is_on():
            return
        try:
            tab = _current_tab()
            if tab is None:
                return
            preview = tab.previewView()
            line = _build_line(tab)
            if preview is None or line is None:
                return
            try:
                scale = float(options["Scale"])
            except Exception:
                scale = float(preview.scale())
            try:
                flip = bool(preview.flip())
            except Exception:
                flip = False
            _draw_line_api(line, preview.safeAreaRect(), scale, bool(preview.black()), flip)
            _show_title(tab)
        except Exception as e:
            import traceback
            print("Show OHno error:", e)
            print(traceback.format_exc())


# ----------------------------------------------------------- preview bar menu

class ShowOHnoPreviewMenu(NSObject):

    def popUpWillShow_(self, notification):
        try:
            tab = _current_tab()
            if tab is None or notification.object() != tab.instancePopupButton():
                return
            menu = tab.instancePopupButton().menu()
            for item in list(menu.itemArray()):
                if item.representedObject() == MENU_TAG:
                    menu.removeItem_(item)

            current = _string_index()
            show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show OHno", "showOHno:", "")
            show.setTarget_(self)
            show.setRepresentedObject_(MENU_TAG)
            show.setState_(ONSTATE if _is_on() else OFFSTATE)

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
        except Exception as e:
            print("Show OHno menu error:", e)

    def showOHno_(self, sender):
        _set_on(True)
        tab = _current_tab()
        if tab is not None:
            _show_title(tab)

    def chooseString_(self, sender):
        Glyphs.defaults[KEY_STRING] = int(sender.tag())
        self.showOHno_(sender)

    def menuSentAction_(self, notification):
        # Show All Instances, "-" or an instance switches OHno off.
        try:
            tab = _current_tab()
            if tab is None or not _is_on():
                return
            if notification.object() != tab.instancePopupButton().menu():
                return
            item = notification.userInfo().get("MenuItem")
            if item is not None and item.representedObject() == MENU_TAG:
                return
            _set_on(False)
            # Glyphs keeps the button title when its own mode did not change.
            if item is not None and item.title():
                tab.instancePopupButton().itemAtIndex_(0).setTitle_(item.title())
        except Exception as e:
            print("Show OHno menu error:", e)


# ----------------------------------------------------------------- the plugin

_drawer = None
_preview_menu = None


class ShowOHno(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = "Show OHno"
        Glyphs.registerDefaults({KEY_STRING: 0, KEY_ON: False})

    @objc.python_method
    def start(self):
        global _drawer, _preview_menu
        if _drawer is not None:
            return
        if _glyphs_build() < PREVIEW_API_BUILD:
            print("Show OHno needs Glyphs 4 (build %d or later)." % PREVIEW_API_BUILD)
            return
        _drawer = ShowOHnoPreviewDrawer.alloc().init()
        # "DrawPreview": the preview asks these callbacks whether to draw its own
        # text and lets them draw (not in the SDK's constants yet).
        objc.lookUpClass("GSCallbackHandler").addCallback_forOperation_(_drawer, DRAWPREVIEW)
        Glyphs.redraw()

        _preview_menu = ShowOHnoPreviewMenu.alloc().init()
        center = NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(
            _preview_menu, "popUpWillShow:", "NSPopUpButtonWillPopUpNotification", None)
        center.addObserver_selector_name_object_(
            _preview_menu, "menuSentAction:", "NSMenuDidSendActionNotification", None)

    @objc.python_method
    def __file__(self):
        return __file__
