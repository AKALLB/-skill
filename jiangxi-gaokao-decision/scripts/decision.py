"""Deterministic decision guardrails for the Jiangxi admissions skill."""

DIMENSIONS = {
    "school_resources": "普通学生可获得的学校资源",
    "identity_coverage": "招聘身份覆盖面",
    "jiangxi_demand": "江西真实岗位需求",
    "industry_delivery": "产业兑现度",
    "ai_resilience": "AI任务韧性",
    "execution_fit": "个人执行匹配",
}


def hard_gate(profile, option):
    reasons = []
    if profile.get("public_only") and option.get("school_type") != "public":
        reasons.append("非公办")
    if not profile.get("accept_sino_foreign", False) and option.get("sino_foreign"):
        reasons.append("中外合作")
    max_tuition = profile.get("max_tuition")
    if max_tuition is not None and option.get("tuition") is not None:
        if option["tuition"] > max_tuition:
            reasons.append("学费超预算")
    if profile.get("province_only"):
        if option.get("province") != profile.get("province"):
            reasons.append("不在目标省份")
    required = set(option.get("required_subjects", []))
    selected = set(profile.get("subjects", []))
    if required and not required.issubset(selected):
        reasons.append("选科不满足")
    if option.get("medical_restriction_conflict"):
        reasons.append("体检限制冲突")
    if option.get("worst_major_acceptable") is False:
        reasons.append("组内最坏调剂不可接受")
    return {"eligible": not reasons, "reasons": reasons}


def confidence_for_claim(evidence, critical=False):
    official = [item for item in evidence if item.get("authority") == "official"]
    fresh_official = [
        item for item in official if item.get("freshness") in {"fresh", "current"}
    ]
    if critical and not official:
        return "insufficient"
    if fresh_official:
        return "high" if len(fresh_official) >= 2 else "medium"
    if official:
        return "low"
    return "insufficient"


def execution_guidance(level):
    if level == "high":
        return "允许选择竞争更强的路线，但必须设置阶段里程碑和退出条件。"
    if level == "low":
        return (
            "优先选择规则清晰、反馈及时、外部约束较强的培养路线；"
            "用课程、证书、实习和固定复盘代替对突然自律的假设。"
        )
    return "选择路径清晰且保留转换空间的路线，用学期结果逐步提高目标。"


def dimension_matrix(scores):
    """Return separate dimensions without manufacturing a universal total."""
    return {
        key: {
            "label": label,
            "rating": scores.get(key, "unknown"),
        }
        for key, label in DIMENSIONS.items()
    }
