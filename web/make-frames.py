"""Render demo video frames for bootcheck with PIL, then ffmpeg them.

Every terminal line shown is real captured output of the real tool.
Nothing mocked up.
"""
import textwrap
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG = (16, 16, 22)
TERM_BG = (12, 12, 18)
HEADER = (30, 30, 40)
FG = (212, 212, 212)
DIM = (140, 140, 160)
GREEN = (126, 231, 135)
RED = (255, 123, 114)
YELLOW = (227, 179, 65)
CYAN = (121, 192, 255)
CAPTION_BG = (0, 0, 0)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

font_term = ImageFont.truetype(MONO, 16)
font_term_b = ImageFont.truetype(MONO_B, 16)
font_term_s = ImageFont.truetype(MONO, 13)
font_title = ImageFont.truetype(MONO_B, 44)
font_sub = ImageFont.truetype(MONO, 22)
font_cap = ImageFont.truetype(MONO_B, 21)


def color_for(line):
    s = line.strip()
    if s.startswith("$"):
        return GREEN
    if s.startswith("[E"):
        return RED
    if s.startswith("[W"):
        return YELLOW
    if "clean. ship it." in s:
        return GREEN
    if s.startswith("ISSUES"):
        return CYAN if "none" in s else RED
    if s in ("BOOT",) or s.startswith("IMAGE"):
        return CYAN
    if s.startswith("bootcheck"):
        return CYAN
    return FG


def draw_terminal(draw, lines, y0=86, x0=48, line_h=24):
    # shrink the font when a frame has long lines so nothing clips
    font = font_term
    if max((len(ln) for ln in lines), default=0) > 124:
        font, line_h = font_term_s, 20
    for ln in lines:
        draw.text((x0, y0), ln, font=font, fill=color_for(ln))
        y0 += line_h


def header_bar(draw):
    draw.rectangle([0, 0, W, 56], fill=HEADER)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([24 + i * 28, 18, 40 + i * 28, 34], fill=c)
    draw.text((110, 16), "bootcheck — terminal", font=font_term, fill=DIM)


def caption(draw, text):
    wrapped = textwrap.wrap(text, width=72)
    n = len(wrapped)
    h = 28 + n * 28
    draw.rectangle([0, H - h, W, H], fill=CAPTION_BG)
    y = H - h + 12
    for ln in wrapped:
        draw.text((W // 2, y), ln, font=font_cap, fill=(255, 255, 255),
                  anchor="ma")
        y += 28


def term_frame(lines, cap_text, path):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    header_bar(draw)
    draw_terminal(draw, lines)
    caption(draw, cap_text)
    img.save(path)


def title_frame(path):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 250), "bootcheck", font=font_title, fill=GREEN,
              anchor="ma")
    draw.text((W // 2, 330),
              "your QNX buildfile, checked before it bricks your board.",
              font=font_sub, fill=FG, anchor="ma")
    draw.text((W // 2, 380), "a host tool for QNX SDP 8.0  ·  python, no deps",
              font=font_sub, fill=DIM, anchor="ma")
    caption(draw, "flash a bad QNX image and your board just sits there, dead. this checks the buildfile first.")
    img.save(path)


def end_frame(path):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 200), "one file. no deps. no QNX box needed.",
              font=font_title, fill=GREEN, anchor="ma")
    draw.text((W // 2, 290), "$ python3 bootcheck.py your-board.bld",
              font=font_sub, fill=FG, anchor="ma")
    draw.text((W // 2, 340), "bootcheck  ·  MIT  ·  QNX Global Hackathon 2026",
              font=font_sub, fill=DIM, anchor="ma")
    caption(draw, "check it before it bricks your board.")
    img.save(path)


def read(path):
    with open(path) as f:
        return [ln.rstrip("\n") for ln in f]


def main():
    out = "web/frames"
    good = read("/tmp/out-good.txt")
    broken = read("/tmp/out-broken.txt")
    js = read("/tmp/out-json.txt")
    tests = read("/tmp/out-tests.txt")

    title_frame(f"{out}/01-title.png")
    term_frame(good, "a good buildfile: boot setup, full layout, and... clean. ship it.",
               f"{out}/02-good.png")
    # broken output is 47 lines — show it in three scrolling frames
    term_frame(broken[:22],
               "now a buildfile somebody had a bad day writing.",
               f"{out}/03-broken-a.png")
    term_frame(broken[20:42],
               "fourteen errors, six warnings. no procnto — that's the kernel, my guy.",
               f"{out}/04-broken-b.png")
    term_frame(broken[40:],
               "typos, dupes, a .script with no +script so nothing runs at boot.",
               f"{out}/05-broken-c.png")
    term_frame(js, "and if you live in CI, there's --json. same report, machine-readable.",
               f"{out}/06-json.png")
    term_frame(tests, "thirty-seven tests. all green.",
               f"{out}/07-tests.png")
    end_frame(f"{out}/08-end.png")
    print("frames done")


if __name__ == "__main__":
    main()
