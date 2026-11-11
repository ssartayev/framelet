"""Render Framelet's original arcade backgrounds with Pillow and FFmpeg."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT, FPS, SECONDS = 360, 640, 24, 8
THEMES = {
    "neon": ("#141129", "#302145", "#30223b", "#ee65ac", "#65468b", "#29e5e5"),
    "desert": ("#ed8c54", "#934957", "#5e3a3e", "#ffce79", "#b86f57", "#ffd080"),
}


def draw_frame(frame, palette):
    sky, land, road, accent, building, car = palette
    phase = frame / (FPS * SECONDS)
    image = Image.new("RGB", (WIDTH, HEIGHT), sky)
    draw = ImageDraw.Draw(image)
    draw.ellipse((208, 78, 288, 158), fill=accent)
    draw.polygon([(0, 192), (51, 140), (106, 186), (157, 149), (218, 201),
                  (279, 145), (359, 171), (360, 640), (0, 640)], fill=land)
    horizon = 200
    draw.polygon([(174, horizon), (186, horizon), (405, HEIGHT), (-45, HEIGHT)], fill=road)
    draw.line([(174, horizon), (-45, HEIGHT)], fill=accent, width=4)
    draw.line([(186, horizon), (405, HEIGHT)], fill=accent, width=4)
    for index in range(12):
        depth = (index / 12 + phase * 4) % 1
        y = horizon + int(440 * depth**2)
        length = max(2, int(32 * depth**2))
        w = max(1, int(6 * depth))
        draw.rectangle((180-w, y, 180+w, y+length), fill=accent)
    for index in range(10):
        depth = (index / 10 + phase * 2) % 1
        y = horizon + int(440 * depth**2)
        size = max(2, int(74 * depth))
        for side in (-1, 1):
            x = 180 + side * (20 + int(275 * depth**2))
            draw.rectangle((x-size//2, y-size*2, x+size//2, y), fill=building)
            for window in range(1, 4):
                wy = y - int(size * window / 2)
                draw.line((x-size//3, wy, x+size//3, wy), fill=accent, width=max(1, size//14))
    from math import sin, tau
    x = 180 + round(38 * sin(phase * tau * 2))
    y = 502 + round(7 * sin(phase * tau * 4))
    draw.ellipse((x-43, y+31, x+43, y+50), fill="#17131f")
    draw.rounded_rectangle((x-39, y+8, x+39, y+42), 8, fill=car)
    draw.polygon([(x-29, y+14), (x-17, y-10), (x+17, y-10), (x+29, y+14)], fill=car)
    draw.polygon([(x-22, y+11), (x-13, y-5), (x+13, y-5), (x+22, y+11)], fill="#302945")
    for side in (-1, 1):
        lamp = x + side * 26
        draw.rounded_rectangle((lamp-8, y+20, lamp+8, y+25), 2, fill="#ff4263")
    return image


if __name__ == "__main__":
    folder = ROOT / "dist/backgrounds"
    folder.mkdir(exist_ok=True)
    for name, palette in THEMES.items():
        target = folder / f"{name}.mp4"
        if target.is_file():
            continue
        temporary = target.with_suffix(".part.mp4")
        with subprocess.Popen(
            [ROOT / "runtime/bin/ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt",
             "yuv420p", "-movflags", "+faststart", temporary],
            stdin=subprocess.PIPE,
        ) as encoder:
            for frame in range(FPS * SECONDS):
                encoder.stdin.write(draw_frame(frame, palette).tobytes())
            encoder.stdin.close()
            if encoder.wait():
                raise SystemExit(f"Could not encode {name}")
        temporary.replace(target)
