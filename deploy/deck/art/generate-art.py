#!/usr/bin/env python3
"""Generates the Steam grid artwork: two PS1 consoles joined by a link cable.

Outputs, sized for Steam's non-Steam-shortcut art slots:
  portrait.png  600x900   library capsule
  banner.png    920x430   horizontal capsule
  hero.png      1920x620  library header
  logo.png      transparent title lockup
  icon.png      512x512

Install by copying into userdata/<uid>/config/grid/ named for the shortcut's appid:
  <appid>p.png, <appid>.png, <appid>_hero.png, <appid>_logo.png, <appid>_icon.png
"""

import math
import sys

from PIL import Image, ImageDraw, ImageFont

BG = (16, 18, 24, 255)
GOLD = (224, 178, 74, 255)
BODY = (191, 188, 180, 255)
BODY_SHADE = (160, 157, 149, 255)
DETAIL = (85, 82, 78, 255)
CABLE = (42, 42, 46, 255)

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
]


def font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_ps1(draw, cx, cy, w, angle_serial="right"):
    """A PlayStation from above, centred at (cx, cy), body width w. Serial port faces
    `angle_serial` ('left'/'right'), which is where the cable plugs in."""
    h = w * 0.94
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    r = w * 0.08
    # body with a subtle two-tone: rear third slightly darker
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=BODY, outline=DETAIL, width=max(2, int(w * 0.012)))
    draw.rounded_rectangle([x0, y0, x1, y0 + h * 0.30], radius=r, fill=BODY_SHADE)
    draw.rectangle([x0, y0 + h * 0.22, x1, y0 + h * 0.30], fill=BODY_SHADE)

    # disc lid: concentric circles, off-centre toward the rear like the real thing
    lidc = (cx, y0 + h * 0.42)
    lid_r = w * 0.27
    for i, rr in enumerate([lid_r, lid_r * 0.80, lid_r * 0.58, lid_r * 0.36]):
        draw.ellipse([lidc[0] - rr, lidc[1] - rr, lidc[0] + rr, lidc[1] + rr],
                     outline=DETAIL, width=max(2, int(w * 0.010)),
                     fill=BODY if i == 0 else None)
    hub = lid_r * 0.13
    draw.ellipse([lidc[0] - hub, lidc[1] - hub, lidc[0] + hub, lidc[1] + hub], fill=DETAIL)

    # power + open buttons flanking the lid
    bw, bh = w * 0.10, h * 0.045
    draw.rounded_rectangle([x0 + w * 0.06, lidc[1] - bh / 2, x0 + w * 0.06 + bw, lidc[1] + bh / 2],
                           radius=bh / 2, fill=DETAIL)
    draw.rounded_rectangle([x1 - w * 0.06 - bw, lidc[1] - bh / 2, x1 - w * 0.06, lidc[1] + bh / 2],
                           radius=bh / 2, fill=DETAIL)

    # the little gold square where the logo lives, front-centre
    ls = w * 0.055
    draw.rounded_rectangle([cx - ls, y1 - h * 0.18 - ls, cx + ls, y1 - h * 0.18 + ls],
                           radius=ls * 0.3, fill=GOLD)

    # controller/memory-card ports on the front edge
    pw, ph = w * 0.16, h * 0.06
    for side in (-1, 1):
        px = cx + side * w * 0.24
        draw.rounded_rectangle([px - pw / 2, y1 - ph * 1.6, px + pw / 2, y1 - ph * 0.6],
                               radius=ph * 0.3, fill=DETAIL)

    # serial port on the requested flank, rear third - the cable's destination
    sw, sh = w * 0.035, h * 0.12
    sx = x1 if angle_serial == "right" else x0
    port = [sx - sw, y0 + h * 0.30 - sh / 2, sx + sw, y0 + h * 0.30 + sh / 2]
    draw.rounded_rectangle(port, radius=sw * 0.8, fill=DETAIL)
    return (sx, y0 + h * 0.30)  # where the cable attaches


def bezier(p0, p1, p2, p3, n=120):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def draw_cable(draw, a, b, sag, width, vertical=False):
    """A link cable from serial port a to serial port b with plugs at both ends."""
    if vertical:
        c1 = (a[0] + sag, a[1] + (b[1] - a[1]) * 0.25)
        c2 = (b[0] + sag, b[1] - (b[1] - a[1]) * 0.25)
    else:
        c1 = (a[0] + (b[0] - a[0]) * 0.25, a[1] + sag)
        c2 = (b[0] - (b[0] - a[0]) * 0.25, b[1] + sag)
    pts = bezier(a, c1, c2, b)
    draw.line(pts, fill=CABLE, width=width, joint="curve")
    # plugs: a block with a gold band where it meets each console
    for (px, py), (qx, qy) in ((a, pts[6]), (b, pts[-7])):
        ang = math.atan2(qy - py, qx - px)
        pl, pw2 = width * 2.6, width * 1.5
        dx, dy = math.cos(ang), math.sin(ang)
        nx, ny = -dy, dx
        poly = [(px + nx * pw2 / 2, py + ny * pw2 / 2),
                (px - nx * pw2 / 2, py - ny * pw2 / 2),
                (px - nx * pw2 / 2 + dx * pl, py - ny * pw2 / 2 + dy * pl),
                (px + nx * pw2 / 2 + dx * pl, py + ny * pw2 / 2 + dy * pl)]
        draw.polygon(poly, fill=CABLE)
        band = [(px + nx * pw2 / 2 + dx * pl * 0.35, py + ny * pw2 / 2 + dy * pl * 0.35),
                (px - nx * pw2 / 2 + dx * pl * 0.35, py - ny * pw2 / 2 + dy * pl * 0.35),
                (px - nx * pw2 / 2 + dx * pl * 0.55, py - ny * pw2 / 2 + dy * pl * 0.55),
                (px + nx * pw2 / 2 + dx * pl * 0.55, py + ny * pw2 / 2 + dy * pl * 0.55)]
        draw.polygon(band, fill=GOLD)


def title(draw, cx, cy, size, text="PS1 LINK CABLE"):
    f = font(size)
    box = draw.textbbox((0, 0), text, font=f)
    draw.text((cx - (box[2] - box[0]) / 2, cy - (box[3] - box[1]) / 2), text, font=f, fill=GOLD)


def portrait(path):
    im = Image.new("RGBA", (600, 900), BG)
    d = ImageDraw.Draw(im)
    a = draw_ps1(d, 300, 210, 300, "right")
    b = draw_ps1(d, 300, 640, 300, "right")
    draw_cable(d, a, b, 130, 13, vertical=True)
    title(d, 300, 830, 52)
    im.convert("RGB").save(path)


def banner(path, w=920, h=430):
    im = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(im)
    a = draw_ps1(d, w * 0.22, h * 0.44, h * 0.62, "right")
    b = draw_ps1(d, w * 0.78, h * 0.44, h * 0.62, "left")
    draw_cable(d, a, b, h * 0.30, max(8, h // 40))
    title(d, w / 2, h * 0.87, h // 8)
    im.convert("RGB").save(path)


def hero(path):
    im = Image.new("RGBA", (1920, 620), BG)
    d = ImageDraw.Draw(im)
    a = draw_ps1(d, 430, 300, 400, "right")
    b = draw_ps1(d, 1490, 300, 400, "left")
    draw_cable(d, a, b, 190, 18)
    im.convert("RGB").save(path)


def logo(path):
    im = Image.new("RGBA", (1000, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    title(d, 500, 110, 96)
    # a short cable underlining the text
    draw_cable(d, (150, 220), (850, 220), 40, 10)
    im.save(path)


def icon(path):
    im = Image.new("RGBA", (512, 512), BG)
    d = ImageDraw.Draw(im)
    a = draw_ps1(d, 256, 140, 210, "right")
    b = draw_ps1(d, 256, 380, 210, "right")
    draw_cable(d, a, b, 95, 10, vertical=True)
    im.convert("RGB").save(path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    portrait(f"{out}/portrait.png")
    banner(f"{out}/banner.png")
    hero(f"{out}/hero.png")
    logo(f"{out}/logo.png")
    icon(f"{out}/icon.png")
    print(f"art written to {out}")
