"""bot/features/chatgraph.py — Генерация PNG-графиков статистики чата через Pillow."""
from __future__ import annotations

import io
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
    _PILLOW = True
except ImportError:
    _PILLOW = False


# Цветовая палитра (тёмная тема как в Telegram)
BG = (14, 22, 33)
GRID = (30, 50, 70)
BAR_COLORS = [
    (100, 180, 255),
    (80, 220, 160),
    (255, 180, 80),
    (220, 100, 130),
    (160, 130, 255),
    (80, 200, 220),
    (255, 140, 60),
    (180, 255, 100),
]
TEXT_MAIN = (226, 230, 234)
TEXT_DIM = (139, 152, 165)
ACCENT = (100, 180, 255)


def _try_font(size: int) -> Any:
    """Возвращает шрифт если доступен, иначе None (Pillow default)."""
    try:
        from PIL import ImageFont
        # Пробуем системные шрифты
        for name in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                     "/System/Library/Fonts/Helvetica.ttc",
                     "/usr/share/fonts/dejavu/DejaVuSans.ttf"]:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()
    except Exception:
        return None


def make_hourly_chart(hours: list[int], peak_h: int) -> bytes | None:
    """Возвращает PNG с гистограммой активности по часам (0-23)."""
    if not _PILLOW:
        return None
    W, H = 800, 340
    PAD = 48
    BAR_W = (W - PAD * 2) // 24

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_sm = _try_font(14)
    font_md = _try_font(16)

    max_val = max(hours) if any(hours) else 1

    # Заголовок
    title = "Активность по часам"
    draw.text((W // 2, 14), title, fill=TEXT_MAIN, font=font_md, anchor="mt" if font_md else None)

    # Сетка
    chart_h = H - PAD - 50
    for i in range(5):
        y = PAD + chart_h - int(chart_h * i / 4)
        draw.line([(PAD, y), (W - PAD, y)], fill=GRID, width=1)
        val_label = str(int(max_val * i / 4))
        draw.text((PAD - 6, y), val_label, fill=TEXT_DIM, font=font_sm)

    # Столбцы
    for h, count in enumerate(hours):
        x0 = PAD + h * BAR_W + 2
        x1 = x0 + BAR_W - 4
        bar_h = int(chart_h * count / max_val) if max_val > 0 else 0
        y0 = PAD + chart_h - bar_h
        y1 = PAD + chart_h

        color = (255, 220, 60) if h == peak_h else BAR_COLORS[0]
        draw.rectangle([(x0, y0), (x1, y1)], fill=color)

        # Метки часов (каждые 3 часа)
        if h % 3 == 0:
            draw.text((x0 + BAR_W // 2 - 2, H - 30), f"{h:02d}", fill=TEXT_DIM, font=font_sm)

    # Легенда
    draw.rectangle([(PAD, H - 22), (PAD + 12, H - 10)], fill=(255, 220, 60))
    draw.text((PAD + 16, H - 22), f"Пик: {peak_h:02d}:00", fill=TEXT_MAIN, font=font_sm)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def make_kinds_pie(kinds: dict[str, int], kind_labels: dict[str, str]) -> bytes | None:
    """Возвращает PNG с кольцевой диаграммой типов сообщений."""
    if not _PILLOW:
        return None

    import math

    W, H = 600, 380
    CX, CY = 210, 190
    R_OUT, R_IN = 155, 75

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_sm = _try_font(14)
    font_md = _try_font(16)
    font_lg = _try_font(22)

    total = sum(kinds.values()) or 1
    items = sorted(kinds.items(), key=lambda x: x[1], reverse=True)

    # Рисуем сегменты
    start_angle = -90.0
    for idx, (kind, count) in enumerate(items):
        sweep = count / total * 360
        color = BAR_COLORS[idx % len(BAR_COLORS)]
        # Рисуем через прямоугольник pieslice
        bbox = [(CX - R_OUT, CY - R_OUT), (CX + R_OUT, CY + R_OUT)]
        draw.pieslice(bbox, start=start_angle, end=start_angle + sweep, fill=color)
        start_angle += sweep

    # Закрашиваем центр для эффекта кольца
    draw.ellipse([(CX - R_IN, CY - R_IN), (CX + R_IN, CY + R_IN)], fill=BG)

    # Центральный текст — суммарное кол-во
    draw.text((CX, CY - 14), str(total), fill=TEXT_MAIN, font=font_lg, anchor="mm" if font_lg else None)
    draw.text((CX, CY + 12), "сообщений", fill=TEXT_DIM, font=font_sm, anchor="mm" if font_sm else None)

    # Легенда справа
    lx = CX + R_OUT + 24
    ly = 30
    for idx, (kind, count) in enumerate(items[:8]):
        color = BAR_COLORS[idx % len(BAR_COLORS)]
        label = kind_labels.get(kind, kind)
        pct = int(count / total * 100)
        draw.rectangle([(lx, ly), (lx + 14, ly + 14)], fill=color)
        draw.text((lx + 20, ly), f"{label}: {count} ({pct}%)", fill=TEXT_MAIN, font=font_sm)
        ly += 28

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


