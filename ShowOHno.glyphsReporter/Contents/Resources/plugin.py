# encoding: utf-8
"""
Show OHno — reporter plugin for Glyphs 3/4.

While active, the preview panel shows the glyph selected in the Edit View in a
spacing proof string such as HHxHnxnn.

Switch it on with View > Show OHno or with "Show OHno" in the instance pop-up
menu of the preview bar. Choosing Show All Instances, an instance or "-" in
that menu switches it off again. Another proof string can be picked in
"OHno String" in the same menu or in the Edit View context menu.

Drawing:
- Glyphs 4 (build 3855 and later) has a reporter preview API: the proof is
  drawn through drawPreviewWithOptions: / needsExtraMainOutlineDrawingInPreview
  inside preview.safeAreaRect() and replaces the preview text.
- Glyphs 3 has no such API. There the proof is drawn in a view placed over the
  preview, following its size, baseline, black/white, blur and flip settings.

Stylistic sets active in the Edit View are applied by suffix naming
(n -> n.ss01).

MIT License — see LICENSE in the repository.
"""

import time
import objc
from AppKit import NSView, NSColor, NSBezierPath, NSAffineTransform, NSMenu, NSMenuItem
from Foundation import NSObject, NSNotificationCenter, NSTimer
from GlyphsApp import Glyphs, ONSTATE, OFFSTATE
from GlyphsApp.plugins import ReporterPlugin

try:
    from Quartz import CIFilter
except Exception:
    CIFilter = None
try:
    from Quartz import CGContextGetClipBoundingBox
except Exception:
    CGContextGetClipBoundingBox = None
from AppKit import NSGraphicsContext

# Fallback only: where Glyphs' own preview puts the baseline, as a share of the
# preview height (measured while the native preview draws underneath the proof).
_native_baseline = None

PLUGIN_ID = "com.pilstype.ShowOHno"
KEY_STRING = PLUGIN_ID + ".primaryString"
MENU_TAG = PLUGIN_ID + ".previewMenu"
REPORTER_CLASS = "ShowOHno"

# {g} = glyph selected in the Edit View
TEMPLATE_LINES = [
    ["H", "H", "{g}", "H", "n", "{g}", "n", "n"],
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
]

FIT_MARGIN = 0.94       # a line wider than the preview is shrunk to this share of its width
FIT_HEIGHT = 0.92       # ascender..descender never takes more than this share of the preview height
FLIP_VERTICAL = False   # fallback only — the F button: False = mirror left/right, True = upside down
SYNC_INTERVAL = 0.2     # fallback only — seconds between checks of the preview bar


PREVIEW_API_BUILD = 3855   # Glyphs 4 build that added the reporter preview drawing callback


def _glyphs_build():
    try:
        return int(Glyphs.buildNumber)
    except Exception:
        try:
            return int(float(Glyphs.versionNumber) >= 4) * PREVIEW_API_BUILD
        except Exception:
            return 0


# Glyphs 4 draws through the preview API; the Glyphs 3 fallback is never used there.
# (_api_seen also turns true if Glyphs calls drawPreviewWithOptions: anyway.)
_api_seen = _glyphs_build() >= PREVIEW_API_BUILD
API_GRACE = 0.6         # Glyphs 3: seconds to wait after switching on before drawing the fallback


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


def _reporter_active():
    try:
        return any(str(r.className()) == REPORTER_CLASS for r in (Glyphs.activeReporters or []))
    except Exception:
        return False


def _current_tab():
    font = Glyphs.font
    return font.currentTab if font is not None else None


def _refresh():
    tab = _current_tab()
    try:
        tab.updatePreview()
        tab.previewView().setNeedsDisplay_(True)
    except Exception:
        pass
    if not _api_seen and _preview_menu is not None:
        _preview_menu.syncTick_(None)
    Glyphs.redraw()


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


def _draw_line_api(line, area, scale, black):
    """Glyphs 4: centre everything letters can reach (accents included) in the safe area."""
    bottom, top = _vertical_range(line["font"], line["masterId"], line["ascender"], line["descender"])
    x0, y0 = area.origin.x, area.origin.y
    w, h = area.size.width, area.size.height
    body = max(top - bottom, 1.0)
    scale = min(scale, w * FIT_MARGIN / line["width"], h * FIT_HEIGHT / body)
    startX = x0 + (w - line["width"] * scale) / 2.0
    baseline = y0 + (h - body * scale) / 2.0 - bottom * scale
    (NSColor.whiteColor() if black else NSColor.blackColor()).set()
    for layer, x in zip(line["layers"], line["positions"]):
        path = layer.completeBezierPath
        if path is None:
            continue
        t = NSAffineTransform.transform()
        t.translateXBy_yBy_(startX + x * scale, baseline)
        t.scaleBy_(scale)
        p = path.copy()
        p.transformUsingAffineTransform_(t)
        p.fill()


def _draw_line(line, x0, y0, w, h, scale, black, flip=False, baseline_share=None):
    """Draw the proof line centred in the rectangle (x0, y0, w, h)."""
    if scale <= 0:
        scale = h * 0.7 / max(line["ascender"] - line["descender"], 1.0)
    body = max(line["ascender"] - line["descender"], 1.0)
    # never larger than the panel: shrink to fit its width and its height
    scale = min(scale, w * FIT_MARGIN / line["width"], h * FIT_HEIGHT / body)
    startX = x0 + (w - line["width"] * scale) / 2.0
    if baseline_share is not None and 0.0 < baseline_share < 1.0:
        baseline = y0 + baseline_share * h          # same baseline as Glyphs' preview
    else:
        baseline = y0 + (h - body * scale) / 2.0 - line["descender"] * scale

    mirror = NSAffineTransform.transform()
    if flip:
        if FLIP_VERTICAL:
            mirror.translateXBy_yBy_(0, 2 * y0 + h)
            mirror.scaleXBy_yBy_(1, -1)
        else:
            mirror.translateXBy_yBy_(2 * x0 + w, 0)
            mirror.scaleXBy_yBy_(-1, 1)

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


# ---------------------------------------------------------------- the reporter

class ShowOHno(ReporterPlugin):

    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "OHno", "cs": "OHno"})
        Glyphs.registerDefaults({KEY_STRING: 0})

    @objc.python_method
    def start(self):
        _install_preview_menu()

    @objc.python_method
    def _tab(self):
        try:
            if self.controller:
                return self.controller
        except Exception:
            pass
        return _current_tab()

    # --- preview API (newer Glyphs versions)

    @objc.typedSelector(b'Z@:')
    def needsExtraMainOutlineDrawingInPreview(self):
        return not _reporter_active()

    @objc.typedSelector(b'v@:@')
    def drawPreviewWithOptions_(self, options):
        global _api_seen
        if not _api_seen:
            _api_seen = True
            _remove_all_overlays()
        try:
            tab = self._tab()
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
            _draw_line_api(line, preview.safeAreaRect(), scale, bool(preview.black()))
            _show_title(tab)
        except Exception as e:
            import traceback
            print("Show OHno error:", e)
            print(traceback.format_exc())

    # --- measuring the native preview (fallback only)

    @objc.python_method
    def preview(self, layer):
        """Called by Glyphs for every glyph its preview draws, in that glyph's
        coordinates (font units, baseline at y = 0). The visible area, seen from
        here, tells where the preview puts the baseline."""
        global _native_baseline
        if _api_seen or CGContextGetClipBoundingBox is None:
            return
        try:
            tab = _current_tab()
            pv = tab.previewView() if tab is not None else None
            if pv is None:
                return
            ctx = NSGraphicsContext.currentContext()
            try:
                cg = ctx.CGContext()
            except Exception:
                cg = ctx.graphicsPort()
            box = CGContextGetClipBoundingBox(cg)
            height_units = pv.bounds().size.height / float(pv.scale() or 1)
            # only trust a clip that spans the whole preview height
            if box.size.height > 0 and abs(box.size.height - height_units) / height_units < 0.05:
                share = -box.origin.y / box.size.height
                if 0.0 < share < 1.0:
                    _native_baseline = share
        except Exception:
            pass

    # --- Edit View context menu

    @objc.python_method
    def conditionalContextMenus(self):
        current = _string_index()
        return [{"name": "OHno String: %s" % _template_text(t), "action": self.chooseString_,
                 "state": ONSTATE if i == current else OFFSTATE}
                for i, t in enumerate(TEMPLATE_LINES)]

    def chooseString_(self, sender):
        title = str(sender.title())
        for i, t in enumerate(TEMPLATE_LINES):
            if title.endswith(": " + _template_text(t)):
                Glyphs.defaults[KEY_STRING] = i
        _refresh()

    @objc.python_method
    def __file__(self):
        return __file__


# -------------------------------------------- fallback for Glyphs without the API

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
        # follow the preview immediately while the panel is being resized
        preview.setPostsFrameChangedNotifications_(True)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "previewFrameChanged:", "NSViewFrameDidChangeNotification", preview)
        return self

    def previewFrameChanged_(self, notification):
        try:
            self.setFrame_(self.preview.frame())
            self.setNeedsDisplay_(True)
            # the preview recomputes its scale when it redraws; draw again right after
            self.performSelector_withObject_afterDelay_("redrawAfterPreview:", None, 0)
        except Exception:
            pass

    def redrawAfterPreview_(self, sender):
        self.state = None
        self.sync()

    def isOpaque(self):
        return True

    def isFlipped(self):
        return False

    def hitTest_(self, point):
        return None

    @objc.python_method
    def previewState(self):
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
            _native_baseline,
        )

    @objc.python_method
    def sync(self, force=False):
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

    def drawRect_(self, rect):
        try:
            line = _build_line(self.tab)
            black, flip, radius, scale, size, _baseline = self.previewState()
            b = self.bounds()
            # cover the preview underneath with its own background colour
            bg = None
            try:
                bg = self.preview.canvasColor()
            except Exception:
                bg = None
            if bg is None:
                bg = NSColor.blackColor() if black else NSColor.whiteColor()
            bg.set()
            NSBezierPath.fillRect_(b)
            if line is None:
                return
            _draw_line(line, 0, 0, b.size.width, b.size.height, scale, black, flip, _native_baseline)
        except Exception as e:
            import traceback
            print("Show OHno error:", e)
            print(traceback.format_exc())


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


def _remove_all_overlays():
    try:
        for doc in Glyphs.documents:
            for tab in doc.font.tabs:
                if _proof_view_of(tab) is not None:
                    _set_overlay(tab, False)
    except Exception:
        pass


def _set_overlay(tab, on):
    preview = _preview_of(tab)
    if preview is None or preview.superview() is None:
        return
    proof = _proof_view_of(tab)
    if on:
        if proof is None:
            proof = ShowOHnoProofView.alloc().initWithTab_preview_(tab, preview)
            preview.superview().addSubview_positioned_relativeTo_(proof, 1, preview)  # NSWindowAbove
        # The preview stays visible underneath (covered by the proof view):
        # that way Glyphs keeps computing its size for the current panel height.
        preview.setHidden_(False)
        proof.sync(force=True)
        _show_title(tab)
    elif proof is not None:
        NSNotificationCenter.defaultCenter().removeObserver_(proof)
        proof.removeFromSuperview()
        preview.setHidden_(False)
        preview.setNeedsDisplay_(True)


# ----------------------------------------------------------- preview bar menu

class ShowOHnoPreviewMenu(NSObject):

    # --- fallback: keep the overlay in step with the reporter and the preview bar

    @objc.python_method
    def startSync(self):
        if getattr(self, "timer", None) is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                SYNC_INTERVAL, self, "syncTick:", None, True)

    def syncTick_(self, timer):
        try:
            if _api_seen:
                # Glyphs draws the proof through the preview API: nothing to do here.
                if getattr(self, "timer", None) is not None:
                    self.timer.invalidate()
                    self.timer = None
                return
            tab = _current_tab()
            if tab is None:
                return
            on = _reporter_active()
            # give Glyphs a moment to call the preview API after switching on
            if on and getattr(self, "onSince", None) is None:
                self.onSince = time.time()
            if not on:
                self.onSince = None
            if on and time.time() - self.onSince < API_GRACE:
                return
            proof = _proof_view_of(tab)
            if on and proof is None:
                _set_overlay(tab, True)
            elif not on and proof is not None:
                _set_overlay(tab, False)
            elif proof is not None:
                proof.sync()
                proof.setNeedsDisplay_(True)
        except Exception as e:
            print("Show OHno sync error:", e)

    # --- the menu

    def popUpWillShow_(self, notification):
        try:
            tab = _current_tab()
            if tab is None or notification.object() != tab.instancePopupButton():
                return
            menu = tab.instancePopupButton().menu()
            for item in list(menu.itemArray()):
                if item.representedObject() == MENU_TAG:
                    menu.removeItem_(item)

            on = _reporter_active()
            current = _string_index()

            show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show OHno", "showOHno:", "")
            show.setTarget_(self)
            show.setRepresentedObject_(MENU_TAG)
            show.setState_(ONSTATE if on else OFFSTATE)

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
        if not _reporter_active():
            Glyphs.activateReporter(REPORTER_CLASS)
        tab = _current_tab()
        if tab is not None:
            _show_title(tab)
        _refresh()

    def chooseString_(self, sender):
        Glyphs.defaults[KEY_STRING] = int(sender.tag())
        self.showOHno_(sender)

    def menuSentAction_(self, notification):
        # Show All Instances, "-" or an instance switches OHno off.
        try:
            tab = _current_tab()
            if tab is None or not _reporter_active():
                return
            if notification.object() != tab.instancePopupButton().menu():
                return
            item = notification.userInfo().get("MenuItem")
            if item is not None and item.representedObject() == MENU_TAG:
                return
            Glyphs.deactivateReporter(REPORTER_CLASS)
            # Glyphs keeps the button title when its own mode did not change.
            if item is not None and item.title():
                tab.instancePopupButton().itemAtIndex_(0).setTitle_(item.title())
            _refresh()
        except Exception as e:
            print("Show OHno menu error:", e)


_preview_menu = None


def _install_preview_menu():
    """One observer, however many reporter instances Glyphs creates."""
    global _preview_menu
    if _preview_menu is not None:
        return
    _preview_menu = ShowOHnoPreviewMenu.alloc().init()
    center = NSNotificationCenter.defaultCenter()
    center.addObserver_selector_name_object_(
        _preview_menu, "popUpWillShow:", "NSPopUpButtonWillPopUpNotification", None)
    center.addObserver_selector_name_object_(
        _preview_menu, "menuSentAction:", "NSMenuDidSendActionNotification", None)
    _preview_menu.startSync()
