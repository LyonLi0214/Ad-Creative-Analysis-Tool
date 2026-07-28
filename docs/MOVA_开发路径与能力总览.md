# MOVA 素材分析系统 — 开发路径与能力总览

> 更新于 2026-07-23

---

## 一、已具备的能力

### 1.1 数据获取

| 能力 | 方式 | 状态 |
|------|------|------|
| AppsFlyer 每日拉取 | `appsflyer_pull.py` — API V2 → normalize → 聚合 → SQLite | ✅ 725条 daily_facts, 2天数据 |
| AppsFlyer CSV 列名修复 | `normalize.py` ALIASES 匹配实际 CSV 列名 | ✅ media_source/campaign 不再为空 |
| Google Ads 消耗表读取 | lark-cli 读飞书 Wiki 表格（截止到07xx系列） | ✅ 845行累计数据，20+个历史Sheet |
| OSS 文件索引 | 阿里云 OSS `心动防线-日/` → 本地缓存 | ✅ 3514个文件，5秒索引，缓存到 `data/oss_index.json` |

### 1.2 AI 分析

| 能力 | 方式 | 状态 |
|------|------|------|
| 视频拆解 | OCR(PaddleOCR) + ASR(faster-whisper) + CLIP → Claude | ✅ `analyze_material.py` 已跑121条 |
| 图片拆解 | CLIP → Claude（跳过ASR） | ✅ 同流程，按 `get_media()` 区分 |
| 四维拆解 | 前3秒钩子、核心卖点、视听包装、转化路径 | ✅ 每条分析输出四维 |
| 六维标签 | hook_策略(13值)、角色元素(9值)、场景元素(8值)、制作方式(7值)、叙事模式(12值)、画面风格(8值) | ✅ 标签值符合 `label_schema.yaml` |
| 单素材分析管道 | 读累计表 → OSS匹配 → 下载 → Claude → SQLite → 群同步 | ✅ `run_analysis_batch.py` 已验证(30s/条) |
| 批量分析 | 同上，逐条推进，进度同步到测试群 | ✅ 支持 `--limit N` |
| Claude 适配器 | `feishu_bot/claude_adapter.py` 替换 Mimo API | ✅ Bot 运行中，UTF-8修复 |

### 1.3 数据存储

| 库 | 表 | 行数 | 用途 |
|----|-----|------|------|
| SQLite | `appsflyer_daily_facts` | 725 | 每日投放效果 |
| SQLite | `material_registry` | 286 | 素材注册 |
| SQLite | `material_appsflyer_mapping` | 270 | 素材↔投放映射 |
| SQLite | `ai_analysis` | 132 | AI分析结果(六维标签) |
| SQLite | `aggregation_results` | 130 | 标签维度聚合 |
| SQLite | `material_performance` | (新建) | 每期素材表现(历史迭代) |
| Base | `素材表现排行` | — | TOP/BOTTOM推送 |
| Base | `效果聚合` | — | 标签×KPI |

### 1.4 Bot 能力

| 能力 | 方式 | 状态 |
|------|------|------|
| 飞书长连接 | WebSocket 接收消息 | ✅ 运行中 |
| Claude 对话 | `call_claude` 替换 Mimo | ✅ |
| `/日报` 命令 | 触发全流程管道（读累计表→分析→推送） | ✅ 后台线程，不阻塞 |
| `/周报` `/趋势` `/概况` | `report_engine.py` 纯SQL秒回 | ✅ |
| Base 读取 | 素材主表、AI分析结果、效果聚合 | ✅ |
| 推送通知 | 测试群 `oc_bb4354db8aaa610f03f0c1e095dc4bc8` | ✅ |
| 新素材发现 | `pipeline_new_material.py` OSS匹配+注册+映射+通知 | ✅ 已验证 |

### 1.5 报告与看板

| 能力 | 方式 | 状态 |
|------|------|------|
| 日报 | `report_engine.py` → 纯SQL + Python模板 | ✅ 秒回 |
| 周报 | 7天汇总 + 环比 + 平台对比 + sparkline | ✅ |
| 趋势 | N天安装/收入走势 | ✅ |
| HTML看板 | `gen_dashboard.py` → Chart.js | ✅ |
| 回归分析 | sklearn LinearRegression → 标签×ROI特征权重 | ✅ 待数据量充足后使用 |

---

## 二、开发路径

### 阶段 1（当前）：跑通单素材分析管道 ✅

- [x] 读飞书累计表（8wvQBI "截止到0712（心动-日）"）845行
- [x] OSS缓存（`data/oss_index.json`，3514文件）
- [x] 视频 vs 图片区分
- [x] OSS匹配 → 下载 → Claude拆解 → SQLite入库
- [x] 跳过已拆解素材（模糊匹配防重复）
- [x] 进度同步到测试群
- [x] `call_claude` UTF-8编码修复

### 阶段 2（进行中）：批量分析 + 回归

- [ ] 前300条分批次分析（每批10条，30s/条 ≈ 5min/批）
- [ ] 每批完成后通知：进度 + 预计剩余时间
- [ ] 磁盘不足自动清理旧下载
- [ ] 全量分析完成后：回归分析（标签×ROI）
- [ ] 结果推送 Base + 群

### 阶段 3（下一步）：历史数据迭代

- [ ] `material_performance` 表存每期数据
- [ ] 读多个历史 Sheet（0719/0712/0705/0628/0621/0614/0531）
- [ ] 本期 vs 上期趋势对比
- [ ] `/日报` 命令触发时自动找最新周期

### 阶段 4：Bot 瘦身 + 自动化

- [ ] Bot 去掉通用 Base 写工具
- [ ] `/日报` 命令一键触发全流程
- [ ] 每周一自动跑周报
- [ ] Windows Task Scheduler 定时编排

### 阶段 5：多数据源融合

- [ ] Google Ads 素材级消耗 ← 目前手动贴表，等 API
- [ ] AppsFlyer LTV 数据 JOIN
- [ ] Facebook Ads 接入（待投放后）
- [ ] 成本端 + LTV端 + 标签 → AI综合归因

---

## 三、关键数据源

### 飞书 Wiki 表格（主要数据源）

```
https://dreametech.feishu.cn/wiki/JjgNwLEFaitohZk0g73c0yyfnMI

22个Sheet，心动日服相关：
  截止到0719（心动-日累计总表） O5qYGZ  — 总览汇总
  截止到0719（心动-日周表）     TFI4VR  — 周级别素材数据(80行)
  截止到0712（心动-日）         8wvQBI  — 累计全量数据(845行) ⭐ 主要分析源
  截止到0705（心动-日）         u8Q9mO  — 历史数据
  截止到0628（心动-日）         YzkY6B  — 历史数据
  ...更多历史Sheet
  截止到0531（心动）            zcY3JN  — 素材消耗总表($717,862)
  6月总数据2                   VMqUpB  — 媒体渠道级(Moloco)
```

### 关键表结构

**截止到0712（心动-日）** — 累计全量数据（845行）:
```
Ad | 花费 | 点击率 | 安装 | 转化率 | 单价 | 首充用户 | 首充率 | 
首日回收 | 单个付费用户成本 | 首日ROI | 累计付费 | 累计ROI | 次留
```

### SQLite 关键表

```
ai_analysis:  material_key, material_name, hook_strategy, character_elements,
              scene_elements, production_method, narrative_mode, visual_style, ...

material_performance: period, ad_name, spend, ctr, installs, ..., tags_json
```

---

## 四、当前分析的素材范围

| 维度 | 数据 |
|------|------|
| 游戏 | 心动防线（日服） |
| 素材总量 | 累计表 845 条（产出1228，已消耗1078，剩余150） |
| 已AI拆解 | 132 条 |
| 待拆解 | ~200+ 条（前300条中~186条待处理） |
| OSS可匹配 | 3514个文件（1747视频 + 1329图片 + 426其他） |
| 磁盘 | D盘 543GB 剩余，downloads 4GB |

---

## 五、技术栈

| 层 | 技术 |
|----|------|
| AI引擎 | Claude (via `claude --print`) |
| 本地识别 | PaddleOCR + faster-whisper + CLIP (OpenAI ViT-B/32) |
| 数据处理 | Python + SQLite + pandas + sklearn |
| 云存储 | 阿里云 OSS |
| 协作平台 | 飞书 Base + Wiki + IM |
| 自动化 | lark-cli + Windows Task Scheduler |
| 数据源 | AppsFlyer API V2 + 飞书表格（Google Ads手动导出） |

---

## 六、当前阻塞与风险

| 问题 | 影响 | 缓解 |
|------|------|------|
| Google Ads 无素材级API | 素材级消耗手动贴表 | 等待API审批；目前用飞书表格替代 |
| Claude 分析速度 ~30s/条 | 300条约需2.5小时 | 批量 + 后台运行 + 分批通知 |
| 日周表只有80行 vs 累计表845行 | 趋势分析缺中间数据 | 用累计表全量 + 历史Sheet对比 |
| 名称匹配不完美（54/80匹配率） | 部分素材挂不上AI标签 | 版本号+日期双键匹配已提升到67% |
