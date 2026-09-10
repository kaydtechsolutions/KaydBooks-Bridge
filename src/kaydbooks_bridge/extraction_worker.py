"""Bounded isolated raster/PDF decoding and offline OCR. No Bridge credentials."""

import json
import math
import os
import subprocess
import sys
import warnings
from pathlib import Path

MAX_PAGES = 4
MAX_PIXELS = 12_000_000
MAX_TEXT = 60_000


def ocr(image, directory, number):
    target = directory / f"ocr-{number}.json"
    result = subprocess.run(
        [
            os.environ["KAYDBOOKS_OCR_NODE"],
            str(Path(__file__).with_name("ocr_worker.cjs")),
            os.environ["KAYDBOOKS_OCR_MODULES"],
            str(image),
            str(target),
        ],
        capture_output=True,
        timeout=45,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode or not target.is_file() or target.stat().st_size > 2_000_000:
        raise ValueError("local OCR failed")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data["text"], str) or len(data["text"]) > MAX_TEXT:
        raise ValueError("OCR text limit")
    if (
        not isinstance(data["confidence"], (int, float))
        or not math.isfinite(data["confidence"])
        or not 0 <= data["confidence"] <= 100
    ):
        raise ValueError("OCR confidence invalid")
    return data


def extract(source, media, directory):
    import PIL
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    pages = []
    if media == "application/pdf":
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(source)
        try:
            if not 1 <= len(document) <= MAX_PAGES:
                raise ValueError("PDF page limit exceeded")
            for number in range(len(document)):
                page = document[number]
                try:
                    width, height = page.get_size()
                    if (
                        not all(math.isfinite(v) and 1 <= v <= 3000 for v in (width, height))
                        or width * height * 4 > MAX_PIXELS
                    ):
                        raise ValueError("PDF page dimensions exceed limit")
                    textpage = page.get_textpage()
                    try:
                        if textpage.count_chars() > MAX_TEXT:
                            raise ValueError("PDF text limit exceeded")
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                    # Rasterize even text PDFs: hidden/misordered text is never approval.
                    bitmap = page.render(scale=2)
                    image = directory / f"page-{number + 1}.png"
                    try:
                        bitmap.to_pil().convert("RGB").save(image)
                    finally:
                        bitmap.close()
                    data = ocr(image, directory, number + 1)
                    data.update(
                        page=number + 1, embedded_text=text, embedded_text_is_untrusted=True
                    )
                    pages.append(data)
                finally:
                    page.close()
        finally:
            document.close()
        decoder = {"pdfium": str(pdfium.PDFIUM_INFO), "pypdfium2": str(pdfium.PYPDFIUM_INFO)}
    elif media in {"image/png", "image/jpeg"}:
        with Image.open(source) as original:
            if (
                original.format not in {"PNG", "JPEG"}
                or original.width * original.height > MAX_PIXELS
            ):
                raise ValueError("unsupported or oversized image")
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError("animated images unavailable")
            image = ImageOps.exif_transpose(original).convert("RGB")
            target = directory / "page-1.png"
            image.save(target)
            image.close()
        data = ocr(target, directory, 1)
        data.update(page=1, embedded_text=None, embedded_text_is_untrusted=True)
        pages.append(data)
        decoder = {"pillow": PIL.__version__}
    else:
        raise ValueError("unsupported document type")
    if sum(len(p["text"]) for p in pages) > MAX_TEXT:
        raise ValueError("total extraction text limit")
    return {"pages": pages, "decoder": decoder, "page_count": len(pages)}


def main():
    try:
        source, media, output = sys.argv[1:]
        destination = Path(output)
        result = extract(source, media, destination.parent)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, allow_nan=False)
    except Exception:
        print("document decoding/OCR failed or exceeded limits", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
