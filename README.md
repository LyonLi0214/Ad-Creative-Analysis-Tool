# Ad Creative Analysis Tool

游戏素材拆解分析协作平台 — 四维拆解 + 6维标签体系 + AI分析管道

跨平台运行（Windows / macOS），核心是 Python 分析脚本 + AI 驱动的素材拆解框架。

## 项目架构

```
Ad-Creative-Analysis-Tool/
├── *.py                  # 核心分析脚本（并行分析、管道、同步、部署）
├── feishu_bot/           # 飞书 Bot 服务（长连接消息处理 + Base 操作）
├── local_recognition/    # 本地识别管道（OCR/ASR/视觉，不含模型权重）
├── appsflyer_integration/ # AppsFlyer 投放数据集成
├── migration/            # NocoDB → 飞书Base 迁移工具
├── tests/                # 测试用例
├── schema/               # 🆕 架构定义（分析框架、标签体系、表结构）
├── docs/                 # 架构文档
├── label_schema.yaml     # 6维标签体系配置
└── 素材拆解分析模板.xlsx  # 5-Sheet Excel 输入输出框架
```

## 核心分析框架

### 四维拆解

| 维度 | 关注点 |
|------|--------|
| **前3秒·钩子** | 视觉冲击 / 听觉锚点 / 文案悬念 |
| **核心卖点·主体** | 玩法展示 / 心理诉求 / 角色情感 |
| **视听语言·包装** | 节奏设计 / 风格调性 / 文化适配 |
| **转化路径·CTA** | 类型 / 文案 / 位置 |

### 6维标签体系

`hook_策略` · `角色元素` · `场景元素` · `制作方式` · `叙事模式` · `画面风格`

详见 `label_schema.yaml` 和 `schema/label_system.yaml`

## 快速开始

### macOS

```bash
# 1. 克隆
git clone git@github.com:LyonLi0214/Ad-Creative-Analysis-Tool.git
cd Ad-Creative-Analysis-Tool

# 2. Python 环境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 配置密钥
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 4. 运行分析
python analyze_parallel.py --game 心动防线 --limit 5
```

### Windows

```cmd
git clone git@github.com:LyonLi0214/Ad-Creative-Analysis-Tool.git
cd Ad-Creative-Analysis-Tool
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python analyze_parallel.py --game 心动防线 --limit 5
```

## 依赖

- Python 3.10+
- 见 `requirements.txt`（openpyxl, pyyaml, requests, oss2, opencv-python-headless, numpy, imageio-ffmpeg）
- **可选**：飞书 Bot 需要 [lark-cli](https://www.npmjs.com/package/@lark-project/cli)
- **可选**：本地识别需要 CLIP 模型（参考 `local_recognition/` 内文档）

## 迁移说明

本项目是从原 `D:\素材拆解分析库` 剥离架构+脚本后迁移到 GitHub 的独立仓库：
- ❌ 不包含视频素材（太大）
- ❌ 不包含 ML 模型权重（`.models/`）
- ❌ 不包含 API 密钥（`.env` 在 gitignore 中）
- ✅ 仅含架构定义、分析脚本、管道编排、Bot 服务、配置模板

## License

Internal use.
