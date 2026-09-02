"""Build labelled contact sheets for complete stage-2 visual QA."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def build_contact_sheet(
    images: list[Path], output_path: Path, title: str, columns: int = 4
) -> None:
    if not images:
        raise ValueError(f"No images supplied for {title}")
    thumb_width, thumb_height = 360, 255
    label_height, margin, title_height = 46, 18, 54
    rows = math.ceil(len(images) / columns)
    sheet = Image.new(
        "RGB",
        (
            margin + columns * (thumb_width + margin),
            title_height + rows * (thumb_height + label_height + margin),
        ),
        "#edf3f7",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((margin, 12), title, fill="#0a2a43", font=_font(24))
    for index, image_path in enumerate(images):
        row, column = divmod(index, columns)
        x = margin + column * (thumb_width + margin)
        y = title_height + row * (thumb_height + label_height + margin)
        with Image.open(image_path) as source:
            rendered = source.convert("RGB")
            rendered.thumbnail((thumb_width, thumb_height))
            tile = Image.new("RGB", (thumb_width, thumb_height), "white")
            tile.paste(
                rendered,
                ((thumb_width - rendered.width) // 2, (thumb_height - rendered.height) // 2),
            )
            sheet.paste(tile, (x, y))
        label = image_path.stem
        if len(label) > 35:
            label = f"{label[:32]}..."
        draw.text((x, y + thumb_height + 7), label, fill="#16384f", font=_font(15))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    qa = root / ".qa"
    scenarios = sorted(
        path for path in root.iterdir() if path.is_dir() and path.name.startswith("S")
    )
    for scenario in scenarios:
        if scenario.name.startswith("S00_"):
            workbook_dir = qa / "workbooks"
        else:
            workbook_dir = qa / scenario.name.split("_D00_CLEAN", maxsplit=1)[0] / "workbooks"
        build_contact_sheet(
            sorted(workbook_dir.glob("*.png")),
            qa / "contact-sheets-complete" / f"{scenario.name}-workbooks.png",
            f"{scenario.name} | XLSX | 15 sheets",
        )
        build_contact_sheet(
            sorted((qa / "documents" / scenario.name).glob("page-*.png")),
            qa / "contact-sheets-complete" / f"{scenario.name}-docx.png",
            f"{scenario.name} | DOCX | 8 pages",
        )
        build_contact_sheet(
            sorted((qa / "pdfs" / scenario.name).glob("page-*.png")),
            qa / "contact-sheets-complete" / f"{scenario.name}-pdf.png",
            f"{scenario.name} | PDF | 6 pages",
        )
    source_images = [
        scenario / "source-documents" / "10_现场照片说明.png" for scenario in scenarios
    ]
    source_images.extend(sorted((root / "variants").glob("*/*.png")))
    build_contact_sheet(
        source_images,
        qa / "contact-sheets-complete" / "all-source-images-and-variants.png",
        f"PNG source documents and variants | {len(source_images)} images",
    )
    build_contact_sheet(
        sorted((qa / "pdfs" / "D30_LOW_QUALITY_SCAN").glob("page-*.png")),
        qa / "contact-sheets-complete" / "D30_LOW_QUALITY_SCAN-pdf.png",
        "D30_LOW_QUALITY_SCAN | PDF | 6 pages",
    )


if __name__ == "__main__":
    main()
