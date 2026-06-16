# CPU 优化指南 - Sound2Text

## 你的情况

- ❌ **没有 GPU**（或 GPU 不可用）
- ⚠️ **队列堆积问题** - 转录速度跟不上输入速度
- 原配置：`device=cpu, model_size=medium, max_sec=20.0`
- 新配置：`device=cpu, model_size=tiny, max_sec=15.0`

## 预期改进

| 指标 | 旧配置 | 新配置 | 改善 |
|------|--------|--------|------|
| 20s 音频处理时间 | 40-60s | 5-10s | **4-8倍** |
| 队列堆积 | 严重（depth=4+） | 轻微 | ✅ |
| 1小时录制总耗时 | 3-4小时 | 1.5小时 | **50% 更快** |
| 转录精度 | 高 | 中等 | 权衡 |

## 配置选项（从快到慢）

### ⚡ 快速模式（推荐）
```ini
[recording]
model_size = tiny

[subtitle]
max_sec = 15.0
silence_sec = 2.5
```
**优点：** 速度快，队列不堆积  
**缺点：** 转录精度较低（1-2% 错误率）  
**适合：** 会议摘要、实时转录

### ⚖️ 平衡模式
```ini
[recording]
model_size = base

[subtitle]
max_sec = 15.0
silence_sec = 2.0
```
**优点：** 速度和精度均衡  
**缺点：** 1小时录制仍需 2-2.5 小时  
**适合：** 精度和速度都重要的情况

### 🎯 精度优先模式
```ini
[recording]
model_size = small

[subtitle]
max_sec = 30.0
silence_sec = 2.0
```
**优点：** 精度高（推荐用于重要文档）  
**缺点：** 队列可能堆积，1小时需要 2-3 小时  
**适合：** 重要会议记录

## 关键参数说明

### `model_size`
```
tiny    → 39 MB  → 最快（1-2s/20s音频）但精度低
base    → 140 MB → 较快（3-5s/20s音频）精度中等
small   → 244 MB → 标准（8-12s/20s音频）精度较高
medium  → 769 MB → 较慢（40-60s/20s音频 on CPU）
```

### `max_sec` - 强制刷新间隔
```
15.0  → 每15秒产生一个segment（推荐CPU用）
20.0  → 每20秒产生一个segment（原设置）
30.0  → 每30秒产生一个segment（可能堆积）
```

更小的值 = 更小的 segment = 更快的个体处理，但总overhead增加

### `silence_sec` - 停顿检测
```
2.0  → 检测2秒的停顿即结束当前句子（标准）
2.5  → 检测2.5秒停顿（给CPU更多处理时间）
3.0  → 检测3秒停顿（最长的自然停顿）
```

## 性能监测

### 查看队列深度
在日志中查找：
```
[TR] Transcribing 20.0s system audio (queue depth=4)
```

- `queue depth=0` ✅ 完美（转录速度 > 输入速度）
- `queue depth=1-2` ✅ 良好（略有滞后但可接受）
- `queue depth=3+` ⚠️ 堵塞（需要优化）

### 计算实时倍速
日志显示：
```
[TR] Transcribed in 7.2s (2.78x speed)
```

这表示 20s 的音频用了 7.2s 转录 = 2.78x 实时速度（好！）

目标：**至少 1.0x 实时速度**（不堆积）

## 优化技巧

### 1. 关闭不必要的功能
```ini
[recording]
enable_mic = false          # 如果不需要麦克风，关闭可省20%CPU
```

### 2. 减少后处理并发
```ini
[summary]
mode = skip                 # 完全跳过纠错/摘要（仅测试速度用）
```

### 3. 减少日志输出
```ini
[logging]
log_level = INFO            # 改为INFO（DEBUG会增加10%开销）
ui_level = INFO
```

### 4. 检查系统资源

**查看CPU占用（任务管理器）：**
- python.exe 应该用 > 80% 的 CPU
- 如果低于 50%，说明有其他瓶颈（I/O、网络等）

**查看内存占用：**
- tiny 模型：200-300 MB
- base 模型：400-500 MB
- small 模型：600-800 MB
- 如果超过 2GB，可能内存泄漏

### 5. 关闭其他后台进程
- 杀死浏览器（可省 500MB+ 内存）
- 关闭 IDE（VS Code 用 200MB+）
- 停止 antivirus 扫描

## 故障排除

### 问题：改了 tiny 模型后仍然堆积

**原因：** 可能有其他瓶颈
1. 检查磁盘 I/O（音频文件读写）
2. 检查网络（代理延迟）
3. 检查其他进程占用 CPU

**解决：**
```bash
# 查看进程 CPU 占用
tasklist /v | find "python"

# 杀死无关进程
taskkill /IM chrome.exe /F    # 浏览器
taskkill /IM Code.exe /F      # VS Code
```

### 问题：转录精度太低（太多错别字）

**改用 base 或 small：**
```ini
[recording]
model_size = base
```

然后耐心等待（1小时录制 ≈ 2 小时处理）

### 问题：内存占用不断增长

这可能是内存泄漏。试试：
```ini
[logging]
log_level = WARN        # 降低日志级别
```

## 最终建议

根据你的优先级选择：

1. **优先速度**（急着要结果）
   ```ini
   model_size = tiny
   max_sec = 15.0
   ```

2. **优先精度**（不在乎耗时）
   ```ini
   model_size = small
   max_sec = 30.0
   silence_sec = 2.0
   ```

3. **平衡**（推荐）
   ```ini
   model_size = base
   max_sec = 15.0
   silence_sec = 2.0
   ```

## 监测进度

下次运行 1 小时录制后，运行：
```bash
python analyze_timeline.py
```

看看实际耗时是否改善。

---

**注：** 所有模型都是自动下载到 `~/.cache/huggingface/hub/` 的，首次运行会慢，之后会用缓存版本。
