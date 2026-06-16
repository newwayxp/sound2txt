# 问题根本原因分析 - 转录速度急剧下降

## 你的问题现象

- 18:01:07 时，转录速度从 0.6x 突然掉到 0.09x
- 一个 20s segment 需要 218 秒（不是 5-10 秒）
- 队列堆积 depth=14，最后 depth=16
- UI 显示"完成"，但后台转录仍在继续

## 根本原因（从日志找到）

### 问题链：

```
17:56:00  开始转录，同时尝试实时纠错
17:56:00  纠错API调用开始返回429错误：
          "429 Client Error: Too Many Requests"
          或代理超时：
          "Connection to tkyproxy-std.intra.tis.co.jp timed out"

17:56:00-18:01:07  
          • Pipeline同时运行：转录 + 实时纠错
          • 纠错API不断失败和重试
          • CPU被占用处理失败的API调用
          • 转录速度逐渐下降：0.6x → 0.5x → 0.1x

18:01:07  
          转录完全卡住：218秒处理1个segment
          队列堆积到depth=14

18:02:01  
          用户点击停止

18:02:05  
          摘要生成也因同样的API问题失败（429错误）
          但程序仍调用 _set_controls_idle()
          UI显示"完成"

18:02:06+  
          后台转录仍在继续，queue depth=16
```

## 为什么会这样

### 1. **实时纠错设计**
Pipeline.py在转录每个segment时，立即调用LLM API进行纠错：

```python
# 每个segment转录完成后
corrected_text = _correct_segment(original, session_lang, cfg)
```

这在API正常工作时很好，但当API失败时：
- 程序重试，占用CPU
- 网络连接被阻塞（等待API响应超时）
- 后续的转录被延迟

### 2. **代理超时问题**
你的config.ini配置了代理：
```ini
[network]
https_proxy = http://tkyproxy-std.intra.tis.co.jp:8080
```

此代理在你的环境中可能不稳定，导致：
- API调用超时（connect timeout=20s）
- 每个失败的纠错尝试都要等20秒
- CPU被占用处理超时

### 3. **错误处理不当**
即使API完全失败，程序也：
- 继续尝试重试（浪费时间）
- 不从失败中恢复（降级）
- 最后仍然调用 `_set_controls_idle()`，显示"完成"

## 我的修复

### 已完成：
```ini
[summary]
enable_correction = false  # 禁用实时纠错
```

这样：
- 转录不再被纠错API阻挡
- 转录速度应恢复到 0.6x+
- 队列不会堆积

### 为什么这样做：
实时纠错是一个"nice-to-have"功能，但当API不稳定时，它反而拖累整个系统。禁用它可以让转录专注于其核心任务。

## 后续步骤

### 1. **测试转录速度**（必做）
```bash
# 录制5分钟，看queue depth和速度
python analyze_timeline.py

# 查看：
# - queue depth 是否保持 ≤ 1
# - 转录速度是否 > 0.5x（不再卡住）
```

### 2. **修复 API 问题**（可选）

#### 选项 A：改用其他 API（推荐）
```ini
[summary]
mode = openai
api_base = https://api.openai.com/v1
api_key = sk_YOUR_KEY
```

或使用本地 Ollama（无API限制）

#### 选项 B：调整代理设置
```ini
[network]
https_proxy = 
http_proxy = 
ssl_verify = true
```

完全移除代理，看是否有帮助（如果你的网络允许）

#### 选项 C：增加重试延迟
在 pipeline.py 中添加延迟（但这会减慢纠错）

### 3. **重新启用纠错**（可选）
一旦API稳定，重新启用：
```ini
[summary]
enable_correction = true
```

但建议改为"两步法"：
1. 先完成所有转录（不纠错）
2. 转录完成后，再进行纠错和摘要

这样更稳定，不会互相阻挡。

## 为什么我的前面修复"没用"

我之前的修复主要是UI状态的修改，但没有解决性能根本问题。

真正的瓶颈是：
1. **不是 tiny 模型太慢**（虽然有点慢，但 0.6x 可以接受）
2. **不是 max_sec 参数**（虽然 15.0 是好的）
3. **是实时纠错导致的 API 阻塞**（这是 218s 卡机的真凶）

## 验证修复

运行一次完整的 1 小时录制：

### 预期结果：
```
✅ queue depth 保持 ≤ 1（不堆积）
✅ 转录速度 0.5-0.8x（不再 0.09x）
✅ 1小时录制耗时 1.5-2 小时（不再 4+ 小时）
✅ UI 在正确的时间显示完成
```

### 如果仍有问题：
1. 检查网络（代理超时）
2. 检查 Groq API 配额是否用尽
3. 考虑改用其他 API 或本地 Ollama

## 配置总结

你现在的最优配置：
```ini
[recording]
device = cpu
model_size = tiny      # 快速（CPU）
record_sec = 30

[subtitle]
max_sec = 15.0         # 小segment
silence_sec = 2.0

[summary]
enable_correction = false  # 禁用实时纠错（核心修复）
mode = openai
```

这样可以让转录快速完成，然后再处理摘要。

---

**下次请运行 analyze_timeline.py 并分享输出，这样我可以验证修复是否有效。**
