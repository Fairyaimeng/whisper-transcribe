#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcribe_batch.py —— 批量视频转文字（whisper.cpp + ffmpeg）

功能
    遍历指定目录下的视频文件，先用 ffmpeg 抽取为 16kHz 单声道 16bit PCM wav，
    再调用 whisper-cli 转写为 txt（纯文本）与 srt（带时间轴字幕），
    每转完一个视频就删除临时 wav，支持断点续传与失败重试。

依赖
    仅 Python 3.8+ 标准库，无需 pip 安装任何第三方包。

用法
    # 使用脚本内置的默认路径（见下方 DEFAULTS）
    python3 transcribe_batch.py

    # 指定输入/输出目录
    python3 transcribe_batch.py --input "/path/to/视频" --output "/path/to/out"

    # 强制重转（忽略已有结果，覆盖断点续传）
    python3 transcribe_batch.py --force

    # 指定线程数
    python3 transcribe_batch.py --threads 10

参数
    --input     输入视频目录，默认 DEFAULTS["input"]
    --output    输出根目录，默认 DEFAULTS["output"]
    --model     whisper 模型文件路径
    --ffmpeg    ffmpeg 可执行文件路径
    --whisper   whisper-cli 可执行文件路径
    --force     忽略断点续传，已转过的视频也重转
    --threads   whisper 推理线程数，默认 8

输出结构
    <output>/<视频basename>/<视频basename>.txt     纯文本转写结果
    <output>/<视频basename>/<视频basename>.srt     带时间轴字幕
    <output>/.wav_tmp/<视频basename>.wav           转写过程中的临时音频（成功后自动删除）
    <项目根>/logs/run_<时间戳>.log                  本次运行日志

断点续传
    若 <output>/<视频basename>/<视频basename>.txt 已存在且非空，则跳过该视频（--force 可关闭）。

退出码
    0 = 全部成功或跳过；1 = 至少一个视频失败。
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

# ───────────────────────── 默认环境参数 ─────────────────────────
# 项目根目录：由脚本自身位置推导（scripts/ 的上一级），换机器或换克隆路径都无需改代码
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 用户主目录：用于拼接默认输入目录
_HOME = Path.home()

DEFAULTS = {
    "whisper": str(_PROJECT_ROOT / "bin" / "whisper-cli"),
    "model": str(_PROJECT_ROOT / "models" / "ggml-large-v3-turbo-q5_0.bin"),
    "ffmpeg": str(_PROJECT_ROOT / "bin" / "ffmpeg"),
    "input": str(_HOME / "Documents" / "AI就业课程" / "day01. Python核心语法-数据存储与运算" / "视频"),
    "output": str(_PROJECT_ROOT / "out"),
    "threads": 8,
    "language": "zh",
    # 领域提示词：引导模型优先识别课程常见术语，减少同音字误判
    "prompt": (
        "以下是普通话授课内容，涉及 Python 编程、解释器、开发环境、PyCharm、变量、"
        "数据类型、字符串、注释、标识符、关键字、运算符、输入输出、项目目录结构。"
    ),
    "cpu": False,
}

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".m4v")
WAV_TMP_DIRNAME = ".wav_tmp"
RETRY = 1                      # 单个视频失败后的重试次数
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

_LOG_FH = None                  # 当前日志文件句柄


# ───────────────────────── 小工具 ─────────────────────────
def setup_logger():
    """创建 logs 目录并打开本次运行日志文件（run_<时间戳>.log），同时回显到终端。"""
    global _LOG_FH
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _LOG_FH = open(path, "w", encoding="utf-8")
    return path


def log(msg="", echo=True):
    """写一行日志：落盘 + 可选打印。"""
    if _LOG_FH:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()
    if echo:
        print(msg, flush=True)


def close_logger():
    """关闭日志文件句柄。"""
    global _LOG_FH
    if _LOG_FH:
        _LOG_FH.close()
        _LOG_FH = None


def run_cmd(cmd, timeout=None):
    """以列表方式执行外部命令（正确保留含空格的路径），返回 (返回码, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return (
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )
    except subprocess.TimeoutExpired:
        return -9, "", f"命令执行超时（>{timeout}s）"
    except FileNotFoundError as exc:
        return -2, "", f"命令不存在：{exc}"
    except Exception as exc:                                  # noqa: BLE001 兜底
        return -1, "", f"命令执行异常：{exc}"


def media_duration(ffmpeg, video_path):
    """用 ffmpeg -i 读取媒体时长（秒）；失败返回 None（不依赖 ffprobe）。"""
    _, _, err = run_cmd([ffmpeg, "-hide_banner", "-i", str(video_path)], timeout=120)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not match:
        return None
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def fmt_duration(seconds):
    """把秒数格式化为 mm:ss 或 h:mm:ss。"""
    if seconds is None:
        return "-"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def count_chars(txt_path):
    """统计转写文本字数（忽略所有空白字符，中文按字符计）。"""
    try:
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(re.sub(r"\s", "", text))


def display_width(text):
    """计算字符串在终端中的显示宽度（中日韩全角字符按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text, width):
    """按显示宽度右填充空格，用于对齐表格。"""
    return text + " " * max(0, width - display_width(text))


# ───────────────────────── 核心步骤 ─────────────────────────
def collect_videos(input_dir):
    """列出输入目录下受支持的视频文件，按文件名排序。"""
    videos = [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")
    ]
    return sorted(videos, key=lambda p: p.name)


def video_to_wav(ffmpeg, video_path, wav_path):
    """把视频抽取为 16kHz / 单声道 / 16bit PCM 的 wav。返回 (是否成功, 错误信息)。"""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    rc, _, err = run_cmd(cmd)
    if rc != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
        return False, f"ffmpeg 抽音失败（rc={rc}）：{err.strip()[:400]}"
    return True, ""


def whisper_transcribe(whisper, model, wav_path, out_prefix, threads, language,
                       prompt="", cpu=False):
    """调用 whisper-cli 生成 txt 与 srt。返回 (是否成功, 错误信息)。"""
    cmd = [
        whisper,
        "-m", str(model),
        "-f", str(wav_path),
        "-l", language,
        "-otxt", "-osrt",
        "-t", str(threads),
        "-np",
        "--output-file", str(out_prefix),
    ]
    if prompt:                      # 空字符串表示不传该参数
        cmd.extend(["--prompt", prompt])
    if cpu:                         # GPU 后端异常时降级：禁用 Metal
        cmd.append("-ng")
    rc, out, err = run_cmd(cmd)
    txt_path = Path(str(out_prefix) + ".txt")
    if rc != 0 or not txt_path.exists() or txt_path.stat().st_size == 0:
        detail = (err or out).strip()[:400]
        return False, f"whisper 转写失败（rc={rc}）：{detail}"
    return True, ""


def process_one(video_path, cfg):
    """处理单个视频：抽音 → 转写 → 清理临时 wav。返回结果字典。"""
    base = video_path.stem
    out_dir = cfg["output"] / base
    txt_path = out_dir / f"{base}.txt"
    wav_path = cfg["wav_tmp"] / f"{base}.wav"
    result = {
        "name": video_path.name,
        "status": "失败",
        "duration": None,
        "chars": 0,
        "out_dir": str(out_dir),
        "elapsed": 0.0,
        "error": "",
    }

    # 断点续传：已有非空 txt 直接跳过
    if not cfg["force"] and txt_path.exists() and txt_path.stat().st_size > 0:
        result.update(
            status="跳过",
            duration=media_duration(cfg["ffmpeg"], video_path),
            chars=count_chars(txt_path),
        )
        log(f"[跳过] {video_path.name} —— 已存在结果：{txt_path}")
        return result

    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    result["duration"] = media_duration(cfg["ffmpeg"], video_path)
    log(f"[开始] {video_path.name}（时长 {fmt_duration(result['duration'])}）")

    # 初次尝试 + 失败重试
    for attempt in range(1 + RETRY):
        if attempt:
            log(f"[重试] {video_path.name} —— 第 {attempt} 次重试")
        wav_path.unlink(missing_ok=True)
        ok, err = video_to_wav(cfg["ffmpeg"], video_path, wav_path)
        if ok:
            ok, err = whisper_transcribe(
                cfg["whisper"], cfg["model"], wav_path,
                out_dir / base, cfg["threads"], cfg["language"],
                cfg["prompt"], cfg["cpu"],
            )
        wav_path.unlink(missing_ok=True)      # 无论成败都不残留 wav
        if ok:
            result.update(
                status="成功",
                chars=count_chars(txt_path),
                elapsed=time.time() - started,
            )
            log(f"[完成] {video_path.name} —— {result['chars']} 字，"
                f"耗时 {result['elapsed']:.1f}s → {out_dir}")
            return result
        result["error"] = err

    result["elapsed"] = time.time() - started
    log(f"[失败] {video_path.name} —— {result['error']}")
    return result


def print_summary(results, elapsed_total):
    """打印汇总表：视频名 / 状态 / 时长 / 字数 / 输出目录。"""
    headers = ("视频名", "状态", "时长", "字数", "输出目录")
    rows = [
        (r["name"], r["status"], fmt_duration(r["duration"]),
         str(r["chars"]), r["out_dir"])
        for r in results
    ]
    widths = [
        max([display_width(headers[i])] + [display_width(row[i]) for row in rows])
        for i in range(len(headers))
    ]
    line = "-+-".join("-" * w for w in widths)

    log("")
    log("=" * 24 + " 转写汇总 " + "=" * 24)
    log(" | ".join(pad(headers[i], widths[i]) for i in range(len(headers))))
    log(line)
    for row in rows:
        log(" | ".join(pad(row[i], widths[i]) for i in range(len(headers))))

    success = sum(1 for r in results if r["status"] == "成功")
    skipped = sum(1 for r in results if r["status"] == "跳过")
    failed = sum(1 for r in results if r["status"] == "失败")
    log(line)
    log(f"合计 {len(results)} 个：成功 {success} / 跳过 {skipped} / 失败 {failed}，"
        f"总耗时 {elapsed_total:.1f}s")
    if failed:
        log("失败明细：")
        for r in results:
            if r["status"] == "失败":
                log(f"  - {r['name']}：{r['error']}")
    log("=" * 62)
    return success, skipped, failed


# ───────────────────────── 入口 ─────────────────────────
def build_parser():
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="批量视频转文字（whisper.cpp + ffmpeg），输出 txt 与 srt。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default=DEFAULTS["input"], help="输入视频目录")
    parser.add_argument("--output", default=DEFAULTS["output"], help="输出根目录")
    parser.add_argument("--model", default=DEFAULTS["model"], help="whisper 模型文件路径")
    parser.add_argument("--ffmpeg", default=DEFAULTS["ffmpeg"], help="ffmpeg 可执行文件路径")
    parser.add_argument("--whisper", default=DEFAULTS["whisper"], help="whisper-cli 可执行文件路径")
    parser.add_argument("--force", action="store_true", help="忽略断点续传，强制重转全部视频")
    parser.add_argument("--threads", type=int, default=DEFAULTS["threads"], help="whisper 推理线程数")
    parser.add_argument(
        "-P", "--prompt", default=DEFAULTS["prompt"],
        help="传给 whisper-cli 的领域提示词（--prompt），传空字符串则不启用",
    )
    parser.add_argument(
        "--cpu", action="store_true", default=DEFAULTS["cpu"],
        help="禁用 Metal GPU（whisper-cli -ng），用于 GPU 后端异常时降级到 CPU",
    )
    return parser


def main(argv=None):
    """主流程：解析参数 → 校验环境 → 逐个处理视频 → 打印汇总。"""
    args = build_parser().parse_args(argv)
    log_path = setup_logger()
    started_all = time.time()

    cfg = {
        "whisper": args.whisper,
        "model": args.model,
        "ffmpeg": args.ffmpeg,
        "input": Path(args.input).expanduser(),
        "output": Path(args.output).expanduser(),
        "threads": max(1, args.threads),
        "language": DEFAULTS["language"],
        "prompt": args.prompt,
        "cpu": args.cpu,
        "force": args.force,
    }
    cfg["wav_tmp"] = cfg["output"] / WAV_TMP_DIRNAME

    try:
        log(f"日志文件：{log_path}")
        log(f"输入目录：{cfg['input']}")
        log(f"输出目录：{cfg['output']}")
        log(f"模型文件：{cfg['model']}")
        log(f"线程数：{cfg['threads']}｜语言：{cfg['language']}｜强制重转：{cfg['force']}")
        log(f"CPU 降级（-ng）：{'开启' if cfg['cpu'] else '关闭'}")
        log(f"领域提示词：{cfg['prompt'] if cfg['prompt'] else '（未启用，不传 --prompt）'}")

        # ── 环境校验：尽早暴露路径问题，避免逐个失败 ──
        problems = []
        for label, path in (("whisper-cli", cfg["whisper"]), ("模型文件", cfg["model"])):
            if not Path(path).exists():
                problems.append(f"{label}不存在：{path}")
        if not Path(cfg["ffmpeg"]).exists() and not shutil.which(cfg["ffmpeg"]):
            problems.append(f"ffmpeg 不存在：{cfg['ffmpeg']}")
        if not cfg["input"].is_dir():
            problems.append(f"输入目录不存在：{cfg['input']}")
        if problems:
            for msg in problems:
                log(f"[错误] {msg}")
            return 1

        cfg["output"].mkdir(parents=True, exist_ok=True)
        cfg["wav_tmp"].mkdir(parents=True, exist_ok=True)

        videos = collect_videos(cfg["input"])
        if not videos:
            log(f"[提示] 输入目录下未找到视频文件（支持 {', '.join(VIDEO_EXTS)}）")
            return 0
        log(f"发现 {len(videos)} 个视频，开始处理……")
        log("")

        results = []
        for index, video in enumerate(videos, 1):
            log(f"── ({index}/{len(videos)}) {video.name}")
            try:
                results.append(process_one(video, cfg))
            except KeyboardInterrupt:
                log("[中断] 用户中断，已停止后续任务")
                break
            except Exception as exc:                          # noqa: BLE001 单视频异常不影响后续
                log(f"[失败] {video.name} —— 未预期异常：{exc}")
                results.append({
                    "name": video.name, "status": "失败", "duration": None,
                    "chars": 0, "out_dir": str(cfg["output"] / video.stem),
                    "elapsed": 0.0, "error": f"未预期异常：{exc}",
                })
            log("")

        _, _, failed = print_summary(results, time.time() - started_all)
        return 1 if failed else 0
    finally:
        close_logger()


if __name__ == "__main__":
    sys.exit(main())
