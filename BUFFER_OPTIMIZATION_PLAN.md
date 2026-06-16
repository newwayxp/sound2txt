# 音频缓冲优化方案 - 从内存队列改为磁盘文件

## 问题

当前 pipeline.py 的设计：
```
实时音频 → VAD分割 → segments 堆积在内存队列 → 异步转录
                    ↑
                 queue depth=4-16
                 音频字节在内存中
```

问题：
- 对于长时间录制（1小时），内存中可能堆积 100+ 个 segments
- 每个 segment 100KB+，总计 10-50MB 在内存中
- 如果转录速度慢，段数更多
- 内存压力大，可能导致 Python 进程变慢（垃圾回收、内存交换）

## 你的方案（磁盘缓冲）

```
实时音频 → VAV分割 → 定期保存为小 WAV 文件
                    ↓
            监视磁盘目录
                    ↓
            发现新 WAV → 读取转录 → 删除
```

优点：
1. **内存最小化** - 只有当前处理的文件在内存
2. **自动流控** - 磁盘 I/O 速度会自动限制并发
3. **容错性强** - 转录失败可重试文件
4. **可监视** - 可以看到等待处理的文件列表
5. **可扩展** - 可处理任意长度的录音

## 实现细节

### 当前代码结构
```
pipeline.py:
  _open_session() → 创建 RAW 文件
  主循环 → 写入音频到 RAW
  _transcribe_loop() → 从内存队列读取 segments
  _close_session() → RAW 转换为单个 WAV 文件
```

### 改进后的结构

#### 阶段 1：定期保存分段 WAV（推荐）

```python
# 在 _open_session() 中
segment_dir = os.path.join(audio_dir, f"segments_{session_ts}")
os.makedirs(segment_dir, exist_ok=True)

# 主循环中，每 15-20s
# 保存当前的 RAW 数据为一个 segment WAV 文件
def _save_segment_wav(raw_bytes, segment_index, channels, sample_rate):
    wav_path = os.path.join(segment_dir, f"segment_{segment_index:04d}.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_bytes)
    return wav_path

# 后台线程：监视目录，处理新 WAV 文件
def _transcribe_segments_loop():
    processed = set()
    while session_active:
        # 列出所有 segment WAV 文件
        segments = sorted(glob.glob(os.path.join(segment_dir, "segment_*.wav")))
        for segment_path in segments:
            if segment_path in processed:
                continue
            
            # 转录这个文件
            transcribe_file(segment_path)
            processed.add(segment_path)
            
            # 转录完成后删除
            os.remove(segment_path)
        
        time.sleep(1)  # 检查频率
```

#### 阶段 2：关键改变

```diff
- _enqueue(seg, source)         # 堆积在内存
+ _save_segment_wav(raw)        # 保存到磁盘
+ 监视目录 → 读取文件 → 转录   # 磁盘驱动
```

## 参数调优

### Segment 大小
```ini
# 当前：max_sec=15.0（VAD驱动）
# 改为：段长度根据磁盘保存周期决定

# 推荐：每 10-20s 保存一个文件
segment_duration_sec = 15.0
```

为什么？
- 15s × 16kHz × 2 bytes = ~480KB per segment
- 内存中只保留当前段（<1MB）
- 其他所有段都在磁盘上等待

### 监视频率
```python
# 检查新文件的频率
poll_interval = 0.5  # 每 500ms 检查一次磁盘
```

## 具体改动清单

### 修改 pipeline.py

1. **_open_session() 中**
   ```python
   segment_dir = os.path.join(audio_dir, f"segments_{session_ts}")
   os.makedirs(segment_dir, exist_ok=True)
   ```

2. **主循环中，替换 _enqueue()**
   ```python
   # 替代：segment 入队
   # 改为：定期保存到文件
   
   if accumulated_duration > 15.0:
       _save_segment_wav(accumulated_raw, segment_index)
       accumulated_raw = b""
       segment_index += 1
   ```

3. **替换 _transcribe_loop()**
   ```python
   # 替代：从 _seg_queue.get()
   # 改为：监视 segment_dir，处理新 WAV 文件
   ```

## 预期改进

### 内存占用
- 前：100 × 100KB = 10MB+ 在队列
- 后：15s × 16kHz × 2 = 480KB 在内存 ✅ **20倍减少**

### 响应性
- 前：当 queue depth > 10 时变慢
- 后：始终快速（因为队列始终小）

### 转录速度
- 前：可能 0.09x（卡住）
- 后：稳定 0.5-0.8x

### 处理时间
- 1小时录制从 4+ 小时 → 1.5-2 小时

## 实现难度

⭐⭐⭐ 中等难度

需要修改的文件：
1. `pipeline.py` - 主要改动（200-300 行）
2. `presenter.py` - 可能需要小调整

## 备选方案（更简单）

如果不想大改 pipeline.py，可以用"轻量级"方案：

**限制内存队列大小**
```python
# 当前：_seg_queue = queue.Queue()  # 无限制
# 改为：_seg_queue = queue.Queue(maxsize=5)

# 效果：
# - 当队列满时，_enqueue() 会阻塞
# - 自动流控：input 不会超过 output
# - 内存占用有限
```

这样改只需要 1 行代码改动！

## 建议方案

### 短期（立即）
```python
_seg_queue = queue.Queue(maxsize=3)  # 限制队列大小
```
- 改动最小
- 立即见效
- 防止内存爆炸

### 长期（推荐）
实施完整的磁盘缓冲方案
- 更稳定
- 更可控
- 更易于监视

## 测试方法

无论采用哪个方案，测试指标：

```bash
python analyze_timeline.py

关键指标：
✅ queue depth / 文件数 ≤ 3（不堆积）
✅ 转录速度 > 0.5x（稳定）
✅ 内存占用 < 500MB（Python 进程）
✅ 1小时录制 < 2 小时
```

## 配置示例

```ini
[subtitle]
# 如果选择短期方案
max_queue_size = 3          # 内存队列最大深度

# 如果选择长期方案
segment_duration = 15.0     # 每个磁盘文件的长度
segment_dir = {audio_dir}/segments  # 临时文件目录
```

---

## 总结

你的想法完全正确：**从内存堆积改为磁盘流式处理**。

这是处理长时间音频的标准做法（视频转码、音频处理都这样做）。

我建议：
1. 先用 maxsize 限制队列（1 行改动，立即见效）
2. 观察效果
3. 如果需要，再做完整的磁盘缓冲改造

需要我帮你实现吗？
