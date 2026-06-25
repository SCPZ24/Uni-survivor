---
name: exam-cram-from-slides
description: 把大学课程的 PPT 课件转成「例题驱动」的考前突击复习材料。当用户提供 PPT / PDF形式的课件并要求做考前复习、例题复习、考前突击、或知识点整理时命中。产出以一组最小完备例题为主线、就地讲透知识点的 mainline，加一份纯查阅的 cheatsheet。
---

# exam-cram-from-slides

把一份（或多份）课程课件，转成**考前突击 + 临场应试**用的复习材料。

## 核心理念

- **例题是牵引，知识点是被讲透的目标。** 用一组例题当复习主线，让这组例题的**并集**铺满整张考纲；知识点在被某道例题「路过」时就地讲透。
- **覆盖是集合属性，不是单题属性。** 不要求每道题覆盖很多点，要求「这一组题加起来」不漏考点。
- **不做课程类型前置分类。** 不要先给整门课贴「数值分析 / 操作系统」之类标签。而是对**每一道例题**，自行判断它覆盖**一个**还是**多个**知识点：数值分析、算法常呈 1:1；操作系统、计算机网络常呈 1:多。这是自然涌现的，不需要预先决定。
- **one-shot。** 用户给课件就直接产出成品，不多轮追问、不要用户补充信息。
- **全面 vs 凝炼的裁决规则：判不准「会不会考」的内容，不删，降级到 cheatsheet 一句带过。** 宁可降级，不要丢失。
- **格式渲染** 代码框用```code```，数学公式块用 $$formula$$。

## 输入

用户提供的 PPT / PPTX / PDF 课件。Claude Code， Codex 等主流 Agent 已能自行拆 PPTX/PDF；正文内容直接读，PPTX / PPTM 的图片资源按下文脚本提取。一个 PPT 视为一个 chapter。

## PPT 图片提取

处理 PPTX / PPTM 课件时，先调用 `ref/extract_image.py` 提取 deck 内的唯一图片资源，供复习材料按需引用：

```bash
python3 <skill-dir>/ref/extract_image.py path/of/slides.pptx path/of/chapter/images
```

- 输出目录会包含去重后的图片文件和 `manifest.json`；脚本用图片 bytes 的 SHA-256 去重，相同背景图等重复资源只会写出一次。
- `manifest.json` 记录每张导出图片对应的 PPT 内部来源路径，可用于追踪同一图片在 deck 中的重复引用。
- 只把有助于理解知识点、例题、流程、结构、图示、截图的图片插入 `01-mainline.md` 或 `02-cheatsheet.md`；纯装饰背景、logo、水印不进入复习资料。
- PDF 输入不使用该脚本；老式 `.ppt` 仅在本机存在 LibreOffice / `soffice` 时可由脚本自动转换后提取，否则先转成 `.pptx` 再处理。

## 输出目录约定

一个 PPT = 一个 chapter 目录：

```
<课程名>/
  chapter_01_<topic-slug>/
    01-mainline.md      # 例题主线（先覆盖表，再例题主体）
    02-cheatsheet.md    # 纯查阅 + 降级内容
    images/             # PPTX/PPTM 图片提取结果（按需引用）
  chapter_02_<topic-slug>/
    ...
  INDEX.md              # 仅当一次处理多个 PPT 时，由主 agent 生成，串联各 chapter
```

`<topic-slug>` 用该 PPT 主题的英文小写连字符串（如 `numerical-integration`）。

## 单个 PPT 的 one-shot 主程序

按顺序执行，每一步都要做：

1. **准备 chapter 目录与图片资源。** 确定 `<topic-slug>` 后创建 chapter 目录；如果输入是 PPTX / PPTM，立即运行 `python3 <skill-dir>/ref/extract_image.py <deck> <chapter-dir>/images`，后续写作按需引用 `images/` 中的非装饰性图片。
2. **抽取知识点清单。** 通读 deck，列出**完整**知识点清单，作为内部覆盖核对表（这一步的清单不必写进交付物，但要在脑中/草稿里完整持有）。
3. **选 / 编最小完备例题组。** 选一组**尽量少**的例题，使其并集覆盖第 2 步清单里**所有应考知识点**。能用一道题串多个点就串（1:多），自然 1:1 的就 1:1。选题细则见 `ref/mainline-guide.md`。
4. **写 `01-mainline.md`。** 先写头部**覆盖表**（含「覆盖去向」标注），再写例题主体（引导式解答、就地讲透）。骨架见 `templates/mainline.md`，写法细则见 `ref/mainline-guide.md`。遇到关键图示、流程图、系统结构图、截图题时，用相对路径引用 `images/` 中对应图片，并在图下就地讲解。
5. **写 `02-cheatsheet.md`。** 速查内容 + 降级内容。骨架见 `templates/cheatsheet.md`，细则见 `ref/cheatsheet-guide.md`。
6. **覆盖自检（质量闸门）。** 逐条核对第 2 步清单的**每一项**：要么被某道例题讲到，要么进了 cheatsheet。有遗漏立刻补（补题或补降级条目）。结束时必须确认**零遗漏**——这是硬性闸门，不通过不算完成。

## 多 PPT 编排

用户一次丢多个 PPT 时，查看你手头是否有sub-agent / task派遣工具。如果有，优先按照如下方式做多Agent编排；如果没有，就只能你手动一个个做了。

主 agent **只做轻量调度**：

1. 为**每个** PPT 用 Task 工具 fan-out 一个 sub-agent。
2. 每个 sub-agent 在**干净上下文**里，对自己那一个 PPT 跑完整条「单个 PPT 的 one-shot 主程序」（含第 6 步覆盖自检），写自己的 `chapter_NN_<slug>/` 目录。
3. 全部 sub-agent 完成后，主 agent 写 `INDEX.md` 串联各 chapter（列出每个 chapter 的主题、覆盖的核心考点、相对链接）。

**为什么必须每个 PPT 一个独立 sub-agent：** 一个会话扛整门课，上下文会被多份 deck 互相干扰、变糊，导致覆盖不全、例题串味。每个 PPT 独立 sub-agent，才能保证上下文纯净、单章覆盖自检可靠、整理全面。主 agent 自己不要去读课件内容、不要在主上下文里堆 deck。

## 渐进式加载

- 本文件（SKILL.md）只放**编排与结构**——每次都要用到的内容，保持短小。
- 具体**写作细则按需加载**：搭主线读 `ref/mainline-guide.md`，写速查读 `ref/cheatsheet-guide.md`，起稿套 `templates/` 下骨架。不要一上来就全部加载。
