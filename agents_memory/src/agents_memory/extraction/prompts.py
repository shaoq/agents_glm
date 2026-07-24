EXTRACTION_SYSTEM_PROMPT = """\
从对话中抽取未来可复用的独立记忆。仅接受用户明确表达、用户确认或工具验证的信息。
禁止把 assistant 建议、推断或旧记忆当成用户事实。保留“可能、考虑、暂时”等不确定性。
event 还需输出 event_frame：actor、predicate、object、location、status、
polarity、modality 和 temporal_anchor.raw_text；未知字段必须写 "unknown"，不得猜测。
输出 JSON 对象：{"candidates": [{"content": "...", "type": "fact|event",
"importance": 1-10, "confidence": 0-1, "source_message_ids": ["..."],
"source_kind": "user_explicit|user_confirmed|tool_verified",
"event_frame": null}]}。无候选时返回空数组。
"""
