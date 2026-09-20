import json
import time
import uuid
from pathlib import Path

from planner import plan_next
from ai_planner import AIPlanner
from tool_router import ToolRouter
from tool_registry import validate_tool_call

COMMAND = Path("/sdcard/agent_command.json")
RESULT = Path("/sdcard/agent_result.json")
UI_STATE = Path("/sdcard/ui_state.json")

MAX_STEPS = 12


# ============================================================
# JSON / STATE
# ============================================================

def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def observe():
    """Return the newest UI snapshot."""
    for _ in range(30):
        state = read_json(UI_STATE)

        if (
            state
            and isinstance(state.get("elements"), list)
            and state.get("package")
        ):
            return state

        time.sleep(0.1)

    return None


# ============================================================
# ELEMENT MATCHING
# ============================================================

def matches(element, selector):
    """Semantic selector matching."""

    if "text" in selector:
        if element.get("text", "") != selector["text"]:
            return False

    if "content_desc" in selector:
        if element.get("content_desc", "") != selector["content_desc"]:
            return False

    if "resource_id" in selector:
        if element.get("resource_id", "") != selector["resource_id"]:
            return False

    if "class_name" in selector:
        if element.get("class", "") != selector["class_name"]:
            return False

    if "focused" in selector:
        if element.get("focused") != selector["focused"]:
            return False

    if "clickable" in selector:
        if element.get("clickable") != selector["clickable"]:
            return False

    if "enabled" in selector:
        if element.get("enabled") != selector["enabled"]:
            return False

    if "bounds" in selector:
        if element.get("bounds") != selector["bounds"]:
            return False

    return True


def find_element(selector):
    """Find an element from the CURRENT snapshot."""

    state = observe()

    if not state:
        return None

    for element in state.get("elements", []):
        if matches(element, selector):
            return element

    return None


def find_focused_edittext():
    """Find the current focused editable field."""

    state = observe()

    if not state:
        return None

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
            return element

    return None


# ============================================================
# COMMAND EXECUTION
# ============================================================

def element_matches(element, condition):
    if "text" in condition and element.get("text", "") != condition["text"]:
        return False
    if "content_desc" in condition and element.get("content_desc", "") != condition["content_desc"]:
        return False
    if "resource_id" in condition and element.get("resource_id", "") != condition["resource_id"]:
        return False
    if "class_name" in condition and element.get("class", "") != condition["class_name"]:
        return False
    if "focused" in condition and element.get("focused") != condition["focused"]:
        return False
    if "clickable" in condition and element.get("clickable") != condition["clickable"]:
        return False
    return True


def condition_matches(state, condition):
    """Check whether 'condition' is satisfied by the current state.
    A bare {"package": "..."} checks only the current app.
    Otherwise, checks whether any element matches the given fields.
    """
    if not state:
        return False

    if "package" in condition and len(condition) == 1:
        return state.get("package") == condition["package"]

    for element in state.get("elements", []):
        if element_matches(element, condition):
            return True

    return False


def wait_until(condition, timeout_ms=5000, poll_interval=0.2):
    """Poll observe() until 'condition' matches or the timeout elapses."""

    deadline = time.time() + (timeout_ms / 1000.0)

    while time.time() < deadline:
        state = observe()

        if condition_matches(state, condition):
            return True

        time.sleep(poll_interval)

    return False


def send(action):
    """Send one command and wait for its matching result."""

    command_id = uuid.uuid4().hex

    payload = dict(action)
    payload["command_id"] = command_id

    try:
        RESULT.unlink()
    except FileNotFoundError:
        pass

    COMMAND.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8"
    )

    for _ in range(60):
        time.sleep(0.1)

        result = read_json(RESULT)

        if (
            result
            and result.get("timestamp")
            and result.get("command_id") == command_id
        ):
            return result

    return {
        "ok": False,
        "message": "timeout waiting for agent_result.json"
    }


def wait_for_new_ui(old_timestamp=None):
    """Wait for AccessibilityService to publish a newer UI state."""

    for _ in range(30):
        state = observe()

        if state:
            timestamp = state.get("timestamp")

            if (
                old_timestamp is None
                or timestamp != old_timestamp
            ):
                return state

        time.sleep(0.1)

    return observe()


# ============================================================
# ACTION RESOLUTION
# ============================================================

def resolve_action(action):
    """
    Convert semantic action into the temporary element_id
    from the CURRENT UI snapshot.
    """

    action = dict(action)
    action_type = action.get("action")

    if action_type == "type":

        selector = action.get("selector")

        if selector:
            element = find_element(selector)
        else:
            element = find_focused_edittext()

        if element is None:
            print("ERROR: current editable element not found")
            return None

        action["element_id"] = element["id"]

        print(
            "Resolved TYPE:",
            element["id"],
            "|",
            element.get("class"),
            "|",
            repr(element.get("text"))
        )

        action.pop("selector", None)

    elif action_type == "tap":

        selector = action.get("selector")

        if selector is None:
            print("ERROR: TAP requires a selector")
            return None

        element = find_element(selector)

        if element is None:
            print(
                "ERROR: TAP target not found in CURRENT ui_state:",
                selector
            )
            return None

        action["element_id"] = element["id"]

        print(
            "Resolved TAP:",
            element["id"],
            "|",
            element.get("class"),
            "|",
            repr(element.get("text")),
            "|",
            repr(element.get("content_desc"))
        )

        action.pop("selector", None)

    elif action_type == "long_press":

        selector = action.get("selector")

        if selector is None:
            print("ERROR: LONG_PRESS requires a selector")
            return None

        element = find_element(selector)

        if element is None:
            print(
                "ERROR: LONG_PRESS target not found in CURRENT ui_state:",
                selector
            )
            return None

        action["element_id"] = element["id"]

        print(
            "Resolved LONG_PRESS:",
            element["id"],
            "|",
            element.get("class"),
            "|",
            repr(element.get("text"))
        )

        action.pop("selector", None)

    elif action_type == "scroll":

        selector = action.get("selector")

        if selector:
            element = find_element(selector)

            if element is not None:
                action["element_id"] = element["id"]

                print(
                    "Resolved SCROLL container:",
                    element["id"],
                    "|",
                    element.get("class")
                )

            action.pop("selector", None)

    return action


# ============================================================
# VERIFICATION
# ============================================================

def verify_type(text):
    """Verify that the current focused EditText contains the text."""

    state = observe()

    if not state:
        return False

    for element in state.get("elements", []):
        cls = element.get("class", "")

        if (
            element.get("focused") is True
            and "EditText" in cls
            and element.get("text", "") == text
        ):
            return True

    return False


def verify_package(package):
    state = observe()

    return bool(
        state
        and state.get("package") == package
    )


def verify_tap(action):
    """
    The bridge can report 'UI did not change' even when a tap
    successfully changes focus.

    Therefore verify the semantic result when possible.
    """

    selector = action.get("selector")

    if not selector:
        return True

    # Focused target.
    if selector.get("focused") is True:
        return find_element(selector) is not None

    # A tap that is expected to activate an EditText.
    focused = find_focused_edittext()

    if focused:
        return True

    # Otherwise the bridge's result is our evidence.
    return False


# ============================================================
# EXECUTE ONE ACTION
# ============================================================

# General AI planner instance.
# No model/API is connected yet.
AI_PLANNER = AIPlanner()
TOOL_ROUTER = ToolRouter()


def test_ai_model(context):
    """
    Deterministic test model.

    It does not contact any service.
    It simply returns one valid action so that the
    AI planner connection can be tested safely.
    """
    return {
        "action": "wait_until",
        "condition": {
            "package": context.get("current_package")
        },
        "timeout_ms": 1000,
    }


def enable_test_ai_planner():
    """
    Enable the deterministic local test model.
    """
    global AI_PLANNER
    AI_PLANNER = AIPlanner(
        model_callable=test_ai_model
    )


def execute_tool_action(action):
    """
    Execute one AI-requested workspace tool.
    """
    tool = action.get("tool")
    arguments = action.get("arguments", {})

    if not isinstance(tool, str) or not tool:
        print("TOOL ERROR: missing tool")
        return False, {
            "ok": False,
            "error": "missing_tool",
        }

    if not isinstance(arguments, dict):
        print("TOOL ERROR: invalid arguments")
        return False, {
            "ok": False,
            "error": "invalid_arguments",
        }

    valid, validation_error = validate_tool_call(
        tool,
        arguments,
    )

    if not valid:
        print(f"TOOL ERROR: {validation_error}")
        return False, {
            "ok": False,
            "error": "tool_validation_failed",
            "message": validation_error,
        }

    print(f"TOOL REQUEST: {tool}")
    print(f"TOOL ARGUMENTS: {arguments}")

    try:
        result = TOOL_ROUTER.execute(
            tool,
            arguments,
        )
    except Exception as e:
        result = {
            "ok": False,
            "error": "tool_exception",
            "message": str(e),
        }

    print(f"TOOL RESULT: {result}")

    return bool(result.get("ok")), result



def execute_action(action):

    action_type = action.get("action")

    if action_type == "wait_until":

        condition = action.get("condition", {})
        timeout_ms = action.get("timeout_ms", 5000)

        ok = wait_until(condition, timeout_ms)

        print(
            "WAIT_UNTIL:",
            "matched" if ok else "timeout",
            "|",
            condition
        )

        return ok

    old_state = observe()
    old_timestamp = (
        old_state.get("timestamp")
        if old_state else None
    )

    if action.get("element_id") is not None:
        resolved = dict(action)
    else:
        resolved = resolve_action(action)

    if resolved is None:
        return False

    result = send(resolved)

    print("RESULT:", result)

    # --------------------------------------------------------
    # Normal success
    # --------------------------------------------------------

    if result.get("ok"):

        if action_type == "swipe":
            return action

        if action_type == "type":

            expected = action.get("text", "")

            # Bridge already verified it.
            if result.get("message") == "type verified":
                return True

            time.sleep(0.3)

            if verify_type(expected):
                print("TYPE semantic verification: OK")
                return True

            print("TYPE semantic verification: FAILED")
            return False

        wait_for_new_ui(old_timestamp)

        return True

    # --------------------------------------------------------
    # Tap inconclusive verification
    # --------------------------------------------------------

    if (
        action_type == "tap"
        and "UI did not change" in result.get("message", "")
    ):
        print(
            "Tap verification inconclusive; "
            "checking semantic state..."
        )

        time.sleep(0.5)

        if verify_tap(action):
            print("TAP semantic verification: OK")
            return True

        print("TAP semantic verification: FAILED")
        return False

    print("ACTION FAILED:", result.get("message"))

    return False


# ============================================================
# LOCAL PLANNER
# ============================================================

def find_comment_input_target():
    state = observe()

    if not state:
        return None

    elements = state.get("elements", [])

    candidates = []

    for element in elements:
        cls = element.get("class", "")
        text = element.get("text", "")
        desc = element.get("content_desc", "")
        bounds = element.get("bounds", [])

        if len(bounds) != 4:
            continue

        left, top, right, bottom = bounds

        # نبحث عن الحاوية الموجودة أسفل الشاشة
        # بدل استخدام إحداثيات ثابتة.
        score = 0

        if cls == "android.widget.FrameLayout":
            score += 2

        if element.get("clickable") is True:
            score += 4

        if not text and not desc:
            score += 1

        if bottom > 0:
            score += 1

        # العناصر الموجودة في الجزء السفلي من الشاشة
        if top > 0:
            score += 2

        if score > 0:
            print(
                "CANDIDATE:",
                element.get("id"),
                "|",
                cls,
                "|",
                repr(text),
                "|",
                repr(desc),
                "|",
                "clickable=",
                element.get("clickable"),
                "| bounds=",
                bounds,
                "| score=",
                score
            )
            candidates.append((score, element))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1].get("bounds", [0, 0, 0, 0])[1]
        ),
        reverse=True
    )

    return candidates[0][1]


def build_plan(goal):
    goal_lower = goal.lower()

    if "telegram" not in goal_lower:
        raise ValueError(
            "No planner rule for this app yet."
        )

    if "محمد" not in goal:
        raise ValueError(
            "No text target found in goal."
        )

    target = find_comment_input_target()

    if target is None:
        raise ValueError(
            "Could not find a suitable comment input target."
        )

    print("\nPLANNER DISCOVERED TARGET:")
    print(
        "id =", target.get("id"),
        "| class =", target.get("class"),
        "| text =", repr(target.get("text")),
        "| content_desc =", repr(target.get("content_desc")),
        "| bounds =", target.get("bounds")
    )

    return [
        {
            "action": "open_app",
            "package": "org.telegram.messenger"
        },
        {
            "action": "tap",
            "selector": {
                "class_name": target.get("class", ""),
                "bounds": target.get("bounds", [])
            }
        },
        {
            "action": "type",
            "text": "محمد"
        },
        {
            "action": "back"
        }
    ]


# ============================================================
# AGENT LOOP
# ============================================================

def run_goal(goal):
    print("\n================================")
    print("AGENT")
    print("GOAL:", goal)
    print("================================")

    # General agent memory.
    #
    # The legacy typed/returned fields are intentionally preserved
    # so the existing planner keeps working during the migration.
    memory = {
        "typed": False,
        "returned": False,
        "step": 0,
        "history": [],
        "last_action": None,
        "last_result": None,
        "last_state": None,
    }

    for step in range(1, MAX_STEPS + 1):

        print(f"\n========== STEP {step} ==========")

        state = observe()

        if state:
            print(
                "CURRENT UI:",
                state.get("package"),
                "| timestamp:",
                state.get("timestamp")
            )

        # -----------------------------
        # ASK PLANNER
        # -----------------------------

        memory["step"] = step
        memory["last_state"] = state

        action = AI_PLANNER.plan(
            goal,
            state,
            memory
        )

        print("PLANNER ACTION:", action)

        if action is None:
            print("\nPLANNER FAILED")
            break

        # -----------------------------
        # GOAL COMPLETE
        # -----------------------------

        if action.get("action") == "done":

            # Tool-based goals are verified when at least one
            # tool action succeeded and its latest result is successful.
            tool_history = [
                item
                for item in memory.get("history", [])
                if item.get("action", {}).get("action") == "tool"
            ]

            tool_goal_verified = (
                bool(tool_history)
                and bool(
                    memory.get("last_result", {}).get("ok")
                )
            )

            legacy_goal_verified = (
                memory.get("typed")
                and memory.get("returned")
            )

            if tool_goal_verified or legacy_goal_verified:
                print("\nGOAL VERIFIED")
                print("\n================================")
                print("GOAL COMPLETED")
                print("================================")

                return True

            print(
                "Planner returned done "
                "but goal is not verified."
            )
            break

        # -----------------------------
        # EXECUTE
        # -----------------------------

        print(
            "\nACTION:",
            action
        )

        # -----------------------------
        # TOOL ACTION
        # -----------------------------

        if action.get("action") == "tool":
            success, tool_result = execute_tool_action(action)

            memory["last_action"] = action
            memory["last_result"] = tool_result

            memory["history"].append({
                "step": step,
                "action": action,
                "success": bool(success),
                "result": tool_result,
            })

            if not success:
                print("\nTOOL ACTION FAILED")
                print("REPLANNING...")
                time.sleep(0.5)
                continue

            print("\nTOOL ACTION VERIFIED")

            continue

        # -----------------------------
        # ANDROID ACTION
        # -----------------------------

        success = execute_action(action)

        memory["last_action"] = action
        memory["last_result"] = {
            "success": bool(success)
        }

        memory["history"].append({
            "step": step,
            "action": action,
            "success": bool(success),
        })

        if not success:

            print("\nACTION FAILED")
            print("REPLANNING...")

            time.sleep(0.5)

            continue

        # -----------------------------
        # UPDATE MEMORY
        # -----------------------------

        if action.get("action") == "type":
            memory["typed"] = True
            print("STATE: typed = True")

        elif action.get("action") == "back":
            memory["returned"] = True
            print("STATE: returned = True")

        # -----------------------------
        # OBSERVE AFTER ACTION
        # -----------------------------

        time.sleep(0.4)

        new_state = observe()

        if new_state:
            print(
                "OBSERVE:",
                new_state.get("package"),
                "| timestamp:",
                new_state.get("timestamp")
            )

    print("\n================================")
    print("GOAL FAILED")
    print("================================")

    return False



# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    goal = (
        "افتح Telegram واكتب محمد في حقل التعليق ثم ارجع"
    )

    success = run_goal(goal)

    print(
        "\nFINAL RESULT:",
        "SUCCESS" if success else "FAILED"
    )
