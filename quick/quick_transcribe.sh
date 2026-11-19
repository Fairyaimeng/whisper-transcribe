#!/bin/zsh
# 兼容壳：历史上这是「收集文件 → 建时间戳 INBOX/OUTDIR → 调 tui.py」的中转层。
# 现在文件收集、固定输出目录、断点续传、并行调度全部内联进 tui.py，
# 本脚本退化为一行转发，仅为兼容 droplet / install_service.sh 的旧引用而保留。
#
# 用法：quick_transcribe.sh <视频或目录> [更多路径...]

# 项目根目录：由脚本自身位置推导（quick/ 的上一级），换机器或换克隆路径都无需改代码
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/usr/bin/python3"
TUI="$ROOT/tui.py"

if [[ $# -eq 0 ]]; then
  echo "用法：$(basename "$0") <视频文件或目录> [更多路径...]" >&2
  exit 2
fi

if [[ ! -f "$TUI" ]]; then
  echo "找不到 $TUI" >&2
  exit 1
fi

exec "$PY" "$TUI" "$@"
