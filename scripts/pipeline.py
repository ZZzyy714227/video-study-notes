#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video-study-notes 流水线脚本：B站视频 → 学习笔记素材。仅标准库。

子命令：
  doctor                  检查依赖工具与通道状态
  prepare <链接|BV号>     拉官方字幕(OpenCLI) + 元数据(bili-cli) + 下载1080P(yt-dlp) + 抽关键帧(ffmpeg)
  analyze [--workdir]     MiMo 批量分析关键帧（每批6帧，显式 --files 模式）
  diagrams <秒数...>      按时间戳抽高清单帧，并可让视觉模型验证内容
  export <笔记.md>        笔记转 PDF：pandoc 生成自包含 HTML（图片内嵌）→ Edge headless 打印
  clean [--workdir]       清空流水线临时工作区（释放视频与临时抽帧）

在项目根目录执行；工作区默认 _pipeline/<BV号>。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 在 Windows 控制台（GBK）下也能安全输出中文
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# MiMo 画面分析脚本（deepseek-vision 技能提供）。默认取 ~/.claude/skills/deepseek-vision/，
# 可用环境变量 MIMO_SCRIPT 覆盖为自定义路径。
MIMO = Path(os.environ.get("MIMO_SCRIPT")
            or Path.home() / ".claude" / "skills" / "deepseek-vision" / "scripts" / "mimo.py")

PROMPT_FRAMES = (
    "视频教学关键帧，文件名序号n对应视频时间(n-1)*30秒。逐帧描述："
    "1)画面文字/术语卡片（照抄原文，用于术语纠错）；"
    "2)图表/示意图/公式内容及标注（曲线图、角度示意、结构图，照抄所有数值和文字）；"
    "3)实物演示或场景内容。按帧号输出。"
)
PROMPT_VERIFY = (
    "文件名是视频时间秒数。对每张图：一句话说明它是什么内容（图示/公式/术语卡/人物画面等），"
    "若有文字标注请照抄关键标注。逐个文件输出。"
)
BATCH = 6
BILI_URL = "https://www.bilibili.com/video/{bv}"


def _strip_img_fences(md_path: Path) -> int:
    """修复笔记里被 ```markdown 代码围栏包裹的图片（PDF 不渲染的元凶）。

    若某段是 ```markdown + 单张图片 + ``` 的三行组合，pandoc 会把它当代码块，
    图片原样显示 ![](...) 文本而不渲染。此处自动摘除围栏，写回文件。
    返回修复的图片张数。
    """
    t = md_path.read_text(encoding="utf-8")
    pat = re.compile(r"```markdown\s*\n(!\[[^\]]*\]\([^)]*\))\s*\n```")
    fixed, n = pat.subn(r"\1", t)
    if n:
        md_path.write_text(fixed, encoding="utf-8")
        print(f"⚠️ 检测到 {n} 张图片被 ```markdown 围栏包裹（会导致 PDF 不渲染），已自动修复 → {md_path.name}")
    return n


def run(cmd, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # Windows: subprocess 不认 PATH 里的 .cmd/.bat（npm 全局装的可执行文件），
    # 先解析真实路径，.cmd 用 cmd /c 包裹
    resolved = shutil.which(cmd[0])
    if resolved:
        if resolved.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c"] + cmd
        else:
            cmd = [resolved] + cmd[1:]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def tool_ok(name):
    return shutil.which(name) is not None


def parse_bv(text):
    m = re.search(r"BV[0-9A-Za-z]+", text or "")
    if not m:
        sys.exit("❌ 无法解析 BV 号：请传 B站视频链接或 BV 号")
    return m.group(0)


def default_workdir():
    pipe = Path("_pipeline")
    subs = sorted([p for p in pipe.iterdir() if p.is_dir()]) if pipe.exists() else []
    if len(subs) == 1:
        return subs[0]
    if not subs:
        sys.exit("❌ _pipeline 下没有工作目录：请先运行 prepare")
    sys.exit("❌ _pipeline 下有多个工作目录，请用 --workdir 指定："
             + ", ".join(p.name for p in subs))


def cmd_doctor(_):
    checks = [
        ("python", sys.executable),
        ("yt-dlp", tool_ok("yt-dlp")),
        ("ffmpeg", tool_ok("ffmpeg")),
        ("opencli", tool_ok("opencli")),
        ("bili (bili-cli)", tool_ok("bili")),
        ("mimo.py (可选: 仅纯文本模型需要)", MIMO.exists()),
    ]
    for name, ok in checks:
        print(f"{'✅' if ok else '❌'} {name}")
    if not tool_ok("opencli"):
        print("  opencli 安装: npm install -g @jackwener/opencli")
    if not tool_ok("bili"):
        print("  bili-cli 安装: agent-reach install --system --channels=bilibili")
    if not MIMO.exists():
        print("  mimo.py 未安装: 若当前 Agent 具备多模态读图能力（如 Gemini/Claude 3.5+/GPT-4o），可直接读图，无需 MiMo")


def _download_bili_direct(bv: str, out_file: Path) -> bool:
    """B站风控 412 时通过官方 HTML5 接口 + ffmpeg 流媒体直链下载 1080P/720P MP4 兜底。"""
    try:
        import ssl, urllib.request, json, subprocess
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://www.bilibili.com/video/{bv}"
        }
        view_req = urllib.request.Request(f"https://api.bilibili.com/x/web-interface/view?bvid={bv}", headers=headers)
        with urllib.request.urlopen(view_req, context=ctx) as r:
            v_data = json.loads(r.read().decode("utf-8"))
        cid = v_data["data"]["cid"]
        play_req = urllib.request.Request(f"https://api.bilibili.com/x/player/playurl?bvid={bv}&cid={cid}&qn=80&platform=html5", headers=headers)
        with urllib.request.urlopen(play_req, context=ctx) as r:
            p_data = json.loads(r.read().decode("utf-8"))
        video_url = p_data["data"]["durl"][0]["url"]
        
        ffmpeg_headers = f"Referer: https://www.bilibili.com/video/{bv}\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
        r = subprocess.run([
            "ffmpeg", "-y", "-headers", ffmpeg_headers, "-i", video_url,
            "-c", "copy", str(out_file)
        ], capture_output=True)
        return out_file.exists() and out_file.stat().st_size > 1024 * 1024
    except Exception as e:
        print(f"  ⚠️ 直链下载兜底异常: {e}")
        return False


def cmd_prepare(args):
    for name in ["yt-dlp", "ffmpeg", "opencli", "bili"]:
        if not tool_ok(name):
            sys.exit(f"❌ 缺少依赖 {name}，先运行 pipeline.py doctor 查看安装提示")
    bv = parse_bv(args.input)
    workdir = Path(args.workdir) if args.workdir else Path("_pipeline") / bv
    (workdir / "frames").mkdir(parents=True, exist_ok=True)
    print(f"工作区: {workdir}")

    # [1/4] 字幕
    print("[1/4] OpenCLI 拉取官方字幕 ...")
    code, out, err = run(["opencli", "bilibili", "subtitle", bv, "--page", str(args.page), "-f", "yaml"], timeout=120)
    if code != 0:
        print("⚠️ 字幕拉取失败（OpenCLI 扩展未连接？）：")
        print("  1. 保持 Edge/Chrome 打开且已登录 B站")
        print("  2. opencli doctor 确认 Extension: connected")
        print("  3. 必要时 opencli daemon restart")
        print("  4. 兜底：MiMo ASR 转写（见 SKILL.md「兜底」）")
    else:
        (workdir / "字幕.yaml").write_text(out, encoding="utf-8")
        lines = [ln.strip()[len("content:"):].strip() for ln in out.splitlines()
                 if ln.strip().startswith("content:")]
        (workdir / "字幕.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"  ✅ {len(lines)} 句 → 字幕.yaml / 字幕.txt")

    # [2/4] 元数据
    print("[2/4] bili-cli 拉取元数据 ...")
    code, out, err = run(["bili", "video", bv], timeout=120)
    (workdir / "元数据.yaml").write_text(out if code == 0 else f"失败:\n{err}",
                                         encoding="utf-8")
    print("  ✅ 元数据.yaml" if code == 0 else f"  ⚠️ 失败（不影响后续）: {err[:120]}")

    # [3/4] 下载
    if args.no_download:
        print("[3/4] 跳过下载（--no-download）")
    else:
        print("[3/4] yt-dlp 下载 1080P ...")
        code, out, err = run(
            ["yt-dlp", "--playlist-items", str(args.page),
<<<<<<< HEAD
             "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best",
             "--merge-output-format", "mp4",
             "-o", str(workdir / "视频.%(ext)s"), BILI_URL.format(bv=bv)],
=======
              "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best",
              "--merge-output-format", "mp4",
              "-o", str(workdir / "视频.%(ext)s"), BILI_URL.format(bv=bv)],
>>>>>>> ba43453 (feat: 支持原生多模态直读、Anthropic 风格排版与轻量化资产清理)
            timeout=1800)
        if code != 0:
            print("  ⚠️ yt-dlp 下载遇到风控，自动切换 B站直链流媒体下载通道 ...")
            ok = _download_bili_direct(bv, workdir / "视频.mp4")
            if ok:
                print("  ✅ 视频.mp4（直链流媒体通道）")
            else:
                print(f"  ❌ 下载失败: {err[-200:]}")
        else:
            print("  ✅ 视频.mp4")

    # [4/4] 关键帧
    mp4 = next(workdir.glob("视频.*"), None)
    if mp4:
        print("[4/4] ffmpeg 抽关键帧（每30秒） ...")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
             "-vf", "fps=1/30,scale=960:-2", "-q:v", "4",
             str(workdir / "frames" / "f_%06d.jpg")], timeout=600)
        n = len(list((workdir / "frames").glob("*.jpg")))
        print(f"  ✅ {n} 帧")
    print(f"\n下一步: 结合字幕与关键帧提取图解 (python pipeline.py diagrams <timestamps> --no-mimo)")


def cmd_analyze(args):
    workdir = Path(args.workdir) if args.workdir else default_workdir()
    frames = sorted((workdir / "frames").glob("*.jpg"))
    if not frames:
        sys.exit(f"❌ {workdir}/frames 为空：请先 prepare")
    outdir = workdir / "frame_analysis"
    outdir.mkdir(exist_ok=True)
    total_cost = 0.0
    for i in range(0, len(frames), BATCH):
        batch = frames[i:i + BATCH]
        idx = i // BATCH
        cmd = [sys.executable, str(MIMO), "analyze"]
        for f in batch:
            cmd += ["--files", str(f)]
        cmd += [args.prompt or PROMPT_FRAMES, "--max-tokens", "3000", "--timeout", "300"]
        print(f"batch{idx}: {len(batch)} 帧分析中 ...", flush=True)
        try:
            code, out, err = run(cmd, timeout=700)
        except subprocess.TimeoutExpired:
            (outdir / f"batch{idx}.json").write_text(
                '{"ok": false, "content": "TIMEOUT", "error": "subprocess timeout"}',
                encoding="utf-8")
            print(f"  ❌ batch{idx} 超时（700s），跳过继续")
            continue
        json_path = outdir / f"batch{idx}.json"
        json_path.write_text(out if out else err, encoding="utf-8")
        try:
            d = json.loads(out)
            cost = d.get("cost_cny") or 0
            total_cost += cost
            mark = "✅" if d.get("ok") else "❌"
            print(f"  {mark} batch{idx} truncated={d.get('truncated')} cost={cost:.4f}")
            if d.get("truncated"):
                print("  ⚠️ 该批被截断：单独重跑并提高 --max-tokens")
        except Exception:
            print(f"  ❌ batch{idx} 输出非 JSON: {out[:100]}")
    parts = []
    for p in sorted(outdir.glob("batch*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            parts.append(f"=== {p.name} ===\n{d.get('content', d.get('error'))}\n")
        except Exception:
            pass
    summary = workdir / "画面分析汇总.txt"
    summary.write_text("\n".join(parts), encoding="utf-8")
    print(f"汇总 → {summary}；本次 MiMo 约 ¥{total_cost:.4f}")


def cmd_diagrams(args):
    workdir = Path(args.workdir) if args.workdir else default_workdir()
    mp4 = next(workdir.glob("视频.*"), None)
    if not mp4:
        sys.exit("❌ 工作区没有 视频.mp4：请先 prepare")
    ddir = workdir / "diagrams"
    ddir.mkdir(exist_ok=True)
    for t in args.timestamps:
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(mp4),
             "-frames:v", "1", "-q:v", "1", str(ddir / f"d_{t}s.png")], timeout=300)
    pngs = sorted(ddir.glob("*.png"))
    if not pngs:
        sys.exit("❌ 未抽到帧")
    print(f"✅ 已抽取 {len(pngs)} 张高清图解 → {ddir}")
    if getattr(args, "no_mimo", False) or not MIMO.exists():
        print("  💡 多模态直读模式：跳过 MiMo 外部调用，由 Agent 直接查看图像文件并验证")
        return
    cmd = [sys.executable, str(MIMO), "analyze"]
    for f in pngs:
        cmd += ["--files", str(f)]
    cmd += [PROMPT_VERIFY, "--max-tokens", "2000", "--timeout", "300"]
    code, out, err = run(cmd, timeout=330)
    (ddir / "验证.txt").write_text(out if out else err, encoding="utf-8")
    try:
        print(json.loads(out).get("content", out[:500]))
    except Exception:
        print(out[:500])


NOTE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap');

/* ===== Anthropic 视觉风格（Claude 公司） ===== */
:root{
  --clay:#CC785C;          /* Anthropic clay / coral */
  --clay-deep:#9E5A41;
  --clay-tint:#F3E3DA;
  --clay-tint-2:#EBD3C7;
  --cream:#FBF6EF;         /* warm paper */
  --paper:#FFFFFF;
  --ink:#2A2420;           /* warm near-black */
  --ink-soft:#5C524B;
  --line:#E7DCD0;
  --serif:"Fraunces",Georgia,"Songti SC","SimSun",serif;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif;
}

* { -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
@page { size: A4; margin: 16mm 15mm 22mm 15mm; }
body { font-family: var(--sans); font-size: 10.5pt; line-height: 1.78; color: var(--ink);
       background: var(--cream); -webkit-font-smoothing: antialiased; }
p { margin: 0.55em 0; }
li { margin: 0.3em 0; }
strong { color: var(--clay-deep); font-weight: 600; }
em { color: var(--ink-soft); }
a { color: var(--clay-deep); text-decoration: none; border-bottom: 1px solid var(--clay-tint-2); }
ul, ol { margin: 0.4em 0; padding-left: 1.6em; }
li::marker { color: var(--clay); }
hr { border: none; height: 2px; background: var(--clay); margin: 1.6em 0; opacity: .35; }

/* ===== 标题（人文衬线 Fraunces + CJK 衬线回退） ===== */
h1, h2, h3, h4 { font-family: var(--serif); color: var(--ink); font-weight: 500; }
h1 { font-size: 23pt; line-height: 1.18; margin: 0 0 0.4em; letter-spacing: .01em; }
h2 { font-size: 15.5pt; margin: 1.6em 0 0.5em; padding: 0 0 6px;
     border-bottom: 2px solid var(--line); page-break-after: avoid; }
h2::before { content: ""; display: inline-block; width: 6px; height: 0.95em;
             background: var(--clay); border-radius: 3px; margin-right: 9px; vertical-align: -2px; }
h3 { font-size: 12.5pt; margin: 1.2em 0 0.35em; page-break-after: avoid; color: var(--clay-deep); }
h4 { font-size: 11pt; margin: 1em 0 0.3em; color: var(--ink); }

/* ===== 封面（h1 + 导言 blockquote） ===== */
.cover { padding: 4px 0 20px; border-bottom: 2px solid var(--clay); margin: 0 0 1.4em; }
.cover h1 { color: var(--ink); font-size: 25pt; }
.cover blockquote { border: none; background: none; padding: 10px 0 0; margin: 0.6em 0 0;
                    color: var(--ink-soft); font-size: 9.5pt; line-height: 1.7;
                    font-family: var(--sans); font-style: normal; }
.cover blockquote p { margin: 0.15em 0; }
.cover a { color: var(--clay-deep); }

/* ===== 引用块（要点卡片 / 引语） ===== */
blockquote { border-left: 3px solid var(--clay); background: var(--clay-tint);
             border-radius: 0 10px 10px 0; margin: 0.9em 0; padding: 8px 16px;
             color: var(--ink); page-break-inside: avoid; font-family: var(--serif);
             font-size: 11.5pt; font-style: italic; line-height: 1.55; }
blockquote p { margin: 0.3em 0; }

/* ===== 标注徽章（【前置】【推理链】…）— 暖色协调九色 ===== */
.tag { display: inline-block; font-size: 8pt; font-weight: 600; padding: 1px 8px;
       border-radius: 999px; margin: 0 2px; vertical-align: 1px; letter-spacing: .3px; }
.tag-qz { background: #E8E0D5; color: #7A6A58; }   /* 前置 */
.tag-dz { background: #F0D9CC; color: #9E5A41; }   /* 定义 */
.tag-zj { background: #E4E8DC; color: #5E6B4B; }   /* 对照 */
.tag-tz { background: #F2E7C9; color: #927117; }   /* 拓展 */
.tag-cz { background: #EBD3C7; color: #A23E22; }   /* 查证 */
.tag-tl { background: #ECD9C9; color: #B5673A; }   /* 推理链 */
.tag-ys { background: #E2DDE3; color: #6B5A78; }   /* 演算 */
.tag-al { background: #F3DCDA; color: #B0402E; }   /* 案例 */
.tag-ks { background: #ECE3D2; color: #8A6D3B; }   /* 口述 */

/* ===== 表格（暖色卡片式） ===== */
table { border-collapse: separate; border-spacing: 0; width: 100%; margin: 0.9em 0;
        font-size: 9.2pt; border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(120,80,55,.06); }
th { background: var(--clay-deep); color: #fff; font-weight: 600; text-align: left; padding: 6px 10px; }
td { border-top: 1px solid var(--line); padding: 5px 10px; vertical-align: top; }
tr:nth-child(even) td { background: #F7F0E9; }

/* ===== 代码 ===== */
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: #F1E8DF;
       padding: 1px 5px; border-radius: 5px; border: 1px solid var(--line); color: var(--ink); }
pre { background: #F6F0E9; border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px;
      overflow-x: auto; font-size: 9pt; line-height: 1.6; page-break-inside: avoid; }
pre code { background: none; border: none; padding: 0; }

/* ===== 图解 ===== */
figure { margin: 0.9em 0; page-break-inside: avoid; }
figure img { width: 100%; height: auto; display: block; margin: 0 auto;
<<<<<<< HEAD
             border-radius: 10px; border: 1px solid #e2e8f0;
             box-shadow: 0 4px 14px rgba(30,41,59,.10); }
figcaption { font-size: 8.5pt; color: #64748b; text-align: center; margin-top: 4px; }

/* ===== 覆盖 pandoc 默认模板的 36em 版心，让内容占满 A4 版心 ===== */
body { max-width: none !important; padding-left: 2mm; padding-right: 2mm; }

/* ===== 自测答案 ===== */
.answer { border: 1px solid #c7d6ef; border-radius: 10px; margin: 1em 0; padding: 10px 14px;
          background: #fbfdff; page-break-inside: avoid; }
.answer-head { font-size: 9.5pt; font-weight: 700; color: #1e4e8c; margin-bottom: 6px;
               background: #e8f0fd; border-radius: 6px; padding: 3px 10px; display: inline-block; }
=======
             border-radius: 10px; border: 1px solid var(--line); }
figcaption { font-size: 8.5pt; color: var(--ink-soft); text-align: center; margin-top: 4px; }

/* ===== 覆盖 pandoc 默认模板的 36em 版心，让内容占满 A4 版心 ===== */
body { max-width: none !important; padding-left: 2mm; padding-right: 2mm; }
>>>>>>> ba43453 (feat: 支持原生多模态直读、Anthropic 风格排版与轻量化资产清理)

/* ===== 自测答案盒 ===== */
.answer { border: 1px solid var(--clay-tint-2); border-radius: 10px; margin: 1em 0; padding: 10px 14px;
          background: #FCF7F1; page-break-inside: avoid; }
.answer-head { font-size: 9.5pt; font-weight: 700; color: var(--clay-deep); margin-bottom: 6px;
               background: var(--clay-tint); border-radius: 6px; padding: 3px 10px; display: inline-block; }

/* ===== 页脚：改由 _stamp_pdf_footer() 在 PDF 后处理阶段补画进下边距带 ===== */
body { padding-bottom: 8mm; }
</style>
"""

# pandoc 路径检测
PANDOC = (os.environ.get("PANDOC_PATH")
          or shutil.which("pandoc")
          or str(Path.home() / "AppData" / "Local" / "Pandoc" / "pandoc.exe"))

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_edge():
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


TAG_NAMES = ["前置", "对照", "定义", "拓展", "查证", "推理链", "演算", "案例", "口述"]
TAG_CLASS = {"前置": "qz", "对照": "zj", "定义": "dz", "拓展": "tz", "查证": "cz",
             "推理链": "tl", "演算": "ys", "案例": "al", "口述": "ks"}


def _polish_html(html: str) -> str:
    """pandoc HTML 后处理：标注徽章、图注、答案盒、封面块。"""
    # 1) 【标注】→ 彩色徽章
    for name, cls in TAG_CLASS.items():
        html = re.sub(rf"【{name}([^】]*)】",
                      lambda m, c=cls: f'<span class="tag tag-{c}">{m.group(0)[1:-1]}</span>',
                      html)
<<<<<<< HEAD
    # 2) 图片图注兜底：pandoc implicit_figures 已为"独立成段的图片"生成 <figure>+<figcaption>
    #    （图注=alt），这里只处理"整段仅一张图但 pandoc 未转 figure"的罕见情况，
    #    绝不再包一层，否则 figure/figcaption 双份、图注重复。
=======
    # 2) 图片图注兜底
>>>>>>> ba43453 (feat: 支持原生多模态直读、Anthropic 风格排版与轻量化资产清理)
    def img_wrap(m):
        tag = m.group(1)
        am = re.search(r'alt="([^"]*)"', tag)
        if not am or not am.group(1):
            return m.group(0)
        return f'<figure>{tag}<figcaption>{am.group(1)}</figcaption></figure>'
    html = re.sub(r'<p>(<img[^>]*>)</p>', img_wrap, html)
<<<<<<< HEAD
    # 3) <details><summary> → 答案盒（PDF 中 details 默认折叠，无法展开）
=======
    # 3) <details><summary> → 答案盒
>>>>>>> ba43453 (feat: 支持原生多模态直读、Anthropic 风格排版与轻量化资产清理)
    def det_repl(m):
        head = m.group(1).strip() or "答案"
        body = m.group(2).strip()
        return f'<div class="answer"><span class="answer-head">💡 {head}</span>{body}</div>'
    html = re.sub(r'<details>\s*<summary>(.*?)</summary>\s*(.*?)\s*</details>',
                  det_repl, html, flags=re.S)
    # 4) h1 + 紧跟的引用块 → 封面块
    def cover_repl(m):
        return f'<div class="cover">{m.group(1)}{m.group(2)}</div>'
    html = re.sub(r'(<h1[^>]*>.*?</h1>)\s*(<blockquote>.*?</blockquote>)',
                  cover_repl, html, flags=re.S)
    return html


def _stamp_pdf_footer(pdf_path: Path):
    """在每页 @page 下边距带补画页脚（分隔线 + 标题 · 页脚文字）。"""
    try:
        import fitz
    except ImportError:
        return
    doc = fitz.open(pdf_path)
    lines = doc[0].get_text().strip().splitlines()
    title = lines[0].strip() if lines else "学习笔记"
    W, H = doc[0].rect.width, doc[0].rect.height
    y_line = H - 45
    line_c, ink_c = (0.80, 0.73, 0.65), (0.46, 0.41, 0.36)
    footer_note = os.environ.get("NOTE_FOOTER", "video-study-notes")
    text = f"{title} · {footer_note}"
    for page in doc:
        page.draw_line(fitz.Point(43, y_line), fitz.Point(W - 43, y_line),
                       color=line_c, width=0.6)
        page.insert_textbox(fitz.Rect(43, y_line + 3, W - 43, y_line + 15),
                            text, fontsize=7.5, fontname="china-s",
                            color=ink_c, align=fitz.TEXT_ALIGN_CENTER)
    tmp = pdf_path.with_suffix(".__stamp__.pdf")
    doc.save(str(tmp), garbage=3, deflate=True)
    doc.close()
    tmp.replace(pdf_path)


def cmd_export(args):
    md = Path(args.input)
    if not md.exists():
        sys.exit(f"❌ 找不到笔记: {md}")
    # 导出前自动修复"图片被代码围栏包裹"
    _strip_img_fences(md)
    if not Path(PANDOC).exists():
        sys.exit(f"❌ pandoc 未找到: {PANDOC}")
    edge = _find_edge()
    if not edge:
        sys.exit("❌ 未找到 Edge（PDF 渲染引擎）")

    if args.out:
        out = Path(args.out)
    else:
        # 默认输出：笔记若位于"主题文件夹"（含 素材/ 或 插图/ 子目录），
        # PDF 浮到其父目录（系列根/领域根），以主题文件夹名命名，避免 PDF 埋在多层子目录。
        parent = md.parent
        is_topic_dir = (parent / "素材").is_dir() or (parent / "插图").is_dir()
        if is_topic_dir:
            out = parent.parent / f"{parent.name}.pdf"
        else:
            out = md.with_suffix(".pdf")
    out = out.resolve()
    tmpdir = Path(os.environ.get("TEMP", ".")).resolve()
    html = tmpdir / f"_note_{md.stem}.html"
    style = tmpdir / f"_note_{md.stem}.style.html"
    edge_profile = tmpdir / f"_edge_pdf_{md.stem}"

    # 1) pandoc: markdown → 自包含 HTML（图片/样式内嵌，公式走 MathML）
    style.write_text(NOTE_CSS, encoding="utf-8")
    cmd = [PANDOC, "-s", "-H", str(style), "--embed-resources", "--mathml",
           "--resource-path", str(md.parent), "-o", str(html), str(md)]
    code, out_txt, err = run(cmd, timeout=600)
    if code != 0 or not html.exists():
        sys.exit(f"❌ pandoc 转换失败: {err[-300:]}")
    if "Could not fetch resource" in out_txt + err:
        print("⚠️ 部分图片未嵌入，检查笔记中的图片相对路径（相对于笔记所在目录）")

    # 2) 后处理：徽章 / 图注 / 答案盒 / 封面块
    html.write_text(_polish_html(html.read_text(encoding="utf-8")), encoding="utf-8")

    # 3) Edge headless 打印 PDF
    pdf_cmd = [edge, "--headless=new", "--disable-gpu",
               "--no-pdf-header-footer", f"--user-data-dir={edge_profile}",
               f"--print-to-pdf={out}", html.resolve().as_uri()]
    code, out_txt, err = run(pdf_cmd, timeout=300)
    if code != 0:
        sys.exit(f"❌ Edge 打印失败: {err[-300:]}")
    if not out.exists():
        sys.exit("❌ PDF 未生成（Edge 无输出）")

    # 4) 页脚盖章
    _stamp_pdf_footer(out)

    style.unlink(missing_ok=True)
    html.unlink(missing_ok=True)
    shutil.rmtree(edge_profile, ignore_errors=True)
    size = out.stat().st_size / 1024
    print(f"✅ PDF: {out} ({size:.0f} KB)")


def cmd_clean(args):
    workdir = Path(args.workdir) if args.workdir else default_workdir()
    shutil.rmtree(workdir)
    print(f"✅ 已清空流水线临时工作区 {workdir}（大文件视频与关键帧已清除）")


def _add_workdir(p):
    p.add_argument("--workdir", help="工作区路径（默认 _pipeline/<BV号> 或自动识别）")


def main():
    ap = argparse.ArgumentParser(description="B站视频 → 学习笔记 流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="检查依赖与通道").set_defaults(func=cmd_doctor)
    p_prep = sub.add_parser("prepare", help="拉字幕+元数据+下载+抽帧")
    p_prep.add_argument("input", help="B站视频链接或 BV 号")
    p_prep.add_argument("--page", type=int, default=1, help="分P选集序号（1 开始），多P视频取该集；默认 1")
    p_prep.add_argument("--no-download", action="store_true", help="跳过视频下载")
    _add_workdir(p_prep)
    p_prep.set_defaults(func=cmd_prepare)
    p_an = sub.add_parser("analyze", help="MiMo 批量分析关键帧")
    p_an.add_argument("--prompt", help="自定义画面分析提示词")
    _add_workdir(p_an)
    p_an.set_defaults(func=cmd_analyze)
    p_dg = sub.add_parser("diagrams", help="按时间戳抽高清单帧并验证")
    p_dg.add_argument("timestamps", type=int, nargs="+", help="视频秒数，如 300 420")
    p_dg.add_argument("--no-mimo", action="store_true", help="跳过 MiMo 验证（多模态模型直读模式）")
    _add_workdir(p_dg)
    p_dg.set_defaults(func=cmd_diagrams)
    p_clean = sub.add_parser("clean", help="清空流水线工作区")
    _add_workdir(p_clean)
    p_clean.set_defaults(func=cmd_clean)
    p_exp = sub.add_parser("export", help="笔记转 PDF（pandoc→HTML→Edge）")
    p_exp.add_argument("input", help="Markdown 笔记路径")
    p_exp.add_argument("--out", help="PDF 输出路径（默认与笔记同名）")
    p_exp.set_defaults(func=cmd_export)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
