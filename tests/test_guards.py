"""代码层面的护栏测试：把「口头约定」变成会失败的检查。

1. 工具 schema 从类型注解推导（P3 前所有参数都是 string，模型只能猜）
2. 只有唯一入口能实例化真实 LLM（否则预算护栏会被绕过）
"""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Literal, Optional

from app import tools as tools_mod

ROOT = Path(__file__).resolve().parent.parent


class ToolSchemaTest(unittest.TestCase):
    def test_int_and_bool_are_typed(self):
        def f(count: int, verbose: bool) -> str:
            """示例工具。"""
            return ""

        fn = tools_mod.schema_for(f)["function"]
        props = fn["parameters"]["properties"]
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["verbose"]["type"], "boolean")
        self.assertEqual(fn["parameters"]["required"], ["count", "verbose"])

    def test_optional_with_default_not_required(self):
        def f(tier: Optional[str] = None, top_k: int = 3) -> str:
            """示例工具。"""
            return ""

        fn = tools_mod.schema_for(f)["function"]
        self.assertEqual(fn["parameters"]["required"], [])
        self.assertEqual(fn["parameters"]["properties"]["top_k"]["type"], "integer")

    def test_literal_becomes_enum(self):
        def f(mode: Literal["fast", "careful"]) -> str:
            """示例工具。"""
            return ""

        prop = tools_mod.schema_for(f)["function"]["parameters"]["properties"]["mode"]
        self.assertEqual(prop["type"], "string")
        self.assertEqual(prop["enum"], ["fast", "careful"])

    def test_list_items_described(self):
        def f(tags: list[str]) -> str:
            """示例工具。"""
            return ""

        prop = tools_mod.schema_for(f)["function"]["parameters"]["properties"]["tags"]
        self.assertEqual(prop["type"], "array")
        self.assertEqual(prop["items"]["type"], "string")

    def test_annotation_absent_falls_back_to_string(self):
        def f(anything) -> str:  # noqa: ANN001 - 故意不写注解
            """示例工具。"""
            return ""

        prop = tools_mod.schema_for(f)["function"]["parameters"]["properties"]["anything"]
        self.assertEqual(prop["type"], "string")

    def test_args_section_becomes_description(self):
        fn = tools_mod.schema_for(tools_mod.save_feedback, "save_feedback")["function"]
        self.assertEqual(fn["parameters"]["properties"]["note"]["description"], "反馈内容，一句话即可")

    def test_registered_tools_all_have_schema(self):
        names = [s["function"]["name"] for s in tools_mod.tool_schemas()]
        self.assertEqual(sorted(names), ["next_question", "save_feedback"])


class LLMFactoryGuardTest(unittest.TestCase):
    """只有 app/main.py 能实例化 RealLLM；其他模块一旦直接 new，预算护栏就被绕过了。"""

    ALLOWED = {"app/main.py", "app/llm.py"}  # llm.py 是定义处，main.py 是唯一入口

    def test_real_llm_only_instantiated_through_budget(self):
        offenders = []
        for py in (ROOT / "app").glob("*.py"):
            rel = f"app/{py.name}"
            if rel in self.ALLOWED:
                continue
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if "RealLLM(" not in line:
                    continue
                # 允许的就一种写法：BudgetedLLM(RealLLM(), ...)，同一行就必须被包住
                if "BudgetedLLM(" not in line:
                    offenders.append(f"{rel}:{lineno}")
        self.assertEqual(offenders, [], f"这些位置绕过了预算入口: {offenders}")


if __name__ == "__main__":
    unittest.main()
