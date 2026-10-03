# Show OHno

A plugin for [Glyphs 4](https://glyphsapp.com). It turns the preview panel under the Edit View into a spacing proof for the glyph you are working on, so you can check it between control letters without typing a test string or switching tabs.

With `x` selected, the preview shows the glyph in a control string such as `HHxHnxnn`, set in the current master. *Show OHno* sits in the preview bar's instance menu next to *Show All Instances*, so switching between the spacing proof and your instances is one click.

## Installation

Download or clone this repository and double-click `ShowOHno.glyphsPlugin`. Glyphs will install it into `~/Library/Application Support/Glyphs 4/Plugins/`. Restart Glyphs.

If you used an earlier version, delete `ShowOHno.glyphsReporter` from the Plugins folder first.

If macOS blocks the plugin, run:

```
xattr -cr ~/Library/Application\ Support/Glyphs\ 4/Plugins/ShowOHno.glyphsPlugin
```

## Usage

1. Open the preview panel of the Edit View (drag it up from the bottom of the window).
2. Open the instance pop-up menu of the preview bar (the one with *Show All Instances*) and choose **Show OHno** at its end. The preview now shows the selected glyph in the last used proof string.
3. To use another string, pick it in **OHno String** right below. This also switches OHno on.
4. To get back, choose *Show All Instances*, an instance or *-* in the same menu, as usual.

The proof replaces the preview text through Glyphs' preview drawing callback. It is set at the preview's size and colour, with kerning, and centred so that accents above caps and below the baseline stay inside the panel. Show OHno stays on after Glyphs restarts.

If stylistic sets or other features are active in the Edit View (Features menu, bottom left), the control glyphs are replaced by their alternates, e.g. `n` → `n.ss01`. This works with the usual suffix naming; the feature code itself is not interpreted.

## Proof strings

`x` stands for the selected glyph.

1. `HHxHnxnn` (default)
2. `nnxooxHHxOO`
3. `nnxnoxoo`
4. `HHxHOxOO`
5. `nnnxnnn`
6. `HHHxHHH`

To change or add strings, edit `TEMPLATE_LINES` at the top of `Contents/Resources/plugin.py`.

## Limitations

- The proof is set in the master of the selected layer, not in instances.
- Show OHno is on or off for all Edit View tabs at once.

## Requirements

Glyphs 4, build 3855 or later (it added the preview drawing callback).

## License

MIT, see [LICENSE](LICENSE).
