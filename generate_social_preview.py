"""
GitHub ソーシャルプレビュー画像生成（1280 × 640 px）
出力: dist/social_preview.png
"""
import os, math
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
OUT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "social_preview.png")
FONT = r"C:\Windows\Fonts\msyh.ttc"

# ── カラーパレット ────────────────────────────────────────
BG1      = (10,  25, 47)    # 背景ダーク
BG2      = (15,  40, 75)    # 背景ライト
ACCENT1  = (0,  180, 240)   # シアン
ACCENT2  = (80, 100, 255)   # パープルブルー
WAVE1    = (0,  200, 255)   # ウェーブ明
WAVE2    = (0,  100, 180)   # ウェーブ暗
WHITE    = (255, 255, 255)
GRAY     = (160, 190, 220)
PILL_BG  = (20,  55,  95)   # タグ背景
PILL_BD  = (0,  150, 210)   # タグ枠


def lerp_color(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def draw_rounded_rect(draw, xy, r, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    if fill:
        draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
        draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
        draw.ellipse([x1, y1, x1 + 2*r, y1 + 2*r], fill=fill)
        draw.ellipse([x2 - 2*r, y1, x2, y1 + 2*r], fill=fill)
        draw.ellipse([x1, y2 - 2*r, x1 + 2*r, y2], fill=fill)
        draw.ellipse([x2 - 2*r, y2 - 2*r, x2, y2], fill=fill)
    if outline:
        draw.rounded_rectangle(xy, radius=r, outline=outline, width=width)


def make():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # ── 1. 背景グラデーション ─────────────────────────────
    for y in range(H):
        t = y / H
        c = lerp_color(BG1, BG2, t)
        draw.line([(0, y), (W, y)], fill=c)

    # ── 2. 背景ドットグリッド（装飾）────────────────────
    for gx in range(0, W, 50):
        for gy in range(0, H, 50):
            dist_from_center = math.sqrt((gx - W/2)**2 + (gy - H/2)**2)
            alpha = max(0, 1 - dist_from_center / 600)
            bright = int(30 + alpha * 25)
            dot_c = (bright, bright + 15, bright + 35)
            draw.ellipse([gx-1, gy-1, gx+1, gy+1], fill=dot_c)

    # ── 3. 左側グロー ────────────────────────────────────
    glow_layer = Image.new("RGB", (W, H), BG1)
    glow_draw  = ImageDraw.Draw(glow_layer)
    for radius in range(280, 0, -4):
        t       = (280 - radius) / 280
        c_glow  = lerp_color((0, 60, 120), (0, 20, 50), t)
        glow_draw.ellipse([320 - radius, H//2 - radius, 320 + radius, H//2 + radius],
                          fill=c_glow)
    img = Image.blend(img, glow_layer, alpha=0.5)
    draw = ImageDraw.Draw(img)

    # ── 4. 音声ウェーブバー ──────────────────────────────
    bar_heights = [
        40, 65, 95, 130, 170, 210, 240, 260, 255, 245,
        230, 200, 170, 140, 115, 95,  80,  70,  65,  60,
        68,  80,  95, 115, 140, 165, 185, 195, 180, 165,
        145, 120, 100, 85,  75,  68,  62,  58,  55,  52,
    ]
    bar_w, gap  = 10, 6
    total_bars  = len(bar_heights)
    start_x     = 320 - (total_bars * (bar_w + gap)) // 2

    for i, bh in enumerate(bar_heights):
        t     = i / total_bars
        color = lerp_color(WAVE1, WAVE2, abs(t - 0.5) * 2)
        alpha = 0.5 + 0.5 * (1 - abs(t - 0.5) * 2)
        color = tuple(int(c * alpha + bg * (1 - alpha))
                      for c, bg in zip(color, BG2))
        bx = start_x + i * (bar_w + gap)
        by = H // 2
        r  = bar_w // 2
        draw.rounded_rectangle([bx, by - bh // 2, bx + bar_w, by + bh // 2],
                                radius=r, fill=color)

    # ハイライトバー（中央付近を明るく）
    for i, bh in enumerate(bar_heights[15:25], start=15):
        t     = (i - 15) / 10
        color = lerp_color(WAVE1, (180, 240, 255), abs(t - 0.5) * 2)
        bx    = start_x + i * (bar_w + gap)
        r     = bar_w // 2
        draw.rounded_rectangle([bx, H//2 - bh//2, bx + bar_w, H//2 + bh//2],
                                radius=r, fill=color)

    # ── 5. 垂直区切り線 ──────────────────────────────────
    sep_x = 600
    for y in range(80, H - 80):
        t     = (y - 80) / (H - 160)
        alpha = math.sin(t * math.pi)
        c     = tuple(int(BG2[i] + alpha * (ACCENT1[i] - BG2[i]) * 0.4)
                      for i in range(3))
        draw.point((sep_x, y), fill=c)

    # ── 6. テキスト ──────────────────────────────────────
    def font(size, bold=False):
        try:
            return ImageFont.truetype(FONT, size, index=0)
        except Exception:
            return ImageFont.load_default()

    cx = (sep_x + W) // 2   # 右エリア中央

    # マイクアイコン（簡略）
    mic_r = 28
    mic_x, mic_y = cx, 120
    draw.ellipse([mic_x - mic_r, mic_y - mic_r, mic_x + mic_r, mic_y + mic_r],
                 fill=ACCENT1, outline=(180, 240, 255), width=2)
    draw.text((mic_x, mic_y), "♪", font=font(28, True),
              fill=BG1, anchor="mm")

    # タイトル
    draw.text((cx, 180), "Sound2Text", font=font(72, True),
              fill=WHITE, anchor="mm")

    # グラデーション下線
    line_w = 320
    for x in range(cx - line_w // 2, cx + line_w // 2):
        t = (x - (cx - line_w // 2)) / line_w
        c = lerp_color(ACCENT1, ACCENT2, t)
        draw.line([(x, 225), (x, 228)], fill=c)

    # サブタイトル
    draw.text((cx, 260), "实时音频转写 + AI 纠错 + 会议纪要生成",
              font=font(24), fill=GRAY, anchor="mm")
    draw.text((cx, 292), "Real-time Transcription  ·  AI Correction  ·  Meeting Summary",
              font=font(16), fill=(100, 140, 180), anchor="mm")

    # フィーチャータグ
    tags = ["多语言自动检测", "GPU 加速", "本地/云端 LLM", "一键生成纪要"]
    tag_font = font(17)
    pad_x, pad_y = 18, 8
    total_tag_w = sum(draw.textlength(t, font=tag_font) + pad_x * 2 for t in tags) + 12 * (len(tags) - 1)
    tx = cx - total_tag_w // 2

    for tag in tags:
        tw = int(draw.textlength(tag, font=tag_font)) + pad_x * 2
        th = 34
        ty = 335
        draw_rounded_rect(draw, [tx, ty, tx + tw, ty + th], r=th//2,
                          fill=PILL_BG, outline=PILL_BD, width=1)
        draw.text((tx + tw // 2, ty + th // 2), tag, font=tag_font,
                  fill=ACCENT1, anchor="mm")
        tx += tw + 12

    # テックスタック
    draw.text((cx, 405), "Powered by", font=font(15),
              fill=(80, 110, 150), anchor="mm")

    stacks = [
        ("faster-whisper", (0, 160, 200)),
        ("Groq / DeepSeek", (80, 180, 100)),
        ("CUDA GPU", (200, 120, 50)),
        ("Python", (60, 130, 200)),
    ]
    st_font  = font(16, True)
    st_pad_x = 14
    total_st = sum(int(draw.textlength(s, font=st_font)) + st_pad_x * 2
                   for s, _ in stacks) + 10 * (len(stacks) - 1)
    sx = cx - total_st // 2

    for sname, scolor in stacks:
        sw = int(draw.textlength(sname, font=st_font)) + st_pad_x * 2
        sh = 30
        sy = 425
        draw.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=6,
                                fill=(*scolor[:3], 1),
                                outline=tuple(min(255, c + 80) for c in scolor[:3]),
                                width=1)
        bg_c = tuple(int(b * 0.15 + BG2[i] * 0.85) for i, b in enumerate(scolor))
        draw.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=6, fill=bg_c)
        draw.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=6,
                                outline=tuple(min(255, c + 80) for c in scolor), width=1)
        draw.text((sx + sw // 2, sy + sh // 2), sname, font=st_font,
                  fill=tuple(min(255, c + 100) for c in scolor), anchor="mm")
        sx += sw + 10

    # Windows ロゴ風バッジ
    draw.text((cx, 480), "Windows 10 / 11  ·  Open Source",
              font=font(15), fill=(70, 100, 140), anchor="mm")

    # GitHubリンク
    draw.text((cx, 515), "github.com/newwayxp/sound2txt",
              font=font(16), fill=(60, 130, 190), anchor="mm")

    # ── 7. 左側ラベル ────────────────────────────────────
    draw.text((320, H - 90), "WASAPI Loopback", font=font(14),
              fill=(0, 140, 190), anchor="mm")
    draw.text((320, H - 65), "無損音頻捕获 · Zero Latency",
              font=font(13), fill=(60, 100, 140), anchor="mm")

    # ── 8. 角丸外枠 ──────────────────────────────────────
    overlay = Image.new("RGB", (W, H), BG1)
    mask    = Image.new("L",   (W, H), 0)
    mask_d  = ImageDraw.Draw(mask)
    mask_d.rounded_rectangle([0, 0, W, H], radius=0, fill=255)
    img.paste(overlay, mask=Image.eval(mask, lambda p: 255 - p))

    img.save(OUT, "PNG", optimize=True)
    print(f"生成完了: {OUT}")
    print(f"サイズ: {os.path.getsize(OUT):,} bytes")


if __name__ == "__main__":
    make()
