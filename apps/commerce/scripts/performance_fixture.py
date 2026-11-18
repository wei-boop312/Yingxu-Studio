"""Create a deterministic 4MP synthetic test asset, never a marketplace sample."""

from pathlib import Path

from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
folder = root / "work" / "performance"
folder.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (2000, 2000), "#e4e0d4")
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((500, 180, 1500, 1820), radius=150, fill="#ba6535")
draw.rectangle((700, 700, 1300, 1150), fill="#e8d7ac")
draw.text((820, 850), "SYNTHETIC PERFORMANCE FIXTURE", fill="#243d2a")
for x in range(0, 2000, 100):
    draw.line((x, 0, x, 1999), fill="#c7c9b9", width=1)
image.save(folder / "4mp.png")
