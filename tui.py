#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课程视频转文字 · 终端控制台（自带调度引擎）

不再包装 scripts/transcribe_batch.py，自身完成：视频收集 → ffmpeg 抽音 →
whisper-cli 转写 → 并行调度 → 断点续传 → 失败重跑 → 界面渲染。
仅依赖 Python 3.8+ 标准库。

用法：
    python3 tui.py <视频或目录> [<更多路径>...]
    python3 tui.py --input "<视频目录>"                 # 兼容旧用法
    python3 tui.py vid.mp4 clip/ --output out --parallel 3
    python3 tui.py vid.mp4 --snapshot                   # 渲染一帧后退出（自检用）

输出结构（固定，可断点续传）：
    <output>/<视频名>/<视频名>.txt        纯文本
    <output>/<视频名>/<视频名>.srt        带时间轴字幕
    <output>/<视频名>/.done.json          完成标记（断点续传只认它）
    <output>/.wav_tmp/                    临时 wav（任务结束必定清理）
    <output>/.run.lock                    单实例锁（同时对同一目录只允许一份）

同名视频（不同目录下的 lesson.mp4）会自动改名成 d1__lesson / d2__lesson，
各自独立输出，不再互相覆盖；目录名取「父目录名__文件名」。

按键：q 退出 · s 停止 · r 重跑失败项 · p 暂停/继续调度 · ↑↓ 滚动任务列表
"""

import argparse
import atexit
import concurrent.futures
import fcntl
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
import unicodedata
import zlib
from collections import deque
from datetime import datetime
from pathlib import Path

# ───────────────────────── 常量 ─────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "out" / "quick"
WHISPER_BIN = PROJECT_ROOT / "bin" / "whisper-cli"
FFMPEG_BIN = PROJECT_ROOT / "bin" / "ffmpeg"
_MODEL_DIR = PROJECT_ROOT / "models"
# 精度优先：q8_0 存在就用 q8_0，否则回退已下载的 q5_0
MODEL_PATH = next(
    (p for p in (
        _MODEL_DIR / "ggml-large-v3-turbo-q8_0.bin",
        _MODEL_DIR / "ggml-large-v3-turbo-q5_0.bin",
    ) if p.exists() and p.stat().st_size > 100 * 1024 * 1024),
    _MODEL_DIR / "ggml-large-v3-turbo-q5_0.bin",
)
WAV_TMP_DIRNAME = ".wav_tmp"
LOCK_NAME = ".run.lock"           # 单实例锁：同一输出目录只允许一个实例
DONE_MARKER = ".done.json"        # 完整性标记：只有转写跑完才会写
SCAN_DEPTH = 2                    # 目录递归收集层数（目录本身为第 0 层）

# VAD（语音活动检测）：有模型就默认开，跳过静音段提速
VAD_MODEL_PATH = next(
    (p for p in sorted(_MODEL_DIR.glob("ggml-silero-*.bin")) if p.stat().st_size > 100 * 1024),
    _MODEL_DIR / "ggml-silero-v5.1.2.bin",
)

VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi",
    ".flv", ".wmv", ".webm", ".ts", ".mpg", ".mpeg", ".mts", ".3gp",
}

DEFAULT_PROMPT = (
    "以下是普通话授课内容，涉及 Python 编程、解释器、开发环境、PyCharm、变量、"
    "数据类型、字符串、注释、标识符、关键字、运算符、输入输出、项目目录结构。"
)

MAX_PARALLEL = 4
DEFAULT_PARALLEL = 1      # Metal 单路已吃满 GPU，多路只抢带宽：默认串行
LOG_KEEP = 200            # 日志保留行数
RENDER_INTERVAL = 0.1     # 渲染节流：最多每 100ms 重绘一次
LOG_PANEL_H = 8           # 日志区目标高度

# ANSI 颜色
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GRAY = "\033[90m"
WHITE = "\033[97m"

LANE_COLORS = [CYAN, MAGENTA, BLUE, GREEN, YELLOW, WHITE]

# 任务状态
ST_WAIT, ST_RUN, ST_OK, ST_SKIP, ST_FAIL = "等待", "进行中", "成功", "跳过", "失败"
SYMBOL = {ST_WAIT: "○", ST_RUN: "▶", ST_OK: "✓", ST_SKIP: "–", ST_FAIL: "✗"}
STATUS_COLOR = {ST_WAIT: GRAY, ST_RUN: BLUE, ST_OK: GREEN, ST_SKIP: YELLOW, ST_FAIL: RED}
LOG_COLOR = {"start": BLUE, "done": GREEN, "skip": YELLOW,
             "fail": RED, "info": GRAY, "retry": MAGENTA}

RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
RE_PROGRESS = re.compile(r"progress\s*=\s*(\d+)\s*%")


def lane_color(stem):
    """按视频名稳定映射一种颜色，让并行多路的日志一眼可分辨。"""
    return LANE_COLORS[zlib.crc32(stem.encode("utf-8")) % len(LANE_COLORS)]


# ───────────────────────── 宽度与文本工具 ─────────────────────────
def dw(text):
    """计算字符串的终端显示宽度：中日韩全角字符按 2 列计。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def trunc(text, width):
    """按显示宽度截断，超出部分用省略号代替，保证不串行。"""
    if width <= 0:
        return ""
    if dw(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def pad(text, width, align="left"):
    """按显示宽度填充空格对齐。"""
    text = trunc(text, width)
    space = max(0, width - dw(text))
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


def strip_ansi(s):
    """去除 ANSI 转义序列，用于可见宽度计算。"""
    return re.sub(r"\033\[[0-9;?]*[a-zA-Z]", "", s)


def fit_line(s, cols):
    """把一行裁到终端宽度内（超宽时降级为无色截断，优先保证不换行）。"""
    if dw(strip_ansi(s)) <= cols:
        return s
    return trunc(strip_ansi(s), cols)


def fmt_clock(seconds, dash="--:--"):
    """秒数格式化为 MM:SS / H:MM:SS；None 显示占位符。"""
    if seconds is None:
        return dash
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_duration(seconds):
    """媒体时长格式化为 m:ss / h:mm:ss。"""
    if seconds is None:
        return "--:--"
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def count_chars(txt_path):
    """统计转写文本字数（忽略所有空白字符）。"""
    try:
        return len(re.sub(r"\s", "", Path(txt_path).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


# ───────────────────────── 输入收集 ─────────────────────────
def _is_video(path):
    return path.is_file() and not path.name.startswith(".") and path.suffix.lower() in VIDEO_EXTS


def collect_inputs(items):
    """收集视频：文件直接收，目录按 SCAN_DEPTH 层递归；同名文件全部保留。

    同名不再丢弃（旧实现会静默吃掉第二个），改由 unique_stems 分配唯一输出目录。
    返回 (paths, warnings)。
    """
    found, seen_real, warnings = [], set(), []
    seen_stem = {}

    def add(path):
        try:
            real = str(Path(path).resolve())
        except OSError:
            real = str(path)
        if real in seen_real:               # 同一文件被重复传入
            return
        seen_real.add(real)
        stem = Path(path).stem
        if stem in seen_stem:               # 同名但不同文件：保留，输出目录会自动区分
            warnings.append(
                f"发现同名文件：{path} 与 {seen_stem[stem]}，将分别输出到不同子目录")
        else:
            seen_stem[stem] = path
        found.append(Path(path))

    def scan_dir(root, level):
        try:
            entries = sorted(Path(root).iterdir(), key=lambda p: p.name)
        except OSError as exc:
            warnings.append(f"目录无法读取：{root}（{exc}）")
            return
        for entry in entries:
            if _is_video(entry):
                add(entry)
            elif entry.is_dir() and not entry.name.startswith(".") and level + 1 <= SCAN_DEPTH:
                scan_dir(entry, level + 1)

    for item in items:
        p = Path(item).expanduser()
        if p.is_dir():
            scan_dir(p, 0)
        elif _is_video(p):
            add(p)
        elif p.exists():
            warnings.append(f"非视频文件跳过：{p}")
        else:
            warnings.append(f"路径不存在：{p}")

    found.sort(key=lambda p: p.name)
    return found, warnings


# ───────────────────────── 媒体工具 ─────────────────────────
def media_duration(ffmpeg, video_path):
    """用 ffmpeg -i 读取媒体时长（秒）；失败返回 None（不依赖 ffprobe）。"""
    try:
        proc = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(video_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        err = proc.stderr.decode("utf-8", "replace")
    except Exception:
        return None
    match = RE_DURATION.search(err)
    if not match:
        return None
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


class TaskError(Exception):
    """单任务内的可预期失败（抽音/转写）。"""


# ───────────────────── 产物完整性 / 锁 / 命名 ─────────────────────
def marker_path(out_dir):
    return Path(out_dir) / DONE_MARKER


def _txt_is_sane(txt, srt):
    """无标记时的兜底体检：UTF-8 必须完整、txt 行数要与 srt 条数对得上。"""
    try:
        raw = Path(txt).read_bytes()
        text = raw.decode("utf-8")            # 半截写入常留下非法序列
    except (OSError, UnicodeDecodeError):
        return False
    lines = sum(1 for ln in text.splitlines() if ln.strip())
    entries = 0
    try:
        for ln in Path(srt).read_text(encoding="utf-8", errors="ignore").splitlines():
            if "-->" in ln:
                entries += 1
    except OSError:
        return False
    if lines == 0 or entries == 0:
        return False
    return abs(lines - entries) <= max(1, int(entries * 0.15))


def result_state(out_dir, stem):
    """判定已有产物状态，返回 (可跳过, 说明)。

    以 .done.json 为权威；没有标记的老产物要做体检（UTF-8 完整 + 行数配平），
    只落了半截 txt 的绝不当成品跳过。
    """
    out_dir = Path(out_dir)
    txt = out_dir / f"{stem}.txt"
    srt = out_dir / f"{stem}.srt"
    txt_ok = txt.exists() and txt.stat().st_size > 0
    srt_ok = srt.exists() and srt.stat().st_size > 0
    if txt_ok and marker_path(out_dir).exists():
        return True, "已完成"
    if txt_ok and srt_ok:
        if _txt_is_sane(txt, srt):            # 老产物：体检通过就补标记，下次不用再查
            write_marker(out_dir, stem, {"path": "", "chars": count_chars(txt)},
                         {"legacy": True})
            return True, "已完成（旧产物，已补标记）"
        return False, "产物不完整（文本截断或行数不匹配），将重转"
    if txt.exists() or srt.exists():
        return False, "发现半截产物，将重转"
    return False, ""


def write_marker(out_dir, stem, task, extra=None):
    """转写成功后落一份完整性标记，断点续传只认它。"""
    out_dir = Path(out_dir)
    txt = out_dir / f"{stem}.txt"
    srt = out_dir / f"{stem}.srt"
    data = {
        "source": str(task.get("path", "")),
        "txt": txt.name,
        "txt_bytes": txt.stat().st_size if txt.exists() else 0,
        "srt_bytes": srt.stat().st_size if srt.exists() else 0,
        "chars": task.get("chars"),
        "seconds": round(task["seconds"], 2) if task.get("seconds") else None,
        "model": MODEL_PATH.name,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "version": 1,
    }
    if extra:
        data.update(extra)
    try:
        marker_path(out_dir).write_text(json.dumps(data, ensure_ascii=False, indent=1))
        return True
    except OSError:
        return False


def unique_stems(videos):
    """给每个视频分配唯一 stem：重名时用「父目录名__原名」区分，杜绝互相覆盖。"""
    bases = [p.stem for p in videos]
    dup = {s for s in set(bases) if bases.count(s) > 1}
    used, result = set(), []
    for path in videos:
        stem = path.stem
        if stem in dup:
            parent = path.parent.name or "根目录"
            stem = f"{parent}__{path.stem}"
        cand, n = stem, 2
        while cand in used:
            cand = f"{stem}__{n}"
            n += 1
        used.add(cand)
        result.append(cand)
    return result


_LOCK_FD = None


def acquire_lock(output_root):
    """同输出目录只允许一个实例：抢到 flock 才能跑，否则返回 (False, 占用信息)。"""
    global _LOCK_FD
    lock_path = Path(output_root) / LOCK_NAME
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return True, ""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            info = lock_path.read_text(errors="replace").strip()
        except OSError:
            info = ""
        os.close(fd)
        return False, info
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()} start={datetime.now():%Y-%m-%d %H:%M:%S}\n".encode())
    except OSError:
        pass
    _LOCK_FD = fd
    atexit.register(release_lock)
    return True, ""


def release_lock():
    """释放锁（flock 随 fd 关闭自动释放，锁文件保留复用）。"""
    global _LOCK_FD
    if _LOCK_FD is None:
        return
    try:
        fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(_LOCK_FD)
    except OSError:
        pass
    _LOCK_FD = None



# ───────────────────────── 调度引擎 ─────────────────────────
class Engine:
    """自带调度：N 路并发、暂停/继续、失败重跑、断点续传、临时文件清理。"""

    def __init__(self, tasks, cfg):
        self.tasks = tasks
        self.by_stem = {t["stem"]: t for t in tasks}
        self.cfg = cfg
        self.parallel = max(1, min(MAX_PARALLEL, cfg["parallel"]))
        self.lane_threads = cfg["lane_threads"]

        self.queue = deque(t["stem"] for t in tasks if t["status"] == ST_WAIT)
        self.running = {}                  # stem -> {"started", "phase"}
        self.cond = threading.Condition()
        self.lock = threading.Lock()       # 保护 _procs / own_wavs
        self._procs = {}                   # stem -> Popen
        self.own_wavs = set()

        self.paused = False
        self.stopping = False
        self.active = False
        self.pool = None
        self._probe_started = False         # 时长预探测只跑一次
        self._probe_thread = None

        self.logs = deque(maxlen=LOG_KEEP)
        self.start_ts = time.time()
        self.end_ts = None
        self.last_top = 0

    # ——— 日志 ———
    def log(self, kind, text, stem=""):
        with self.cond:
            self.logs.append((kind, text, stem))

    # ——— 统计（渲染线程读取）———
    def finished(self):
        return sum(1 for t in self.tasks if t["status"] in (ST_OK, ST_SKIP, ST_FAIL))

    def queue_len(self):
        return len(self.queue)

    def running_count(self):
        return len(self.running)

    def running_snapshot(self):
        with self.cond:
            return [(stem, info["phase"], time.time() - info["started"])
                    for stem, info in sorted(self.running.items(), key=lambda kv: kv[1]["started"])]

    def current_index(self):
        """进行中项（取最早一个）的下标；否则最后一个非等待项。"""
        running = self.running_snapshot()
        if running:
            return self.tasks.index(self.by_stem[running[0][0]])
        for i in range(len(self.tasks) - 1, -1, -1):
            if self.tasks[i]["status"] != ST_WAIT:
                return i
        return 0

    def all_done(self):
        with self.cond:
            return not self.queue and not self.running

    def elapsed(self):
        return (self.end_ts or time.time()) - self.start_ts

    def eta(self):
        """按已完成任务的平均耗时估算剩余时间。"""
        done = [t for t in self.tasks if t["status"] in (ST_OK, ST_FAIL) and t["seconds"]]
        if not done:
            return None
        remaining = len(self.tasks) - self.finished()
        if remaining <= 0:
            return 0
        avg = sum(t["seconds"] for t in done) / len(done)
        return avg * remaining / max(1, self.parallel)

    def counts(self):
        return {
            "total": len(self.tasks),
            "success": sum(1 for t in self.tasks if t["status"] == ST_OK),
            "skip": sum(1 for t in self.tasks if t["status"] == ST_SKIP),
            "fail": sum(1 for t in self.tasks if t["status"] == ST_FAIL),
            "wait": sum(1 for t in self.tasks if t["status"] == ST_WAIT),
            "seconds": self.elapsed(),
        }

    # ——— 生命周期 ———
    def start(self):
        if self.active:
            return
        if self.pool is None:
            self.pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.parallel, thread_name_prefix="lane")
        self.stopping = False
        self.active = True
        self._spawn_duration_prefetch()
        with self.cond:
            for _ in range(self.parallel):
                self.pool.submit(self._worker)
            self.cond.notify_all()

    def toggle_pause(self):
        with self.cond:
            self.paused = not self.paused
            self.cond.notify_all()
            return self.paused

    # ——— 时长预探测（后台并发，避免串行喂给调度队列）———
    def _spawn_duration_prefetch(self):
        if self._probe_started:
            return
        self._probe_started = True
        self._probe_thread = threading.Thread(
            target=self._probe_all, name="probe", daemon=True)
        self._probe_thread.start()

    def _probe_all(self):
        todo = [t for t in self.tasks if t["duration"] is None]
        if not todo:
            return
        workers = min(4, len(todo))
        try:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="probe") as ex:
                futs = {ex.submit(media_duration, self.cfg["ffmpeg"], t["path"]): t
                        for t in todo}
                for fut in concurrent.futures.as_completed(futs):
                    if self.stopping:
                        break
                    task = futs[fut]
                    try:
                        task["duration"] = fut.result()
                    except Exception:                         # noqa: BLE001 探测失败不影响主流程
                        task["duration"] = None
        except Exception:                                     # noqa: BLE001 兜底
            pass

    def stop(self):
        """停止派发 + 杀掉在跑的子进程组（已在跑的被标记失败，可用 r 重跑）。"""
        with self.cond:
            self.stopping = True
            self.active = False
            self.cond.notify_all()
        self._kill_running()

    def quit(self, wait=True):
        self.stop()
        if self.pool is not None:
            self.pool.shutdown(wait=wait)
            self.pool = None
        self.cleanup_wavs()

    def retry_failed(self):
        """把失败项清掉半成品后重新排队；若引擎已停止则顺带恢复调度。"""
        requeue = []
        for t in self.tasks:
            if t["status"] != ST_FAIL:
                continue
            self._clean_partial(t)
            t.update(status=ST_WAIT, chars=None, seconds=None,
                     error="", started=None, phase="")
            requeue.append(t["stem"])
        if not requeue:
            return 0
        self.end_ts = None
        with self.cond:
            self.stopping = False
            self.queue.extend(requeue)
            self.cond.notify_all()
        if not self.active:
            self.start()
        self.log("retry", f"重新排队 {len(requeue)} 个失败项")
        return len(requeue)

    # ——— 清理 ———
    def _register_wav(self, wav):
        with self.lock:
            self.own_wavs.add(Path(wav))

    def _drop_wav(self, wav):
        with self.lock:
            self.own_wavs.discard(Path(wav))
        try:
            Path(wav).unlink()
        except OSError:
            pass

    def cleanup_wavs(self):
        """删除本进程产生的临时 wav，并在 .wav_tmp 空时移除该目录。"""
        with self.lock:
            wavs = list(self.own_wavs)
            self.own_wavs.clear()
        for wav in wavs:
            try:
                wav.unlink()
            except OSError:
                pass
        tmp = self.cfg["wav_tmp"]
        try:
            if tmp.exists() and not any(tmp.iterdir()):
                tmp.rmdir()
        except OSError:
            pass

    def _clean_partial(self, task):
        """清掉某个任务的半成品：txt / srt / 残留 wav。"""
        out_dir = self.cfg["output"] / task["stem"]
        for suffix in (".txt", ".srt"):
            try:
                (out_dir / f"{task['stem']}{suffix}").unlink()
            except OSError:
                pass
        try:
            for wav in self.cfg["wav_tmp"].glob(f"{task['stem']}.*.wav"):
                self._drop_wav(wav)
        except OSError:
            pass
        try:                                  # 任务目录若已空则一并收掉，不留空壳
            if out_dir.is_dir() and not any(out_dir.iterdir()):
                out_dir.rmdir()
        except OSError:
            pass

    # ——— 子进程 ———
    def _run(self, stem, cmd):
        """执行一条命令，返回 (rc, stdout, stderr)；过程中登记进程便于停止。"""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(PROJECT_ROOT), start_new_session=True)
        except FileNotFoundError as exc:
            return -2, "", f"命令不存在：{exc}"
        except Exception as exc:                              # noqa: BLE001 兜底
            return -1, "", f"命令启动异常：{exc}"
        with self.lock:
            self._procs[stem] = proc
        try:
            out, err = proc.communicate()
        finally:
            with self.lock:
                self._procs.pop(stem, None)
        return (proc.returncode,
                out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    def _run_stream(self, stem, cmd, on_progress=None):
        """同 _run，但边跑边解析 whisper 的 progress 回调，实时回报百分比。"""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(PROJECT_ROOT), start_new_session=True)
        except FileNotFoundError as exc:
            return -2, "", f"命令不存在：{exc}"
        except Exception as exc:                              # noqa: BLE001 兜底
            return -1, "", f"命令启动异常：{exc}"
        with self.lock:
            self._procs[stem] = proc
        out_buf = []

        def drain_stdout():
            try:
                for chunk in iter(lambda: proc.stdout.readline(), b""):
                    out_buf.append(chunk)
            except Exception:                                # noqa: BLE001 读取中断忽略
                pass

        reader = threading.Thread(target=drain_stdout, daemon=True, name="stdout-drain")
        reader.start()
        err_lines, last_pct = [], -1
        try:
            for raw in iter(lambda: proc.stderr.readline(), b""):
                line = raw.decode("utf-8", "replace")
                err_lines.append(line)
                if len(err_lines) > 4000:                     # 只留尾部，防长视频撑爆内存
                    del err_lines[:2000]
                if on_progress:
                    match = RE_PROGRESS.search(line)
                    if match:
                        pct = min(100, int(match.group(1)))
                        if pct != last_pct:
                            last_pct = pct
                            try:
                                on_progress(pct)
                            except Exception:                # noqa: BLE001 回调异常不影响转写
                                pass
            proc.wait()
        finally:
            reader.join(timeout=2.0)
            with self.lock:
                self._procs.pop(stem, None)
        return (proc.returncode,
                b"".join(out_buf).decode("utf-8", "replace"),
                "".join(err_lines))

    def _kill_running(self):
        with self.lock:
            procs = list(self._procs.values())
        for proc in procs:
            if proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if all(p.poll() is not None for p in procs):
                break
            time.sleep(0.05)
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    # ——— 命令构造 ———
    def _ffmpeg_cmd(self, video_path, wav_path):
        return [str(self.cfg["ffmpeg"]), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video_path),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_path)]

    def _whisper_cmd(self, wav_path, out_prefix):
        cmd = [str(self.cfg["whisper"]),
               "-m", str(self.cfg["model"]),
               "-f", str(wav_path),
               "-l", "zh",
               "-otxt", "-osrt",
               "-t", str(self.lane_threads),
               "-np",
               "--output-file", str(out_prefix)]
        if self.cfg.get("vad") and self.cfg.get("vad_model"):
            cmd.extend(["--vad", "-vm", str(self.cfg["vad_model"]),
                        "-vt", str(self.cfg.get("vad_threshold", 0.5)),
                        "-vsd", str(self.cfg.get("vad_min_silence_ms", 300))])
        if self.cfg.get("progress"):
            cmd.append("-pp")                 # print-progress：向 stderr 吐百分比
        if self.cfg["prompt"]:
            cmd.extend(["--prompt", self.cfg["prompt"]])
        if self.cfg["cpu"]:
            cmd.append("-ng")
        return cmd

    # ——— 工作线程 ———
    def _worker(self):
        while True:
            with self.cond:
                while True:
                    if self.stopping:
                        return
                    if not self.paused and self.queue:
                        break
                    self.cond.wait(0.2)
                stem = self.queue.popleft()
            try:
                self._process(stem)
            except Exception as exc:                          # noqa: BLE001 兜底
                task = self.by_stem.get(stem)
                if task is not None:
                    task["status"] = ST_FAIL
                    task["error"] = f"内部异常：{exc}"
                    task["seconds"] = (time.time() - (task["started"] or time.time()))
                self.log("fail", f"内部异常 {stem}：{exc}", stem)
            finally:
                with self.cond:
                    self.running.pop(stem, None)
                    if not self.queue and not self.running and self.end_ts is None:
                        self.end_ts = time.time()

    def _process(self, stem):
        task = self.by_stem[stem]
        started = time.time()
        task.update(status=ST_RUN, started=started, seconds=None, phase="抽音")
        with self.cond:
            self.running[stem] = {"started": started, "phase": "抽音"}
        self.log("start", f"开始 {task['name']}", stem)

        out_dir = self.cfg["output"] / stem
        txt_path = out_dir / f"{stem}.txt"
        wav_path = self.cfg["wav_tmp"] / f"{stem}.{os.getpid()}.{threading.get_ident()}.wav"

        try:
            # 断点续传：必须「txt 非空 + 有完成标记」才算成品；半截产物一律重转
            if not self.cfg["force"]:
                done, why = result_state(out_dir, stem)
                if done:
                    task.update(status=ST_SKIP, seconds=0.0,
                                chars=count_chars(txt_path), outdir=str(out_dir))
                    self.log("skip", f"跳过 {task['name']} —— 已存在完整结果：{txt_path}", stem)
                    return
                if why:
                    self.log("info", f"{task['name']}：{why}，重新转写", stem)
                    for stale in (txt_path, out_dir / f"{stem}.srt", marker_path(out_dir)):
                        try:
                            stale.unlink()
                        except OSError:
                            pass

            if task["duration"] is None:
                task["duration"] = media_duration(self.cfg["ffmpeg"], task["path"])

            out_dir.mkdir(parents=True, exist_ok=True)
            self.cfg["wav_tmp"].mkdir(parents=True, exist_ok=True)
            self._register_wav(wav_path)

            rc, out, err = self._run(stem, self._ffmpeg_cmd(task["path"], wav_path))
            if rc != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
                raise TaskError(f"ffmpeg 抽音失败（rc={rc}）：{(err or out).strip()[:300]}")

            def progress_cb(pct):
                """whisper 进度回调：百分比同步进界面阶段文字。"""
                phase = f"转写 {pct}%"
                task["phase"] = phase
                with self.cond:
                    if stem in self.running:
                        self.running[stem]["phase"] = phase

            with self.cond:
                if stem in self.running:
                    self.running[stem]["phase"] = "转写"
            task["phase"] = "转写"

            rc, out, err = self._run_stream(
                stem, self._whisper_cmd(wav_path, out_dir / stem),
                on_progress=progress_cb if self.cfg.get("progress") else None)
            if rc != 0 or not txt_path.exists() or txt_path.stat().st_size == 0:
                raise TaskError(f"whisper 转写失败（rc={rc}）：{(err or out).strip()[:300]}")

            task.update(status=ST_OK, chars=count_chars(txt_path),
                        seconds=time.time() - started, outdir=str(out_dir), error="")
            write_marker(out_dir, stem, task, {"vad": bool(self.cfg.get("vad"))})
            self.log("done", f"完成 {task['name']} —— {task['chars']} 字，"
                             f"耗时 {task['seconds']:.1f}s → {txt_path}", stem)
        except TaskError as exc:
            task.update(status=ST_FAIL, error=str(exc),
                        seconds=time.time() - started, outdir=str(out_dir))
            self.log("fail", f"失败 {task['name']} —— {exc}", stem)
        except Exception as exc:                              # noqa: BLE001 兜底
            task.update(status=ST_FAIL, error=f"异常：{exc}",
                        seconds=time.time() - started, outdir=str(out_dir))
            self.log("fail", f"失败 {task['name']} —— 异常：{exc}", stem)
        finally:
            self._drop_wav(wav_path)
            if task["status"] == ST_FAIL:            # 失败不留空壳目录
                try:
                    if out_dir.is_dir() and not any(out_dir.iterdir()):
                        out_dir.rmdir()
                except OSError:
                    pass


# ───────────────────────── 渲染 ─────────────────────────
def render(engine, cols, rows, scroll=None, hint="", color=True, running=True):
    """渲染一帧完整界面，返回行列表（每行不超过终端宽度、总行数不超过终端高度）。"""
    def c(code, text):
        return f"{code}{text}{RESET}" if color else text

    cols = max(40, cols)
    rows = max(12, rows)
    total = len(engine.tasks)
    finished = engine.finished()
    out = []

    # ① 标题行：左标题 + 右时钟
    model_tag = MODEL_PATH.stem.replace("ggml-", "")
    title = f"课程视频转文字 · whisper.cpp {model_tag}"
    clock = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    gap = max(1, cols - dw(title) - dw(clock))
    out.append(c(BOLD, title) + " " * gap + c(GRAY, clock))

    # ② 进度行：进度条 + 并行度 / 队列数 / 时耗
    pct = (finished / total) if total else 0.0
    bar_w = max(8, min(22, cols // 6))
    filled = int(round(bar_w * pct))
    bar = c(GREEN, "█" * filled) + c(GRAY, "░" * (bar_w - filled))
    out.append(f"[{bar}] 完成 {finished}/{total} ({pct:.0%})"
               f" · 并行 {engine.parallel}（在跑 {engine.running_count()}）"
               f" · 队列 {engine.queue_len()}"
               f" · 已用 {fmt_clock(engine.elapsed())}"
               f" · 预计剩余 {fmt_clock(engine.eta())}")

    # ③ 进行中任务区：每路一条，各自秒表
    run_h = min(max(1, engine.parallel), 3)
    run_lines = []
    for stem, phase, secs in engine.running_snapshot()[:run_h]:
        task = engine.by_stem[stem]
        suffix = f" · {phase} · 时长 {fmt_duration(task['duration'])} · 已用 {secs:.1f}s"
        if cols - dw(suffix) - 2 < 14:              # 窄屏降级为紧凑写法
            suffix = f" · {phase} {secs:.1f}s"
        name_w_run = max(6, cols - dw(suffix) - 2)
        run_lines.append(
            c(lane_color(stem), "▶ ")
            + pad(task["name"], name_w_run)
            + c(GRAY, suffix))
    if not run_lines:
        if engine.paused:
            run_lines.append(c(YELLOW, "已暂停调度（不再派新任务，在跑的不中断）— 按 p 继续"))
        elif engine.all_done() and engine.end_ts:
            run_lines.append(c(GREEN, "任务已结束")
                             + c(GRAY, f" · 总耗时 {engine.elapsed():.1f}s · 按 q 退出，r 重跑失败项"))
        elif running:
            run_lines.append(c(GRAY, "准备中…"))
        else:
            run_lines.append(c(GRAY, "空闲"))
    out.extend(run_lines + [""] * (run_h - len(run_lines)))

    out.append(c(GRAY, "─" * cols))

    # ④ 任务列表区：固定行数，超出时围绕进行中项滚动
    log_h = min(LOG_PANEL_H, max(3, rows - 12))
    list_h = max(3, rows - 6 - run_h - log_h)
    n = len(engine.tasks)
    list_h = min(list_h, max(3, n))            # 任务少时不铺满，避免大片空白
    cur = engine.current_index()
    if scroll is None:                        # 自动跟随当前进度
        top = max(0, min(cur - list_h // 2, max(0, n - list_h)))
    else:
        top = max(0, min(scroll, max(0, n - list_h)))
    engine.last_top = top

    name_w = max(12, cols - 20)
    shown = engine.tasks[top:top + list_h]
    for task in shown:
        sym_cell = c(STATUS_COLOR[task["status"]], f" {SYMBOL[task['status']]} ")
        if task["status"] == ST_RUN:
            name_cell = c(lane_color(task["stem"]), pad(task["name"], name_w))
            stopwatch = f"{(time.time() - task['started']):.1f}s" if task["started"] else "—"
        else:
            name_cell = pad(task["name"], name_w)
            stopwatch = f"{task['seconds']:.1f}s" if task["seconds"] is not None else "—"
        chars = pad(str(task["chars"]) if task["chars"] is not None else "—", 6, "right")
        out.append(sym_cell + name_cell + c(GRAY, chars + pad(stopwatch, 8, "right")))
    if not shown:
        out.append(c(GRAY, "  暂未发现视频文件"))
    out.extend([""] * (list_h - len(shown)))

    # ⑤ 日志区：前缀按视频名着色，多路输出不串味
    out.append(c(GRAY, f" 日志 · 最近 {LOG_KEEP} 行"))
    logs = list(engine.logs)[-log_h:]
    for kind, text, stem in logs:
        prefix = c(lane_color(stem), f"[{trunc(stem, 14)}] ") if stem else ""
        body = c(LOG_COLOR.get(kind, GRAY), trunc(text, cols - 2 - (16 if stem else 0)))
        out.append(" " + prefix + body)
    out.extend([""] * (log_h - len(logs)))

    # ⑥ 底栏：分隔线 + 按键提示
    out.append(c(GRAY, "─" * cols))
    out.append(c(CYAN, " " + (hint or
               "q 退出 · s 停止 · r 重跑失败 · p 暂停/继续 · ↑↓ 滚动任务列表")))

    body = [fit_line(line, cols) for line in out]
    if len(body) > rows:                      # 超高时优先保住底栏提示行
        body = body[:rows - 1] + [body[-1]]
    return body


def summary_lines(engine, color=True):
    """退出后打印到普通终端的汇总块。"""
    def c(code, text):
        return f"{code}{text}{RESET}" if color else text

    s = engine.counts()
    lines = [
        c(BOLD, "转写汇总"),
        f"  成功 {c(GREEN, str(s['success']))}  ·  跳过 {c(YELLOW, str(s['skip']))}"
        f"  ·  失败 {c(RED, str(s['fail']))}"
        + (f"  ·  未开始 {s['wait']}" if s["wait"] else "")
        + f"  ·  总耗时 {s['seconds']:.1f}s",
    ]
    failed = [t for t in engine.tasks if t["status"] == ST_FAIL]
    if failed:
        lines.append(c(RED, "  失败明细："))
        for t in failed[:10]:
            lines.append(f"    · {t['name']} —— {trunc(t['error'] or '未知错误', 80)}")
        if len(failed) > 10:
            lines.append(f"    · 其余 {len(failed) - 10} 个略")
    return lines


# ───────────────────────── 参数与入口 ─────────────────────────
def build_parser():
    p = argparse.ArgumentParser(
        description="课程视频转文字 · 终端控制台（自带并行调度引擎）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("paths", nargs="*", help="视频文件或目录（目录按 2 层递归收集）")
    p.add_argument("--input", default=None, help="兼容旧用法：视频所在目录")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="产出根目录（固定，可续传）")
    p.add_argument("--force", action="store_true", help="忽略断点续传，强制重转")
    p.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL,
                   help=f"并行转写路数（1-{MAX_PARALLEL}）")
    p.add_argument("--threads", type=int, default=8, help="whisper 每路线程数上限")
    p.add_argument("--cpu", action="store_true", help="禁用 Metal GPU（-ng），降级到 CPU")
    p.add_argument("--no-vad", dest="vad", action="store_false",
                   help="关闭 VAD 静音裁剪（默认有模型就启用）")
    p.add_argument("--vad-threshold", type=float, default=0.5,
                   help="VAD 语音判定阈值（0-1，越高越严格）")
    p.add_argument("--vad-min-silence", type=int, default=300,
                   help="VAD 最小静音切分毫秒数")
    p.add_argument("--no-progress", action="store_true",
                   help="关闭 whisper 百分比进度回调（-pp）")
    p.set_defaults(vad=True)
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help='领域提示词；传 "" 表示不使用提示词')
    p.add_argument("--snapshot", action="store_true", help="渲染一帧到 stdout 后退出（自检用）")
    p.add_argument("--snapshot-wait", type=float, default=6.0,
                   help="快照模式下等待任务推进的最长秒数")
    p.add_argument("--exit-after", type=float, default=0.0,
                   help="任务结束后自动退出的秒数（0 表示一直等待按键 q）")
    return p


def build_tasks(videos, args, output_root):
    """把视频列表转成任务记录，并按断点续传规则预判「跳过」。"""
    tasks = []
    stems = unique_stems(videos)
    for path, stem in zip(videos, stems):
        out_dir = output_root / stem
        txt_path = out_dir / f"{stem}.txt"
        done, _why = (False, "") if args.force else result_state(out_dir, stem)
        tasks.append({
            "stem": stem,
            "name": path.name if stem == path.stem else f"{stem}{path.suffix}",
            "path": path,
            "status": ST_SKIP if done else ST_WAIT,
            "chars": count_chars(txt_path) if done else None,
            "seconds": 0.0 if done else None,
            "duration": None,
            "outdir": str(out_dir),
            "error": "",
            "started": None,
            "phase": "",
        })
    return tasks


def make_cfg(args, output_root):
    cpu = os.cpu_count() or 4
    parallel = max(1, min(MAX_PARALLEL, args.parallel))
    if parallel > 1:                    # 每路线程按比例下调，总线程不超过核心数
        lane_threads = max(1, min(args.threads, cpu // parallel))
    else:
        lane_threads = max(1, args.threads)
    wav_tmp = output_root / WAV_TMP_DIRNAME
    vad_on = bool(getattr(args, "vad", False)) and Path(VAD_MODEL_PATH).exists()
    return {
        "output": output_root,
        "wav_tmp": wav_tmp,
        "parallel": parallel,
        "lane_threads": lane_threads,
        "force": args.force,
        "cpu": args.cpu,
        "prompt": args.prompt or "",
        "whisper": WHISPER_BIN,
        "ffmpeg": FFMPEG_BIN,
        "model": MODEL_PATH,
        "vad": vad_on,
        "vad_model": VAD_MODEL_PATH if vad_on else None,
        "vad_threshold": getattr(args, "vad_threshold", 0.5),
        "vad_min_silence_ms": getattr(args, "vad_min_silence", 300),
        "progress": not getattr(args, "no_progress", False),
    }


def run_snapshot(args, engine):
    """启动引擎跑一小段，渲染一帧无色文本后退出，用于自检。"""
    engine.start()
    deadline = time.time() + max(1.0, args.snapshot_wait)
    while time.time() < deadline:
        if engine.all_done():
            break
        time.sleep(0.05)

    cols, rows = shutil.get_terminal_size((100, 30))
    frame = render(engine, cols, rows, scroll=None, color=False, running=True,
                   hint="快照模式：仅渲染一帧，不读取键盘")

    unfinished = not engine.all_done()
    engine.quit()
    print("\n".join(frame))
    print()
    if unfinished:
        print("（快照在等待时限内未跑完，已终止在跑任务；"
              "被中断项的「失败」仅为终止回执，不代表转写失败）")
    print("\n".join(summary_lines(engine, color=False)))
    print(f"输出目录：{engine.cfg['output']}")
    return 0


def run_plain(args, engine):
    """非 tty 退化模式（管道 / 自动化 / 被其他程序调用）：逐行输出日志，不刷全屏。

    与交互模式共用同一个 Engine，并行、续传、清理、退出保护行为完全一致，
    只是把「整屏画面」换成「日志流」。
    """
    engine.start()
    last = None
    try:
        while True:
            logs = list(engine.logs)
            start = 0
            if last is not None:
                try:
                    start = logs.index(last) + 1
                except ValueError:
                    start = 0
            for item in logs[start:]:
                kind, text, stem = item
                last = item
                who = f"[{stem}] " if stem else ""
                print(f"{who}{text}", flush=True)
            if engine.all_done():
                break
            time.sleep(0.2)
        logs = list(engine.logs)
        start = 0
        if last is not None:
            try:
                start = logs.index(last) + 1
            except ValueError:
                start = 0
        for kind, text, stem in logs[start:]:
            who = f"[{stem}] " if stem else ""
            print(f"{who}{text}", flush=True)
    except KeyboardInterrupt:
        engine.stop()
        engine.quit()
        return 130
    finally:
        engine.quit(wait=False) if engine.all_done() else None

    print()
    print("\n".join(summary_lines(engine, color=False)))
    print(f"输出目录：{engine.cfg['output']}")
    return 0 if engine.counts()["fail"] == 0 else 1


def run_interactive(args, engine):
    """全屏交互：交替屏幕 + cbreak 单键读取 + 100ms 节流重绘。"""
    engine.start()

    signal.signal(signal.SIGWINCH, lambda *_: None)   # 窗口尺寸变化：渲染时自适应
    fd = sys.stdin.fileno()
    old_attr = termios.tcgetattr(fd)
    scroll = None
    hint = ""
    quit_armed = False
    interrupted = False
    auto_exit_at = None
    last_draw = 0.0

    def draw(force=False):
        """按节流间隔重绘一帧（定位到左上角后逐行覆写，避免整屏闪烁）。"""
        nonlocal last_draw
        now = time.time()
        if not force and now - last_draw < RENDER_INTERVAL:
            return
        last_draw = now
        cols, rows = shutil.get_terminal_size((100, 30))
        frame = render(engine, cols, rows, scroll=scroll, hint=hint,
                       color=True, running=engine.active)
        sys.stdout.write("\033[H" + "\n".join(line + "\033[K" for line in frame) + "\033[J")
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?1049h\033[?25l")       # 进入交替屏幕并隐藏光标
        sys.stdout.flush()
        draw(force=True)

        while True:
            # ① 非阻塞读单键
            try:
                ready, _, _ = select.select([fd], [], [], 0.03)
            except (OSError, ValueError):
                ready = []
            if ready:
                keys = os.read(fd, 16).decode("utf-8", "ignore")
                if not keys:                            # stdin EOF：直接退出，避免空转
                    break
                if "\x03" in keys:                      # Ctrl+C：cbreak 下不产生 SIGINT
                    hint = "收到 Ctrl+C，停止任务并恢复终端…"
                    draw(force=True)
                    break
                if not keys.startswith("q"):
                    quit_armed = False
                if "q" in keys:
                    if engine.active and not quit_armed:
                        quit_armed = True
                        hint = "任务仍在运行：再按 q 退出并终止，或按 s 停止任务"
                    else:
                        break
                elif "s" in keys and engine.active:
                    hint = "正在停止任务…"
                    draw(force=True)
                    engine.stop()
                    hint = "已停止：在跑任务已终止（r 可重新排队失败项）"
                elif "r" in keys:
                    n = engine.retry_failed()
                    hint = (f"已重新排队 {n} 个失败项" if n
                            else "没有失败项可重跑")
                elif "p" in keys:
                    paused = engine.toggle_pause()
                    hint = ("已暂停调度：不再派新任务，在跑的不中断（p 继续）"
                            if paused else "已继续调度")
                elif "\x1b[A" in keys or "k" in keys:      # ↑ 上滚
                    base = scroll if scroll is not None else engine.last_top
                    scroll = max(0, base - 1)
                elif "\x1b[B" in keys or "j" in keys:      # ↓ 下滚
                    base = scroll if scroll is not None else engine.last_top
                    scroll = base + 1

            # ② 定时重绘（内部节流）
            draw()

            # ③ 任务结束后可选自动退出（--exit-after，默认一直等待 q）
            if engine.all_done() and args.exit_after > 0:
                if auto_exit_at is None:
                    auto_exit_at = time.time() + args.exit_after
                elif time.time() >= auto_exit_at:
                    break
    except KeyboardInterrupt:
        interrupted = True
    finally:
        engine.quit()
        sys.stdout.write("\033[?25h\033[?1049l")       # 恢复光标、退出交替屏幕
        sys.stdout.flush()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
        except Exception:
            pass

    print()
    if interrupted:
        print("已按 Ctrl+C 中断，终端状态已恢复")
    print("\n".join(summary_lines(engine, color=True)))
    print(f"输出目录：{engine.cfg['output']}")
    s = engine.counts()
    notify("课程视频转文字",
           f"完成：成功 {s['success']} / 跳过 {s['skip']} / 失败 {s['fail']}")
    return 0


def notify(title, message):
    """macOS 系统通知，任何失败静默忽略。"""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            timeout=4, capture_output=True)
    except Exception:
        pass


def install_cleanup(engine):
    """atexit + 信号：任何退出路径都清掉本进程的 .wav_tmp 残留。"""
    atexit.register(engine.cleanup_wavs)

    def handler(signum, _frame):
        engine.cleanup_wavs()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    if hasattr(signal, "SIGPIPE"):
        try:
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        except (ValueError, OSError):
            pass


def main(argv=None):
    args = build_parser().parse_args(argv)

    items = list(args.paths)
    if args.input:
        items.append(args.input)
    if not items:
        print("未指定视频：请传入视频文件/目录，或用 --input 指定目录。", file=sys.stderr)
        return 2

    for path, label in ((WHISPER_BIN, "whisper-cli"), (FFMPEG_BIN, "ffmpeg"),
                        (MODEL_PATH, "模型文件")):
        if not Path(path).exists():
            print(f"{label} 不存在：{path}", file=sys.stderr)
            return 1

    videos, warnings = collect_inputs(items)
    for line in warnings:
        print(f"[提示] {line}", file=sys.stderr)
    if not videos:
        print("没有找到可转写的视频（支持 mp4/mov/mkv/avi/m4v/webm 等）。", file=sys.stderr)
        return 1

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if not args.snapshot:
        locked, holder = acquire_lock(output_root)
        if not locked:
            print("已有实例正在往这个输出目录转写，本次直接退出（避免两份幻觉抢同一块 GPU）。",
                  file=sys.stderr)
            if holder:
                print(f"占用信息：{holder}", file=sys.stderr)
            print(f"锁文件：{output_root / LOCK_NAME}", file=sys.stderr)
            return 3

    args.parallel = max(1, min(MAX_PARALLEL, args.parallel))
    cfg = make_cfg(args, output_root)
    tasks = build_tasks(videos, args, output_root)
    engine = Engine(tasks, cfg)
    install_cleanup(engine)

    skipped = sum(1 for t in tasks if t["status"] == ST_SKIP)
    if skipped:
        engine.log("skip", f"断点续传：{skipped} 个视频已有结果，本次直接跳过")
    engine.log("info", f"共 {len(tasks)} 个视频 · 并行 {cfg['parallel']}"
                       f" · 每路 {cfg['lane_threads']} 线程 · 输出 {output_root}")
    engine.log("info", "VAD 静音裁剪：" + (f"已开启 · {Path(VAD_MODEL_PATH).name}"
                                          if cfg["vad"] else "未启用（缺模型或 --no-vad）"))
    engine.log("info", "模型：" + Path(cfg["model"]).name
                       + f" · 进度回调{'开' if cfg['progress'] else '关'}")

    if args.snapshot:
        return run_snapshot(args, engine)
    if not sys.stdin.isatty():
        return run_plain(args, engine)
    return run_interactive(args, engine)


if __name__ == "__main__":
    sys.exit(main())
