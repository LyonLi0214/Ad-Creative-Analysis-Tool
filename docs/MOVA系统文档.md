# MOVA 素材分析系统 — 完整文档

> 版本：`2026.07.23-v1`
> 本文档是系统的唯一权威文档，涵盖架构、配置、表结构、Bot、脚本和运维。

---

## 1. 系统概述

MOVA 是一个游戏广告素材分析系统，功能包括：

- 从阿里云 OSS 下载素材（视频/图片）
- AI 自动拆解（四维拆解 + 六维标签）
- AppsFlyer 投放数据关联
- 飞书 Base 数据协作
- 飞书 Bot 智能问答与推送

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                         飞书云                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Base       │  │   Sheets     │  │   IM 消息    │          │
│  │  (多维表格)   │  │  (电子表格)   │  │  (Bot 通信)  │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
└─────────┼─────────────────┼─────────────────┼──────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  Windows（公司内网，当前唯一运行端）                                │
│                                                                 │
│  飞书 Bot（feishu_bot/service.py）                               │
│  ├─ 接收飞书消息（长连接）                                        │
│  ├─ AI 对话（Mimo API）                                          │
│  ├─ 工具调用（Base/Sheets/AppsFlyer）                            │
│  └─ 定时推送日报                                                 │
│                                                                 │
│  数据处理脚本                                                    │
│  ├─ analyze_parallel.py — 素材分析主程序                         │
│  ├─ appsflyer_pull.py — AppsFlyer 数据拉取（待开发）             │
│  ├─ aggregate.py — 数据聚合（待开发）                            │
│  └─ gen_dashboard.py — HTML 看板生成（待开发）                   │
│                                                                 │
│  lark-cli — 飞书表格操作工具                                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 运行环境

### 3.1 Windows 端

| 项目 | 值 |
|------|-----|
| 项目目录 | `D:\素材拆解分析库` |
| 兼容入口 | `C:\Users\admin\mova_project`（Junction） |
| Python 环境 | `D:\素材拆解分析库\venv` |
| 本地识别环境 | `D:\素材拆解分析库\.venv-recognition` |
| 私密配置 | `C:\Users\admin\.mova-secrets.env` |
| Bot 启动 | `C:\Users\admin\start_mova_feishu_bot.cmd` |
| Bot 日志 | `D:\素材拆解分析库\logs\feishu_bot.log` |
| Bot 状态 | `D:\素材拆解分析库\logs\feishu_bot_state.json` |
| 会话持久化 | `D:\素材拆解分析库\logs\feishu_bot_sessions.json` |
| 工具审计 | `D:\素材拆解分析库\logs\bot_tool_audit.jsonl` |
| lark-cli | `C:\Users\admin\AppData\Roaming\npm\lark-cli.cmd` |

### 3.2 ECS 服务器

| 项目 | 值 |
|------|-----|
| IP | `[SERVER_IP]` |
| SSH | `root@[SERVER_IP]`（密码：`[REDACTED]`） |
| 系统 | Ubuntu 22.04.5 LTS |
| 进度面板 | `http://[SERVER_IP]/` |
| NocoDB | `http://[SERVER_IP]/nc/` |

---

## 4. 外部服务与凭据

> ⚠️ 敏感信息，不得提交到代码库或公共平台。

### 4.1 飞书应用

| 项目 | 值 |
|------|-----|
| App ID | `[REDACTED]` |
| App Secret | `[REDACTED]` |
| 应用名称 | 小麦 |
| 身份 | Bot（应用身份） |
| 长连接 | 已启用 |

### 4.2 飞书 Base

| 项目 | 值 |
|------|-----|
| Base URL | `https://dreametech.feishu.cn/base/[REDACTED]` |
| Base Token | `[REDACTED]` |

### 4.3 Mimo AI

| 项目 | 值 |
|------|-----|
| API Base | `https://token-plan-cn.xiaomimimo.com/v1` |
| API Key | `[REDACTED]` |
| 模型 | `mimo-v2.5-pro` |

### 4.4 阿里云 OSS

| 项目 | 值 |
|------|-----|
| Endpoint | `https://oss-cn-shanghai.aliyuncs.com` |
| Bucket | `mova-eu-game-uxd` |
| AccessKey ID | `[REDACTED]` |
| AccessKey Secret | `[REDACTED]` |

### 4.5 AppsFlyer

| 项目 | 值 |
|------|-----|
| API Token | 见 `.mova-secrets.env` |
| App ID（Android） | `id6766773102` |
| App ID（iOS） | `com.singularity.xdfxrw` |

### 4.6 NocoDB（历史，不再作为主入口）

| 项目 | 值 |
|------|-----|
| API Token | `[REDACTED]` |
| Base ID | `pjg5uoh4uqz2xhp` |

---

## 5. 飞书 Base 表结构

### 5.1 表清单

| # | 表名 | table_id | 字段数 | 用途 |
|---|------|----------|--------|------|
| 1 | 素材主表 | `tblrjnWVj9yJwreB` | 32 | 核心表，每条素材一行 |
| 2 | AI分析结果 | `tblNyyHI4nFhl6aQ` | 28 | AI 标签分析 |
| 3 | 逐帧拆解 | `tblSSwX14UkYlytk` | 10 | 视频逐帧内容 |
| 4 | 标签字典 | `tblGukljxPXieW1c` | 6 | 标签定义参考 |
| 5 | 效果聚合 | `tblPHmTzGV5cBCbO` | 11 | 按维度汇总指标 |
| 6 | 游戏背景库 | `tbl6pBNByhtyMcRC` | 18 | 游戏元数据 |
| 7 | AppsFlyer应用与查询口径 | `tblnEL7xGupXHgIT` | 12 | API 配置 |
| 8 | 素材与AppsFlyer映射 | `tbla6UqLJBoZPIlq` | 15 | 素材↔投放关联 |
| 9 | AppsFlyer日效果 | `tbl3mQId98o6Uafq` | 15 | 每日投放数据 |
| 10 | 运行日志 | `tblO5PlfpzRVsM1n` | 8 | 脚本/Bot 执行记录 |

### 5.2 素材主表（tblrjnWVj9yJwreB）— 32 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| 素材名称 | `fldPGAJExb` | text | 主键 |
| 素材ID | `fldSGrI39a` | text | |
| 游戏名称 | `fldVBUmbKl` | text | |
| 平台 | `fldRDD4XZn` | select | Android/iOS |
| 类型 | `fld0e1Vhmn` | text | 视频/图片 |
| Campaign | `fldWE3aJrp` | text | |
| 链接 | `fld7HX926Y` | text | |
| 入库时间 | `fldXF09kiO` | text | |
| 状态 | `fld1u4RBXV` | text | |
| 处理状态 | `fldKYpHmMs` | select | 待处理/处理中/待复核/已完成/异常 |
| 上传人 | `fldrt6TtUs` | text | |
| 花费 | `fldqlXyzw6` | number | |
| 点击率 | `fldHVAQJmC` | number | |
| 安装 | `fldwTfwu6Q` | number | |
| 转化率 | `fldlKqZDLN` | number | |
| 单价 | `fldtGFvoVK` | number | |
| 首充用户 | `fldOSilEyW` | number | |
| 首充率 | `fld6gxtST5` | number | |
| 首日回收 | `fldKIwFOgn` | number | |
| 单个付费用户成本 | `fldISpZfDB` | number | |
| 首日ROI | `fldzpxwxqB` | number | |
| 累计付费 | `fldTt3592P` | number | |
| 累计ROI | `fldOZfQVX6` | number | |
| 次留 | `fldvg3JIe7` | number | |
| 时长 | `fldKySZNEJ` | number | |
| 分辨率 | `fldmNUVG0q` | text | |
| 备注 | `fld9M5gZlD` | text | |
| 效果数据粒度 | `fldeQzsJk2` | select | Campaign/广告组/广告/素材候选/单素材已确认 |
| AppsFlyer App ID | `fldbm3hrDG` | text | |
| AppsFlyer映射 | `fldf1T2WQy` | link | → 素材与AppsFlyer映射 |
| 创建时间 | `fldkScV8ci` | created_at | 系统字段 |
| 更新时间 | `fldeIkvALS` | updated_at | 系统字段 |

### 5.3 AI分析结果（tblNyyHI4nFhl6aQ）— 28 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| 素材名称 | `fldiZEfsDC` | text | |
| 游戏名称 | `fldslOmAq4` | text | |
| 素材类型 | `fldIScvo87` | text | |
| 名称 | `fld58QRyc5` | text | |
| Hook内容 | `fldwiMJZQk` | text | |
| Hook类型 | `fld8Nbl229` | text | |
| 人物 | `fldcGQecBF` | text | |
| UI元素 | `fldVafP2N3` | text | |
| 特效 | `fldKqm7IL8` | text | |
| 色彩 | `fldCoAQkag` | text | |
| 字幕 | `fldkE5MKjQ` | text | |
| 节奏 | `fldEV9cyC6` | text | |
| BGM | `fldY1Vxacs` | text | |
| 核心卖点 | `fldTvVNOhi` | text | |
| hook_策略 | `fldglAhVbn` | text | 六维标签1 |
| 角色元素 | `fldxUIM8PX` | text | 六维标签2 |
| 场景元素 | `fldexyoFRA` | text | 六维标签3 |
| 制作方式 | `fldUr4PaLj` | text | 六维标签4 |
| 叙事模式 | `fldpnQuH2k` | text | 六维标签5 |
| 画面风格 | `fldsBQswuA` | text | 六维标签6 |
| 目标受众 | `fldLYsaaYS` | text | |
| 情绪触发 | `fld0RQDZfb` | text | |
| 营销策略 | `fldGjfbXxJ` | text | |
| 可优化方向 | `fldq4tP5sy` | text | |
| 标签状态 | `fldqs58cNB` | text | |
| AI置信度 | `fldGqGsSiR` | number | |
| 分析时间 | `fldHc9ziv3` | text | |
| 人工批注 | `fld4uhnGhO` | text | |

### 5.4 逐帧拆解（tblSSwX14UkYlytk）— 10 字段

| 字段名 | field_id | 类型 |
|--------|----------|------|
| 素材名称 | `fld0DjziUM` | text |
| 游戏名称 | `fldnME4zRo` | text |
| 名称 | `fldt13Y5wb` | text |
| 时间轴 | `fldfAdF00Z` | text |
| 画面内容 | `fldF3sYWov` | text |
| 画面元素 | `fldl8fnouo` | text |
| 文案/字幕 | `fldnnMUaBx` | text |
| 音效 | `fldiY004ys` | text |
| 目的 | `fldlC5x9OM` | text |
| 可借鉴点 | `fldswvJuzJ` | text |

### 5.5 标签字典（tblGukljxPXieW1c）— 6 字段

| 字段名 | field_id | 类型 |
|--------|----------|------|
| 游戏名称 | `fld6w2iIZb` | text |
| 名称 | `fld2UqUlbe` | text |
| 维度 | `fldsfHg3My` | text |
| 标签值 | `fldGLztkYa` | text |
| 说明 | `fldAuu9ZPC` | text |
| 示例素材 | `fldCka5KYW` | text |

### 5.6 效果聚合（tblPHmTzGV5cBCbO）— 11 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| 游戏名称 | `fldxthvn8d` | text | |
| 名称 | `fldd2gAMKv` | text | |
| 分析维度 | `fldkKGIF5x` | text | |
| 标签值 | `fldfeerjdm` | text | |
| 素材数 | `fldQFIK2C1` | number | |
| 平均花费 | `fldxJIhB5s` | number | |
| 平均单价 | `fld4XyqNLY` | number | |
| 平均首日ROI | `fldzJHLENr` | number | |
| 平均累计ROI | `fldyAnxlcH` | number | |
| 平均次留 | `fld5YYL0Hp` | number | |
| 结论 | `fldazrgb3o` | text | |

### 5.7 游戏背景库（tbl6pBNByhtyMcRC）— 18 字段

| 字段名 | field_id | 类型 |
|--------|----------|------|
| 游戏名称 | `flduc17Ex2` | text |
| 名称 | `fldlcWzHtu` | text |
| 别名 | `fldXTM4fdQ` | text |
| 游戏Key | `fld9b67Psm` | text |
| 游戏类型 | `fldUPAsNTy` | text |
| 游戏卖点 | `fldwvkAM0W` | text |
| 题材风格 | `fldIMFDvYp` | text |
| 核心玩法 | `fldLPxy6GO` | text |
| 游戏标签 | `fldnOYvQ5e` | text |
| 核心理念 | `fldeZf7DrC` | text |
| 游戏特色 | `fld80oblK2` | text |
| 世界观与团队 | `fld7OQTim0` | text |
| 角色设定 | `fldA9Dn3xM` | text |
| 产品背景 | `fldMlmTPXp` | text |
| 目标市场 | `fldeMSumTc` | text |
| 证据边界 | `fldWT4n9Cc` | text |
| 素材库现状 | `fldPbUwyvx` | text |
| 更新时间 | `fldoIAHHzJ` | text |

### 5.8 AppsFlyer应用与查询口径（tblnEL7xGupXHgIT）— 12 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| 名称 | `fldiSJY9Fq` | text | |
| 游戏名称 | `fldfMpvhdv` | text | |
| App ID | `fldbtB6Od4` | text | |
| 平台 | `fld5TPu1NQ` | text | |
| Campaign接口 | `fld5Pc1TuX` | text | |
| Campaign粒度 | `fldezdECeR` | text | |
| 明细安装接口 | `fldn61EAL1` | text | |
| 明细事件接口 | `fld6yS7Eph` | text | |
| Raw粒度 | `fldRxAmDRc` | text | |
| 素材关联规则 | `fld5JstbZW` | text | |
| 时区口径 | `fldMhi1zzZ` | text | |
| 更新时间 | `fldxnbvHB0` | text | |

### 5.9 素材与AppsFlyer映射（tbla6UqLJBoZPIlq）— 15 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| 素材名称 | `fldT4X75Ad` | text | |
| 素材Key | `fldJSZScGg` | text | |
| 游戏名称 | `fldc4kHtLb` | text | |
| 平台 | `fldiPQ798K` | text | |
| App ID | `fldcubcCpM` | text | |
| Campaign ID | `fldvr68e4m` | text | |
| Adset ID | `fld74hpPM0` | text | |
| Ad ID | `fldAo0CSpl` | text | |
| AppsFlyer广告名 | `fldq8hqinT` | text | |
| 映射键 | `fld61qt4lu` | text | |
| 匹配方式 | `fldUe9A4xC` | text | |
| 匹配置信度 | `fld472BABz` | number | |
| 人工确认状态 | `flddqybP75` | text | |
| 关联素材 | `fldofmpjOR` | link | → 素材主表 |
| 更新时间 | `fldwhOepUo` | datetime | |

### 5.10 AppsFlyer日效果（tbl3mQId98o6Uafq）— 15 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| App ID | `fldxKmSmFN` | text | |
| 平台 | `fld3TqrrwB` | text | |
| Campaign ID | `fldNgbhG7Y` | text | |
| Adset ID | `fldY1QQHSm` | text | |
| Ad ID | `fldsRhYMni` | text | |
| AppsFlyer广告名 | `fldMLONw5O` | text | |
| Adset名称 | `fld37tC99M` | text | |
| 安装 | `fldMCYQ8QJ` | number | |
| 事件数 | `fldLizQQdb` | number | |
| 付费事件数 | `fld6epZ8x2` | number | |
| 收入USD | `fldtYlrJiW` | number | |
| 日期 | `fld6aVPze9` | datetime | |
| 更新时间 | `fld0iXn1PW` | datetime | |
| 同步批次 | `fldwUUKqFv` | text | |
| 事实键 | `fld2oH3tgY` | text | |

### 5.11 运行日志（tblO5PlfpzRVsM1n）— 8 字段

| 字段名 | field_id | 类型 | 说明 |
|--------|----------|------|------|
| ID | `fldoK1ucsZ` | auto_number | 自增编号 |
| 时间 | `fldGBKAaTY` | datetime | 执行时间 |
| 任务 | `fldMtm83pL` | select | appsflyer_pull/aggregate/gen_dashboard/bot_restart/data_sync/manual |
| 状态 | `fldaCU2RwZ` | select | success/failed/running/skipped |
| 详情 | `fld6Zsyae9` | text | 执行结果或错误信息 |
| 素材数 | `fldcome74h` | number | 处理的素材数量 |
| 耗时秒 | `fldrt8I8gS` | number | 执行耗时 |
| 触发方式 | `fldjBifa0P` | select | 定时/手动/Bot/Webhook |

---

## 6. 飞书 Bot

### 6.1 架构

```
飞书用户 @Bot
      │
      ▼
feishu_bot/service.py（长连接）
      │
      ├─ 消息路由（parse_tool_request）
      │   ├─ chat — 普通对话
      │   ├─ base_agent — Base 读写
      │   ├─ sheets_agent — 电子表格操作
      │   ├─ game_context — 游戏知识查询
      │   ├─ appsflyer_status — AppsFlyer 状态
      │   └─ appsflyer_sync — AppsFlyer 同步
      │
      ├─ 工具执行（tools.py）
      │   ├─ FeishuBaseTools — Base API
      │   └─ lark-cli — Sheets 操作
      │
      └─ 回复用户
```

### 6.2 文件结构

| 文件 | 用途 |
|------|------|
| `feishu_bot/service.py` | 主服务，消息处理、路由、回复 |
| `feishu_bot/tools.py` | 工具实现（Base/Sheets/AppsFlyer） |
| `feishu_bot/tools_config.json` | 表 ID 配置 |
| `feishu_bot/机器人身份.md` | Bot 身份和行为规则 |
| `feishu_bot/session_manager.py` | Per-Chat 会话绑定 |
| `feishu_bot/utils.py` | 工具函数（重试、原子写入等） |
| `feishu_bot/taskqueue.py` | 文件任务队列（备用） |

### 6.3 关键设计

- **会话持久化**：历史对话和工具结果保存到文件，重启不丢失
- **Per-Chat 绑定**：每个群独立会话状态
- **API 重试**：飞书 API 调用自动重试（429/5xx）
- **Map 容量淘汰**：防止内存无限增长
- **ToolError 具体错误**：用户看到具体原因而非通用提示

### 6.4 Bot 命令

| 命令 | 说明 |
|------|------|
| `/health` | 查看 Bot 状态 |
| `/help` | 查看帮助 |
| 任何问题 | 普通对话 |
| 提到表名 + 查询/写入 | 触发 Base 工具 |
| 给出表格链接 + 操作 | 触发 Sheets 工具 |

---

## 7. 分析能力

### 7.1 四维拆解

| 维度 | 关注点 |
|------|--------|
| 前3秒·钩子 | 用什么方式抓住注意力？ |
| 核心卖点·主体 | 主体展示了什么？ |
| 视听语言·包装 | 画面风格、BGM、节奏如何？ |
| 转化路径·CTA | 结尾如何引导转化？ |

### 7.2 六维标签

| 维度 | 标签数 | 示例 |
|------|--------|------|
| hook_策略 | 13 | 反常识标题、教学攻略、福利展示... |
| 角色元素 | 8 | 黑皮、猫耳、Q版角色... |
| 场景元素 | 7 | 弹球/战斗、选择界面... |
| 制作方式 | 6 | K帧动画、真人口播... |
| 叙事模式 | 10 | 问题-解法、反差逆袭... |
| 画面风格 | 6 | 二次元精致渲染、Q版可爱风... |

---

## 8. 运维

### 8.1 启动 Bot

```powershell
# 方式1：运行启动脚本
C:\Users\admin\start_mova_feishu_bot.cmd

# 方式2：手动启动
cd D:\素材拆解分析库
.\venv\Scripts\python.exe -m feishu_bot.service
```

### 8.2 查看日志

```powershell
# Bot 日志
Get-Content D:\素材拆解分析库\logs\feishu_bot.log -Tail 50

# 工具审计
Get-Content D:\素材拆解分析库\logs\bot_tool_audit.jsonl -Tail 20
```

### 8.3 健康检查

```
在飞书群中 @Bot /health
```

### 8.4 重启 Bot

```powershell
# 停止
taskkill /f /im python.exe

# 启动
Start-Process -FilePath "C:\Users\admin\start_mova_feishu_bot.cmd" -WindowStyle Hidden
```

---

## 9. 安全要求

- API Key、App Secret、Token 不得写入日志或提交到代码库
- 敏感配置只从 `.env` 文件加载
- 删除操作需要二次确认
- 密钥不进入模型上下文

---

## 10. 待开发

| 优先级 | 事项 | 说明 |
|--------|------|------|
| P0 | 端到端 Base 更新验证 | 真实写入测试 |
| P0 | 写入后复读校验 | 确认写入值正确 |
| P1 | aggregate.py | 按标签维度聚合指标 |
| P1 | gen_dashboard.py | 生成 HTML 看板 |
| P1 | appsflyer_pull.py | AppsFlyer 数据拉取 |
| P2 | 视觉模型校准 | CLIP 标签准确率 |
| P2 | 人工复核流程 | 低置信度回收 |

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 2026.07.23-v1 | 2026-07-23 | 初始完整文档 |
