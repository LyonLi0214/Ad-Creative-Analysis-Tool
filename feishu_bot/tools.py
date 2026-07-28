from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

try:
    from feishu_bot.utils import safe_json_parse, retry_with_backoff
except ModuleNotFoundError:
    from utils import safe_json_parse, retry_with_backoff


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("tools_config.json")
BUILD_ID = "2026.07.22-architecture-v2"
ALLOWED_ACTIONS = {
    "system_status", "base_list_tables", "base_list_fields", "base_list_records", "base_create_table",
    "base_create_record", "base_update_record", "base_upsert_game_background", "game_context",
    "base_agent", "appsflyer_status", "appsflyer_sync", "proactive_test", "chat",
    "sheets_agent",
}

GAME_BACKGROUND_FIELDS = {
    "游戏名称", "游戏类型", "游戏卖点", "题材风格", "核心玩法", "游戏标签",
    "核心理念", "游戏特色", "世界观与团队", "角色设定", "产品背景", "证据边界",
}


def canonical_game_name(value: str) -> str:
    name = str(value).strip("《》〈〉 ")
    aliases = {"心動防線": "心动防线", "心动防线": "心动防线", "心動JP": "心动防线", "心动JP": "心动防线"}
    return aliases.get(name, name)


def extract_game_background_fields(text: str) -> dict[str, str]:
    """Extract labelled game facts without asking the model to invent field values."""
    aliases = {
        "遊戲名稱": "游戏名称", "游戏名称": "游戏名称", "遊戲類型": "游戏类型", "游戏类型": "游戏类型",
        "遊戲賣點": "游戏卖点", "游戏卖点": "游戏卖点", "題材風格": "题材风格", "题材风格": "题材风格",
        "核心玩法": "核心玩法", "遊戲標籤": "游戏标签", "游戏标签": "游戏标签",
        "核心理念": "核心理念", "遊戲特色": "游戏特色", "游戏特色": "游戏特色",
        "世界觀 & 團隊": "世界观与团队", "世界观 & 团队": "世界观与团队",
        "世界觀與團隊": "世界观与团队", "世界观与团队": "世界观与团队",
        "角色設定": "角色设定", "角色设定": "角色设定",
    }
    label_pattern = re.compile(r"(?m)^(" + "|".join(re.escape(x) for x in sorted(aliases, key=len, reverse=True)) + r")\s*[:：]?\s*$")
    matches = list(label_pattern.finditer(text))
    output: dict[str, str] = {}
    for index, match in enumerate(matches):
        value = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        value = re.sub(r"^[-—]\s*", "", value).strip()
        if value:
            output[aliases[match.group(1)]] = value[:9000]
    if "游戏名称" in output:
        output["游戏名称"] = canonical_game_name(output["游戏名称"])
    world = output.get("世界观与团队", "")
    character_match = re.search(r"(?:四位角色形象|角色)[（(]([^）)]+)[）)]", world)
    if character_match:
        output["角色设定"] = character_match.group(1).replace("、", "、")
    return output


def knowledge_context() -> dict[str, Any]:
    import sqlite3
    db = ROOT / "data" / "mova_materials.sqlite3"
    if not db.is_file():
        return {"games": [], "appsflyer_apps": [], "status": "local knowledge database missing"}
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        games = [dict(row) for row in connection.execute("SELECT game_key,game_name,aliases_json,product_background,core_gameplay,target_market,material_context,evidence_boundary,updated_at FROM game_background")]
        apps = [dict(row) for row in connection.execute("SELECT app_id,game_key,platform,campaign_endpoint,raw_installs_endpoint,raw_events_endpoint,campaign_granularity,raw_granularity,material_match_rule,timezone_note FROM appsflyer_app_mapping")]
    except sqlite3.OperationalError:
        games, apps = [], []
    finally:
        connection.close()
    return {"games": games, "appsflyer_apps": apps}


class ToolError(RuntimeError):
    pass


class FeishuBaseTools:
    def __init__(self, app_id: str, app_secret: str, config_path: Path = CONFIG_PATH) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.base_token = os.environ.get("FEISHU_BASE_TOKEN") or self.config.get("base_token", "")
        self.material_table_id = self.config["material_table_id"]
        self._tenant_token = ""

    def _token(self) -> str:
        if self._tenant_token:
            return self._tenant_token
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=20,
        )
        payload = response.json()
        if not response.ok or payload.get("code") != 0:
            raise ToolError("无法获取飞书应用访问令牌")
        self._tenant_token = payload["tenant_access_token"]
        return self._tenant_token

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        response = requests.request(method, "https://open.feishu.cn" + path, headers=headers, timeout=30, **kwargs)
        payload = response.json()
        if not response.ok or payload.get("code") != 0:
            if payload.get("code") == 99991663:
                self._tenant_token = ""
            raise ToolError(f"飞书 Base 调用失败：code={payload.get('code')} msg={payload.get('msg', 'unknown')}")
        return payload.get("data") or {}

    def list_tables(self) -> dict[str, Any]:
        data = self._request("GET", f"/open-apis/bitable/v1/apps/{self.base_token}/tables", params={"page_size": 100})
        return {"base": self.config["base_url"], "tables": [{"id": x.get("table_id"), "name": x.get("name")} for x in data.get("items", [])]}

    def list_records(self, table_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        table_id = table_id or self.material_table_id
        limit = max(1, min(int(limit), 50))
        data = self._request("GET", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/records", params={"page_size": limit})
        return {"table_id": table_id, "count": len(data.get("items", [])), "has_more": bool(data.get("has_more")), "records": [{"record_id": x.get("record_id"), "fields": x.get("fields", {})} for x in data.get("items", [])]}

    def list_fields(self, table_id: str | None = None) -> dict[str, Any]:
        table_id = table_id or self.material_table_id
        data = self._request("GET", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields", params={"page_size": 100})
        return {"table_id": table_id, "fields": [{"id": x.get("field_id"), "name": x.get("field_name"), "type": x.get("type")} for x in data.get("items", [])]}

    def _table_id_by_name(self, name: str) -> str:
        tables = self.list_tables()["tables"]
        match = next((row["id"] for row in tables if row["name"] == name), None)
        if not match:
            raise ToolError(f"未找到数据表：{name}")
        return str(match)

    def resolve_table(self, name_or_id: str) -> dict[str, str]:
        value = str(name_or_id or "").strip()
        tables = self.list_tables()["tables"]
        match = next((row for row in tables if row["id"] == value or row["name"] == value), None)
        if not match:
            raise ToolError(f"未找到数据表：{value}")
        return {"id": str(match["id"]), "name": str(match["name"])}

    @staticmethod
    def _normalize(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
        return re.sub(r"[《》〈〉\s_\-—·・]+", "", text)

    def upsert_by_key(self, table: str, key_field: str, key_value: Any, fields: dict[str, Any]) -> dict[str, Any]:
        resolved = self.resolve_table(table)
        schema = self.list_fields(resolved["id"])["fields"]
        available = {row["name"] for row in schema}
        if key_field not in available:
            raise ToolError(f"表“{resolved['name']}”不存在业务键字段“{key_field}”")
        clean = {str(k): v for k, v in fields.items() if v not in (None, "")}
        missing = sorted(set(clean) - available)
        if missing:
            raise ToolError("目标表缺少字段：" + "、".join(missing))
        clean[key_field] = key_value
        rows = self.list_records(resolved["id"], 50)["records"]
        target_norm = self._normalize(key_value)
        exact = [row for row in rows if self._normalize(row["fields"].get(key_field)) == target_norm]
        matched_by = "exact"
        if not exact and target_norm:
            scored = sorted(
                ((SequenceMatcher(None, target_norm, self._normalize(row["fields"].get(key_field))).ratio(), row) for row in rows),
                key=lambda item: item[0], reverse=True,
            )
            if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
                exact = [scored[0][1]]; matched_by = f"unique_fuzzy:{scored[0][0]:.2f}"
        if len(exact) > 1:
            raise ToolError(f"业务键“{key_value}”匹配到多条记录，需要人工消歧")
        before = exact[0]["fields"] if exact else {}
        changed = {key: value for key, value in clean.items() if before.get(key) != value}
        if exact and not changed:
            return {"executed": True, "operation": "noop", "table_id": resolved["id"], "table_name": resolved["name"], "record_id": exact[0]["record_id"], "matched_by": matched_by, "changed_fields": []}
        if exact:
            result = self.update_record(exact[0]["record_id"], changed, resolved["id"])
            operation = "update"
        else:
            primary = next((row["name"] for row in schema if row.get("name") in {"名称", "标题"}), None)
            if primary:
                clean.setdefault(primary, str(key_value))
            result = self.create_record(clean, resolved["id"])
            operation = "create"
        return {"executed": True, "operation": operation, "table_id": resolved["id"], "table_name": resolved["name"], "record_id": result.get("record_id"), "matched_by": matched_by if exact else "new", "changed_fields": sorted(changed or clean)}

    def upsert_game_background(self, fields: dict[str, Any]) -> dict[str, Any]:
        clean = {key: str(value).strip()[:9000] for key, value in fields.items() if key in GAME_BACKGROUND_FIELDS and str(value).strip()}
        game_name = canonical_game_name(clean.get("游戏名称", ""))
        if not game_name:
            raise ToolError("游戏背景写入缺少游戏名称")
        clean["游戏名称"] = game_name
        table_id = self._table_id_by_name("游戏背景库")
        available = {row["name"] for row in self.list_fields(table_id)["fields"]}
        unsupported = sorted(set(clean) - available)
        if unsupported:
            raise ToolError("游戏背景库缺少字段：" + "、".join(unsupported))
        records = self.list_records(table_id, 50)["records"]
        record = next((row for row in records if canonical_game_name(row["fields"].get("游戏名称", "")) == game_name), None)
        clean["更新时间"] = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
        if record:
            result = self.update_record(record["record_id"], clean, table_id)
        else:
            clean.setdefault("名称", game_name)
            result = self.create_record(clean, table_id)
        db = ROOT / "data" / "mova_materials.sqlite3"
        if db.is_file():
            connection = sqlite3.connect(db)
            mapping = {
                "游戏类型": "game_type", "游戏卖点": "selling_points", "题材风格": "theme_style",
                "游戏标签": "game_tags", "核心理念": "core_concept", "游戏特色": "game_features",
                "世界观与团队": "worldview_team", "角色设定": "characters", "核心玩法": "core_gameplay",
                "产品背景": "product_background", "证据边界": "evidence_boundary",
            }
            assignments, values = [], []
            columns = {row[1] for row in connection.execute("PRAGMA table_info(game_background)")}
            for source, target in mapping.items():
                if source in clean and target in columns:
                    assignments.append(f'"{target}"=?'); values.append(clean[source])
            if assignments:
                assignments.append('"updated_at"=?'); values.append(clean["更新时间"]); values.append(game_name)
                connection.execute(f'UPDATE game_background SET {", ".join(assignments)} WHERE game_name=?', values)
                connection.commit()
            connection.close()
        return {**result, "table_id": table_id, "table_name": "游戏背景库", "game_name": game_name, "written_fields": sorted(clean)}

    def create_table(self, name: str) -> dict[str, Any]:
        name = str(name).strip()[:100]
        if not name:
            raise ToolError("缺少数据表名称")
        body = {"table": {"name": name, "default_view_name": "默认视图", "fields": [{"field_name": "名称", "type": 1}]}}
        data = self._request("POST", f"/open-apis/bitable/v1/apps/{self.base_token}/tables", json=body)
        table = data.get("table") or {}
        return {"created": True, "table_id": table.get("table_id"), "name": table.get("name", name)}

    def create_record(self, fields: dict[str, Any], table_id: str | None = None) -> dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            raise ToolError("缺少要写入的字段")
        data = self._request("POST", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id or self.material_table_id}/records", json={"fields": fields})
        record = data.get("record") or {}
        return {"created": True, "record_id": record.get("record_id"), "fields": record.get("fields", fields)}

    def update_record(self, record_id: str, fields: dict[str, Any], table_id: str | None = None) -> dict[str, Any]:
        if not record_id or not isinstance(fields, dict) or not fields:
            raise ToolError("更新记录必须提供 record_id 和字段")
        data = self._request("PUT", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id or self.material_table_id}/records/{record_id}", json={"fields": fields})
        record = data.get("record") or {}
        return {"updated": True, "record_id": record.get("record_id", record_id), "fields": record.get("fields", fields)}


def appsflyer_status() -> dict[str, Any]:
    manifest = ROOT / "appsflyer_data" / "latest_manifest.json"
    if not manifest.is_file():
        return {"configured": bool(os.getenv("APPSFLYER_API_TOKEN")), "last_sync": None}
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return {"configured": bool(os.getenv("APPSFLYER_API_TOKEN")), "last_sync": payload}


def appsflyer_sync() -> dict[str, Any]:
    day = (date.today() - timedelta(days=1)).isoformat()
    completed = subprocess.run(
        [sys.executable, "-m", "appsflyer_integration.sync", "pull", "--from", day, "--to", day],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if completed.returncode != 0:
        raise ToolError("AppsFlyer 同步失败，详情已记录在本机")
    return json.loads(completed.stdout)


# ── lark-cli sheets 集成 ──────────────────────────────────────────────────

LARK_CLI = os.environ.get("LARK_CLI_PATH", "lark-cli")
SHEETS_TIMEOUT = 120


def _run_lark_cli(args: list[str], timeout: int = SHEETS_TIMEOUT) -> dict[str, Any]:
    """Run a lark-cli command and return parsed JSON output."""
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        result = subprocess.run(
            [LARK_CLI, *args, "--format", "json"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except FileNotFoundError:
        raise ToolError("lark-cli 未安装或不在 PATH 中")
    except subprocess.TimeoutExpired:
        raise ToolError(f"lark-cli 命令超时（{timeout}s）")
    if result.stdout.strip():
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and data.get("ok") is False:
                error = data.get("error", {})
                raise ToolError(f"lark-cli 错误：{error.get('message', result.stderr[:500])}")
            return data
        except json.JSONDecodeError:
            pass
    if result.returncode != 0:
        raise ToolError(f"lark-cli 失败（exit {result.returncode}）：{result.stderr[:500]}")
    return {"raw": result.stdout.strip()}


def sheets_execute(user_request: str, call_model: Callable[[str, list[dict[str, str]]], str], history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Schema-first sheets agent: let the model plan the lark-cli call, then execute."""
    request_text = contextual_request(user_request, history)
    # Step 1: get workbook info if URL is present
    url_match = re.search(r'https?://[^\s<>"\']+|/[a-zA-Z0-9_-]{20,}', request_text)
    workbook_info = None
    if url_match:
        url = url_match.group(0)
        try:
            info = _run_lark_cli(["sheets", "+workbook-info", "--url", url])
            workbook_info = info.get("data", info)
        except ToolError:
            pass
    # Step 2: let the model plan the command
    plan_prompt = f"""你是飞书电子表格执行计划器。用户要操作飞书表格。只输出一个 JSON 对象，不要 markdown。
可用 lark-cli sheets 快捷命令（选择最匹配的一个）：
- +csv-get: 读取数据（纯值/CSV）
- +cells-get: 读取值+公式+样式+批注
- +csv-put: 写入纯文本值（CSV平铺）
- +table-put: 写入带类型的数据
- +cells-set: 写入公式/样式/批注/富文本
- +workbook-info: 查看表格结构
- +workbook-create: 新建表格
- +chart-create: 创建图表
- +pivot-create: 创建透视表
- +cells-search: 查找文本
- +cells-replace: 替换文本
- +filter-create: 创建筛选器

输出格式：{{"shortcut":"+xxx","args":["--url","...","--sheet-name","...","--range","..."],"needs_confirmation":false,"explanation":"简短说明"}}
规则：
1. 如果用户给了表格URL，必须用 --url 传入
2. sheet 定位用 --sheet-name 或 --sheet-id（不确定时先用 +workbook-info 查）
3. 写操作 needs_confirmation 设为 true
4. args 里每个 flag 和值分开成两个元素
5. 不确定具体参数时，先用 +workbook-info 查看结构

{f"已获取的工作簿信息：{json.dumps(workbook_info, ensure_ascii=False)}" if workbook_info else "未获取到工作簿信息（可能没有URL）"}"""
    raw = call_model(plan_prompt, [{"role": "user", "content": request_text}]).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        raise ToolError("无法生成有效的 sheets 执行计划")
    shortcut = plan.get("shortcut", "")
    if not shortcut.startswith("+"):
        raise ToolError(f"无效的 sheets 命令：{shortcut}")
    args = plan.get("args", [])
    needs_confirmation = plan.get("needs_confirmation", False)
    # Step 3: execute
    cli_args = ["sheets", shortcut, *args]
    result = _run_lark_cli(cli_args)
    return {
        "executed": True,
        "command": f"lark-cli {' '.join(cli_args)}",
        "shortcut": shortcut,
        "explanation": plan.get("explanation", ""),
        "needs_confirmation": needs_confirmation,
        "data": result.get("data", result),
    }


def contextual_request(text: str, history: list[dict[str, str]] | None = None) -> str:
    """Build a bounded transcript so follow-ups can be routed and planned safely."""
    recent = (history or [])[-6:]
    if not recent:
        return text
    lines = ["最近对话（仅用于消解本轮指代）："]
    for item in recent:
        role = "用户" if item.get("role") == "user" else "Bot"
        lines.append(f"{role}: {str(item.get('content', ''))[:1200]}")
    lines.append(f"用户本轮: {text}")
    return "\n".join(lines)


def parse_tool_request(text: str, call_model: Callable[[str, list[dict[str, str]]], str], history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    if re.search(r"(?:录入|寫入|写入|更新|修改|新增|创建记录|補充|补充|保存)", text) and re.search(r"(?:表|库|Base|base|多维)", text):
        return {"action": "base_agent", "params": {"request": text}, "reason": "明确的飞书Base业务操作，交给受约束执行器"}
    # 电子表格路由：检测 sheets 相关意图
    if re.search(r"(?:电子表格|表格|spreadsheet|sheet|工作表|工作簿)", text, re.IGNORECASE) and re.search(
        r"(?:创建|新建|读取|查看|写入|编辑|修改|统计|汇总|图表|透视|筛选|查找|替换|公式|格式|美化|列宽|行高)", text
    ):
        if re.search(r'https?://[^\s<>"\']+', text) or re.search(r'(?:sht|shcn)[a-zA-Z0-9_-]+', text):
            return {"action": "sheets_agent", "params": {"request": text}, "reason": "用户请求操作飞书电子表格（含URL或token）"}
    prompt = """你是 MOVA 工具路由器，只输出一个 JSON 对象，不要 markdown。你会收到最近对话和用户本轮消息；必须结合上下文消解"它、这条、可以、继续、结果"等指代。可选 action：
system_status；base_list_tables；base_list_fields；base_list_records；base_create_table；base_create_record；base_update_record；base_upsert_game_background；game_context；appsflyer_status；appsflyer_sync；proactive_test；sheets_agent；chat。
params 规则：base_list_fields 必须含 table_id；base_list_records 可含 limit(1-50)、table_id；base_create_table 必须含 name；base_create_record 必须含 fields 对象，可含 table_id；base_update_record 必须含 record_id、fields，可含 table_id；base_upsert_game_background 必须含 fields，其中必须有游戏名称。
sheets_agent 用于操作飞书电子表格（当用户提到表格URL、spreadsheet、电子表格、工作表、并给出URL或token时选择）。
询问游戏背景、App ID、AppsFlyer接口或数据粒度时选择 game_context；只有用户本轮明确要求创建或修改，或本轮明确确认紧邻上一轮待确认的写操作时，才选择 base_agent；询问上一轮执行结果时 action=chat；用户明确要求测试主动消息时选 proactive_test；删除、权限修改、任意命令执行一律 action=chat。普通问答 action=chat。输出格式：{"action":"...","params":{},"reason":"简短原因"}。"""
    raw = call_model(prompt, [{"role": "user", "content": contextual_request(text, history)}]).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        return {"action": "chat", "params": {}, "reason": "路由解析失败"}
    if request.get("action") not in ALLOWED_ACTIONS or not isinstance(request.get("params", {}), dict):
        return {"action": "chat", "params": {}, "reason": "未允许的工具"}
    return request


def _json_object(raw: str | None, retries: int = 2) -> dict[str, Any]:
    """Parse JSON from model output with retry logic."""
    for attempt in range(retries + 1):
        result = safe_json_parse(raw)
        if result is not None:
            return result
        if attempt < retries:
            time.sleep(0.5)
            continue
        raise ToolError(f"模型返回了无效JSON：{(raw or '')[:200]}")
    raise ToolError("模型返回了空内容，无法解析为JSON")



def execute_base_request(text: str, base: FeishuBaseTools, call_model: Callable[[str, list[dict[str, str]]], str], history: list[dict[str, str]] | None = None, audit: bool = True) -> dict[str, Any]:
    """Schema-first Base agent: discover -> plan -> validate -> execute -> receipt."""
    request_text = contextual_request(text, history)
    catalog = base.list_tables()["tables"]
    named = [row for row in catalog if row["name"] in request_text or row["id"] in request_text]
    if len(named) == 1:
        target = named[0]
    else:
        choose_prompt = "你是飞书Base表选择器。只输出JSON：{\"table\":\"真实表名\"}。只能从目录选择，不得虚构。目录：" + json.dumps(catalog, ensure_ascii=False)
        choice = _json_object(call_model(choose_prompt, [{"role": "user", "content": request_text}]))
        target = next((row for row in catalog if row["name"] == choice.get("table") or row["id"] == choice.get("table")), None)
        if not target:
            raise ToolError("无法从真实目录确定目标表")
    schema = base.list_fields(target["id"])["fields"]
    planner_prompt = """你是飞书Base受约束执行计划器。只输出JSON，不要解释。
允许操作仅有：
1. upsert：{"operation":"upsert","key_field":"字段名","key_value":"值","fields":{"真实字段名":"用户明确提供的值"}}
2. read：{"operation":"read","limit":10}
规则：字段名只能来自真实schema；写入值只能来自用户原文，不得补写；更新/录入时优先选游戏名称、素材ID、名称、映射键、事实键等稳定字段作key；没有稳定键则报错：{"operation":"error","message":"原因"}。
真实schema：""" + json.dumps(schema, ensure_ascii=False)
    plan = _json_object(call_model(planner_prompt, [{"role": "user", "content": request_text}]))
    operation = plan.get("operation")
    if operation == "error":
        raise ToolError(str(plan.get("message") or "无法生成安全执行计划"))
    if operation == "read":
        return {"executed": True, "operation": "read", "table_name": target["name"], **base.list_records(target["id"], plan.get("limit", 10))}
    if operation != "upsert":
        raise ToolError("不允许的Base操作")
    explicit_write = bool(re.search(r"(?:录入|寫入|写入|更新|修改|新增|创建|補充|补充|保存|改一下|改成)", text))
    confirmed_followup = text.strip().lower() in {"可以", "确认", "确认执行", "继续", "执行", "好", "好的"} and bool(history)
    if not (explicit_write or confirmed_followup):
        raise ToolError("用户没有明确授权写入")
    key_field, key_value, fields = plan.get("key_field"), plan.get("key_value"), plan.get("fields")
    if not isinstance(key_field, str) or key_value in (None, "") or not isinstance(fields, dict) or not fields:
        raise ToolError("写入计划缺少业务键或字段")
    result = base.upsert_by_key(target["id"], key_field, key_value, fields)
    if audit:
        audit_path = ROOT / "logs" / "bot_tool_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": __import__("datetime").datetime.now().astimezone().isoformat(), "action": "base_agent", "table": target["name"], "plan": plan, "result": result}, ensure_ascii=False) + "\n")
    return result


class ToolRegistry:
    def __init__(self, base: FeishuBaseTools, call_mimo: Callable[[str, list[dict[str, str]]], str] | None = None) -> None:
        self.base = base
        self._call_mimo = call_mimo

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "system_status":
            return {
                "bot": "running", "build": BUILD_ID,
                "base": {"url": self.base.config.get("base_url"), "material_table_name": self.base.config.get("material_table_name")},
                "capabilities": ["schema_discovery", "record_read", "schema_validated_upsert", "business_key_dedup", "execution_receipt", "audit_log", "sheets_read", "sheets_write", "sheets_chart", "sheets_pivot"],
                "appsflyer": appsflyer_status(),
            }
        if action == "base_list_tables":
            return self.base.list_tables()
        if action == "base_list_fields":
            return self.base.list_fields(params.get("table_id"))
        if action == "base_list_records":
            return self.base.list_records(params.get("table_id"), params.get("limit", 10))
        if action == "base_create_table":
            return self.base.create_table(params.get("name", ""))
        if action == "base_create_record":
            return self.base.create_record(params.get("fields", {}), params.get("table_id"))
        if action == "base_update_record":
            return self.base.update_record(params.get("record_id", ""), params.get("fields", {}), params.get("table_id"))
        if action == "base_upsert_game_background":
            return self.base.upsert_game_background(params.get("fields", {}))
        if action == "game_context":
            return knowledge_context()
        if action == "appsflyer_status":
            return appsflyer_status()
        if action == "appsflyer_sync":
            return appsflyer_sync()
        if action == "sheets_agent":
            return sheets_execute(params.get("request", ""), self._call_mimo)
        raise ToolError("该请求不需要调用工具")
