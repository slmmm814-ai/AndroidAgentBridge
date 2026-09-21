"""
Groq-backed model provider.

Additive — does NOT replace GeminiModelProvider. Implements the same
ModelProvider interface (a .generate(context) method returning one
normalized action dict), so it can be swapped in via create_planner()
exactly like Gemini.

Groq free tier (no credit card required): 30 requests/minute, up to
14,400 requests/day depending on model — check
https://console.groq.com/docs/rate-limits for current numbers, since
these can change.
"""

import json
import os
import urllib.request
import urllib.error

from model_provider import ModelProvider


class GroqModelProvider(ModelProvider):
    """
    Groq-backed provider using its OpenAI-compatible chat completions
    endpoint. Reads context (goal, elements, history) and returns one
    normalized action dictionary matching planner.ACTION_TYPES.
    """

    def __init__(self, api_key=None, model="openai/gpt-oss-120b"):
        raw_key = api_key or os.environ.get("GROQ_API_KEY", "")
        # ننظّف أي مسافات أو أسطر جديدة زائدة قد تتسلل عبر النسخ/التصدير،
        # لأن ترويسة HTTP لا تقبل أي حرف تحكم مثل \n.
        self.api_key = raw_key.strip()
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY غير موجود")
        self.model = model

    def _build_prompt(self, context):
        elements = context.get("elements", [])

        def trim(value, max_len=60):
            value = value or ""
            if len(value) > max_len:
                return value[:max_len] + "…"
            return value

        compact = [
            {
                "id": e.get("id"),
                "text": trim(e.get("text", "")),
                "content_desc": trim(e.get("content_desc", "")),
                "class_name": e.get("class", ""),
                "clickable": e.get("clickable"),
                "bounds": e.get("bounds"),
            }
            for e in elements
            if e.get("clickable") or e.get("text") or e.get("content_desc")
        ]

        # سقف احترازي إضافي: لا نرسل أكثر من 40 عنصرًا بأي حال،
        # لتفادي استهلاك رموز زائد في الشاشات المزدحمة جدًا.
        compact = compact[:40]

        return (
            f"الهدف: {context.get('goal')}\n"
            f"التطبيق الحالي: {context.get('current_package')}\n"
            f"الخطوة رقم: {context.get('step')}\n"
            f"آخر إجراء ونتيجته: {context.get('last_action')} -> {context.get('last_result')}\n"
            f"العناصر المرئية حاليًا:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
            "اختر إجراءً واحدًا فقط. استخدم selector مطابقًا تمامًا "
            "(bounds + class_name) من العناصر أعلاه، لا تخترع إحداثيات.\n"
            "إذا كان الهدف يطلب منك إخبار المستخدم بمعلومة (مثل آخر "
            "رسالة، أو نص ظاهر، أو أي محتوى تراه في العناصر أعلاه)، "
            "فلا تكتفِ بإنهاء المهمة فورًا بعد الوصول للشاشة الصحيحة — "
            "اقرأ المعلومة المطلوبة من العناصر المرئية أولًا، ثم أنهِ "
            "المهمة بإرسال done مع حقل answer يحوي تلك المعلومة نصًا "
            "واضحًا. لا تنهِ المهمة بـ done بدون answer إذا كان الهدف "
            "يطلب معلومة صراحة.\n"
            "مهم جدًا: إذا طُلب منك 'آخر رسالة' في محادثة أو قناة "
            "معيّنة بالاسم، فيجب عليك أولًا فتح تلك المحادثة/القناة "
            "بالتحديد (عبر البحث ثم الضغط على نتيجتها) — لا يكفي فتح "
            "تطبيق تلغرام والبقاء في الشاشة الرئيسية (قائمة "
            "المحادثات)؛ العناصر الظاهرة هناك تخص محادثات مختلفة "
            "تمامًا وليست محتوى القناة المطلوبة إطلاقًا. "
            "ممنوع منعًا باتًا إرسال done مع answer إلا بعد التأكد أن "
            "current_package يساوي org.telegram.messenger وأنك فعليًا "
            "داخل شاشة المحادثة/القناة المحددة بالاسم (وليس شاشة "
            "قائمة المحادثات العامة أو نتائج بحث لم تُفتح بعد). إذا "
            "لم تكن متأكدًا من ذلك، تابع التنقل (بحث ثم فتح النتيجة "
            "الصحيحة) قبل أي محاولة للإجابة.\n"
            "وحتى بعد فتح المحادثة/القناة الصحيحة، الشاشة قد لا تكون "
            "بالضرورة عند آخر رسالة فعليًا (قد تكون عند رسالة غير "
            "مقروءة قديمة). تحقق هل ما تراه هو الأحدث زمنيًا (الطوابع "
            "الزمنية إن وُجدت)؛ وإذا لم تكن متأكدًا أنك في آخر "
            "المحادثة، نفّذ "
            '{"action":"scroll","direction":"down"} '
            "مرة أو أكثر أولًا قبل قراءة الرسالة والإجابة.\n"
            "رد بـ JSON فقط بدون أي شرح ولا Markdown، بأحد هذه الأشكال:\n"
            '{"action":"open_app","package":"اسم_الحزمة"}\n'
            '{"action":"tap","selector":{"bounds":[x1,y1,x2,y2],"class_name":"..."}}\n'
            '{"action":"type","text":"النص","selector":{"bounds":[x1,y1,x2,y2],"class_name":"..."}}\n'
            '{"action":"back"}\n'
            '{"action":"wait_until","condition":{"package":"..."},"timeout_ms":3000}\n'
            '{"action":"done"}\n'
            '{"action":"done","answer":"المعلومة المطلوبة هنا كنص واضح"}'
        )

    def generate(self, context):
        import re
        import time

        url = "https://api.groq.com/openai/v1/chat/completions"

        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_prompt(context),
                }
            ],
            "temperature": 0,
        }

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
            # خادم Groq خلف Cloudflare قد يرفض الطلبات التي تحمل
            # توقيع مكتبة urllib الافتراضي (يُعامل كروبوت) بخطأ 1010.
            "user-agent": (
                "Mozilla/5.0 (Linux; Android 13) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Mobile Safari/537.36"
            ),
            "accept": "application/json",
        }

        res = None

        for attempt in range(4):
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers=headers,
            )

            try:
                res = json.loads(
                    urllib.request.urlopen(req, timeout=20).read()
                )
                break

            except urllib.error.HTTPError as e:
                error_body = e.read().decode()

                if e.code == 429 and attempt < 3:
                    # نحاول استخراج مهلة الانتظار المقترحة من نص الخطأ
                    # نفسه (مثال: "Please try again in 4.065s"), وإلا
                    # ننتظر مهلة افتراضية قصيرة.
                    match = re.search(
                        r"try again in ([\d.]+)s",
                        error_body,
                    )

                    wait_seconds = (
                        float(match.group(1)) + 0.5
                        if match
                        else 5.0
                    )

                    print(
                        f"GROQ RATE LIMIT: retrying in "
                        f"{wait_seconds:.1f}s (attempt {attempt + 1}/4)"
                    )

                    time.sleep(wait_seconds)
                    continue

                print("GROQ ERROR:", error_body)
                return None

            except Exception as e:
                print("GROQ REQUEST ERROR:", e)
                return None

        if res is None:
            print("GROQ ERROR: exhausted retries")
            return None

        try:
            text = res["choices"][0]["message"]["content"]
            text = text.strip().strip("```json").strip("```").strip()
            return json.loads(text)
        except Exception as e:
            print("GROQ PARSE ERROR:", e, "-- raw:", res)
            return None
