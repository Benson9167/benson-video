# Benson Video (`benson-video`)

> 从**拉片找材、口播写稿、配音精修听检、素材脱敏打码、16:9/4:3 精剪母带、三比例高点击封面**，到**单浏览器多账号隔离自动发布与随机错峰批发**的完整短视频生产流水线 & AI Agent Skill。

```text
  [1. 找材拉片]        [2. 写稿素文案]      [3. 配音/真人精修]       [4. 脱敏与打码]
fetch_bili_media  ──▶  口播稿 + 素文案  ──▶  voice_pipeline    ──▶    prep_clips
  (1080p + 选镜图)      (按标点断行)        (4引擎/末遍紧剪+听检)    (去原声/模糊/24px马赛克)
                                                                            │
  [7. 多号隔离双发]     [6. 三比例封面]      [5. 双版式渲染母带]             │
 publisher/*      ◀──   make_covers    ◀──   render_video       ◀───────────┘
(4:3+3:4+16:9封面       (16:9+4:3+3:4       (16:9/4:3 + 30fps进度条
 游戏手柄强校验+错峰)      + 预览拼图)         + 响度母带 + 12帧QA图)
```

---

## 核心能力模块

1. **B 站素材检索与选镜拼图**（`scripts/fetch_bili_media.py`）
   - 关键词搜索 + `yt-dlp` 1080p MP4 与封面抓取，自动生成带时间戳的 `sheets/videos_sheet.jpg`。
2. **4 引擎配音 + 真人末遍紧剪与亲耳听检**（`scripts/voice_pipeline.py`）
   - 支持 **豆包 `seed-audio-1.0`**（仅读取环境变量 API Key，不落盘）、**VoiceStudio 本地克隆**（短块防粘连）、**`edge-tts`**、以及 **`human_tighten` 真人录音精修**（末遍优先取舍 + `~95ms` 句内 / `~155ms` 句间自适应气口压缩 + 15ms 交叉淡化 + 可选 `faster-whisper` 词级对齐）。
3. **素材脱敏与防风控打码**（`scripts/prep_clips.py`）
   - 强制剥离原声（`-an`），统一规范化至 `1920×1080 30fps`；
   - 支持裁剪去水印、局部高斯模糊遮盖第三方硬字幕、可选左上角 `测试期画面` 标签、以及 **24px 圆角羽化像素马赛克** + 批量 QA 拼图。
4. **抖音小屏优先双版式渲染 & 母带处理**（`scripts/render_video.py`）
   - **`16x9_full`（默认主力）**：全屏高清原画直出，关闭厚重黑底遮罩，左上极简裸字 HUD + 底部 **`78px`** 单行粗描边字幕 + 居中 `96–116px` 核心数据弹窗（`callouts`）；另支持纯公告专用 **`4x3_card`**；
   - **OpenCV 亚像素浮点微推**：静态图采用 `cv2.warpAffine` 浮点缩放，彻底杜绝 FFmpeg `zoompan` 整数像素抖动；
   - **30fps 连续推进章节进度条**：支持 `separate_bottom`（画面外独立底条，零遮挡游戏 UI）、`separate_top`、`overlay_bottom`、`overlay_top` 四模式；
   - **抖音响度母带**：人声高通压缩 + BGM 人声凹槽 EQ + 侧链闪避 + 2-pass `loudnorm` + 192kHz 过采样硬限幅（`-11.5 ~ -12.0 LUFS` / `-1.0 dBTP`）；
   - 自动输出 `inspection_sheet.jpg`（12 帧验收拼图）并支持 `--dry-run`。
5. **Style-C 撞色斜切三比例封面生成器**（`scripts/make_covers.py`）
   - 一次渲染无裁切输出 `cover_16x9`（B 站个人空间）、`cover_4x3`（抖音横封 + B 站首页推荐）、`cover_3x4`（抖音竖封）及 `covers_preview.jpg`，支持对原画敏感区域预打 24px 马赛克。
6. **多账号隔离双平台自动发布 & 错峰批发**（`scripts/publisher/`）
   - **单浏览器多号隔离**：一号一独立原生 Chrome `user_data_dir`（`profiles/<account>/`），无需导出 Cookie，提供扫码登录与状态巡检（`login_account.py`）；
   - **双平台双比例封面上传**：抖音单弹窗连传 `4:3 + 3:4`（几何匹配 `<150px` + `expect_file_chooser` 兜底）；B 站连传 `4:3 + 16:9` 槽位；
   - **抖音游戏手柄强校验**：完整还原 UI 挂载路径，兼容搜索红字高亮拆分 `<span>` 节点、精准左偏避开 `详情 >`、三重同行强校验 + 截屏留痕，固化「草稿重开必重挂手柄」规则；
   - **安全闸门与错峰**：默认 `--mode draft` 存草稿，显式传入 `--mode publish --confirm-publish` 才发布；`stagger_publish.py` 支持随机分钟间隔错峰批发。

---

## 目录结构

```text
benson-video/
├── SKILL.md                               # AI Agent Skill 主工作流入口
├── README.md
├── LICENSE                                # MIT License
├── requirements.txt                       # 核心依赖与可选组件说明
├── .gitignore                             # 强制屏蔽 profiles/、accounts.json 与媒体/日志
├── config/
│   └── accounts.example.json              # 多账号独立 Chrome Profile 注册模板
├── examples/
│   ├── project.example.json               # 渲染任务配置模板 (render_video.py)
│   ├── clips_spec.example.json            # 批量切材与局部模糊/马赛克模板 (prep_clips.py)
│   ├── voice_chunks.example.json          # 短块配音输入模板 (voice_pipeline.py)
│   ├── publish_job.example.json           # 单条双平台发布任务模板 (publish_dual.py)
│   ├── batch_jobs.example.json            # 随机错峰批发清单模板 (stagger_publish.py)
│   └── launch_qr_login.example.cmd        # Windows 原生 Chrome 扫码快捷启动模板
├── references/
│   ├── layout-and-visuals.md              # 双版式、小屏大字、30fps进度条与封面规范
│   ├── audio-and-voice.md                 # 4模式配音、真人紧剪、听检闸门与响度混音配方
│   ├── clip-sanitization-and-qa.md        # 声画对位、去水印/24px马赛克与出片验收清单
│   ├── multi-account-isolation.md         # 单浏览器多账号隔离架构
│   ├── douyin-game-handle-and-tags.md     # 抖音话题与游戏手柄强校验及草稿重开陷阱
│   └── dual-ratio-cover-upload.md         # 抖音/B站双比例封面弹窗定位机制
└── scripts/
    ├── __init__.py
    ├── media_utils.py                     # FFmpeg/FFprobe 与跨平台 CJK 字体自动探测
    ├── fetch_bili_media.py                # B站视频/封面抓取与选镜拼图
    ├── voice_pipeline.py                  # 5模式配音/克隆/真人气口紧剪与词级对齐
    ├── prep_clips.py                      # 切条/裁切/局部高斯模糊/24px圆角马赛克/QA拼图
    ├── render_video.py                    # 双画幅主渲染引擎 + 混音母带 + 12帧质检图
    ├── make_covers.py                     # Style-C 三比例封面生成器
    └── publisher/
        ├── __init__.py
        ├── browser_profile.py             # 独立 Chrome user_data_dir 会话管理
        ├── login_account.py               # 双平台扫码登录与状态巡检
        ├── douyin_uploader.py             # 抖音上传 + 双封面 + 话题 + 游戏手柄强校验
        ├── bilibili_uploader.py           # B站上传 + 双封面 + 话题弹窗 + 创作声明
        ├── publish_dual.py                # 统一双平台发布入口（dry-run + 安全门禁）
        └── stagger_publish.py             # 多条任务随机分钟间隔错峰批发器
```

---

## 环境安装

- **基础环境**：Python 3.10+、系统已安装 **FFmpeg / FFprobe**（加入 `PATH` 或通过环境变量 `FFMPEG_BIN` / `FFPROBE_BIN` 指定）、本机已安装 **Google Chrome**。
- **安装核心 Python 依赖**：
  ```powershell
  pip install -r requirements.txt
  ```
- **按需选装**：
  - 拉取网页素材：`pip install yt-dlp`
  - 词级时间轴对齐：`pip install faster-whisper`
  - 微软免费 TTS：`pip install edge-tts`
  - 豆包声音克隆：设置临时环境变量 `DOUBAO_SPEECH_API_KEY` 或 `DOUBAO_API_KEY`

### 作为 Antigravity Skill 安装
将本仓库目录直接放入全局或项目 Skills 目录即可被自动发现：
- 全局：`~/.gemini/config/skills/benson-video/`
- 项目级：`<workspace>/.agents/skills/benson-video/`

---

## 快速上手（端到端命令示例）

```powershell
# 1. 拉取素材并生成选镜图
python -X utf8 scripts/fetch_bili_media.py --work-dir work/ep01 --bvids "BV1xxx" --download

# 2. 真人录音气口紧剪（或走 voicestudio / seed_audio / edgetts）
python -X utf8 scripts/voice_pipeline.py --mode human_tighten --audio raw_voice.mp3 --out-dir work/ep01/voice

# 3. 批量预处理素材（去原声 + 局部模糊 + 24px马赛克 + QA拼图）
python -X utf8 scripts/prep_clips.py --spec examples/clips_spec.example.json --qa-sheet work/ep01/clips_qa.jpg

# 4. 校验并渲染 16:9 成片（默认带 52px separate_bottom 章节进度条 + 抖音响度母带）
python -X utf8 scripts/render_video.py --config path/to/project.json --dry-run
python -X utf8 scripts/render_video.py --config path/to/project.json

# 5. 生成三比例封面 + 预览拼图
python -X utf8 scripts/make_covers.py --out-dir outputs/ep01/covers --hero hero.jpg --spark "1分钟省流" --game-tag "游戏名" --title "核心结论!" --pill "重点数据" --sub "机制拆解与排期"

# 6. 配置本地账号（首次需复制模板并扫码一次）
Copy-Item config\accounts.example.json config\accounts.json
python -X utf8 scripts/publisher/login_account.py --account demo_account_1 --auto-wait

# 7. 双平台存草稿 -> 人工核对后错峰正式发布
python -X utf8 scripts/publisher/publish_dual.py path/to/publish_job.json --game-handle on
python -X utf8 scripts/publisher/stagger_publish.py --batch-file path/to/batch_jobs.json --min-delay 180 --max-delay 540 --mode publish --confirm-publish
```

---

## 安全与隐私边界

- **本地登录态严禁提交**：`profiles/` 与 `config/accounts.json` 保存真实浏览器会话与本地账号映射，已强制加入 `.gitignore`。
- **API 密钥仅走环境变量**：云端配音密钥通过环境变量传入，绝不写入任何配置文件或代码。
- **默认草稿保护**：所有发布脚本默认 `mode="draft"`，仅当显式同时传入 `--mode publish --confirm-publish` 时才会触发正式发布。
