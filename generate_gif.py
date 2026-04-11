#!/usr/bin/env python3
"""Generate demo GIF for claude-guard README."""

from PIL import Image, ImageDraw, ImageFont
import os

# Terminal colors (Catppuccin Mocha-ish)
BG = (30, 30, 46)
FG = (205, 214, 244)
GREEN = (166, 227, 161)
YELLOW = (249, 226, 175)
RED = (243, 139, 168)
CYAN = (137, 220, 235)
DIM = (108, 112, 134)
BOLD_WHITE = (255, 255, 255)

WIDTH = 800
HEIGHT = 480
PADDING = 20
LINE_HEIGHT = 18
FONT_SIZE = 14

# Try to find a monospace font
FONT_PATHS = [
    "C:/Windows/Fonts/consola.ttf",  # Consolas
    "C:/Windows/Fonts/cour.ttf",     # Courier New
    "C:/Windows/Fonts/lucon.ttf",    # Lucida Console
]

font = None
for fp in FONT_PATHS:
    if os.path.exists(fp):
        try:
            font = ImageFont.truetype(fp, FONT_SIZE)
            break
        except Exception:
            continue
if font is None:
    font = ImageFont.load_default()


def make_frame(lines):
    """Create a single frame image from colored text lines."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    y = PADDING
    for line in lines:
        x = PADDING
        if isinstance(line, str):
            draw.text((x, y), line, fill=FG, font=font)
        elif isinstance(line, list):
            # List of (text, color) tuples
            for text, color in line:
                draw.text((x, y), text, fill=color, font=font)
                bbox = font.getbbox(text)
                x += bbox[2] - bbox[0]
        y += LINE_HEIGHT

    return img


def bar_text(percent, width=20):
    filled = int(width * min(percent, 100) / 100)
    empty = width - filled
    return "█" * filled + "░" * empty


def status_frame(title_lines, session, hourly, daily, monthly,
                 s_lim=5, h_lim=10, d_lim=50, m_lim=500, extra_lines=None):
    """Generate a status display frame."""
    lines = list(title_lines)

    lines.append("")
    lines.append([("claude-guard", BOLD_WHITE), (" v0.1.0", DIM)])
    lines.append("")

    data = [
        ("Session ", session, s_lim),
        ("Hourly  ", hourly, h_lim),
        ("Daily   ", daily, d_lim),
        ("Monthly ", monthly, m_lim),
    ]

    for name, current, limit in data:
        pct = current / limit * 100 if limit > 0 else 0
        bar = bar_text(pct)

        if pct >= 100:
            bar_color = RED
        elif pct >= 80:
            bar_color = YELLOW
        else:
            bar_color = GREEN

        label = f"  {name} ${current:>7.2f} / ${limit:>7.2f}  "
        bar_str = f"[{bar}]"
        pct_str = f" {pct:5.1f}%"

        parts = [(label, FG), (bar_str, bar_color), (pct_str, FG)]

        if pct >= 100:
            parts.append(("  ⛔ BLOCKED", RED))
        elif pct >= 80:
            parts.append(("  ⚠ WARNING", YELLOW))

        lines.append(parts)

    lines.append("")

    worst_pct = max(
        session / s_lim * 100 if s_lim > 0 else 0,
        hourly / h_lim * 100 if h_lim > 0 else 0,
        daily / d_lim * 100 if d_lim > 0 else 0,
        monthly / m_lim * 100 if m_lim > 0 else 0,
    )

    if worst_pct >= 100:
        status_text, status_color = "DENY", RED
    elif worst_pct >= 80:
        status_text, status_color = "WARN", YELLOW
    else:
        status_text, status_color = "OK", GREEN

    lines.append([("  Status:   ", FG), (status_text, status_color)])
    lines.append([("  Anomaly:  ", FG), ("normal", DIM)])

    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)

    return make_frame(lines)


# Generate frames
frames = []

# Frame 1: Normal status (3 seconds)
f1 = status_frame(
    [[("$ ", CYAN), ("claude-guard status", FG)]],
    session=1.24, hourly=3.80, daily=12.50, monthly=89.30,
)
frames.extend([f1] * 30)  # 3s at 10fps

# Frame 2: Warning status (3 seconds)
f2 = status_frame(
    [[("$ ", CYAN), ("claude-guard status", FG)],
     [("# ... after heavy Claude Code usage ...", DIM)]],
    session=4.20, hourly=8.50, daily=18.50, monthly=142.30,
)
frames.extend([f2] * 30)

# Frame 3: Blocked! (4 seconds)
f3 = status_frame(
    [[("$ ", CYAN), ("claude-guard status", FG)]],
    session=5.50, hourly=9.80, daily=22.50, monthly=146.30,
    extra_lines=[
        [("  🛡 Tool call BLOCKED — budget exceeded", RED)],
    ]
)
frames.extend([f3] * 40)

# Frame 4: Adjust and recover (3 seconds)
f4 = status_frame(
    [[("$ ", CYAN), ("claude-guard set session 10", FG)],
     [("  Set session budget to $10.00", GREEN)]],
    session=5.50, hourly=9.80, daily=22.50, monthly=146.30,
    s_lim=10,
)
frames.extend([f4] * 30)

# Save GIF
frames[0].save(
    "demo.gif",
    save_all=True,
    append_images=frames[1:],
    duration=100,  # 100ms per frame = 10fps
    loop=0,
)

size_kb = os.path.getsize("demo.gif") / 1024
print(f"Generated demo.gif ({size_kb:.0f} KB, {len(frames)} frames)")
