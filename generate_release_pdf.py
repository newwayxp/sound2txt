"""
Release notes を PDF に変換するスクリプト
使用フォント: Microsoft YaHei（中国語・日本語・英語対応）
出力: dist/Sound2Text_v1.0.0_ReleaseNotes.pdf
"""
import os
from fpdf import FPDF

# ── フォント設定 ──────────────────────────────────────────
FONT_PATH  = r"C:\Windows\Fonts\msyh.ttc"   # 微软雅黑（中日英対応）
OUT_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dist", "Sound2Text_v1.0.0_ReleaseNotes.pdf")

# ── カラーパレット ────────────────────────────────────────
C_TITLE   = (30,  80, 160)   # 濃いブルー
C_H2      = (50, 120, 180)   # ミディアムブルー
C_H3      = (80, 120, 160)   # ライトブルー
C_TABLE_H = (44,  62,  80)   # ダークグレー（テーブルヘッダー）
C_TABLE_R = (240, 245, 250)  # ライトブルー（偶数行）
C_WHITE   = (255, 255, 255)
C_TEXT    = (30,  30,  30)
C_MUTED   = (100, 100, 100)
C_WARN_BG = (255, 248, 220)
C_WARN_BD = (200, 160,  50)


class PDF(FPDF):
    def header(self):
        self.set_font("msyh", size=8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 8, "Sound2Text v1.0.0  —  Release Notes", align="R")
        self.ln(4)
        self.set_draw_color(*C_H2)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-13)
        self.set_draw_color(*C_H2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1)
        self.set_font("msyh", size=8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 8, f"- {self.page_no()} -", align="C")


def make_pdf():
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)

    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("msyh", style="",  fname=FONT_PATH)
    pdf.add_font("msyh", style="B", fname=FONT_PATH)
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    W = pdf.w - pdf.l_margin - pdf.r_margin  # 有効幅

    # ── タイトルブロック ──────────────────────────────────
    pdf.set_fill_color(*C_TITLE)
    pdf.rect(0, 0, pdf.w, 42, style="F")
    pdf.set_y(10)
    pdf.set_font("msyh", style="B", size=26)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(0, 12, "Sound2Text", align="C", ln=True)
    pdf.set_font("msyh", size=13)
    pdf.cell(0, 8, "v1.0.0  Release Notes", align="C", ln=True)
    pdf.set_font("msyh", size=9)
    pdf.set_text_color(200, 220, 255)
    pdf.cell(0, 7, "实时音频转文字 + 自动生成会议纪要的 Windows 桌面工具", align="C", ln=True)
    pdf.ln(14)

    def h2(text):
        pdf.set_font("msyh", style="B", size=14)
        pdf.set_text_color(*C_H2)
        pdf.cell(0, 9, text, ln=True)
        pdf.set_draw_color(*C_H2)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + W, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(*C_TEXT)

    def h3(icon, text):
        pdf.ln(2)
        pdf.set_font("msyh", style="B", size=11)
        pdf.set_text_color(*C_H3)
        pdf.cell(0, 7, f"{icon}  {text}", ln=True)
        pdf.set_text_color(*C_TEXT)

    def bullet(text, indent=6):
        pdf.set_font("msyh", size=10)
        pdf.set_text_color(*C_TEXT)
        pdf.set_x(pdf.l_margin + indent)
        pdf.cell(4, 6, "•")
        pdf.multi_cell(W - indent - 4, 6, text)

    def body(text):
        pdf.set_font("msyh", size=10)
        pdf.set_text_color(*C_TEXT)
        pdf.multi_cell(W, 6, text)

    def note(text):
        """⚠ 注意ボックス"""
        pdf.ln(2)
        pdf.set_fill_color(*C_WARN_BG)
        pdf.set_draw_color(*C_WARN_BD)
        pdf.set_line_width(0.5)
        pdf.set_font("msyh", size=10)
        pdf.set_text_color(120, 80, 0)
        pdf.multi_cell(W, 7, f"⚠  {text}", border=1, fill=True)
        pdf.ln(2)

    def table(headers, rows, col_widths=None):
        if col_widths is None:
            col_widths = [W / len(headers)] * len(headers)
        # ヘッダー
        pdf.set_fill_color(*C_TABLE_H)
        pdf.set_text_color(*C_WHITE)
        pdf.set_font("msyh", style="B", size=9)
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 7, h, border=1, fill=True)
        pdf.ln()
        # 行
        pdf.set_font("msyh", size=9)
        for r_idx, row in enumerate(rows):
            fill = r_idx % 2 == 1
            pdf.set_fill_color(*C_TABLE_R)
            pdf.set_text_color(*C_TEXT)
            for i, cell in enumerate(row):
                pdf.cell(col_widths[i], 7, cell, border=1, fill=fill)
            pdf.ln()
        pdf.ln(2)

    # ── 主要功能 ─────────────────────────────────────────
    h2("✨  主要功能")

    h3("🎙", "音频录制与转写")
    bullet("通过 Windows WASAPI 捕获系统音频（耳机 / 扬声器输出）")
    bullet("自动检测音频来源设备，无需手动选择")
    bullet("使用 faster-whisper 进行本地语音识别，数据不上传外部")
    bullet("支持 GPU 加速（NVIDIA CUDA），转写速度提升约 10 倍")
    bullet("自动检测会话语言（中文 / 日语 / 英语），下次启动直接跳过检测")
    bullet("支持 vocabulary.txt 自定义术语表，提升专有名词识别准确率")

    h3("📝", "AI 纠错与会议纪要")
    bullet("调用 LLM（Groq / DeepSeek / 阿里云百炼 / Ollama）对转写文本纠错")
    bullet("自动生成结构化会议纪要，语言与录音一致，不做翻译")
    bullet("纠错文本与会议纪要分别保存，便于对比和存档")

    h3("🖥", "图形界面")
    bullet("一键开始 / 停止，自动完成录音 → 转写 → 纠错 → 生成纪要全流程")
    bullet("支持中文 / 日语 / 英语界面（跟随系统语言）")
    bullet("可视化设置：设备选择、模型大小、语言、API 配置等")

    pdf.ln(3)

    # ── 安装方法 ─────────────────────────────────────────
    h2("📦  安装方法")
    steps = [
        "下载 Sound2Text_Setup_1.0.0.exe",
        "双击运行，按向导完成安装",
        "安装过程中勾选「Python パッケージをインストール」和「ffmpeg をインストール」",
        "安装完成后，在桌面或开始菜单找到 Sound2Text 启动",
    ]
    for i, s in enumerate(steps, 1):
        pdf.set_font("msyh", size=10)
        pdf.set_text_color(*C_TITLE)
        pdf.set_x(pdf.l_margin)
        pdf.cell(7, 7, f"{i}.")
        pdf.set_text_color(*C_TEXT)
        pdf.multi_cell(W - 7, 7, s)

    note("需要提前安装 Python 3.8+，安装时请勾选 \"Add Python to PATH\"")

    # ── 初次配置 ─────────────────────────────────────────
    h2("⚙  初次配置（API Key 设置）")
    body("启动后在「⚙ 纪要/API」标签页填入 API Key：")
    pdf.ln(2)
    table(
        ["服务", "获取地址", "费用"],
        [
            ["Groq（推荐）",  "console.groq.com",              "每天 14,400 次免费"],
            ["DeepSeek",      "platform.deepseek.com",         "极低（按量计费）"],
            ["阿里云百炼",    "bailian.console.aliyun.com",    "新用户赠送 token"],
            ["Ollama（本地）","本地运行，无需 API Key",         "完全免费"],
        ],
        col_widths=[35, 80, 55],
    )

    # ── システム要件 ─────────────────────────────────────
    h2("💻  系统要求")
    table(
        ["项目", "要求"],
        [
            ["OS",     "Windows 10 / 11（64-bit）"],
            ["Python", "3.8 以上（需提前安装）"],
            ["RAM",    "4 GB 以上"],
            ["GPU",    "可选，NVIDIA CUDA 支持（有 GPU 时自动启用，速度提升约 10 倍）"],
            ["网络",   "使用云端 API 时需连接外网"],
        ],
        col_widths=[30, W - 30],
    )

    # ── 输出文件 ─────────────────────────────────────────
    h2("📁  输出文件")
    table(
        ["文件类型", "保存位置"],
        [
            ["原始转写",  r"Sound2Text\transcript\transcript_*.txt"],
            ["纠错文本",  r"Sound2Text\corrected\corrected_*.txt"],
            ["会议纪要",  r"Sound2Text\memo\summary_*.md"],
            ["音频文件",  r"Sound2Text\audio\audio_*.wav"],
        ],
        col_widths=[30, W - 30],
    )

    # ── フッター注記 ─────────────────────────────────────
    pdf.ln(4)
    pdf.set_font("msyh", size=9)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(0, 6,
             "Source: https://github.com/newwayxp/sound2txt",
             align="C", ln=True)

    pdf.output(OUT_FILE)
    print(f"PDF 生成完了: {OUT_FILE}")
    print(f"ファイルサイズ: {os.path.getsize(OUT_FILE):,} bytes")


if __name__ == "__main__":
    make_pdf()
