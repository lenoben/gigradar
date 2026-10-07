"""Run: .venv/Scripts/python -m unittest discover -s tests   (stdlib only, no network)"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
import zlib
from pathlib import Path

from gigradar import profile_import
from gigradar.profile_import import ConversionFailed, ConverterMissing, collect, import_documents, main

HAS_ANYDOC = importlib.util.find_spec("anydoc") is not None


def fake_converter():
    def convert(path: Path) -> str:
        if path.stem == "scan":
            raise ConversionFailed("image-only/scanned PDF")
        return f"# {path.name}\n"
    return convert


def no_converter():
    raise AssertionError("converter must not be created for copy-only imports")


def minimal_pdf(content: bytes, resources: bytes, extra_objs: list[bytes]) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources " + resources + b" >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content), *extra_objs]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


class ImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.src = self.dir / "docs"
        self.out = self.dir / "out"
        self.src.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.src / name
        path.write_bytes(data)
        return path

    def test_convert_copy_ignore_skip(self) -> None:
        self.write("cv.pdf", b"x")
        self.write("cv.DOCX", b"x")
        self.write("scan.pdf", b"x")
        self.write("notes.md", b"# notes\r\nexact bytes\r\n")
        self.write("skills.txt", b"Rust\n")
        self.write("photo.jpg", b"x")
        outcomes = import_documents(collect([self.src]), self.out, fake_converter, False)
        status = {o.source.name: o.status for o in outcomes}
        self.assertEqual(status, {"cv.pdf": "converted", "cv.DOCX": "converted", "scan.pdf": "skipped",
                                  "notes.md": "copied", "skills.txt": "copied", "photo.jpg": "ignored"})
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["cv.DOCX.md", "cv.pdf.md", "notes.md", "skills.txt.md"])
        self.assertEqual((self.out / "notes.md").read_bytes(), b"# notes\r\nexact bytes\r\n")
        self.assertEqual((self.out / "cv.pdf.md").read_text(encoding="utf-8"),
                         "<!-- converted from cv.pdf by gigradar.profile_import -->\n\n# cv.pdf\n")
        skipped = next(o for o in outcomes if o.status == "skipped")
        self.assertIn("image-only", skipped.detail)

    def test_existing_output_kept_unless_force(self) -> None:
        self.write("cv.pdf", b"x")
        self.out.mkdir()
        (self.out / "cv.pdf.md").write_text("hand edited", encoding="utf-8")
        [outcome] = import_documents([self.src / "cv.pdf"], self.out, fake_converter, False)
        self.assertEqual(outcome.status, "kept")
        self.assertEqual((self.out / "cv.pdf.md").read_text(encoding="utf-8"), "hand edited")
        [outcome] = import_documents([self.src / "cv.pdf"], self.out, fake_converter, True)
        self.assertEqual(outcome.status, "converted")

    def test_name_clash_between_inputs(self) -> None:
        (self.dir / "other").mkdir()
        a = self.write("cv.pdf", b"x")
        b = self.dir / "other" / "cv.pdf"
        b.write_bytes(b"x")
        outcomes = import_documents([a, b], self.out, fake_converter, False)
        self.assertEqual([o.status for o in outcomes], ["converted", "skipped"])

    def test_converter_only_created_when_needed(self) -> None:
        self.write("a.md", b"x")
        outcomes = import_documents(collect([self.src]), self.out, no_converter, False)
        self.assertEqual([o.status for o in outcomes], ["copied"])

    def test_collect(self) -> None:
        (self.src / "sub").mkdir()
        (self.src / "sub" / "deep.pdf").write_bytes(b"x")
        b = self.write("b.pdf", b"x")
        a = self.write("a.md", b"x")
        self.assertEqual(collect([self.src]), [a, b])  # sorted, not recursive
        with self.assertRaises(FileNotFoundError):
            collect([self.dir / "missing.pdf"])

    def test_main_exit_codes(self) -> None:
        self.write("ok.md", b"x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.src), "--out", str(self.out)]), 0)
            self.write("scan.pdf", b"x")
            original = profile_import.anydoc_converter
            profile_import.anydoc_converter = fake_converter
            try:
                self.assertEqual(main([str(self.src), "--out", str(self.out)]), 1)
            finally:
                profile_import.anydoc_converter = original
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(self.dir / "missing"), "--out", str(self.out)]), 2)
        self.assertIn("not found", err.getvalue())

    @unittest.skipIf(HAS_ANYDOC, "anydoc installed")
    def test_missing_anydoc_is_reported(self) -> None:
        with self.assertRaises(ConverterMissing) as ctx:
            profile_import.anydoc_converter()
        self.assertIn("requirements-profile.txt", str(ctx.exception))

    @unittest.skipUnless(HAS_ANYDOC, "anydoc not installed (requirements-profile.txt)")
    def test_real_anydoc(self) -> None:
        """Local conversion only; checks the anydoc error mapping for scanned PDFs."""
        self.write("cv.rtf", rb"{\rtf1\ansi {\b Rust} backend.\par}")
        self.write("text.pdf", minimal_pdf(
            b"BT /F1 18 Tf 72 720 Td (Senior Rust developer) Tj ET",
            b"<< /Font << /F1 5 0 R >> >>", [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]))
        pixels = zlib.compress(bytes([0, 255, 0]) * 64 * 64)
        image = (b"<< /Type /XObject /Subtype /Image /Width 64 /Height 64 /ColorSpace /DeviceRGB "
                 b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
                 % (len(pixels), pixels))
        self.write("scan.pdf", minimal_pdf(b"q 540 0 0 720 36 36 cm /Im1 Do Q",
                                           b"<< /XObject << /Im1 5 0 R >> >>", [image]))
        outcomes = import_documents(collect([self.src]), self.out, profile_import.anydoc_converter, False)
        status = {o.source.name: (o.status, o.detail) for o in outcomes}
        self.assertEqual(status["cv.rtf"][0], "converted")
        self.assertEqual(status["text.pdf"][0], "converted")
        self.assertEqual(status["scan.pdf"][0], "skipped")
        self.assertIn("need OCR", status["scan.pdf"][1])
        self.assertIn("**Rust** backend.", (self.out / "cv.rtf.md").read_text(encoding="utf-8"))
        self.assertIn("Senior Rust developer", (self.out / "text.pdf.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
