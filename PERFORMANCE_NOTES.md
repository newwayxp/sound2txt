# Sound2Text 性能诊断指南

## 问题背景

对于**1小时的长时间录制**，你可能观察到：
- 点击停止后，UI 快速响应（几秒内）
- 但文字转换到某个时间点（如16:37）后，还要等20+ 分钟才完全完成（到16:57）
- 日志显示在5点后还有输出

## 问题根源

Sound2Text 的处理流程分为三个阶段：

### 1. **实时转录阶段** (Pipeline.py)
- VAD 将音频分成 ~30 秒的片段
- 后台线程逐个转录每个片段
- 输出到 transcript 文件
- **性能指标**: 显示在日志中为 `{throughput}x realtime`（相对实时播放速度的倍数）

### 2. **转录完成检测** (Presenter.py)
- 文件监视线程等待 transcript 文件完成
- 当转录完成时立即进入下一阶段

### 3. **纠错 + 摘要生成** (Summarizer.py) ⚠️ **通常是瓶颈**
- **纠错步骤**: 对整个转录文本调用 LLM API（OpenAI/Groq/Ollama）
- **摘要步骤**: 对纠错后的文本生成会议纪要
- 对于 1 小时的文本（通常 5000+ 字），这两个步骤合计可能需要 10-30 分钟

## 性能分析日志

### 查看完整时间线

检查应用输出的关键日志行：

```
[SYS] Recording duration: 3600.0s, Transcription time: 1200.5s (0.33x realtime)
[Summarizer] Step1: correcting transcript (12000 chars, 500 lines)...
[Summarizer] API call: 3000 tokens, timeout=600s
[Summarizer] API response: 450.2s
[Summarizer] correction done -> ... (480.5s total)
[Summarizer] Step2: generating summary (lang=zh, 10500 chars)...
[Summarizer] API response: 120.3s
[Summarizer] summary done -> ... (130.2s total)
[SYS] summary pipeline completed in 620.5s
```

### 分析各阶段时间

| 阶段 | 预期耗时 | 计算方法 |
|------|---------|---------|
| 实时转录 | 记录时间 | 日志: `Recording duration` / 转录速度 |
| 纠错 | 取决于文本长度和 API | 通常 200-600 秒（对长文本） |
| 摘要 | 取决于文本长度和 API | 通常 30-180 秒 |

## 性能优化建议

### 问题 1: 转录速度慢（处理 1 小时需要 2+ 小时）

**原因**: 
- GPU 内存不足，fallback 到 CPU
- Whisper 模型选择过大
- 实时修正 API 调用过频繁

**优化方案**:
```ini
# config.ini
[recording]
model_size=tiny        # 改为 tiny/base（而不是 small/medium）
device=cuda            # 确认使用 GPU
record_sec=60          # 增加到 60 秒（减少 API 调用次数）
```

### 问题 2: 纠错 / 摘要超级慢（20+ 分钟）

**原因**:
- 使用本地 Ollama，模型太小或性能不足
- 远程 API 使用免费层有速率限制
- 网络延迟或超时重试

**优化方案**:

#### 选项 A: 使用付费 API（推荐）
```ini
[summary]
mode=openai
api_base=https://api.groq.com/openai/v1   # Groq 速度快，免费额度多
api_key=YOUR_GROQ_KEY
model=llama-3.3-70b-versatile             # 大模型，质量好
```

#### 选项 B: 本地 Ollama（需要好硬件）
```ini
[summary]
mode=ollama
ollama_model=qwen2.5:32b                  # 使用更大的本地模型
ollama_url=http://localhost:11434
```

**需要充足的 VRAM**:
- `qwen2.5:7b`: 需要 ~8GB VRAM
- `qwen2.5:32b`: 需要 ~24GB VRAM
- Llama 2 70B: 需要 ~40GB VRAM

#### 选项 C: 跳过纠错（快速模式）
```ini
[summary]
mode=skip_correction                      # 仅生成摘要，跳过纠错
```

### 问题 3: 内存泄漏（长时间运行变慢）

**症状**: 处理后期的片段变得越来越慢

**原因**:
- Pipeline.py 在队列中积累太多未清理的音频数据
- LLM API 响应被缓存导致内存增长

**检查方法**:
1. 打开"任务管理器"→ Python 进程
2. 观察内存占用是否持续增长
3. 如果超过 2GB，重启应用

## 诊断清单

录制 1 小时后，检查这些指标：

- [ ] **转录速度** >= 0.3x realtime（记录在日志中）
- [ ] **纠错耗时** < 10 分钟（对 1 小时文本）
- [ ] **摘要耗时** < 5 分钟
- [ ] **总耗时** < 30 分钟（从停止到完全完成）
- [ ] **内存占用** < 2GB（任务管理器）

## 进阶调试

### 启用详细日志

编辑 `config.ini`:
```ini
[logging]
log_level=DEBUG
ui_level=DEBUG
```

### 单独测试纠错速度

```bash
python summarizer.py --test-correction path/to/transcript.txt
```

### 分析队列堆积

在日志中查找:
```
queue depth=45  # 表示还有 45 个片段等待处理
```

如果持续 > 30，说明转录跟不上音频输入，需要优化模型。

## 相关资源

- [Faster-Whisper 优化](https://github.com/SYSTRAN/faster-whisper)
- [Groq API (免费使用)](https://console.groq.com)
- [本地 Ollama 安装](https://ollama.ai)
