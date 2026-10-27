import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import main

def response(content="", calls=()):
    return SimpleNamespace(message=SimpleNamespace(content=content,
        tool_calls=[SimpleNamespace(function=SimpleNamespace(name=n, arguments=a))
                    for n, a in calls]))

class ToolTests(unittest.TestCase):
    def test_known_inventory(self):
        self.assertEqual(main.lookup_inventory(" Widget "), {"found": True, "name": "Widget", "quantity": 12})

    def test_zero_stock(self):
        self.assertEqual(main.lookup_inventory("GADGET"), {"found": True, "name": "Gadget", "quantity": 0})

    def test_unknown_is_not_zero(self):
        result = main.lookup_inventory("Unknown")
        self.assertFalse(result["found"])
        self.assertNotIn("quantity", result)

    def test_list_items(self):
        self.assertEqual(main.list_inventory_items(), {"items": ["Widget", "Gadget"]})

    def test_sales(self):
        for name, units in ((" Widget ", 14), ("GADGET", 7)):
            with self.subTest(name=name):
                result = main.get_recent_sales(name)
                self.assertEqual(result["units_sold"], units)
                self.assertEqual(result["period_days"], 7)

    def test_unknown_sales(self):
        self.assertFalse(main.get_recent_sales("Unknown")["found"])

    def test_allowlist(self):
        self.assertEqual(main.execute_tool("place_order", {}), {"error": "Tool not allowed"})

    def test_argument_objects(self):
        for name in main.AVAILABLE_TOOLS:
            for args in (None, [], "Widget", 12):
                with self.subTest(name=name, args=args):
                    self.assertIn("error", main.execute_tool(name, args))

    def test_exact_argument(self):
        for name in ("lookup_inventory", "get_recent_sales"):
            for args in ({}, {"other": "Widget"}, {"item_name": "Widget", "extra": True}):
                with self.subTest(name=name, args=args):
                    self.assertIn("error", main.execute_tool(name, args))

    def test_nonempty_string(self):
        for name in ("lookup_inventory", "get_recent_sales"):
            for value in ("", "  ", None, 0, [], True):
                with self.subTest(name=name, value=value):
                    self.assertIn("error", main.execute_tool(name, {"item_name": value}))

    def test_list_rejects_arguments(self):
        self.assertIn("error", main.execute_tool("list_inventory_items", {"x": 1}))

    def test_dispatch_preserves_data(self):
        before = copy.deepcopy((main.INVENTORY, main.RECENT_SALES))
        result = main.execute_tool("lookup_inventory", {"item_name": "Widget"})
        result["quantity"] = 99
        self.assertEqual(main.execute_tool("list_inventory_items", {}), main.list_inventory_items())
        self.assertEqual(main.execute_tool("get_recent_sales", {"item_name": "Gadget"})["units_sold"], 7)
        self.assertEqual((main.INVENTORY, main.RECENT_SALES), before)

class LoopTests(unittest.TestCase):
    def run_chat(self, inputs, replies):
        client = Mock()
        snapshots = []
        iterator = iter(replies)
        def chat(**kwargs):
            snapshots.append(copy.deepcopy(kwargs))
            return next(iterator)
        client.chat.side_effect = chat
        output = io.StringIO()
        with patch.object(main, "Client", return_value=client), patch("builtins.input", side_effect=inputs), contextlib.redirect_stdout(output):
            main.main()
        return client, snapshots, output.getvalue()

    def test_greeting_and_blank(self):
        client, _, output = self.run_chat([" ", "Hello", "/exit"], [response("Hello!")])
        self.assertEqual(client.chat.call_count, 1)
        self.assertIn("Nemotron: Hello!", output)
        self.assertNotIn("Tool request:", output)

    def test_results_and_history(self):
        _, snapshots, output = self.run_chat(["Check stock", "Thanks", "/exit"],
            [response(calls=[("lookup_inventory", {"item_name": "Widget"}), ("get_recent_sales", {"item_name": "Gadget"})]),
             response("12 Widgets; 7 Gadgets sold."), response("Welcome")])
        results = [m for m in snapshots[1]["messages"] if isinstance(m, dict) and m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertEqual(json.loads(results[0]["content"])["quantity"], 12)
        self.assertEqual(json.loads(results[1]["content"])["units_sold"], 7)
        self.assertEqual(results[1]["tool_name"], "get_recent_sales")
        self.assertEqual(snapshots[2]["messages"][-1]["content"], "Thanks")
        self.assertEqual(snapshots[0]["options"], {"num_ctx": 4096})
        self.assertIn("Tool result:", output)

    def test_disallowed_tool_feedback(self):
        _, snapshots, _ = self.run_chat(["Order", "/exit"], [response(calls=[("place_order", {})]), response("Cannot order")])
        self.assertEqual(json.loads(snapshots[1]["messages"][-1]["content"]), {"error": "Tool not allowed"})

    def test_round_cap_not_total_tool_cap(self):
        replies = [response(calls=[("list_inventory_items", {}), ("list_inventory_items", {})]) for _ in range(main.MAX_STEPS)]
        client, _, output = self.run_chat(["Keep checking", "/exit"], replies)
        self.assertEqual(client.chat.call_count, 8)
        self.assertEqual(output.count("Tool result:"), 16)
        self.assertIn("Step limit reached; no final answer produced.", output)

if __name__ == "__main__":
    unittest.main()
