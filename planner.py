# ============================================================
# GENERAL AGENT PLANNER CONTRACT
# ============================================================
#
# The planner receives:
#   goal   : natural-language user goal
#   state  : current Android UI observation
#   memory : persistent task memory
#
# It must return ONE action dictionary.
#
# Supported action families currently exposed by the bridge:
#
#   open_app
#   tap
#   type
#   swipe
#   scroll
#   long_press
#   back
#   wait_until
#   screenshot
#   done
#
# This contract deliberately does not depend on Gemini, OpenAI,
# Claude, or any other specific model. A model adapter can later
# replace the rule-based decision function without changing the
# Android execution layer.
# ============================================================

import re


APP_PACKAGES = {
    "telegram": "org.telegram.messenger",
    "تلغرام": "org.telegram.messenger",
    "تيليجرام": "org.telegram.messenger",
    "instagram": "com.instagram.android",
    "انستغرام": "com.instagram.android",
    "إنستغرام": "com.instagram.android",
    "whatsapp": "com.whatsapp",
    "واتساب": "com.whatsapp",
    "youtube": "com.google.android.youtube",
    "يوتيوب": "com.google.android.youtube",
    "facebook": "com.facebook.katana",
    "فيسبوك": "com.facebook.katana",
    "twitter": "com.twitter.android",
    "تويتر": "com.twitter.android",
    "settings": "com.android.settings",
    "الإعدادات": "com.android.settings",
}


# ============================================================
# GENERAL ACTION SPACE
# ============================================================

ACTION_TYPES = {
    "open_app",
    "tap",
    "type",
    "swipe",
    "scroll",
    "long_press",
    "back",
    "wait_until",
    "screenshot",
    "tool",
    "done",
}


def make_action(action, **kwargs):
    """
    Create one normalized agent action.

    The planner must always return one action dictionary.
    This keeps the AI layer independent from Android execution.
    """
    if action not in ACTION_TYPES:
        raise ValueError(f"Unsupported action: {action}")

    result = {
        "action": action,
    }

    result.update(kwargs)
    return result


def build_agent_context(goal, state, memory):
    """
    Build the information that an AI planner needs.

    This function deliberately contains no model/API code.
    A future model adapter can consume this context directly.
    """
    state = state or {}
    memory = memory or {}

    from tool_registry import list_tools_for_ai

    return {
        "goal": goal,
        "current_package": state.get("package"),
        "timestamp": state.get("timestamp"),
        "elements": state.get("elements", []),
        "step": memory.get("step", 0),
        "last_action": memory.get("last_action"),
        "last_result": memory.get("last_result"),
        "history": memory.get("history", []),
        "tools": list_tools_for_ai(),
    }

def validate_agent_action(action):
    """
    Validate a planner result before it reaches execute_action().
    """
    if not isinstance(action, dict):
        return False

    action_type = action.get("action")

    if action_type not in ACTION_TYPES:
        return False

    if action_type == "open_app":
        return bool(action.get("package"))

    if action_type == "tap":
        return (
            action.get("element_id") is not None
            or isinstance(action.get("selector"), dict)
        )

    if action_type == "type":
        return isinstance(action.get("text"), str)

    if action_type == "wait_until":
        return isinstance(action.get("condition"), dict)

    if action_type == "tool":
        tool = action.get("tool")
        arguments = action.get("arguments", {})

        return (
            isinstance(tool, str)
            and bool(tool)
            and isinstance(arguments, dict)
        )

    if action_type == "scroll":
        direction = action.get("direction")

        if direction is not None and direction not in (
            "up",
            "down",
            "left",
            "right",
        ):
            return False

        return True

    if action_type == "swipe":
        return all(
            key in action
            for key in ("x1", "y1", "x2", "y2")
        )

    return True


def plan_with_rules(goal, state, memory):
    """
    Existing rule-based planner.

    Kept as a fallback while the general AI planner is being added.
    """
    return plan_next(goal, state, memory)



def extract_text(goal):
    # نلتقط النص الموجود بين "..."
    match = re.search(r'["“](.+?)["”]', goal)

    if match:
        return match.group(1)

    # دعم هدفنا الحالي
    # إذا لم توجد علامات اقتباس، حاول استخراج النص بعد "اكتب"
    match = re.search(r'اكتب\s+(.+?)(?:\s+في\s+|\s+ثم\s+|$)', goal)

    if match:
        text = match.group(1).strip()
        if text:
            return text

    return None


def detect_app(goal):
    goal_lower = goal.lower()

    for name, package in APP_PACKAGES.items():
        if name in goal_lower:
            return package

    return None


def find_editable_target(state):
    if not state:
        return None

    for element in state.get("elements", []):
        cls = element.get("class", "")

        if (
            element.get("enabled") is True
            and (
                element.get("editable") is True
                or "EditText" in cls
            )
        ):
            return element

    return None



def find_tap_target(state):
    if not state:
        return None

    for element in state.get("elements", []):
        if (
            element.get("enabled") is True
            and element.get("clickable") is True
        ):
            return element

    return None

# ============================================================
# AI PLANNER INTERFACE
# ============================================================

def prepare_ai_planner_input(goal, state, memory):
    """
    Prepare a compact, model-independent planning context.

    No API, provider, network, or model dependency is used here.
    """
    return build_agent_context(
        goal,
        state,
        memory
    )


def accept_ai_action(action):
    """
    Accept an action produced by an external AI model.

    The model is NOT executed directly.
    The action must first pass validation.
    """
    if not validate_agent_action(action):
        return None

    return make_action(
        action.get("action"),
        **{
            key: value
            for key, value in action.items()
            if key != "action"
        }
    )


def plan_from_ai_result(ai_result):
    """
    Convert a raw AI result into one safe normalized action.

    Expected input:
        {
            "action": "tap",
            ...
        }

    Returns:
        normalized action dictionary
        or None when invalid.
    """
    if not isinstance(ai_result, dict):
        return None

    return accept_ai_action(ai_result)


def plan_next(goal, state, memory):
    """
    Planner مستقل عن Executor.
    يقرر الخطوة التالية اعتمادًا على:
    - الهدف
    - الحالة الحالية
    - ذاكرة المهمة
    """

    package = detect_app(goal)
    text = extract_text(goal)

    if not package:
        return None

    if not text:
        return None

    current_package = state.get("package") if state else None

    # --------------------------------
    # 1. انتهت المهمة
    # --------------------------------

    if (
        memory.get("typed")
        and memory.get("returned")
    ):
        return {
            "action": "done"
        }

    # --------------------------------
    # 2. بعد الكتابة نحتاج الرجوع
    # --------------------------------

    if memory.get("typed") and not memory.get("returned"):
        return {
            "action": "back"
        }

    # --------------------------------
    # 3. التطبيق غير مفتوح
    # --------------------------------

    if current_package != package:
        return {
            "action": "open_app",
            "package": package
        }

    # --------------------------------
    # 4. نحتاج كتابة النص
    # --------------------------------

    focused = None

    if state:
        for element in state.get("elements", []):
            cls = element.get("class", "")

            if (
                element.get("focused") is True
                and element.get("enabled") is True
                and (
                    element.get("editable") is True
                    or "EditText" in cls
                )
            ):
                focused = element
                break

    if focused:
        return {
            "action": "type",
            "text": text
        }

    # --------------------------------
    # 5. البحث عن حقل قابل للتحرير
    # --------------------------------

    target = find_editable_target(state)

    if target:
        return {
            "action": "tap",
            "selector": {
                "class_name": target.get("class", ""),
                "bounds": target.get("bounds", [])
            }
        }

    # --------------------------------
    # 6. البحث عن عنصر قابل للنقر
    # --------------------------------

    tap_target = find_tap_target(state)

    if tap_target:
        return {
            "action": "tap",
            "selector": {
                "class_name": tap_target.get("class", ""),
                "bounds": tap_target.get("bounds", [])
            }
        }

    return None
