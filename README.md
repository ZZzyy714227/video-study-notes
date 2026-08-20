# video-study-notes

一个 **Claude Code 技能（Agent Skill）**：把 B站/YouTube 视频自动加工成结构化学习笔记，并一键导出精美 PDF。

输入一个视频链接，输出一份带知识导图、推理链、演算、自测题、术语表的 Markdown 笔记 + 杂志风 PDF——整套加工遵循"三层加工模型"（L1 还原 / L2 填充 / L3 查证），确保笔记不是结论表，而是保留了视频的推理过程。

## 特性

- **自动流水线**：字幕 → 元数据 → 1080P 下载 → 关键帧 → MiMo 逐帧画面分析 → 图解提取，一条命令走完素材准备
- **三层加工模型**：L1 还原视频推理链（按讲述顺序、纠错 AI 字幕乱码）→ L2 补前置/对照/定义 → L3 联网查证数值与术语
- **精美 PDF**：pandoc → 自包含 HTML → Edge headless 打印，A4 杂志风样式（封面块、彩色标注徽章、卡片表格、圆角图解、页脚），中文完美
- **PDF 浮出规则**：导出时 PDF 自动放到主题文件夹的父目录（系列根/领域根），避免埋在多层子目录
- **仅标准库**：`pipeline.py` 纯 Python 标准库实现，零 Python 依赖

## 依赖工具

| 工具 | 用途 | 说明 |
|---|---|---|
| [OpenCLI](https://github.com/jackwener/opencli) | B站官方字幕（免费、带时间轴） | 浏览器扩展 + CLI |
| bili-cli | B站视频元数据 | 也可经 [agent-reach](https://github.com/Panniantong/agent-reach) 安装 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 1080P 视频下载 | B站偶发 412 风控，升级后重试 |
| ffmpeg | 抽关键帧 / 提取音频 | |
| deepseek-vision 技能的 `mimo.py` | 关键帧画面分析（视觉模型，按量付费约 ¥0.1/期） | 路径可用环境变量 `MIMO_SCRIPT` 覆盖 |
| pandoc + Edge headless | 笔记 → PDF | pandoc 路径可用环境变量 `PANDOC_PATH` 覆盖 |

## 安装

1. 将本技能放入你的 Claude Code 项目：
   ```
   .claude/skills/video-study-notes/
   ├── SKILL.md
   └── scripts/pipeline.py
   ```
2. 安装依赖工具（见上表），可用 `pipeline.py doctor` 检查：
   ```bash
   python .claude/skills/video-study-notes/scripts/pipeline.py doctor
   ```

## 快速开始

在项目根目录执行：

```bash
# 1. 拉素材（字幕/元数据/1080P/每30秒关键帧）→ 工作区 _pipeline/<BV号>/
python .claude/skills/video-study-notes/scripts/pipeline.py prepare "<B站链接或BV号>"

# 2. MiMo 分批分析关键帧（每批 6 帧）
python .claude/skills/video-study-notes/scripts/pipeline.py analyze

# 3. 定位图解时间点后抽高清单帧并验证
python .claude/skills/video-study-notes/scripts/pipeline.py diagrams 300 420 555

# 4. 按三层加工模型整理笔记（详见 SKILL.md）

# 5. 一键导出 PDF（默认浮到主题文件夹父目录）
python .claude/skills/video-study-notes/scripts/pipeline.py export "<笔记库>/<领域>/<主题>/学习笔记.md"

# 6. 收尾归档素材并清理工作区
python .claude/skills/video-study-notes/scripts/pipeline.py clean
```

## 三层加工模型

| 层 | 动作 | 目标 |
|---|---|---|
| **L1 还原** | 字幕纠错（以画面官方卡片为最高证据，B站 AI 字幕英文术语乱码严重）→ 去重 → 保留视频讲述顺序 | 100% 覆盖视频知识点，零失真 |
| **L2 填充** | 补前置知识 / 平行对照 / 定义补全 / 延伸拓展 | 让笔记成为独立可学的素材 |
| **L3 验证** | 数值、标准、专有名词联网查证后写入 | 防止凭记忆编造工程参数 |

笔记标注规范：`【前置】【对照】【定义】【拓展】【查证】【推理链】【演算】【案例】【口述】`，每条结论配推理链，每个数值配演算。完整模板见 `SKILL.md`。

## 隐私与版权

- 本技能下载的字幕、视频仅用于**个人学习**，请勿再分发或商用。
- 笔记内容基于原视频作者的内容整理，公开笔记时请在 README / 文中注明出处。
- 项目工作区不包含任何 API 密钥；MiMo 的计费凭据由你的本地 deepseek-vision 技能配置，不经过本技能。

## 致谢

本技能重度依赖以下优秀开源项目：
- [OpenCLI](https://github.com/jackwener/opencli) —— B站字幕拉取
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) —— 视频下载
- [agent-reach](https://github.com/Panniantong/agent-reach) —— 多平台检索能力（bili-cli 等）
- MiMo 视觉模型（deepseek-vision 技能）

## License

[MIT](LICENSE)
