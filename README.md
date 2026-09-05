# SmartOps — Intelligent Automation & Operations Control Center

منصة محلية موحدة لجمع البيانات من أنظمة الويب، تشغيل أتمتة الأعمال، مراقبة العمليات والمواقع، تسجيل التاريخ الكامل، واستخدام وكلاء ذكاء اصطناعي للتشخيص والإصلاح والتصعيد الآمن.

> ⚠️ هذا المستودع عام حاليًا. لا تضع فيه بيانات شركة حساسة، روابط داخلية، أسرار، كلمات مرور، ملفات خام حقيقية، أو تفاصيل بنية داخلية خاصة.

## الرؤية

```text
أنظمة ومواقع
    ↓
استخراج وتنزيل
    ↓
تحقق من جودة الملفات
    ↓
تشغيل أتمتة الإدارات
    ↓
ربط العمليات والبيانات
    ↓
مراقبة + تاريخ + إنذارات
    ↓
تشخيص وإصلاح بالذكاء الاصطناعي
```

## المكونات الرئيسية

- محرك متصفح تكيفي متعدد الطبقات.
- مركز عمليات وسير عمل.
- إدارة ملفات خام وتاريخ بيانات.
- سجل أحداث وحوادث كامل.
- إنذار مبكر ومراقبة أداء.
- مدير وكلاء لتشغيل Codex CLI وClaude Code CLI.
- تصعيد ذكي حسب صعوبة المشكلة.
- اختبار وتراجع قبل نشر أي إصلاح.
- واجهة ويب محلية مع شات جانبي للتحكم.

## البداية التقنية

- Python
- FastAPI
- Playwright
- SQLite
- DuckDB
- Parquet
- WebSocket
- OpenTelemetry لاحقًا

## اقرأ بالترتيب

1. `docs/PROJECT_CONTEXT.md`
2. `docs/MASTER_PLAN.md`
3. `docs/ARCHITECTURE.md`
4. `docs/BROWSER_EXTRACTION_ENGINE.md`
5. `docs/AI_AGENT_ORCHESTRATION.md`
6. `docs/OBSERVABILITY_SELF_HEALING.md`
7. `docs/IMPLEMENTATION_ROADMAP.md`
8. `docs/EXECUTION_PLAN.md`
9. `docs/AGENT_TASK_PACKETS.md`
10. `AGENTS.md`

## أول هدف تنفيذي

نسخة تجريبية صغيرة:

- 3 أنظمة
- 5 تقارير
- تنزيل تلقائي
- تحقق من الملفات
- سجل كامل
- لوحة مراقبة
- إنذار تأخير
- تشغيل Codex لتحليل حادثة

بعد إثبات الثبات نوسع المنصة.

## التشغيل المحلي

### تشغيل بنقرة واحدة على Windows

اعمل double-click على `START.cmd` في جذر المشروع. الملف يفحص Python والمتطلبات،
يشغّل خادم SmartOps مرة واحدة فقط، ينتظر نجاح `/health`، ثم يفتح غرفة القيادة
في Google Chrome. سجلات التشغيل وملف PID محفوظة خارج المستودع في
`%LOCALAPPDATA%\SmartOps\launcher`.

للتشغيل اليدوي أو التطوير:

```bash
pip install -e ".[dev]"
pytest -q
uvicorn smartops.main:app --reload --port 8765   # أو: python -m smartops serve
```

## التشغيل الأول (نظام حقيقي)

```bash
pip install -e ".[dev]"
playwright install chromium
set SMARTOPS_SYSTEMS_DIR=C:\smartops-private\systems
python -m smartops doctor
python -m smartops login <system>
python -m smartops collect <system> <report>
python -m smartops work
```

`SMARTOPS_SYSTEMS_DIR` لازم يشاور على مجلد **خارج هذا المستودع العام**
(راجع D012 وD023) فيه ملفات `*.yaml` بتعريفات أنظمتك الحقيقية على نمط
`config/systems/example.yaml`. `smartops login` يفتح متصفحًا مرئيًا لتسجّل
دخولك يدويًا مرة واحدة؛ المنصة لا ترى كلمة مرورك أبدًا (D020).

نقاط الواجهة الحالية: `/health`، `/api/workflows`، `/api/runs`، `/api/runs/{id}`،
`/api/runs/{id}/events`، `/api/events`، `/api/incidents`، `/api/files`،
`/api/systems`، `/api/alerts`، `/api/systems/{system}/{report}/collect`.

## حالة البناء

النواة مكتملة ومختبَرة محليًا: إعدادات، قاعدة SQLite بترحيلات، سجل أحداث،
محرك سير عمل قابل للاستكمال مع قفل وإعادة محاولة مصنّفة، فتح حوادث تلقائي
بحزمة أدلة كاملة (لقطات وتتبع على القرص، لا base64 في القاعدة)، تنبيهات محلية
وWebhook، بث حي عبر WebSocket، محوّل Playwright بجلسات دخول محفوظة وكشف
انتهاء جلسة، مدقق ملفات، تعريفات أنظمة من YAML (مع مصادقة وجدولة)، جدولة
تلقائية + عامل خلفي بتوازي محدود، إنذار بطء بعتبات ثابتة، أرشفة تحليلية
Parquet/DuckDB، تشغيل وكيل ذكاء اصطناعي بوضع تحليل فقط، واجهة سطر أوامر
(`python -m smartops`)، وواجهة ويب (`web/`, متاحة على `/app`).

**لم يُشغَّل بعد ضد نظام إنتاج حقيقي.** كل ما سبق مبني ومختبَر محليًا بصفحات
ومحاكيات، لكن أول تشغيل فعلي على نظام حقيقي لم يحدث بعد — هو الخطوة التالية
الفعلية، وأي مشاكل تظهر فيه هي المدخل الحقيقي لمرحلتَي الإنذار المبكر الكامل
والإصلاح الذاتي (P5، P6). التفاصيل في `docs/EXECUTION_PLAN.md` و
`docs/AGENT_TASK_PACKETS.md` و`docs/FINISH_PACKET_SONNET.md`.
