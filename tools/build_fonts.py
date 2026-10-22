#!/usr/bin/env python3
"""Build TTF fonts from the Adafruit GFX headers in headerlibs/.

Usage: python3 tools/build_fonts.py   (requires fonttools)

Every set pixel of a glyph bitmap becomes a square in the outline, so the
fonts stay pixel-perfect when rendered at multiples of their pixel height.
Codepoints 0x20-0x7E map to ASCII, 0x80-0xFF follow Windows-1251 (Cyrillic);
bytes undefined in cp1251 and the icon glyphs live in the Private Use Area
(U+E000 + byte).
"""
import re
import sys
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = Path(__file__).resolve().parent.parent
PIXEL = 100  # font units per bitmap pixel
VERSION = "1.000"

NAMES = {
    "blathauntbig": "Blathaunt Big",
    "blathauntmid": "Blathaunt Mid",
    "blathauntsmall": "Blathaunt Small",
    "blathauntmono": "Blathaunt Mono",
}


def parse_header(text, name):
    bitmaps = re.search(r"%sBitmaps\[\][^{]*\{(.*?)\};" % name, text, re.S).group(1)
    data = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", bitmaps))
    glyph_block = re.search(r"%sGlyphs\[\][^{]*\{(.*?)\};" % name, text, re.S).group(1)
    glyphs = [tuple(int(v) for v in g) for g in
              re.findall(r"\{\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\}", glyph_block)]
    first, last, y_adv = (int(v, 0) for v in re.search(
        r"Glyphs,\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),\s*(\d+)\}", text).groups())
    return data, glyphs, first, last, y_adv


def to_unicode(code):
    if code < 0x7F:
        return code
    try:
        ch = bytes([code]).decode("cp1251")
        if code >= 0x80 and ord(ch) >= 0x80:
            return ord(ch)
    except UnicodeDecodeError:
        pass
    return 0xE000 + code


def glyph_runs(data, g):
    offset, w, h, _, _, _ = g
    runs, bit = [], 0
    for y in range(h):
        x = 0
        row = []
        for x in range(w):
            i = offset * 8 + bit
            row.append(data[i // 8] >> (7 - i % 8) & 1 if i // 8 < len(data) else 0)
            bit += 1
        x = 0
        while x < w:
            if row[x]:
                start = x
                while x < w and row[x]:
                    x += 1
                runs.append((start, y, x - start))
            else:
                x += 1
    return runs


def build(name):
    text = (ROOT / "headerlibs" / f"{name}.h").read_text(encoding="latin-1")
    data, glyphs, first, last, y_adv = parse_header(text, name)

    ascent = max(-g[5] for g in glyphs) if glyphs else y_adv
    # baseline sits at the bottom of the tallest glyph row; descent covers the rest of the line
    ascent = max(ascent, 1)
    descent = max(y_adv - ascent, 0)
    upem = (ascent + descent) * PIXEL

    order = [".notdef"]
    cmap, metrics, glyf = {}, {}, {}

    pen = TTGlyphPen(None)
    pen.moveTo((PIXEL // 2, 0)); pen.lineTo((PIXEL // 2, ascent * PIXEL))
    pen.lineTo((PIXEL * 4, ascent * PIXEL)); pen.lineTo((PIXEL * 4, 0)); pen.closePath()
    glyf[".notdef"] = pen.glyph()
    metrics[".notdef"] = (PIXEL * 5, 0)

    for i, g in enumerate(glyphs):
        code = first + i
        _, w, h, x_adv, x_off, y_off = g
        gname = "uni%04X" % to_unicode(code)
        pen = TTGlyphPen(None)
        for rx, ry, rw in glyph_runs(data, g):
            x0 = (x_off + rx) * PIXEL
            x1 = x0 + rw * PIXEL
            y1 = (-y_off - ry) * PIXEL
            y0 = y1 - PIXEL
            pen.moveTo((x0, y0)); pen.lineTo((x0, y1)); pen.lineTo((x1, y1)); pen.lineTo((x1, y0))
            pen.closePath()
        glyf[gname] = pen.glyph()
        metrics[gname] = (max(x_adv, 0) * PIXEL, 0)
        cmap[to_unicode(code)] = gname
        order.append(gname)

    family = NAMES[name]
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyf)
    fb.setupHorizontalMetrics({n: (adv, 0) for n, (adv, _) in metrics.items()})
    fb.setupHorizontalHeader(ascent=ascent * PIXEL, descent=-descent * PIXEL)
    ps_name = family.replace(" ", "") + "-Regular"
    fb.setupNameTable({
        "copyright": "Apache License 2.0",
        "familyName": family,
        "styleName": "Regular",
        "uniqueFontIdentifier": f"{VERSION};{ps_name}",
        "fullName": family,
        "version": f"Version {VERSION}",
        "psName": ps_name,
    })
    fb.setupOS2(sTypoAscender=ascent * PIXEL, sTypoDescender=-descent * PIXEL,
                usWinAscent=ascent * PIXEL, usWinDescent=descent * PIXEL,
                usWeightClass=400, fsSelection=0x40, achVendID="BLTH",
                ulCodePageRange1=0x00000005,  # Latin 1 (1252) + Cyrillic (1251); Word ignores fonts with none
                sxHeight=0, sCapHeight=0)
    fb.setupPost(isFixedPitch=int(name.endswith("mono")))
    # recompute bounds/metrics from outlines
    for n in order:
        g = fb.font["glyf"][n]
        g.recalcBounds(fb.font["glyf"])
        fb.font["hmtx"][n] = (metrics[n][0], getattr(g, "xMin", 0) if g.numberOfContours else 0)

    os2 = fb.font["OS/2"]
    os2.recalcUnicodeRanges(fb.font)
    os2.sCapHeight = getattr(fb.font["glyf"]["uni0048"], "yMax", 0)
    os2.sxHeight = getattr(fb.font["glyf"]["uni0078"], "yMax", 0)
    os2.panose.bProportion = 9 if name.endswith("mono") else 0

    out = ROOT / "fonts" / f"{name}.ttf"
    fb.save(out)
    print(f"{out.relative_to(ROOT)}: {len(cmap)} glyphs, upem {upem}")


if __name__ == "__main__":
    for n in NAMES:
        build(n)
