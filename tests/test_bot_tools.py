import unittest
from unittest.mock import Mock, patch

from feishu_bot.tools import FeishuBaseTools, ToolRegistry, execute_base_request, parse_tool_request


class BotToolTests(unittest.TestCase):
    def test_router_rejects_unknown_action(self):
        result = parse_tool_request("删除全部", lambda _p, _m: '{"action":"delete_all","params":{}}')
        self.assertEqual(result["action"], "chat")

    def test_router_accepts_list_records(self):
        result = parse_tool_request("最近5条", lambda _p, _m: '{"action":"base_list_records","params":{"limit":5}}')
        self.assertEqual(result["params"]["limit"], 5)

    def test_registry_has_no_delete_action(self):
        base = Mock()
        registry = ToolRegistry(base)
        with self.assertRaises(Exception):
            registry.execute("delete", {})

    def test_explicit_base_write_routes_to_base_agent(self):
        result = parse_tool_request("把这段内容录入游戏背景库表", lambda _p, _m: "{}")
        self.assertEqual(result["action"], "base_agent")

    def test_base_agent_schema_first_upsert(self):
        base = Mock()
        base.list_tables.return_value = {"tables": [{"id": "tbl1", "name": "游戏背景库"}]}
        base.list_fields.return_value = {"fields": [{"name": "游戏名称", "type": 1}, {"name": "核心玩法", "type": 1}]}
        base.upsert_by_key.return_value = {"executed": True, "operation": "update", "table_name": "游戏背景库", "record_id": "rec1", "matched_by": "exact", "changed_fields": ["核心玩法"]}
        model = Mock(return_value='{"operation":"upsert","key_field":"游戏名称","key_value":"心动防线","fields":{"核心玩法":"策略弹射"}}')
        result = execute_base_request("更新游戏背景库表：游戏名称心动防线，核心玩法策略弹射", base, model, audit=False)
        self.assertEqual(result["operation"], "update")
        base.upsert_by_key.assert_called_once()

    def test_router_receives_history_for_followup_confirmation(self):
        history = [
            {"role": "user", "content": "把素材 mat_1 的状态改成待复核"},
            {"role": "assistant", "content": "确认执行这项修改吗？"},
        ]
        model = Mock(return_value='{"action":"base_agent","params":{},"reason":"确认上一轮写操作"}')
        result = parse_tool_request("可以", model, history)
        self.assertEqual(result["action"], "base_agent")
        self.assertIn("把素材 mat_1", model.call_args.args[1][0]["content"])

    def test_base_agent_accepts_confirmed_followup_with_history(self):
        base = Mock()
        base.list_tables.return_value = {"tables": [{"id": "tbl1", "name": "素材主表"}]}
        base.list_fields.return_value = {"fields": [{"name": "素材ID", "type": 1}, {"name": "状态", "type": 1}]}
        base.upsert_by_key.return_value = {"executed": True, "operation": "update", "table_name": "素材主表", "record_id": "rec1", "matched_by": "exact", "changed_fields": ["状态"]}
        model = Mock(return_value='{"operation":"upsert","key_field":"素材ID","key_value":"mat_1","fields":{"状态":"待复核"}}')
        history = [{"role": "user", "content": "修改素材主表：素材ID mat_1，状态待复核"}, {"role": "assistant", "content": "确认执行吗？"}]
        result = execute_base_request("可以", base, model, history, audit=False)
        self.assertEqual(result["operation"], "update")


if __name__ == "__main__":
    unittest.main()
