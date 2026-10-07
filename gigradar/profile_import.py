"""One-time import: turn my documents (CV, portfolio, case studies) into Markdown raw material.

    python -m gigradar.profile_import CV.pdf portfolio/ [--out profile_sources] [--force]

Writes profile_sources/<file name>.md (gitignored) to copy from when writing profile.md by
hand. Never run by scheduled runs: scoring only reads the curated profile.md.

.pdf/.docx/.pptx/.xlsx/.odt/.rtf go through anydoc (local Rust converter, optional:
pip install -r requirements-profile.txt); .txt/.md are copied as-is. Image-only (scanned)
PDFs are reported and skipped: no OCR. Exit 0 = all done, 1 = some file skipped, 2 = usage.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CONVERT_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".rtf")
COPY_SUFFIXES = (".txt", ".md")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "profile_sources"

Converter = Callable[[Path], str]


class ConversionFailed(Exception):
    """This one document could not be converted; the message says why."""


class ConverterMissing(Exception):
    """anydoc is not installed but a document needs it."""


@dataclass(frozen=True)
class Outcome:
    source: Path
    status: str              # converted | copied | kept | ignored | skipped
    detail: str              # target path or reason


def anydoc_converter() -> Converter:
    try:
        import anydoc
    except ModuleNotFoundError as exc:
        raise ConverterMissing("anydoc is not installed: pip install -r requirements-profile.txt") from exc

    def convert(path: Path) -> str:
        try:
            # ocr="reject" is anydoc's default; spelled out because "hosted" would upload
            # the whole document to Firecrawl's API. Conversion must stay local.
            return anydoc.to_markdown(path, ocr="reject")
        except anydoc.NeedsOcrError as exc:
            raise ConversionFailed(f"image-only/scanned PDF ({len(exc.pages)} of {exc.page_count} "
                                   f"pages need OCR, which is not supported)") from exc
        except anydoc.EncryptedError as exc:
            raise ConversionFailed("encrypted or password-protected") from exc
        except anydoc.ConvertError as exc:
            raise ConversionFailed(f"{type(exc).__name__}: {exc}") from exc
        except OSError as exc:
            raise ConversionFailed(f"unreadable: {exc}") from exc

    return convert


def target_for(source: Path, out_dir: Path) -> Path:
    """cv.pdf -> cv.pdf.md (keeps cv.pdf and cv.docx apart); notes.md stays notes.md."""
    name = source.name if source.suffix.lower() == ".md" else f"{source.name}.md"
    return out_dir / name


def collect(paths: list[Path]) -> list[Path]:
    """Files as given, plus the files directly inside given folders (not recursive)."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"not found: {path}")
    return files


def import_documents(files: list[Path], out_dir: Path, make_converter: Callable[[], Converter],
                     force: bool) -> list[Outcome]:
    """Convert/copy each file into out_dir. One bad file never stops the others."""
    out_dir.mkdir(parents=True, exist_ok=True)
    converter: Converter | None = None
    claimed: set[Path] = set()
    outcomes: list[Outcome] = []
    for source in files:
        suffix = source.suffix.lower()
        if suffix not in CONVERT_SUFFIXES + COPY_SUFFIXES:
            outcomes.append(Outcome(source, "ignored", f"unsupported type '{suffix or source.name}'"))
            continue
        target = target_for(source, out_dir)
        if target in claimed:
            outcomes.append(Outcome(source, "skipped", f"another input also maps to {target.name}"))
            continue
        claimed.add(target)
        if target.exists() and not force:
            outcomes.append(Outcome(source, "kept", f"{target} exists (--force to overwrite)"))
            continue
        if suffix in COPY_SUFFIXES:
            shutil.copyfile(source, target)
            outcomes.append(Outcome(source, "copied", str(target)))
            continue
        if converter is None:
            converter = make_converter()
        try:
            markdown = converter(source)
        except ConversionFailed as exc:
            outcomes.append(Outcome(source, "skipped", str(exc)))
            continue
        target.write_text(f"<!-- converted from {source.name} by gigradar.profile_import -->\n\n{markdown}",
                          encoding="utf-8", newline="\n")
        outcomes.append(Outcome(source, "converted", str(target)))
    return outcomes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m gigradar.profile_import", description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", type=Path, help="documents or folders (folders: top level only)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default: profile_sources/)")
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    args = parser.parse_args(argv)

    try:
        files = collect(args.paths)
        outcomes = import_documents(files, args.out, anydoc_converter, args.force)
    except (FileNotFoundError, ConverterMissing) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for o in outcomes:
        print(f"{o.status.upper():9} {o.source.name}: {o.detail}")
    skipped = [o for o in outcomes if o.status == "skipped"]
    done = sum(o.status in ("converted", "copied") for o in outcomes)
    print(f"\n{done} written, {len(skipped)} skipped, "
          f"{len(outcomes) - done - len(skipped)} kept/ignored -> {args.out}")
    if skipped:
        print("Skipped files were NOT imported; fix or convert them yourself if you need them.")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # non-cp1252 file names on a Windows console
    sys.exit(main(sys.argv[1:]))
