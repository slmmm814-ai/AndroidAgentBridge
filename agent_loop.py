import json
import time
import uuid
from pathlib import Path

from planner import plan_next
from ai_planner import AIPlanner
from tool_router import ToolRouter
from tool_registry import validate_tool_call
from goal_verifier import verify_goal

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


def _bounds_center(bounds):
    if not bounds or len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def _state_fingerprint(state):
    """
    بصمة دلالية للحالة: تلتقط تغير المحتوى الفعلي
    (نصوص/أوصاف/إحداثيات) حتى لو لم يتغير الطابع الزمني للشجرة.
    """
    if not state:
        return frozenset()

    parts = []

    for e in state.get("elements", []):
        b = e.get("bounds")
        if isinstance(b, list):
            b = tuple(b)

        parts.append(
            (
                e.get("class", ""),
                b,
                e.get("content_desc", ""),
                e.get("text", ""),
            )
        )

    return (state.get("package"), frozenset(parts))


def find_element(selector):
    """Find an element from the CURRENT snapshot.

    Tries an exact match first (all selector keys, including bounds,
    matching precisely). If that fails and a 'bounds' key was given,
    falls back to the element whose bounds-center is CLOSEST to the
    requested bounds-center among elements matching the other keys —
    since a search-results list, for example, can shift by a few
    pixels between the moment the AI observed it and the moment we
    resolve the tap, and an exact-bounds requirement was causing
    otherwise-correct taps to fail outright.
    """

    state = observe()

    if not state:
        return None

    elements = state.get("elements", [])

    for element in elements:
        if matches(element, selector):
            return element

    if "bounds" not in selector:
        return None

    target_center = _bounds_center(selector.get("bounds"))

    if target_center is None:
        return None

    other_selector = {
        key: value
        for key, value in selector.items()
        if key != "bounds"
    }

    best_element = None
    best_distance = None

    TOLERANCE_PX = 150

    for element in elements:
        if not matches(element, other_selector):
            continue

        center = _bounds_center(element.get("bounds"))

        if center is None:
            continue

        distance = (
            (center[0] - target_center[0]) ** 2
            + (center[1] - target_center[1]) ** 2
        ) ** 0.5

        if distance > TOLERANCE_PX:
            continue

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_element = element

    return best_element


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


def _bounds_of(element):
    if not isinstance(element, dict):
        return None

    b = element.get("bounds")

    if not b or len(b) != 4:
        return None

    left, top, right, bottom = b

    if right <= left or bottom <= top:
        return None

    return (left, top, right, bottom)


SCROLLABLE_CLASSES = (
    "RecyclerView",
    "NestedScrollView",
    "ScrollView",
    "ListView",
    "GridView",
    "HorizontalScrollView",
    "ViewPager",
    "SlidingPaneLayout",
    "TabLayout",
)

HORIZONTAL_CLASSES = (
    "HorizontalScrollView",
    "ViewPager",
    "SlidingPaneLayout",
    "TabLayout",
)


def _find_scroll_container(state, direction):
    """
    اكتشاف حاوية التمرير المناسبة بشكل عام لأي تطبيق.

    أفقي  : شريط أفقي قصير قريب من أعلى الشاشة
            (شريط القصص، التبويبات، الكاروسيل، الفلاتر).
    عمودي : أكبر قائمة قابلة للتمرير
            (الـ feed، قائمة المحادثات، الإعدادات).
    """
    if not state:
        return None

    horizontal = direction in ("left", "right")
    containers = []

    for element in state.get("elements", []):
        cls = element.get("class", "") or ""

        if not any(key in cls for key in SCROLLABLE_CLASSES):
            continue

        b = _bounds_of(element)

        if not b:
            continue

        left, top, right, bottom = b
        w = right - left
        h = bottom - top

        is_horizontal_class = any(
            key in cls for key in HORIZONTAL_CLASSES
        )

        if horizontal:
            if not (is_horizontal_class or w > h * 1.5):
                continue

            # شريط أفقي حقيقي (قصص/تبويبات) = عرضه أكبر بكثير من ارتفاعه.
            # نفضّل الأشرطة الحقيقية دائمًا قبل أي ViewPager ضخم
            # يغطي الشاشة كاملة.
            is_strip = w > h * 1.8

            containers.append(
                (
                    0 if is_strip else 1,
                    top,
                    -(w * h),
                    element,
                )
            )
        else:
            if is_horizontal_class:
                continue
            if not h > w * 1.2:
                continue
            # المساحة الأكبر هي الأفضل
            containers.append((0, top, -(w * h), element))

    if not containers:
        return None

    # ترتيب الترتيب (tuple sort) يعتمد على:
    # 1) الأولوية (الشريط الأفقي الحقيقي أولًا)
    # 2) الأقرب للأعلى
    # 3) المساحة الأكبر
    containers.sort()
    return containers[0][-1]


def _derive_scroll_bounds(state, direction):
    """
    منطقة احتياطية عندما لا تكشف شجرة العناصر عن حاوية قابلة
    للتمرير. آمنة لكل التطبيقات:
      أفقي  : الثلث العلوي من الشاشة (مكان أشرطة القصص/التبويبات).
      عمودي : منطقة المحتوى الرئيسية.
    """
    if not state:
        return None

    horizontal = direction in ("left", "right")

    screen_h = 0
    screen_w = 0

    for element in state.get("elements", []):
        b = _bounds_of(element)

        if b:
            screen_h = max(screen_h, b[3])
            screen_w = max(screen_w, b[2])

    if screen_h <= 0 or screen_w <= 0:
        return None

    if horizontal:
        return (0, 0, screen_w, int(screen_h * 0.35))

    return (0, int(screen_h * 0.2), screen_w, int(screen_h * 0.85))


def _scroll_to_swipe(bounds, direction):
    """
    تحويل التمرير الدلالي إلى swipe دقيق يُنفَّذ
    داخل حدود الحاوية نفسها، فلا يمكن أن يمرّر الصفحة كلها.
    """
    left, top, right, bottom = bounds

    cx = (left + right) // 2
    cy = (top + bottom) // 2

    mx = max(24, int((right - left) * 0.08))
    my = max(24, int((bottom - top) * 0.08))

    if direction == "right":
        return (right - mx, cy, left + mx, cy)

    if direction == "left":
        return (left + mx, cy, right - mx, cy)

    if direction == "down":
        return (cx, bottom - my, cx, top + my)

    if direction == "up":
        return (cx, top + my, cx, bottom - my)

    return None


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

        # تمرير عام لأي تطبيق.
        #
        # إرسال scroll مجرد قد يحرك الحاوية الخاطئة (مثلًا تمرير
        # الـ feed كاملًا بدل شريط القصص الأفقي في الأعلى).
        # لذلك نحدد الحاوية المستهدفة (من selector أو بالاكتشاف
        # التلقائي) ثم نحوّل التمرير إلى swipe داخل حدودها.

        direction = (action.get("direction") or "down").lower()
        selector = action.get("selector")

        element = find_element(selector) if selector else None

        state = None

        if element is None:
            state = observe()
            element = _find_scroll_container(state, direction)

        bounds = _bounds_of(element)

        if bounds is None:
            if state is None:
                state = observe()
            bounds = _derive_scroll_bounds(state, direction)

        if bounds is not None:
            coords = _scroll_to_swipe(bounds, direction)

            if coords is not None:
                action.pop("selector", None)
                action.pop("element_id", None)
                action.pop("direction", None)

                action["action"] = "swipe"
                action["x1"], action["y1"] = coords[0], coords[1]
                action["x2"], action["y2"] = coords[2], coords[3]

                print(
                    "SCROLL -> SWIPE inside container",
                    bounds,
                    "| direction:",
                    direction,
                    "| coords:",
                    coords
                )

                return action

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



TRANSIENT_WINDOW_ERRORS = (
    "no active root",
    "no visible window",
    "has no visible window",
    "not responding",
)


def _is_transient_window_error(result):
    message = (result or {}).get("message", "") or ""
    return any(k in message for k in TRANSIENT_WINDOW_ERRORS)


def _refresh_windows():
    """أعد تنشيط شجرة الوصول بعد فقدان النافذة النشطة."""
    for _ in range(4):
        send({"action": "dump"})
        time.sleep(1.0)

        state = observe()

        if state and state.get("package"):
            return True

    return False


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

    # --------------------------------------------------------
    # Recover from transient window loss
    # (يحدث بعد open_app مباشرة أو أثناء انتقالات التطبيقات)
    # --------------------------------------------------------
    if not result.get("ok") and _is_transient_window_error(result):
        print(
            "TRANSIENT WINDOW ERROR: refreshing accessibility tree "
            "and retrying the action once..."
        )

        if _refresh_windows():
            resolved = resolve_action(action) or resolved
            result = send(resolved)
            print("RETRY RESULT:", result)

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
    # Inconclusive verification (tap / scroll / swipe)
    # --------------------------------------------------------
    # بعض التطبيقات يعيد تدوير العناصر دون تغيير الطابع الزمني،
    # أو يفتح شاشة جديدة يتأخر تحديث شجرة الوصول لها.
    # لذلك نتحقق دلاليًا من أن المحتوى الفعلي قد تغير.

    if (
        action_type in ("tap", "scroll", "long_press", "swipe")
        and "UI did not change" in result.get("message", "")
    ):
        print(
            "Verification inconclusive; "
            "checking semantic state..."
        )

        send({"action": "dump"})
        time.sleep(1.2)

        new_state = observe()

        if _state_fingerprint(old_state) != _state_fingerprint(new_state):
            print("SEMANTIC VERIFICATION: content changed -> OK")
            return True

        # محاولة بديلة: نقر إيمائي عند مركز العنصر
        # (يحل حالات لا يستجيب فيها العنصر للنقر عبر إمكانية الوصول)
        if action_type in ("tap", "long_press"):
            center = _bounds_center(
                (action.get("selector") or {}).get("bounds")
            )

            if center:
                cx, cy = int(center[0]), int(center[1])

                print(f"GESTURE TAP fallback at ({cx}, {cy})")

                g_result = send(
                    {
                        "action": "swipe",
                        "x1": cx,
                        "y1": cy,
                        "x2": cx,
                        "y2": cy,
                    }
                )

                if g_result.get("ok"):
                    wait_for_new_ui(old_timestamp)
                    return True

        if action_type == "tap" and verify_tap(action):
            print("TAP semantic verification: OK")
            return True

        print("SEMANTIC VERIFICATION: FAILED")
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
        "any_action_succeeded": False,
        "repeat_action": None,
        "repeat_count": 0,
    }

    for step in range(1, MAX_STEPS + 1):

        print(f"\n========== STEP {step} ==========")

        # Force a fresh accessibility dump before every planning
        # decision, instead of trusting the last automatic dump.
        # This avoids the planner reasoning over a stale snapshot
        # (a real bug seen where ui_state.json's timestamp stayed
        # frozen across several identical, useless planner calls).
        previous_package = (
            memory.get("last_state", {}) or {}
        ).get("package")

        state = observe()
        previous_timestamp = (
            state.get("timestamp") if state else None
        )

        previous_timestamp = (
            state.get("timestamp") if state else None
        )

        dump_ok = False

        for dump_attempt in range(4):
            dump_result = send({"action": "dump"})

            if dump_result.get("ok"):
                dump_ok = True
                break

            print(
                f"WARNING: explicit dump failed "
                f"(attempt {dump_attempt + 1}/4):",
                dump_result.get("message")
            )

            # النافذة قد تكون في حالة انتقالية؛ ننتظر ثم نعيد المحاولة.
            time.sleep(1.5)

        if not dump_ok and not _refresh_windows():
            print(
                "\nFATAL: accessibility service is blind. "
                "Cannot continue safely."
            )
            break

        time.sleep(0.2)

        current_package = state.get("package") if state else None

        if (
            previous_package
            and current_package
            and current_package != previous_package
        ):
            print(
                "NOTE: foreground app changed unexpectedly since "
                "last step:",
                previous_package,
                "->",
                current_package
            )

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
        # REPEAT-ACTION GUARD
        # -----------------------------
        #
        # If the planner asks for the EXACT same action three times
        # in a row, the state clearly isn't progressing (whether due
        # to staleness or a genuinely unproductive choice). Stop
        # instead of burning further planner/API calls uselessly.

        action_signature = json.dumps(action, sort_keys=True, ensure_ascii=False)

        if action_signature == memory.get("repeat_action"):
            memory["repeat_count"] += 1
        else:
            memory["repeat_action"] = action_signature
            memory["repeat_count"] = 1

        if memory["repeat_count"] >= 3:
            print(
                "\nREPEAT GUARD: same action requested 3 times in a row "
                "with no progress. Stopping to avoid wasting planner calls."
            )
            break

        # -----------------------------
        # GOAL COMPLETE
        # -----------------------------

        if action.get("action") == "done":

            verification_state = observe() or state

            verification_memory = dict(memory)
            verification_memory["last_action"] = action
            verification_memory["last_result"] = {
                "success": True,
                "answer": action.get("answer"),
            }

            verification = AI_PLANNER.verify_goal(
                goal,
                verification_state,
                verification_memory,
            )

            print("GOAL VERIFICATION:", verification)

            if verification and verification.get("verified") is True:
                print("\nGOAL VERIFIED")
                print("\n================================")
                print("GOAL COMPLETED")
                print("================================")

                return True

            print(
                "Planner returned done "
                "but independent Goal Verifier did not verify the goal."
            )

            if verification:
                print(
                    "VERIFIER REASON:",
                    verification.get("reason", "")
                )

            print("REPLANNING...")
            time.sleep(0.5)
            continue

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

            memory["any_action_succeeded"] = True

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

        if success:
            memory["any_action_succeeded"] = True

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
