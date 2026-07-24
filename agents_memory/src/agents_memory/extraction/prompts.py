EXTRACTION_SYSTEM_PROMPT = """\
从对话中抽取未来可复用的独立记忆。仅接受用户明确表达、用户确认或工具验证的信息。
禁止把 assistant 建议、推断或旧记忆当成用户事实。保留“可能、考虑、暂时”等不确定性。
输出 JSON 对象：{"candidates": [{"content": "...", "type": "fact|event",
"importance": 1-10, "confidence": 0-1, "source_message_ids": ["..."],
"source_kind": "user_explicit|user_confirmed|tool_verified"}]}。无候选时返回空数组。
"""
