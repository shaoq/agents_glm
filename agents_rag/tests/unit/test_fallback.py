"""降级 parser 测试：OCRParser（mock fitz/rapidocr）+ MinerUParser 可用性检测。"""

from __future__ import annotations

import builtins
import sys
import types

import pytest


def test_ocr_parser_produces_document(monkeypatch):
    from agents_rag.parsing.ocr_parser import OCRParser

    class FakePix:
        samples = b"\x00" * 12
        height = 2
        width = 2
        n = 3

    class FakePage:
        def get_pixmap(self, dpi=200):
            return FakePix()

    class FakePdf:
        def __iter__(self):
            return iter([FakePage()])

        def close(self):
            pass

    fake_fitz = types.ModuleType("fitz")
    fake_fitz.open = lambda p: FakePdf()

    class _FakeArr:
        def reshape(self, *a):
            return self

    fake_np = types.ModuleType("numpy")
    fake_np.frombuffer = lambda b, dtype=None: _FakeArr()
    fake_np.uint8 = None

    class FakeOCROutput:
        txts = ["识别出的文字"]

    class FakeOCR:
        def __call__(self, img):
            return FakeOCROutput()

    fake_rapid = types.ModuleType("rapidocr")
    fake_rapid.RapidOCR = FakeOCR

    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    monkeypatch.setitem(sys.modules, "rapidocr", fake_rapid)

    doc = OCRParser().parse("fake.pdf")
    assert doc is not None
    assert "识别出的文字" in next(doc.iter_blocks()).text


def test_mineru_parser_unavailable_when_not_installed(monkeypatch):
    """mineru 未装 → MinerUParser().__init__ raise ImportError。"""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("magic_pdf", "mineru"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from agents_rag.parsing.mineru_parser import MinerUParser

    with pytest.raises(ImportError):
        MinerUParser()
