RELATION_SYSTEM_PROMPT = """\
判断一条候选记忆与全部已有记忆的关系。每个已有 ID 只能标注：
duplicate、supplement、contradict、correct 或 none。不得发明 ID，不得输出存储动作。
输出 JSON：{"relations": [{"memory_id": "...", "relation": "...", "reason": "..."}]}。
"""
