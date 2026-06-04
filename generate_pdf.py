#!/usr/bin/env python3
"""Generate PDF from README.md (Chinese)."""
import re
import sys
from fpdf import FPDF
from fpdf.html import FontFace
from markdown_it import MarkdownIt

FONT_YAHEI     = "C:/Windows/Fonts/msyh.ttc"
FONT_YAHEI_BD  = "C:/Windows/Fonts/msyhbd.ttc"
FONT_MONO      = "C:/Windows/Fonts/consola.ttf"
FONT_MONO_BD   = "C:/Windows/Fonts/consolab.ttf"

INPUT  = "README.md"
OUTPUT = "README_zh.pdf"


def build_pdf(md_text: str) -> FPDF:
    md = MarkdownIt()
    html = md.render(md_text)

    pdf = FPDF(format="A4")
    pdf.set_margins(22, 22, 22)
    pdf.set_auto_page_break(auto=True, margin=22)

    pdf.add_font("YaHei",    fname=FONT_YAHEI)
    pdf.add_font("YaHei",    style="B", fname=FONT_YAHEI_BD)
    pdf.add_font("Consolas", fname=FONT_MONO)
    pdf.add_font("Consolas", style="B", fname=FONT_MONO_BD)

    pdf.add_page()
    pdf.set_font("YaHei", size=10)

    tag_styles = {
        "h1":   FontFace(family="YaHei", size_pt=20, emphasis="B"),
        "h2":   FontFace(family="YaHei", size_pt=15, emphasis="B"),
        "h3":   FontFace(family="YaHei", size_pt=12, emphasis="B"),
        "h4":   FontFace(family="YaHei", size_pt=11, emphasis="B"),
        "pre":  FontFace(family="Consolas", size_pt=8),
        "code": FontFace(family="Consolas", size_pt=8),
    }

    pdf.write_html(html, font_family="YaHei", tag_styles=tag_styles,
                   table_line_separators=True)
    return pdf


def main():
    try:
        with open(INPUT, encoding="utf-8") as f:
            md_text = f.read()
    except FileNotFoundError:
        print(f"Error: {INPUT} not found", file=sys.stderr)
        sys.exit(1)

    pdf = build_pdf(md_text)
    pdf.output(OUTPUT)
    print(f"Created: {OUTPUT}")


if __name__ == "__main__":
    main()
