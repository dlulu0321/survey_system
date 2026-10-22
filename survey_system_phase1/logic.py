"""Question validation, conditional navigation and statistics."""
from datetime import datetime, timezone
import math

SUPPORTED_TYPES = {"single", "multiple", "text", "number"}
SUPPORTED_OPERATORS = {"eq", "contains", "gt", "gte", "lt", "lte"}


class ValidationError(ValueError):
    pass


def validate_question(question, question_ids=None):
    if not isinstance(question, dict):
        raise ValidationError("每道题必须是对象")
    qid = str(question.get("id", "")).strip()
    title = str(question.get("title", "")).strip()
    qtype = question.get("type")
    if not qid or not title:
        raise ValidationError("题目 id 和标题不能为空")
    if qtype not in SUPPORTED_TYPES:
        raise ValidationError(f"不支持的题型：{qtype}")
    if question_ids is not None and qid in question_ids:
        raise ValidationError(f"题目 id 重复：{qid}")
    if qtype in {"single", "multiple"}:
        options = question.get("options")
        if not isinstance(options, list) or len(options) < 2:
            raise ValidationError(f"题目 {qid} 至少需要两个选项")
        normalized = [str(x).strip() for x in options]
        if any(not x for x in normalized) or len(set(normalized)) != len(normalized):
            raise ValidationError(f"题目 {qid} 的选项不能为空或重复")
    if qtype == "multiple":
        minimum = question.get("min_select", 0)
        maximum = question.get("max_select", len(question.get("options", [])))
        if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum < 0 or maximum < minimum:
            raise ValidationError(f"题目 {qid} 的多选数量限制不合法")
        if maximum > len(question.get("options", [])):
            raise ValidationError(f"题目 {qid} 的 max_select 超过选项数")
    if qtype == "text":
        minimum = question.get("min_length", 0)
        maximum = question.get("max_length", 10000)
        if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum < 0 or maximum < minimum:
            raise ValidationError(f"题目 {qid} 的文本长度限制不合法")
    if qtype == "number":
        for key in ("min_value", "max_value"):
            value = question.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValidationError(f"题目 {qid} 的 {key} 必须是有限数字")
        if question.get("min_value") is not None and question.get("max_value") is not None and question["min_value"] > question["max_value"]:
            raise ValidationError(f"题目 {qid} 的数字范围不合法")
    return question


def validate_questions(questions):
    if not isinstance(questions, list):
        raise ValidationError("questions 必须是数组")
    ids = set()
    for question in questions:
        validate_question(question, ids)
        ids.add(question["id"])
    for question in questions:
        for rule in question.get("jump_rules", []):
            if rule.get("operator") not in SUPPORTED_OPERATORS:
                raise ValidationError(f"跳转规则运算符不支持：{rule.get('operator')}")
            target = rule.get("target_question_id")
            if target is not None and target not in ids:
                raise ValidationError(f"跳转目标不存在：{target}")
            if target == question["id"]:
                raise ValidationError("跳转不能指向题目自身")
    return questions


def _condition_matches(answer, rule):
    op, expected = rule.get("operator"), rule.get("value")
    try:
        if op == "eq":
            if answer == expected:
                return True
            # HTML form values arrive as strings; permit numeric equality for number questions.
            try:
                if not isinstance(answer, bool) and not isinstance(expected, bool):
                    return float(answer) == float(expected)
            except (TypeError, ValueError):
                pass
            return str(answer) == str(expected)
        if op == "contains":
            return expected in answer if isinstance(answer, (list, str)) else False
        if op == "gt": return float(answer) > float(expected)
        if op == "gte": return float(answer) >= float(expected)
        if op == "lt": return float(answer) < float(expected)
        if op == "lte": return float(answer) <= float(expected)
    except (TypeError, ValueError):
        return False
    return False


def next_question_id(question, answer):
    """Return configured target ID, None for normal sequential navigation."""
    for rule in question.get("jump_rules", []):
        if _condition_matches(answer, rule):
            return rule.get("target_question_id")
    return None


def active_question_ids(questions, answers):
    """Walk the configured path, preventing cycles from causing an infinite loop."""
    ids = [q["id"] for q in questions]
    by_id = {q["id"]: q for q in questions}
    active = []
    current = ids[0] if ids else None
    seen = set()
    while current is not None and current in by_id and current not in seen:
        seen.add(current)
        active.append(current)
        target = next_question_id(by_id[current], answers.get(current))
        if target is None:
            # None means normal sequential navigation only when no rule matched.
            rules = by_id[current].get("jump_rules", [])
            matched = any(_condition_matches(answers.get(current), rule) for rule in rules)
            if matched:
                break
            idx = ids.index(current)
            current = ids[idx + 1] if idx + 1 < len(ids) else None
        else:
            current = target
    return set(active)


def validate_answers(questions, answers):
    if not isinstance(answers, dict):
        raise ValidationError("answers 必须是对象")
    qmap = {q["id"]: q for q in questions}
    errors = []
    for qid, value in answers.items():
        if qid not in qmap:
            errors.append(f"包含不存在的题目：{qid}")
    active_ids = active_question_ids(questions, answers)
    for q in questions:
        qid, qtype = q["id"], q["type"]
        if qid not in active_ids:
            continue
        value = answers.get(qid)
        empty = value is None or value == "" or value == []
        if q.get("required", False) and empty:
            errors.append(f"{q['title']}：此题为必答题")
            continue
        if empty:
            continue
        if qtype == "single":
            if not isinstance(value, str) or value not in q.get("options", []):
                errors.append(f"{q['title']}：请选择有效选项")
        elif qtype == "multiple":
            if not isinstance(value, list) or any(x not in q.get("options", []) for x in value):
                errors.append(f"{q['title']}：包含无效选项")
            else:
                if len(value) != len(set(value)):
                    errors.append(f"{q['title']}：选项不能重复")
                if len(value) < q.get("min_select", 0):
                    errors.append(f"{q['title']}：至少选择 {q.get('min_select', 0)} 项")
                if len(value) > q.get("max_select", len(q.get('options', []))):
                    errors.append(f"{q['title']}：最多选择 {q.get('max_select')} 项")
        elif qtype == "text":
            if not isinstance(value, str):
                errors.append(f"{q['title']}：请输入文本")
            else:
                if len(value) < q.get("min_length", 0): errors.append(f"{q['title']}：输入内容太短")
                if len(value) > q.get("max_length", 10000): errors.append(f"{q['title']}：输入内容太长")
        elif qtype == "number":
            if isinstance(value, bool):
                errors.append(f"{q['title']}：请输入数字")
                continue
            try:
                number = float(value)
                if not math.isfinite(number): raise ValueError()
                if q.get("integer_only", False) and not number.is_integer():
                    errors.append(f"{q['title']}：必须输入整数")
                if q.get("min_value") is not None and number < q["min_value"]:
                    errors.append(f"{q['title']}：不能小于 {q['min_value']}")
                if q.get("max_value") is not None and number > q["max_value"]:
                    errors.append(f"{q['title']}：不能大于 {q['max_value']}")
            except (TypeError, ValueError):
                errors.append(f"{q['title']}：请输入合法数字")
    if errors:
        raise ValidationError("；".join(errors))
    return answers


def survey_is_open(survey, now=None):
    now = now or datetime.now(timezone.utc)
    if survey.get("status") != "published":
        return False
    deadline = survey.get("deadline")
    if deadline:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if now >= deadline:
            return False
    return True


def calculate_statistics(survey, responses):
    result = {"survey_id": str(survey.get("_id", "")), "title": survey.get("title", ""), "response_count": len(responses), "questions": []}
    for q in survey.get("questions", []):
        values = [r.get("answers", {}).get(q["id"]) for r in responses if q["id"] in r.get("answers", {})]
        item = {"question_id": q["id"], "title": q["title"], "type": q["type"], "answer_count": len(values)}
        if q["type"] in {"single", "multiple"}:
            counts = {option: 0 for option in q.get("options", [])}
            for value in values:
                selected = [value] if q["type"] == "single" and isinstance(value, str) else value if isinstance(value, list) else []
                for option in selected:
                    if option in counts: counts[option] += 1
            item["option_counts"] = counts
        elif q["type"] == "text":
            item["answers"] = values
        elif q["type"] == "number":
            numbers = []
            for value in values:
                try:
                    number = float(value)
                    if math.isfinite(number): numbers.append(number)
                except (TypeError, ValueError):
                    pass
            item["numeric_answers"] = numbers
            item["average"] = sum(numbers) / len(numbers) if numbers else None
        result["questions"].append(item)
    return result
