# Show OHno

A reporter plugin for [Glyphs 3 and 4](https://glyphsapp.com). It turns the preview panel under the Edit View into a spacing proof for the glyph you are working on, so you can check it between control letters without typing a test string or switching tabs.

With `x` selected, the preview shows the glyph in a control string such as `HHxHnxnn`, set in the current master. *Show OHno* sits in the preview bar's instance menu next to *Show All Instances*, so switching between the spacing proof and your instances is one click.

## Installation

Download or clone this repository and double-click `ShowOHno.glyphsReporter`. Glyphs will install it into `~/Library/Application Support/Glyphs 4/Plugins/` (or `Glyphs 3/Plugins/`). Restart Glyphs.

If you tried a 2.0 test build, delete `ShowOHno.glyphsPlugin` from the Plugins folder first.

If macOS blocks the plugin, run:

```
xattr -cr ~/Library/Application\ Support/Glyphs\ 4/Plugins/ShowOHno.glyphsReporter
```

For Glyphs 3, use `Glyphs\ 3` in the path.

## Usage

1. Open the preview panel of the Edit View (drag it up from the bottom of the window).
2. Open the instance pop-up menu of the preview bar (the one with *Show All Instances*) and choose **Show OHno** at its end, or use **View → Show OHno**. The preview now shows the selected glyph in the last used proof string.
3. To use another string, pick it in **OHno String** right below, or in the Edit View context menu (right-click).
4. To get back, choose *Show All Instances*, an instance or *-* in the same menu, as usual.

In Glyphs 4 the proof replaces the preview text through Glyphs' reporter preview API and is centred so that accents above caps and below the baseline stay inside the panel. Glyphs 3 has no such API, so there the plugin draws the proof in a view placed over the preview, following its size, baseline, black/white, blur and flip settings. Kerning is applied in both. Like any reporter, Show OHno stays on after Glyphs restarts.

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
- Show OHno is on or off for all Edit View tabs at once, like any reporter.

## Requirements

Glyphs 3.0 or later, including Glyphs 4. The preview API is used from Glyphs 4 build 3855 on.

## License

MIT, see [LICENSE](LICENSE).
