# Show OHno

A reporter plugin for [Glyphs 3](https://glyphsapp.com). It shows a small spacing proof card above the glyph you are working on in the Edit View, so you can check the glyph between control letters without switching tabs.

With `x` selected, the card shows the glyph in a control string such as `HHxHnxnn`, set in the current master, with kerning applied. The card is centred on the glyph's body and stays out of the way of the outline.

## Installation

Download or clone this repository and double-click `ShowOHno.glyphsReporter`. Glyphs will install it into `~/Library/Application Support/Glyphs 3/Plugins/`. Restart Glyphs.

If macOS blocks the plugin, run:

```
xattr -cr ~/Library/Application\ Support/Glyphs\ 3/Plugins/ShowOHno.glyphsReporter
```

## Usage

Switch it on in **View → Show OHno** and select a glyph in the Edit View.

The card follows the active glyph and fades out when:

- it doesn't fit above the glyph in the visible area,
- the Edit View is zoomed out or in past the set range (by default below 50 pt or above 500 pt),
- no glyph is selected.

If stylistic sets or other features are active in the Edit View (Features menu, bottom left), the control glyphs are replaced by their alternates, e.g. `n` → `n.ss01`. This works with the usual suffix naming; the feature code itself is not interpreted.

## Settings

Right-click in the Edit View while the plugin is on. All settings are remembered.

| Setting | Options |
|---|---|
| Dark Theme | light (default) or dark card |
| Kerning | on (default) / off |
| Highlight Selected Glyph | colours the tested glyph in the card |
| Show Below Glyph | place the card under the glyph instead of above |
| Size | 48 / 72 / 110 pt |
| Hide When Zoomed Below | Never / 50 / 100 / 150 / 250 pt |
| Hide When Zoomed Above | Never / 400 / 500 / 700 / 1000 pt |
| String | one of the proof strings below |

### Proof strings

`x` stands for the selected glyph.

1. `HHxHnxnn` (default)
2. `nnxooxHHxOO`
3. `nnxnoxoo`
4. `HHxHOxOO`
5. `nnnxnnn`
6. `HHHxHHH`

To change or add strings, edit `TEMPLATE_LINES` at the top of `Contents/Resources/plugin.py`. Padding, colours, fade duration and zoom steps are constants in the same file.

## Requirements

Glyphs 3.0 or later. Tested in Glyphs 3.5.

## License

MIT, see [LICENSE](LICENSE).
