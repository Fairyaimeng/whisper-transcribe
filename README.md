---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: bde401623e472eee5c830b5e8e83016f_66d14b9fde6b11f1bc42525400638852
    ReservedCode1: 03oikZDn8Ep324U702kQmxnCPve9EwfhkJWsJeGOifqIqQXC74KtL/eSKHrNGARkDWrsL39OrtKQfQIw+rNvCUfUJub1jo/IHjTB6P0xVnMJ1P5RNQVpnC3t42sBpCuLQY+sY0+CTJoXKNddLFnVxT6cEr9CsLQkxHAFILMXyHCBV4hR/4FGXX1SmKs=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: bde401623e472eee5c830b5e8e83016f_66d14b9fde6b11f1bc42525400638852
    ReservedCode2: 03oikZDn8Ep324U702kQmxnCPve9EwfhkJWsJeGOifqIqQXC74KtL/eSKHrNGARkDWrsL39OrtKQfQIw+rNvCUfUJub1jo/IHjTB6P0xVnMJ1P5RNQVpnC3t42sBpCuLQY+sY0+CTJoXKNddLFnVxT6cEr9CsLQkxHAFILMXyHCBV4hR/4FGXX1SmKs=
---







# whisper-transcribe

本地离线批量视频转文字工具。基于 whisper.cpp（Metal GPU 加速）+ ffmpeg，把课程视频批量转成 `.txt` 纯文本与 `.srt` 字幕，全程不联网、不上传，适合课程学习资料转写归档。

- 模型：`large-v3-turbo` q5_0 量化（547 MB），中文识别效果好、速度约为实时的 16 倍
- 引擎：`whisper.cpp` 自编译 Metal 版，Apple Silicon 上走 GPU；异常时可一键降级到 CPU
- 批处理：整目录一次跑完，支持断点续传、失败重试、逐条日志与汇总表

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `bin/whisper-cli` | whisper.cpp 自编译可执行文件（Metal GPU 版） |
| `bin/*.dylib` | whisper.cpp 运行时依赖库（ggml / metal / cpu / parakeet 等） |
| `bin/ffmpeg` | 静态版 ffmpeg 9.0，仅用于从视频抽取 16 kHz 单声道 wav |
| `models/ggml-large-v3-turbo-q5_0.bin` | large-v3-turbo 的 q5_0 量化模型（547 MB） |
| `scripts/transcribe_batch.py` | 批量转写脚本（仅依赖 Python 标准库） |
| `out/` | 转写产出根目录，按视频名建子目录 |
| `logs/` | 每次运行一个 `run_<时间戳>.log` |

## 快速开始

工作目录为项目根目录（下例用 `~/Documents/whisper-transcribe` 表示克隆位置，请按自己的实际路径替换）。

```bash
# 1) 用内置默认参数跑（默认输入为 day01 视频目录，输出到 out/）
python3 scripts/transcribe_batch.py

# 2) 指定输入目录与输出目录
python3 scripts/transcribe_batch.py \
  --input "$HOME/Documents/AI就业课程/day02. Python-流程控制/视频" \
  --output out/day02

# 3) 强制重转全部视频，并在 GPU 异常时降级到 CPU
python3 scripts/transcribe_batch.py --input "<视频目录>" --output out/dayNN --force --cpu
```

脚本只扫描 `--input` 下的**一层**视频文件（`.mp4/.mov/.mkv/.avi/.m4v`），不递归子目录。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--input` | `$HOME/Documents/AI就业课程/day01. Python核心语法-数据存储与运算/视频` | 视频所在目录，只扫一层 |
| `--output` | `<项目>/out` | 产出根目录 |
| `--model` | `models/ggml-large-v3-turbo-q5_0.bin` | whisper 模型路径 |
| `--ffmpeg` | `bin/ffmpeg` | ffmpeg 可执行文件路径 |
| `--whisper` | `bin/whisper-cli` | whisper-cli 可执行文件路径 |
| `--threads` | `8` | 推理线程数 |
| `-P`, `--prompt` | 中文课程领域词表 | 传给 whisper-cli 的 `--prompt`，引导术语识别；传 `""` 则不启用 |
| `--cpu` | 关闭 | 追加 `-ng` 禁用 Metal GPU，用于 GPU 后端异常时降级 |
| `--force` | 关闭 | 忽略断点续传，强制重转 |
| `-h`, `--help` | — | 查看完整帮助 |

默认提示词：`以下是普通话授课内容，涉及 Python 编程、解释器、开发环境、PyCharm、变量、数据类型、字符串、注释、标识符、关键字、运算符、输入输出、项目目录结构。`

## 输出结构

```
out/
├── .wav_tmp/                      # 临时 wav（单个视频转写完成后即删除）
└── <视频名>/
    ├── <视频名>.txt               # 纯文本（含标点，无时间轴）
    └── <视频名>.srt               # 字幕（带时间轴）
```

运行结束时打印汇总表：视频名 / 状态（成功、跳过、失败）/ 时长 / 字数 / 输出目录，并统计成功、跳过、失败数量。

退出码：`0` 表示全部成功或跳过；`1` 表示至少一个视频失败。

## 性能参考

| 场景 | 耗时 | 备注 |
| --- | --- | --- |
| 11 分钟视频（GPU） | 42 秒 | 约 16 倍实时 |
| 1 分 43 秒视频（GPU） | 7.2 秒 | 输出 538 字 |
| 1 分 43 秒视频（CPU） | 24.5 秒 | `--cpu` 降级模式 |
| day01 全部 23 个视频（约 4 小时音频） | 17.8 分钟 | 模型加载开销分摊后可忽略 |

耗时主要由音频总时长决定，模型加载约 1–2 秒。

## 常见问题

**1. GPU 后端断言失败怎么办？**

报错形如 `GGML_ASSERT([rsets->data count] == 0) failed`、`error: failed to initialize whisper context`，说明 Metal 后端在当前环境不稳定。加 `--cpu` 降级到 CPU 推理，速度约为 GPU 的四分之一，但结果一致。

```bash
python3 scripts/transcribe_batch.py --input "<视频目录>" --output out/dayNN --cpu
```

若加 `--cpu` 后仍报 `failed to initialize whisper context`，优先怀疑**模型文件残缺**（见第 5 条）。

**2. 换 small 模型省内存或提速度**

下载对应量化模型放到 `models/`，用 `--model` 指定即可，无需改脚本：

```bash
python3 scripts/transcribe_batch.py --input "<视频目录>" --model models/ggml-small-q5_1.bin
```

small 系列内存占用更低、速度更快，但中文术语与长句的准确率明显低于 turbo。

**3. 断点续传机制**

判定条件：`<output>/<视频名>/<视频名>.txt` 已存在且大小大于 0 就跳过该视频，日志标记 `[跳过]`。中断后重跑同一命令即可从断点继续。确需全部重转时加 `--force`。

**4. 如何批量处理多个 day 目录**

脚本一次只处理一个 `--input` 目录，用 shell 循环逐个调用（每个 day 输出到独立子目录，互不覆盖）：

```bash
cd "$HOME/Documents/whisper-transcribe"
for d in "$HOME/Documents/AI就业课程"/day*/视频; do
  day=$(basename "$(dirname "$d")" | cut -d. -f1)     # 取 day01 这样的前缀
  python3 scripts/transcribe_batch.py --input "$d" --output "out/$day"
done
```

**5. 模型文件是否完整**

残损模型也能通过初步加载检查，但会在推理阶段报 `failed to initialize whisper context`。`large-v3-turbo-q5_0` 完整大小约 **547 MB**，明显偏小即为下载中断，需重新下载。

**6. 日志在哪里看**

每次运行在 `logs/` 下生成 `run_<YYYYMMDD_HHMMSS>.log`，同时输出到终端。日志开头记录实际生效的输入/输出/模型路径、线程数、CPU 降级状态与提示词，便于排查参数是否按预期生效。

## 磁盘占用

| 组成 | 体积 |
| --- | --- |
| 模型 `models/ggml-large-v3-turbo-q5_0.bin` | 547 MB |
| 可执行文件与依赖 `bin/` | 61 MB |
| 产出 `out/` | 纯文本与字幕，单个视频约几十 KB，可忽略 |
| 临时 wav `out/.wav_tmp/` | 运行中约 1 分钟音频 2 MB（16 kHz 单声道），转写完成即删除 |

合计固定占用约 610 MB。如需节省空间，可换成 small 系列模型（体积约为 turbo 的三分之一）。
*（内容由AI生成，仅供参考）*

---

## Finder 触发 + 终端观察（当前方案）

已放弃网页界面，改用「C 触发 + A 观察」组合：

| 组件 | 文件 | 作用 |
|---|---|---|
| C 触发 | `~/Library/Services/Whisper Transcribe.workflow` | 右键选中视频 → 服务里点「转文字（whisper）」 |
| C 触发（文件夹） | `~/Library/Services/Whisper Transcribe Folder.workflow` | 右键选中文件夹 → 递归收集其中视频 |
| A 观察 | `tui.py` | 全屏终端面板：进度条 + 任务表 + 实时日志 |
| 联机脚本 | `quick/quick_transcribe.sh` | 收集选中视频、建临时软链、开 Terminal 跑 TUI |
| 安装脚本 | `quick/install_service.sh install / uninstall` | 生成或移除两个快速操作 |

### ⚠️ 实测结论：Automator「服务/快速操作」在 macOS 27.2 上失效

`~/Library/Services` 下的 Automator workflow（`Whisper Transcribe.workflow` / `Whisper Transcribe Folder.workflow`）已生成且 plist 校验通过，也能被 `automator` 命令行执行，但**系统服务登记表 `pbs → NSServicesStatus` 里完全没有收录**——连本机早已存在的三个 Claude 服务同样没有登记。说明该机制在 macOS 27.2 已不再扫描第三方 workflow，右键「服务」里不会出现条目。

因此实际可用的入口改为 **Finder「打开方式」**：`~/Applications/转文字.app`（AppleScript droplet）。

该 app 不直接读取用户文件（避免被 TCC 拦截「文稿」目录），只把路径写进 `/tmp` 的 `.command` 再交给 Terminal 执行，文件访问权限由 Terminal 承担。

### 用法

**方式一（推荐）**：在 Finder 里选中视频（或整个文件夹）→ 右键 → **打开方式** → **转文字**。

**方式二**：把 `~/Applications/转文字.app` 拖到 Dock，之后把视频或文件夹直接拖到它的图标上。

**方式三**：命令行 `open -a "转文字" <视频路径>`
自动弹出 Terminal 窗口，全屏显示：

- 进度条 + 已完成 N/M + 已用时间 + 预计剩余
- 每个视频一行状态（○ 等待 / ▶ 进行中 / ✓ 成功 / – 跳过 / ✗ 失败）+ 字数 + 耗时
- 底部 8 行实时日志；`q` 退出、`s` 停止、`↑↓` 滚动列表

结果写到 `out/quick_<时间戳>/<视频名>/<视频名>.txt` 与 `.srt`。

### 命令行直接跑 TUI

```bash
cd "$HOME/Documents/whisper-transcribe"
python3 tui.py --input "$HOME/Documents/AI就业课程/day03. Python核心语法-数据容器/视频"
```

### 右键菜单里找不到时的处理

1. 系统设置 → 键盘 → 键盘快捷键 → 服务 → 文件与文件夹，勾选「转文字（whisper）」
2. 仍不显示则执行 `killall Finder` 让 Finder 重新扫描服务
3. 重装：`zsh quick/install_service.sh install`

### 已知注意点

- `quick/*.sh` 必须保持可执行位（`chmod 755`），Automator 沙盒会直接拒绝执行无 x 位的脚本；`install_service.sh install` 会自动补。
- 服务包名使用 ASCII（`Whisper Transcribe.workflow`），全角括号会导致 `automator` 命令行无法解析；菜单显示名仍是中文。
*（内容由AI生成，仅供参考）*

---

## v2 改造（2026-11-11）

### 1. 输出目录固定化

`v1` 每次触发都新建 `out/quick_<时间戳>/`，同一批视频重复转就会堆出无数目录。

`v2` 改为固定目录：`out/quick/<视频名>/<视频名>.{txt,srt}`。
断点续传判定不变（`.txt` 存在且非空即跳过），因此**同一个视频永远只转一次**，重跑自动跳过；想重转加 `--force`。

### 2. 临时文件零残留

- 抽出的 wav 统一放 `out/quick/.wav_tmp/`，成功/失败/中断后都删除
- `/tmp/whisper_quick_<时间戳>.command` 由脚本内 `trap ... EXIT INT TERM HUP` 自删
- 按 `s`/`q`/Ctrl+C 停止时，`pgrep whisper-cli` 与 `pgrep ffmpeg` 均无残留进程

### 3. 模型

`models/` 下按优先级自动探测：

| 优先级 | 文件 | 大小 | 说明 |
|---|---|---|---|
| 1 | `ggml-large-v3-turbo-q8_0.bin` | 834MB | 8-bit 量化，默认首选 |
| 2 | `ggml-large-v3-turbo-q5_0.bin` | 547MB | 5-bit，q8 缺失时回退 |

探测逻辑：文件存在且 > 100MB 才算有效。q5_0 的已知问题是中文同音字误判（「整体梳理」→「整体消息」、「编程语言」→「编成语言」），q8_0 可改善此类问题。

### 4. 并行转写

`--parallel N`（默认 2，上限 4）。每个视频独立进程，各自跑 Metal。

实测（3 分钟音频 ×2，M3 Pro）：

| 模式 | 单路耗时 | 总耗时 |
|---|---|---|
| `--parallel 1` | 12.6s / 12.9s | **25.5s** |
| `--parallel 2` | 22.0s / 21.2s | **22.0s** |

结论：Metal 支持多实例并行，两个 `whisper-cli` 可同时运行不崩溃，但 GPU 争抢使单路变慢，**总吞吐只提升约 15%**。真正收益在长队列：跨多个视频排队时，GPU 空转被填满。短音频不必强开并行。

### 5. 链路精简

`v1` 五层：app → `/tmp/.command` → Terminal → `quick_transcribe.sh` → `tui.py` → `transcribe_batch.py` → whisper-cli

`v2` 三层：app → `/tmp/.command` → Terminal → **`tui.py` 直接接收路径** → whisper-cli

`tui.py` 现在自带调度引擎，`transcribe_batch.py` 与 `quick_transcribe.sh` 已退出主链路（`quick_transcribe.sh` 退化为一行转发壳，保留兼容）。

```bash
# 位置参数直接传文件 / 目录 / 混合，递归深度 2
python3 tui.py 视频A.mp4 视频B.mp4
python3 tui.py ~/Documents/课程视频
python3 tui.py ~/某目录 --parallel 3 --threads 6
```

### 6. TUI 快捷键

| 键 | 作用 |
|---|---|
| `q` / Ctrl+C | 退出（会杀掉所有子进程） |
| `s` | 停止全部调度 |
| `p` / 空格 | 暂停 / 继续 |
| `r` | **重跑所有失败项**（清掉失败标记重新入队） |
| `↑` `↓` | 滚动任务列表 |

### 完整参数表

```
python3 tui.py [路径 ...]
  --input PATH          输入目录（可多次）
  --output PATH         输出目录（默认 out/quick）
  --force               忽略已有结果强制重转
  --parallel N          并行路数（默认 1，上限 4）
  --threads N           每路线程数（默认 8；并行>1 时按 cpu//parallel 下调）
  --cpu                 禁用 Metal，走 CPU
  --no-vad              关闭 VAD 静音裁剪（默认有模型就开启）
  --vad-threshold F     VAD 语音判定阈值（默认 0.5，越高越严格）
  --vad-min-silence MS  VAD 最小静音切分毫秒（默认 300）
  --no-progress         关闭 whisper 百分比进度（默认开）
  --prompt / -P TEXT    领域提示词
  --snapshot            只渲染一帧就退出（自检用）
  --snapshot-wait SEC   渲染前等待秒数
  --exit-after SEC      跑完自动退出（自动化场景）
```

非交互场景（管道 / 定时任务）自动切到 `run_plain()` 逐行日志模式，不会因为不是 tty 而报错退出。

## v3 修复（2026-11-12）

针对实测暴露的三个会导致「白跑」的问题做了根治，另外补了 VAD 与进度显示。旧版备份：`tui.py.bak.20261112`。

### 1. 同名文件不再互相覆盖（P0）

旧版按「文件名去重」：两个不同目录下的 `lesson.mp4` 只会跑一个，另一个被静默丢弃。
现在同名文件全部保留，输出目录用 `父目录名__文件名` 区分：

| 输入 | 输出 |
|---|---|
| `d1/lesson.mp4` | `out/quick/d1__lesson/` |
| `d2/lesson.mp4` | `out/quick/d2__lesson/` |

### 2. 半截 txt 不再被当成成品跳过（P0）

旧版断点续传判据只有「txt 存在且非空」。进程被 kill 时留下的半截 txt 会被当成已完成永久跳过。
现在以 `<输出目录>/.done.json` 完成标记为唯一权威：

| 情形 | 判定 |
|---|---|
| txt 非空 + 有 `.done.json` | 跳过（真空成品） |
| 无标记的老产物（txt+srt 双全） | 体检：UTF-8 完整 + txt 行数与 srt 条数配平 → 通过则补写标记，否则重转 |
| 只有半截 txt / 非法编码 / 行数不配平 | 判定不完整，清掉残留后重转 |

### 3. 多实例抢 GPU（P0）

连续拖多个文件时会起多个 Terminal，每个进程各占一份 q8 模型，GPU 互相拖速。
现在用 `<输出目录>/.run.lock`（`fcntl.flock`）做单实例锁：进程退出/崩溃后锁自动释放，不会残留死锁；

```
已有实例正在往这个输出目录转写，本次直接退出（避免两份幻觉抢同一块 GPU）。
占用信息：pid=5675 start=2026-11-12 15:01:49
```

### 4. VAD 静音裁剪（P1，默认开）

whisper-cli 原生 `--vad`，配 `models/ggml-silero-v5.1.2.bin`（864KB，需从 `ggml-org/whisper-vad` 下载）。
剪掉课堂里的长静音，实测 76 秒合成音频 5.43s → 4.74s；真实课程视频收益更大。
副作用是极轻的语音可能被裁掉，发现漏句时加 `--no-vad` 关闭。

### 5. 并行默认值改为 1（P1）

实测 `--parallel 2` 相比 1 只快 15%（25.5s → 22.0s），单路反而从 12.6s 涨到 22.0s。
Metal 单路已吃满 GPU，默认串行更省心；确实要压榨吞吐再手动 `--parallel 2`。

### 6. 时长探测改并发（P1）

旧版在调度线程里逐个 `ffmpeg -i` 读时长，任务一多前面几个全在排队。
现在启动时用后台线程池（最多 4 并发）一次性探完，界面进度/剩余时间立刻准确。

### 7. 进度百分比（P1）

给 whisper-cli 加 `-pp`，stderr 的 `progress = NN%` 被实时解析，任务行显示 `转写 45%`。
不想要这份额外解析用 `--no-progress` 关掉。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
