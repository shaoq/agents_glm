RELATION_SYSTEM_PROMPT = """\
判断一条候选记忆与全部已有记忆的关系。每个已有 ID 只能标注：
duplicate、supplement、contradict、correct 或 none。不得发明 ID，不得输出存储动作。
event 还必须分别标注 identity（same_event/different_event/unknown）和 temporal
（same_window/before/after/overlap/unknown），内容关系不得代替事件身份。
证据不足必须输出 unknown，不得猜测；明确纠错另输出 explicit_correction。
输出 JSON：{"relations": [{"memory_id": "...", "relation": "...",
"identity": "...", "temporal": "...", "explicit_correction": false,
"confidence": 0-1, "reason": "..."}]}。
"""
