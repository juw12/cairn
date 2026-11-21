"""Make Cairn.icns for the Mac app from the web app's icon. Used by docs/mac-install.sh.

    python scripts/mac_icon.py docs/icons/icon-512.png Cairn.icns

macOS draws its app icons as a rounded square that fills 824 of 1024 pixels, with empty space around
it. The web icon is a full square, so it is scaled into that shape. Needs Pillow and the `iconutil`
tool that every Mac has.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = 1024
BODY = 824       # Apple's icon grid: the rounded square inside the 1024 canvas
RADIUS = 185     # its corner radius
SIZES = [16, 32, 128, 256, 512]


def mac_shape(source: Path) -> Image.Image:
    art = Image.open(source).convert("RGBA").resize((BODY, BODY), Image.LANCZOS)
    mask = Image.new("L", (BODY, BODY), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BODY - 1, BODY - 1), radius=RADIUS, fill=255)
    # an icon that already has rounded corners keeps its own edge
    mask = Image.composite(art.getchannel("A"), Image.new("L", (BODY, BODY), 0), mask)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    edge = (CANVAS - BODY) // 2
    canvas.paste(art, (edge, edge), mask)
    return canvas


def write_icns(image: Image.Image, target: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / "Cairn.iconset"
        iconset.mkdir()
        for size in SIZES:
            image.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            image.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "--convert", "icns", "--output", str(target), str(iconset)], check=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    write_icns(mac_shape(Path(argv[0])), Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
