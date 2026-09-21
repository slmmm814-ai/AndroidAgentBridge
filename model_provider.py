"""
Provider-neutral model interface.

A real provider such as OpenAI, Claude, Gemini, or a local
model can implement this interface later.
"""


class ModelProvider:
    """
    Base interface for AI model providers.
    """

    def generate(self, context):
        """
        Receive the complete agent context and return
        one planner action as a dictionary.
        """
        raise NotImplementedError(
            "ModelProvider.generate() must be implemented"
        )


class MockModelProvider(ModelProvider):
    """
    Local test provider.

    This does not contact any external service.
    """

    def __init__(self, action=None):
        self.action = action or {
            "action": "done"
        }

    def generate(self, context):
        """
        Return a predefined action.
        """
        return dict(self.action)


import json
import os
import time
import urllib.request
import urllib.error


class GeminiModelProvider(ModelProvider):
    """
    Real Gemini-backed provider.

    Reads context (goal, elements, history) and returns one
    normalized action dictionary matching planner.ACTION_TYPES.
    """

    def __init__(self, api_key=None, model="gemini-3.6-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY غير موجود")
        self.model = model

    def _build_prompt(self, context):
        elements = context.get("elements", [])
        compact = [
            {
                "id": e.get("id"),
                "text": e.get("text", ""),
                "content_desc": e.get("content_desc", ""),
                "class_name": e.get("class", ""),
                "clickable": e.get("clickable"),
                "bounds": e.get("bounds"),
            }
            for e in elements
            if e.get("clickable") or e.get("text") or e.get("content_desc")
        ]

        return (
            f"الهدف: {context.get('goal')}\n"
            f"التطبيق الحالي: {context.get('current_package')}\n"
            f"الخطوة رقم: {context.get('step')}\n"
            f"آخر إجراء ونتيجته: {context.get('last_action')} -> {context.get('last_result')}\n"
            f"العناصر المرئية حاليًا:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "اختر إجراءً واحدًا فقط. استخدم selector مطابقًا تمامًا "
            "(bounds + class_name) من العناصر أعلاه، لا تخترع إحداثيات.\n"
            "رد بـ JSON فقط بدون أي شرح ولا Markdown، بأحد هذه الأشكال:\n"
            '{"action":"open_app","package":"اسم_الحزمة"}\n'
            '{"action":"tap","selector":{"bounds":[x1,y1,x2,y2],"class_name":"..."}}\n'
            '{"action":"type","text":"النص","selector":{"bounds":[x1,y1,x2,y2],"class_name":"..."}}\n'
            '{"action":"back"}\n'
            '{"action":"wait_until","condition":{"package":"..."},"timeout_ms":3000}\n'
            '{"action":"done"}'
        )

    def generate(self, context):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": self._build_prompt(context)}]}]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )

        for attempt in range(5):
            try:
                res = json.loads(
                    urllib.request.urlopen(req, timeout=20).read()
                )
                break
            except urllib.error.HTTPError as e:
                if e.code == 503 and attempt < 4:
                    time.sleep(6)
                    continue
                print("GEMINI ERROR:", e.read().decode())
                return None

        try:
            text = res["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().strip("```json").strip("```").strip()
            return json.loads(text)
        except Exception as e:
            print("GEMINI PARSE ERROR:", e, "-- raw:", res)
            return None
