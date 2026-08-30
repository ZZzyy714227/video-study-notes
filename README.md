# video-study-notes

一个 **Agent 视频学习技能（Skill）**：把 B站/YouTube 视频自动加工成高密度结构化学习笔记，并一键导出 Anthropic 风格精美 PDF。

输入一个视频链接，输出一份带知识导图、推理链、演算、自测题、术语表的 Markdown 笔记 + 杂志风 PDF——整套加工遵循"三层加工模型"（L1 还原 / L2 填充 / L3 查证），确保笔记不是生硬的结论表，而是保留了视频完整的推理演进过程。

---

## ✨ 特性

- **全流程流水线**：字幕拉取 → 元数据获取 → 1080P 下载 → 关键帧截取 → 视觉图解提取，一条命令完成素材准备。
- **原生多模态 + 视觉模型双通道**：
  - **原生多模态直读（推荐）**：支持具备多模态视觉能力的 Agent 直接审视关键帧图像，0 外部 API 成本与零等待；
  - **MiMo 批量辅助**：在纯文本 LLM 环境下通过 deepseek-vision 批量结构化转写画面。
- **三层加工模型**：
  - **L1 还原**：以视频原图解为最高证据纠错字幕，保留原片叙事故事线，深度还原 3 步以上【推理链】与数值【演算】；
  - **L2 填充**：补齐背景【前置】、跨领域【对照】与中英【定义】；
  - **L3 查证**：工程数值、行业标准与专有名词联网严谨查证。
- **Anthropic 暖色调杂志风 PDF**：
  - 基于 pandoc + Edge headless 打印，自包含单文件（图片与 MathML 公式内嵌）；
  - 暖奶油纸底、陶土珊瑚强调色、人文衬线标题与精致标注徽章；
  - 导出时 PDF 自动浮到主题文件夹的父目录（系列根/领域根），避免埋在多层子目录。
- **轻量自包含资产规范**：
  - 核心资产仅保留 `学习笔记.md`、`插图/` 与导出的 `*.pdf`；
  - 大体积视频（.mp4）与临时抽帧随 `pipeline.py clean` 彻底清理，不占用磁盘与云同步空间。
- **纯标准库实现**：`pipeline.py` 零第三方 Python 依赖。

---

## 🛠️ 依赖工具

| 工具 | 用途 | 说明 |
|---|---|---|
| [OpenCLI](https://github.com/jackwener/opencli) | B站官方字幕（免费、带时间轴） | 浏览器扩展 + CLI |
| bili-cli | B站视频元数据 | 也可经 [agent-reach](https://github.com/Panniantong/agent-reach) 安装 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 1080P 视频下载 | 支持直链流媒体兜底 |
| ffmpeg | 抽关键帧 / 提取高清单帧 | |
| pandoc + Edge headless | 笔记 → 精美 PDF | 支持通过 `PANDOC_PATH` 环境变量自定义 |
| PyMuPDF (*可选*) | PDF 下边距带页脚盖章 | `pip install pymupdf` |
| MiMo (*可选*) | 纯文本模型视觉分析兜底 | deepseek-vision 技能 |

---

## 📦 安装与配置

1. 将本技能放入你的 Agent 技能目录（如 `.claude/skills/`）：
   ```
   .claude/skills/video-study-notes/
   ├── SKILL.md
   └── scripts/
       └── pipeline.py
   ```
2. 检查依赖状态：
   ```bash
   python .claude/skills/video-study-notes/scripts/pipeline.py doctor
   ```

---

## 🚀 快速开始

在项目根目录下执行：

```bash
# 1. 准备素材（字幕 / 元数据 / 1080P / 每 30 秒关键帧）→ 工作区 _pipeline/<BV号>/
python .claude/skills/video-study-notes/scripts/pipeline.py prepare "<B站链接或BV号>"

# 2. 画面分析（原生多模态 Agent 可跳过此步直接读图；纯文本 Agent 走 MiMo 批量分析）
python .claude/skills/video-study-notes/scripts/pipeline.py analyze

# 3. 定位图解时间点后抽取 1080P 高清单帧
python .claude/skills/video-study-notes/scripts/pipeline.py diagrams 300 420 555 --no-mimo

# 4. 按三层加工模型系统化编写笔记（详见 SKILL.md）

# 5. 一键导出 Anthropic 风格精美 PDF（默认输出至主题父目录）
python .claude/skills/video-study-notes/scripts/pipeline.py export "<你的笔记库>/<领域>/<主题>/学习笔记.md"

# 6. 收尾归档（笔记与插图落盘，清空流水线临时视频与关键帧）
python .claude/skills/video-study-notes/scripts/pipeline.py clean
```

---

## 📐 三层加工模型

| 层级 | 核心动作 | 质量目标 |
|---|---|---|
| **L1 还原** | 字幕纠错（画面图解为最高证据）→ 去重 → **保留视频叙事故事线** | 100% 覆盖视频要点，严谨还原【推理链】与【演算】 |
| **L2 填充** | 补全前置概念【前置】 / 平行映射【对照】 / 规范术语【定义】 / 延伸拓展【拓展】 | 让笔记脱离视频即可独立精读 |
| **L3 查证** | 数值、公差、工程标准【查证】 | 杜绝凭空编造工程数据 |

---

## 📄 隐私与协议

- 本技能下载的字幕、视频仅用于**个人学习研究**，请遵守版权规范。
- 笔记内容基于原视频作者创作整理，公开分享时请务必注明作者与原片出处。
- 开源协议：[MIT](LICENSE)
