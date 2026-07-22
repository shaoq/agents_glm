"""parsing + cleaning 测试（docling PDF 解析留端到端验证）。"""

from __future__ import annotations

import pytest

from agents_rag.cleaning.normalizer import Normalizer, clean_text
from agents_rag.models import Block, BlockType, DocType, Document, Section
from agents_rag.parsing.base import Parser
from agents_rag.parsing.html_parser import HTMLParser
from agents_rag.parsing.markdown_parser import MarkdownParser, md_to_sections
from agents_rag.parsing.office_parser import DocxParser
from agents_rag.parsing.quality import assess_quality
from agents_rag.parsing.router import ParserRouter


# —— markdown ——
def test_markdown_headings_levels():
    sections = md_to_sections("# H1\na\n\n## H2\nb\n")
    assert sections[0].heading == "H1" and sections[0].level == 1
    assert sections[1].heading == "H2" and sections[1].level == 2


def test_markdown_table_block():
    md = "# T\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    sections = md_to_sections(md)
    tables = [s for s in sections if s.blocks and s.blocks[0].type is BlockType.TABLE]
    assert tables and "a" in tables[0].blocks[0].text


def test_markdown_parser_document(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# H\n正文\n", encoding="utf-8")
    doc = MarkdownParser().parse(f)
    assert doc.doc_type is DocType.MARKDOWN
    assert any(b.text == "正文" for b in doc.iter_blocks())


# —— html ——
def test_html_parser_extracts_body(tmp_path):
    f = tmp_path / "a.html"
    f.write_text(
        "<html><body><nav>导航</nav><article><h1>T</h1><p>正文内容</p></article></body></html>",
        encoding="utf-8",
    )
    doc = HTMLParser().parse(f)
    text = " ".join(b.text for b in doc.iter_blocks())
    assert "正文内容" in text


# —— office docx ——
def _make_docx(path) -> None:
    import docx

    d = docx.Document()
    d.add_paragraph("第一段")
    d.add_paragraph("第二段")
    tbl = d.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "k1"
    tbl.rows[0].cells[1].text = "k2"
    tbl.rows[1].cells[0].text = "v1"
    tbl.rows[1].cells[1].text = "v2"
    d.save(str(path))


def test_docx_parser_paragraphs_and_table(tmp_path):
    p = tmp_path / "a.docx"
    _make_docx(p)
    doc = DocxParser().parse(p)
    texts = [b.text for b in doc.iter_blocks()]
    assert "第一段" in texts
    tables = [b for b in doc.iter_blocks() if b.type is BlockType.TABLE]
    assert tables and "k1" in tables[0].text


# —— quality ——
def test_assess_quality_poor_for_scan_like():
    doc = Document(
        doc_id="",
        source="s",
        doc_type=DocType.PDF,
        sections=(Section(blocks=(Block(text="ab", page=1), Block(text="cd", page=2))),),
    )
    assert assess_quality(doc).is_poor()  # 每页字符极少


# —— normalizer ——
def test_normalizer_preserves_page_metadata():
    b = Block(type=BlockType.PARAGRAPH, text="你好　　世界", page=3)  # 全角双空格
    doc = Document(
        doc_id="d",
        source="s",
        doc_type=DocType.MARKDOWN,
        sections=(Section(blocks=(b,)),),
    )
    out = Normalizer().normalize(doc)
    ob = out.sections[0].blocks[0]
    assert ob.page == 3  # 元数据保留
    assert "　　" not in ob.text  # 全角空格被压缩


def test_clean_text_keeps_symbols():
    assert "#" in clean_text("a # b")
    assert "1.0" in clean_text("型号 A-1.0 编码")


# —— router ——
class _BoomParser(Parser):
    supported_types = (DocType.TXT,)

    def parse(self, path):  # type: ignore[override]
        raise RuntimeError("boom")


def test_router_all_fail_returns_none(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    router = ParserRouter({DocType.TXT: [_BoomParser(), _BoomParser()]})
    assert router.parse(f) is None  # 全失败/异常 → 跳过


def test_router_no_parser_for_type(tmp_path):
    f = tmp_path / "a.xyz"
    f.write_text("hi")
    router = ParserRouter({})
    assert router.parse(f) is None


def test_router_falls_through_to_good_parser(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("正常文本内容足够长不至于被判低质量" * 5)
    good = MarkdownParser()
    router = ParserRouter({DocType.TXT: [_BoomParser(), good]})
    doc = router.parse(f)
    assert doc is not None


def test_xlsx_parser(tmp_path):
    from openpyxl import Workbook

    from agents_rag.parsing.office_parser import XlsxParser

    wb = Workbook()
    ws = wb.active
    ws.append(["型号", "参数"])
    ws.append(["GLM-4.5", "2048维"])
    p = tmp_path / "a.xlsx"
    wb.save(str(p))
    doc = XlsxParser().parse(p)
    tables = [b for b in doc.iter_blocks() if b.type is BlockType.TABLE]
    assert tables and "GLM-4.5" in tables[0].text


def test_pptx_parser(tmp_path):
    from pptx import Presentation

    from agents_rag.parsing.office_parser import PptxParser

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    tb = slide.shapes.add_textbox(0, 0, 300, 100)
    tb.text_frame.text = "智谱GLM模型说明文本内容"
    p = tmp_path / "a.pptx"
    prs.save(str(p))
    doc = PptxParser().parse(p)
    assert "GLM" in " ".join(b.text for b in doc.iter_blocks())


def test_router_with_defaults_disables_pdf():
    r = ParserRouter.with_defaults(enable_pdf=False)
    assert DocType.MARKDOWN in r._parsers
    assert DocType.PDF not in r._parsers  # docling 未启用
