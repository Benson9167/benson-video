---
name: benson-video
description: >-
  End-to-end automated short-video production and multi-account isolated Douyin/Bilibili publishing
  workflow ("Benson Video"). Covers the full pipeline: topic & media research (Bilibili fetch +
  timestamped contact sheets), concise voiceover scriptwriting + plain script export, 4-engine voice
  generation & human final-take pause tightening with mandatory ear-check QA, clip sanitization
  (audio strip, watermark/hard-subtitle blur, test badge, Style-2 24px rounded pixel mosaic), 16:9
  full-screen / 4:3 card rendering with OpenCV subpixel zoom, 4-mode 30fps continuous chapter
  progress bar, exact non-early subtitles & Douyin -11.5 to -12.0 LUFS mastering, Style-C 3-ratio
  cover generation (16:9 + 4:3 + 3:4), and isolated Chrome profile publishing to Douyin & Bilibili
  with verified game handle attachment and randomized staggered batch publishing.
---

# Benson Video · 全自动做视频与双平台分发产线 (`benson-video`)

把「**找材拉片 → 写稿与素文案 → 配音/真人精修 + 亲耳听检闸门 → 素材脱敏打码 → 16:9/4:3 精剪渲染 + 30fps 连续进度条 + 抖音响度母带 → 3 比例封面 → 多号隔离双平台发布与随机错峰批发**」封装为一站式端到端工作流。

---

## 全流程 7 步标准作业（含必问交互闸门）

### Step 1 · 情报核实与素材拉取
1. 先用联网检索或核对官方公告卡死事实口径（时间节点、奖励数值、机制规则），严禁脑补未公开信息。
2. 运行 [scripts/fetch_bili_media.py](./scripts/fetch_bili_media.py) 按关键词或 BVID 拉取 1080p 素材与封面，并自动生成带时间戳的选镜拼图 `sheets/videos_sheet.jpg`：
   ```powershell
   python -X utf8 scripts/fetch_bili_media.py --work-dir <work_dir> --keywords "游戏实机,版本前瞻" --max-per-kw 6
   python -X utf8 scripts/fetch_bili_media.py --work-dir <work_dir> --bvids "BV1xxx,alias=BV1yyy" --download
   ```
3. 用 `read_file` 检视 `sheets/videos_sheet.jpg`，记下与口播精准对位的画面时间戳（讲谁切谁，禁止拿无关画面凑数）。
4. **宁缺毋滥 · 主动提醒补录**：若核心镜头缺失高清对口实机、掉帧严重或带抹不净的中心大水印，严禁凑数硬上，主动列出 3–8 秒具体画面补录清单请用户补录。标准工程目录与可选开源扩展工具地图见 [references/tool-ecosystem.md](./references/tool-ecosystem.md)。

### Step 2 · 口播稿与纯净素文案
1. 参照 [references/script-templates-and-hooks.md](./references/script-templates-and-hooks.md) 的 **5 大爆款文案骨架与陈述式标题规范**，默认控时 **45–65 秒**（约 220–360 字），短句口语化，开头 3 秒直给钩子与结论。
2. 同步保存一份 **纯净素文案**（`voice_script_plain.txt`）：按标点断行（顿号不断行），去掉所有标题/注释/括号，方便随时真人照念或局部补录。

### Step 3 · 配音生成 / 真人精修 + 必做亲耳听检 + 【4选1返修闸门】
详规见 [references/audio-and-voice.md](./references/audio-and-voice.md)，工具见 [scripts/voice_pipeline.py](./scripts/voice_pipeline.py)：

1. **4 种配音模式**：
   - **豆包 `seed-audio-1.0` 零样本克隆**（`--mode seed_audio`）：仅从环境变量 `DOUBAO_SPEECH_API_KEY` / `DOUBAO_API_KEY` 读取密钥，**严禁将任何密钥写入文件**；
   - **`VoiceStudio` 本地克隆**（`--mode voicestudio --voice <id>`）：按 1–2 句短块（35–75 字）合成防粘连；注意商用授权区分（`OmniVoice` 为 `CC-BY-NC`，跑商单/激励请用 `CosyVoice 3` `Apache-2.0` 或真人/豆包）；
   - **`edge-tts`**（`--mode edgetts`）：免显存快出；
   - **真人录音末遍优先 + 气口紧剪**（`--mode human_tighten`）：分辨重复试念只保留最后一遍最流畅满意态，剪净废话/卡壳/长吸气声，句内停顿压至 **`~95ms`**、句间停顿压至 **`~155ms`**（起音 `70ms` / 尾音 `180ms`，禁止留 `>0.3s` 死气口），并支持导出词级时间戳 JSON。
2. **【强红线】亲耳听检**：任何 AI 或精修配音完成后，**必须用 `read_file` 亲耳听检 WAV 音频**，绝不能只看 Whisper 转写文字就放行。
3. **【必问交互闸门】听检后询问是否调整**：
   - **A. 直接采用现版音频出片**；
   - **B. 局部单句外科手术式重配**（仅对瑕疵句重新生成并重拼）；
   - **C. 仅补录瑕疵句**（整理精简重读清单，收到补录后无缝替换）；
   - **D. 全篇照素文案重录真声**（自动紧剪气口并重新对齐时间轴）。

### Step 4 · 素材脱敏与预处理（去原声 / 去水印 / 局部模糊 / 像素马赛克）
详规见 [references/clip-sanitization-and-qa.md](./references/clip-sanitization-and-qa.md)，工具见 [scripts/prep_clips.py](./scripts/prep_clips.py)：
- 第三方实机素材**必须彻底剥离原音轨（`-an`）**；
- 原视频带角落 UP 主水印或底部硬字幕时，采用 `crop_vf` 裁切或 `blur_rects` 精准局部高斯模糊遮盖，混用测试期素材时可加 `--test-badge` 标注 `测试期画面`；
- 面对平台低俗/敏感部位风控画面，统一使用 `--mosaic x1,y1,x2,y2`（24px 圆角羽化像素马赛克），并通过 `--qa-sheet` 生成拼图逐格肉眼核验不露边、不遮脸。

### Step 5 · 双版式组装渲染 + 30fps 连续进度条 + 抖音响度母带
详规见 [references/layout-and-visuals.md](./references/layout-and-visuals.md) 与 [references/editing-and-motion-recipes.md](./references/editing-and-motion-recipes.md)，工具见 [scripts/render_video.py](./scripts/render_video.py)：
1. **制作前交互确认（若用户未指定）**：
   - 询问是否开启 **30fps 连续推进章节进度条**，以及放置模式：`separate_bottom`（画面外独立 `52px` 底条，**零遮挡游戏 UI 与字幕，默认推荐**）、`separate_top`、`overlay_bottom`、`overlay_top`；
   - **改版先出效果图红线**：若需调整 HUD、字幕或模板结构，必须先出 2–3 张单帧效果图供用户拍板再全片渲染。
2. **抖音小屏优先默认值**：
   - 主画幅 `16x9_full`（原画全屏，默认 `use_bottom_scrim: false`、`use_hud_scrim: false`、左上极简裸字 HUD、底部 **`78px`** 单行粗描边字幕、正中 `96–116px` 核心数据 `callouts`）；纯文字公告切 `4x3_card`（`72px` 字幕，可用 [scripts/make_card_overlays.py](./scripts/make_card_overlays.py) + [examples/project_4x3_card.example.json](./examples/project_4x3_card.example.json) 生成三模块省流卡片）；
   - 静态图强制走 OpenCV 亚像素浮点 `warpAffine` 微推（禁用 FFmpeg `zoompan`）；
   - 混音自动做人声高通压缩 + BGM 人声凹槽 EQ 与侧链闪避 + 2-pass `loudnorm` + 192kHz 过采样限幅至 **`-11.5 ~ -12.0 LUFS` / `-1.0 dBTP`**。
3. **执行渲染（支持先 `--dry-run`）**：
   ```powershell
   python -X utf8 scripts/render_video.py --config path/to/project.json --dry-run
   python -X utf8 scripts/render_video.py --config path/to/project.json
   ```
   若后续**仅需换 BGM 或微调配乐音量**，直接用 [scripts/swap_audio.py](./scripts/swap_audio.py) 走 `-c:v copy` **5 秒无损直封**（零重编码画面）。
4. **视觉 QA（必做）**：用 `read_file` 打开自动生成的 `inspection_sheet.jpg` 逐格核验无错字、无重叠、无残余水印/硬字幕。

### Step 6 · Style-C 多比例封面生成与核验
使用 [scripts/make_covers.py](./scripts/make_covers.py) 一次性无裁切输出三比例封面（支持 `--mosaic` 对原图敏感区预打码，以及 `--include-9x16` 可选输出 9:16 竖屏满屏封面）并生成 `covers_preview.jpg`：
```powershell
python -X utf8 scripts/make_covers.py --out-dir <out_dir>/covers --hero <hero.jpg> --spark "1分钟省流" --game-tag "作品名称" --title "核心大标题" --pill "高亮亮点" --sub "副标题补充说明" --safe-zone-guide
```
输出：`cover_16x9.jpg`（B 站个人空间）、`cover_4x3.jpg`（抖音横封 + B 站首页推荐）、`cover_3x4.jpg`（抖音竖封，加 `--include-9x16` 另出 `cover_9x16.jpg`），并用 `read_file` 核验 `covers_preview.jpg`。

### Step 7 · 多账号隔离上传与随机间隔错峰发布
详情分别见 [references/multi-account-isolation.md](./references/multi-account-isolation.md)、[references/douyin-game-handle-and-tags.md](./references/douyin-game-handle-and-tags.md)、[references/dual-ratio-cover-upload.md](./references/dual-ratio-cover-upload.md)：

1. **账号隔离**：复制 [config/accounts.example.json](./config/accounts.example.json) 为 `config/accounts.json`（已 gitignore），每个账号独立 `profiles/<account>/`，用原生 Chrome 扫码保活：
   ```powershell
   python -X utf8 scripts/publisher/login_account.py --account demo_account_1 --auto-wait
   ```
2. **草稿优先安全闸门**：默认 `--mode draft` 存草稿；仅在用户明确授权后加 `--mode publish --confirm-publish` 执行正式发布。
   ```powershell
   python -X utf8 scripts/publisher/publish_dual.py path/to/publish_job.json --dry-run
   python -X utf8 scripts/publisher/publish_dual.py path/to/publish_job.json --game-handle on
   ```
3. **抖音游戏手柄与草稿重开铁律**：
   - 挂载后自动执行弹窗关闭 + 占位符消失 + 与 `添加标签` 同行三重强校验并保存 `douyin_handle_attached.png`；
   - **重要**：抖音存草稿后再点「继续编辑」会清空游戏手柄，因此正式发布当次会话**必须重新挂载并强校验**。
4. **随机间隔错峰批发**：多条成片严禁一次性连续塞入发布，必须走 [scripts/publisher/stagger_publish.py](./scripts/publisher/stagger_publish.py) 插入随机分钟级等待：
   ```powershell
   python -X utf8 scripts/publisher/stagger_publish.py --batch-file path/to/batch_jobs.json --min-delay 180 --max-delay 540 --mode publish --confirm-publish
   ```
