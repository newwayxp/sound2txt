# 历史记录 回放 + 人工订正 功能规格（Codex 实施单）

> 本文是给 Codex 的实施规格，**先不要写代码**，仅作为开发依据。
> 目标：为历史记录增加「回放 + 人工订正」功能；人工订正的内容自动回写到词典和修正清单，下次录音即生效。

---

## 1. 目标（Why）

- 录音结束后，用户可以回到任意一次历史记录，**边播放原始 mp3、边对照纠错文本**。
- 播放时按时间**自动高亮/放大**当前正在播放的那一段文字。
- 允许**暂停后直接编辑**当前显示的文本。
- 保存后：编辑结果**覆盖**该次记录的纠错版本（`corrected_*` / `final_corrected_*`），并把用户做的订正**同步进词典（`vocabulary.txt`）和修正清单（`glossary.txt`，`誤 => 正`）**，使后续录音自动应用。

---

## 2. 现有架构中的关键落点（已确认）

| 关注点 | 位置 |
|---|---|
| GUI 主窗口 | [ui_qt_design.py](../ui_qt_design.py) 中 `class App(QMainWindow)`，由 [run_gui.py](../run_gui.py) 启动 |
| 左侧栏（含音量条 VU meter） | `App._build_left_rail()` [ui_qt_design.py:1105](../ui_qt_design.py#L1105)；音量条是 `self._vumeter_bar`（[ui_qt_design.py:1135](../ui_qt_design.py#L1135)），其下方是 `self._dashboard` |
| 业务逻辑 | [presenter.py](../presenter.py)（MVP Presenter） |
| 词典（专有名词） | `vocabulary.txt`，由 [summarizer.py](../summarizer.py) `_resolve_vocab_file()` / `_load_vocabulary()` 读取 |
| 修正清单（誤=>正 确定性替换） | [glossary.py](../glossary.py)：`load_glossary()` / `apply_glossary()`；文件路径由 `resolve_glossary_file(cfg)` 解析（config `[paths] glossary_file`，默认 `glossary.txt`） |
| 输出目录配置 | `[paths] transcript_dir` / `[paths] audio_dir` / `[summary] corrected_dir` / `[summary] summary_dir` / `[summary] final_corrected_dir`（见 [pipeline.py:719](../pipeline.py#L719) 与 [summarizer.py:763](../summarizer.py#L763)） |

---

## 3. 数据与文件约定（一次会话 = 一个 session_ts）

同一次录音的所有产物共用同一个时间戳 `ts = YYYYMMDD_HHMMSS`（在 [pipeline.py:730](../pipeline.py#L730) 生成为 `session_ts`）：

| 产物 | 路径 | 编码 / 格式 |
|---|---|---|
| 原始转写 | `{transcript_dir}/transcript_{ts}.txt` | utf-8-sig；每行 `[HH:MM:SS] 文本` |
| 实时纠错 | `{corrected_dir}/corrected_{ts}.txt` | utf-8-sig；每行 `[HH:MM:SS] 文本` |
| 最终纠错（可选） | `{final_corrected_dir}/final_corrected_{ts}.txt` | 启用 online refine 时才有 |
| 会议纪要 | `{summary_dir}/summary_{ts}.md` | Markdown，首行 `# 议事录/会议纪要`，含 **主题/テーマ/Topic** 字段 |
| 录音 | `{audio_dir}/audio_{ts}.mp3` | mp3（`[recording] audio_format`） |

**回放时间映射（核心）**：转写/纠错行的 `[HH:MM:SS]` 是**墙上时钟**；mp3 从录音开始播放。
- 录音开始时刻 = `session_ts` 解析出的 `HH:MM:SS`（即文件名时间戳）。
- 某一行在 mp3 中的播放位置（秒）= `该行HH:MM:SS - session_ts的HH:MM:SS`（跨午夜时 +24h 处理）。
- 该行的“结束位置” = 下一行的播放位置（最后一行用音频总时长）。
- 不要依赖 `.recording_start` 文件（它只保存“最后一次”，会被覆盖）；以文件名 `ts` 为准更可靠。

---

## 4. 功能需求（逐条对应原始需求）

### F1. 左栏新增「历史记录」按钮
- 位置：左侧栏音量条 `self._vumeter_bar` **下方**（[ui_qt_design.py:1148](../ui_qt_design.py#L1148) 之后、`self._dashboard` 之前或之后均可，按视觉取舍）。
- 文案多语言：`{zh:"历史记录", ja:"履歴", en:"History"}`，走现有 i18n（[i18n.py](../i18n.py)）。
- 点击 → 进入「历史列表」画面（见 F2）。用 `QStackedWidget` 在主区切换，主录音界面不销毁，便于返回。

### F2. 历史列表画面
- 顶部有「← 返回」按钮，点击回到主录音界面。
- 数据来源：扫描配置目录，列出每次记录。**以 `summary_dir/summary_*.md`（或 corrected_*）为索引枚举 session_ts**，再按需关联同 ts 的 mp3/corrected。
- 每行展示：
  - **时间**：由 `ts` 格式化为 `YYYY-MM-DD HH:MM`。
  - **题目（要约标题）**：取 `summary_{ts}.md` 中的「主题/テーマ/Topic」字段值；取不到时回退为纪要首个非空标题行或纠错文本首句。
- 按时间倒序排列；缺 mp3 或缺 corrected 的条目要么标灰、要么标注（不可回放/不可订正）。
- 点击某条 → 进入「详情/回放」画面（F3）。

### F3. 记录详情 / 回放画面
- 播放该次 `audio_{ts}.mp3`（用 `PyQt6.QtMultimedia.QMediaPlayer + QAudioOutput`；需在 requirements/文档说明依赖）。
- 文本区显示纠错文本，**优先 `final_corrected_{ts}.txt`，否则 `corrected_{ts}.txt`**，按行解析为 `(start_sec, text)` 段落列表。
- 播放过程中按当前播放位置**自动高亮/放大当前段**（用 F3 的时间映射；`QMediaPlayer.positionChanged` 驱动），并自动滚动到该段。

### F4. 暂停 + 编辑
- 提供「⏸ 暂停 / ▶ 播放」按钮。
- 暂停后，当前段（或整篇文本）变为**可编辑**（`QTextEdit` 可写）。播放中为只读，避免冲突。

### F5. 保存（覆盖纠错版本 + 同步词库）
- 「保存」按钮：
  1. 将编辑后的文本**覆盖写回**当前所用纠错文件（`final_corrected_{ts}.txt` 优先，否则 `corrected_{ts}.txt`），保持 **utf-8-sig** 与 `[HH:MM:SS] 文本` 行格式。
  2. **同步词库/修正清单**（见第 5 节算法）：把本次订正抽取成 `誤 => 正` 追加到 `glossary.txt`；把新出现的专有名词追加到 `vocabulary.txt`。
- 保存需做**原子写**（先写临时文件再替换）并保留一次 `.bak` 备份，防止编辑误覆盖。

### F6. 结束 / 返回
- 详情画面提供「结束」按钮，回到 F2 历史列表。
- 列表的「← 返回」回到主录音界面。

---

## 5. 订正 → 词库/修正清单 的同步算法

目标：把“用户把 A 改成了 B”沉淀为下次自动生效的规则，**避免污染**（不要把整句塞进 glossary）。

**逐行比对（仅对内容文本，忽略 `[HH:MM:SS]` 前缀）**：
1. 取保存前的纠错文本 `old_lines` 与编辑后的 `new_lines`，按时间戳对齐（同一时间戳为一对）。
2. 对每对发生变化的行，做**词级 diff**（中文/日文可用 difflib 的字符级或 jieba/分词；英文按空白分词）。
3. 从 diff 抽取“替换”片段 `(wrong, right)`：
   - 过滤：`wrong`/`right` 都非空、不相等、长度有上限（如 ≤ 20 字符）、不是纯标点/纯空白。
   - 候选作为 `wrong => right` 追加到 `glossary.txt`（去重，已存在则跳过）。
   - 若 `right` 看起来是一个专有名词（首字母大写的英文词、片假名词、或用户标记），追加到 `vocabulary.txt`。
4. **建议加一步用户确认**：保存时弹出“将学习以下订正规则”的清单，允许用户勾选/取消，避免把一次性口误写成永久规则（开放项，见第 8 节）。
5. 写 glossary 复用 [glossary.py](../glossary.py) 既有格式（`誤 => 正`，`#` 注释、按左侧长度排序由 `load_glossary` 处理）。建议在 `glossary.py` 新增 `append_glossary_rules(path, pairs)` 与去重逻辑；在 summarizer 侧新增 `append_vocabulary(path, terms)`。

---

## 6. 建议的代码改动清单

| 文件 | 改动 |
|---|---|
| `ui_qt_design.py` | 左栏加「历史记录」按钮；主区改为 `QStackedWidget`；新增 `HistoryListWidget`、`HistoryDetailWidget`（或拆到新文件 `history_view.py`） |
| `history_view.py`（**新增**） | 历史列表与回放/编辑 UI；`QMediaPlayer` 播放与 `positionChanged` 高亮逻辑 |
| `history_store.py`（**新增**） | 纯逻辑层：扫描目录枚举 session、解析纪要标题、解析 `[HH:MM:SS]` → `start_sec`、原子写回纠错文件、生成 `.bak` |
| `glossary.py` | 新增 `append_glossary_rules(path, pairs)`（去重、保留注释、追加写） |
| `summarizer.py` | 新增 `append_vocabulary(vocab_file, terms)`（去重追加） |
| `presenter.py` | 暴露历史目录解析 + 触发同步词库的方法，连接 UI 与 store |
| `i18n.py` | 新增 `history` / `back` / `save` / `pause` / `finish` 等文案键（zh/ja/en） |
| `requirements.txt` / README | 声明 `PyQt6.QtMultimedia`（Qt 多媒体）依赖与系统编解码器要求 |

---

## 7. 验收标准

- [ ] 左栏音量条下方出现「历史记录」按钮，点击进入列表，列表「← 返回」能回到录音界面且录音状态不受影响。
- [ ] 列表按时间倒序列出每次记录，显示 `YYYY-MM-DD HH:MM` 与纪要主题标题。
- [ ] 点开记录能播放对应 mp3；播放时当前段落自动高亮/放大并自动滚动；时间映射误差在 ±1s 内。
- [ ] 暂停后可编辑文本，播放中为只读。
- [ ] 保存后纠错文件被正确覆盖（utf-8-sig、行格式不变），存在 `.bak` 备份。
- [ ] 保存后 `glossary.txt` 新增对应 `誤 => 正` 规则、`vocabulary.txt` 新增专有名词；**重新开始一次新录音，相同误识别被自动修正**。
- [ ] 「结束」回到列表；缺 mp3/缺纠错文件的记录有清晰的禁用/提示。

---

## 8. 已确认决策（实施时按此执行）

1. **保存确认弹窗：要。** 第 5.4 节为正式需求，不是可选项。保存时弹出「将学习以下订正规则」清单，逐条可勾选/取消，用户确认后才写入 `glossary.txt` / `vocabulary.txt`，避免一次性口误被沉淀为永久规则。
2. **同步目标：仅覆盖纠错文件，纪要不自动重算。** 保存只覆盖 `final_corrected_*`/`corrected_*` 并同步 `glossary.txt`+`vocabulary.txt`；**不**自动回写 `transcript_*`，**不**自动重新生成 `summary_*`。在详情画面另留一个「重新生成纪要」按钮（调用现有 [summarizer.py](../summarizer.py) `run_step("summary", ...)`）供用户手动触发，作为后续增量，可不在首版实现。
3. **音频后缀兼容：不写死 `.mp3`。** 回放按实际存在的文件解析后缀（依据 `[recording] audio_format`，可能为 wav）。列表枚举与详情加载都以 `audio_{ts}.*` 的实际文件为准。

### 仍按建议默认执行（无需再确认）

- **段落粒度**：按转写行（每个 `[HH:MM:SS]` 一段）作为高亮/编辑单位，首版即可。
- **跨语言分词**：词级 diff 先用 difflib 字符级起步，专有名词识别后续增强。
- **音频编解码**：Windows 上 `QMediaPlayer` 播放依赖系统解码器，需在不同机器验证（属测试关注点，非阻塞）。
