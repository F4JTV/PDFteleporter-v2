# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
"""What PyMuPDF reports for a page, as JSON.

The reference half of the differential tests: `pdft-cli dump all PDF PAGE`
prints the same structure from the C++ layer over MuPDF, and the two must be
equal value for value.

    python dump_pymupdf.py PDF PAGE
"""

import json
import sys

import pymupdf


def rect(r):
    return [float(v) for v in r]


def point(p):
    return [float(p[0]), float(p[1])]


def item(it):
    kind = it[0]
    if kind == "re":
        return ["re", rect(it[1]), it[2]]
    if kind == "qu":
        q = it[1]
        return ["qu", [point(q.ul), point(q.ur), point(q.ll), point(q.lr)]]
    return [kind] + [point(p) for p in it[1:]]


def colour(value):
    return None if value is None else [float(v) for v in value]


def drawing(path):
    out = {"type": path["type"]}
    if path["type"] != "group":
        out["items"] = [item(i) for i in path["items"]]
    if not path["type"].startswith("clip"):
        out["rect"] = rect(path["rect"])
    else:
        out["scissor"] = rect(path["scissor"])
    for key in ("fill", "color"):
        if key in path:
            out[key] = colour(path[key])
    if path.get("width") is not None:
        out["width"] = float(path["width"])
    if path.get("closePath") is not None:
        out["closePath"] = path["closePath"]
    for key in ("even_odd", "fill_opacity", "stroke_opacity", "seqno", "level"):
        if key in path and path[key] is not None:
            out[key] = path[key]
    return out


def main():
    # PyMuPDF prints MuPDF's messages on stdout by default; keep stdout for the JSON.
    pymupdf.set_messages(fd=2)
    doc = pymupdf.open(sys.argv[1])
    page = doc[int(sys.argv[2])]
    raw = page.get_text("rawdict")
    blocks = []
    for b in raw["blocks"]:
        block = {"type": b["type"], "number": b["number"], "bbox": rect(b["bbox"])}
        if b["type"] == 0:
            block["lines"] = [{
                "wmode": l["wmode"], "dir": point(l["dir"]), "bbox": rect(l["bbox"]),
                "spans": [{
                    "size": s["size"], "flags": s["flags"], "char_flags": s["char_flags"],
                    "font": s["font"], "color": s["color"], "alpha": s["alpha"],
                    "ascender": s["ascender"], "descender": s["descender"],
                    "origin": point(s["origin"]), "bbox": rect(s["bbox"]),
                    "chars": [{"c": c["c"], "origin": point(c["origin"]), "bbox": rect(c["bbox"]),
                               "synthetic": c["synthetic"]} for c in s["chars"]],
                } for s in l["spans"]],
            } for l in b["lines"]]
        blocks.append(block)
    image_blocks = [rect(b["bbox"]) for b in page.get_text("dict")["blocks"] if b["type"] == 1]
    info = [{"number": i["number"], "bbox": rect(i["bbox"]), "transform": [float(v) for v in i["transform"]],
             "width": i["width"], "height": i["height"], "xref": i["xref"]}
            for i in page.get_image_info(xrefs=True)]
    links = [{"from": rect(l["from"]),
              "uri": l.get("uri", "") if l.get("kind") == pymupdf.LINK_URI else "",
              "is_uri": l.get("kind") == pymupdf.LINK_URI}
             for l in page.get_links()]
    widgets = [{"field_type": w.field_type, "field_value": w.field_value, "rect": rect(w.rect)}
               for w in (page.widgets() or [])]
    out = {
        "rotation": page.rotation,
        "rect": rect(page.rect),
        "text_len": len(page.get_text("text").strip()),
        "rawdict": blocks,
        "image_blocks": image_blocks,
        "drawings": [drawing(p) for p in page.get_drawings()],
        "drawings_ext": [drawing(p) for p in page.get_drawings(extended=True)],
        "image_info": info,
        "fonts": [list(f) for f in page.get_fonts(full=True)],
        "images": [i[0] for i in page.get_images()],
        "links": links,
        "widgets": widgets,
    }
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
