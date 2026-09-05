# حزم المهام الجاهزة (تنفيذ Sonnet 5 Medium)

كل حزمة هنا **مكتفية بذاتها**. الهدف إن النموذج المنفّذ ما يحتاجش يقرأ المستودع كله.

## قواعد إلزامية لأي حزمة

1. اقرأ فقط الملفات المذكورة في بند «اقرأ». ممنوع البحث العام في المستودع.
2. ممنوع تعديل ملفات النواة: `core/`، `domain/`، `storage/db.py`، `engine/`، `ports/`.
   لو اضطريت لتعديلها → قف وصعّد لـ Opus.
3. المخرج = كود + اختبار. نقطة التوقف = الاختبار أخضر.
4. تعديل موضعي فقط، بدون إعادة كتابة ملفات كاملة.
5. المحوّلات الجديدة تتحط تحت `src/smartops/adapters/<المجال>/`.
6. التركيب في `services.py` يكون بسطر واحد أو عبر إعداد، من غير تغيير بنية الحاوية.

## قالب أي مهمة جديدة

```
العنوان | الهدف بجملة | الملفات المسموح قراءتها | العقد المطلوب تنفيذه
| الملفات المطلوب إنشاؤها | معيار القبول (أمر اختبار واحد) | ممنوعات
```

---

## S-01 — مدقق الملفات المحلي (تم ✅)
- **الهدف:** تنفيذ `FileValidatorPort` بحيث ما يعديش ملف تالف أو ناقص أو مكرر.
- **اقرأ:** `src/smartops/ports/validation.py`، `tests/test_collect_workflow.py`.
- **أنشئ:** `src/smartops/adapters/validation/local.py` + `tests/test_validation_adapter.py`.
- **المطلوب:** فحص الوجود والحجم والامتداد، حساب sha256، فتح CSV/Excel وقراءة الأعمدة وعدد الصفوف، فحص عمر الملف، وكشف التكرار عبر `services.files.find_by_hash`.
- **القبول:** `pytest tests/test_validation_adapter.py -q` أخضر، ويغطي: ملف سليم، ملف فاضي، امتداد غلط، عمود ناقص، ملف مكرر.
- **ممنوع:** تغيير `ValidationReport` أو `ValidationRules`.

## S-02 — محوّل المتصفح (Playwright) (تم ✅)
- **الهدف:** تنفيذ `BrowserPort` بطبقتي «شبكة» ثم «DOM».
- **اقرأ:** `src/smartops/ports/browser.py`، `tests/test_collect_workflow.py` (المتصفح الوهمي هو المرجع السلوكي).
- **أنشئ:** `src/smartops/adapters/browser/playwright_engine.py` + `tests/test_browser_adapter.py` (اختبار على صفحة محلية، بدون أي موقع حقيقي).
- **المطلوب:** Context معزول، Viewport من الإعدادات، انتظار التنزيل وحفظه في `destination_dir`، تسجيل الطبقة المستخدمة، وجمع الأدلة (لقطة + trace) عند الفشل، ورفع `TransientError` للأخطاء المؤقتة و`AuthError` لانتهاء الجلسة.
- **القبول:** `pytest tests/test_browser_adapter.py -q` أخضر.
- **ممنوع:** إحداثيات شاشة مطلقة، أو تخزين أي بيانات دخول في الكود.

## S-03 — تعريفات الأنظمة والتقارير (تم ✅)
- **الهدف:** تحميل ملفات `config/systems/*.yaml` وتحويلها إلى تشغيلات `collect.report`.
- **اقرأ:** `src/smartops/config.py`، `src/smartops/workflows/builtin.py`.
- **أنشئ:** `src/smartops/workflows/profiles.py` + `config/systems/example.yaml` + `tests/test_profiles.py`.
- **المطلوب:** لكل نظام: الاسم، التقارير، قواعد التحقق، الزمن الطبيعي، قواعد الإنذار. تحقق من التعريف ورفع `ConfigurationError` برسالة واضحة لو ناقص.
- **القبول:** `pytest tests/test_profiles.py -q` أخضر، وتعريف ناقص يعطي رسالة مفهومة.

## S-04 — العامل والجدولة (تم ✅)
- **الهدف:** تشغيل التشغيلات المستحقة تلقائيًا بدل النداء اليدوي.
- **اقرأ:** `src/smartops/engine/runner.py` (دالتا `execute` و`drive` فقط)، `src/smartops/storage/repositories.py` (`RunRepository.due`).
- **أنشئ:** `src/smartops/worker.py` + `tests/test_worker.py`.
- **المطلوب:** حلقة تقرأ `runs.due()` وتنفّذ باحترام `browser.max_concurrency`، مع إيقاف نظيف، ومنع الازدحام (القفل موجود في المستودع فلا تعيد اختراعه).
- **القبول:** `pytest tests/test_worker.py -q` أخضر، ويثبت أن تشغيلين لا يتداخلان.

## S-05 — البث الحي للأحداث (تم ✅)
- **الهدف:** نقطة WebSocket تبث الأحداث لحظيًا للواجهة.
- **اقرأ:** `src/smartops/events/bus.py`، `src/smartops/api/app.py`.
- **أنشئ:** `src/smartops/api/ws.py` + `tests/test_ws.py`.
- **المطلوب:** `/ws/events` يشترك في `services.bus`، ويلغي الاشتراك عند القطع، ويدعم فلترة بـ`run_id`.
- **القبول:** `pytest tests/test_ws.py -q` أخضر (اتصال يستقبل حدثًا واحدًا على الأقل).

## S-06 — أرشفة التاريخ (Parquet + DuckDB) (تم ✅)
- **الهدف:** تحويل الملفات المتحقق منها إلى تاريخ تحليلي.
- **اقرأ:** `src/smartops/storage/paths.py`، `src/smartops/domain/models.py` (`FileArtifact` فقط).
- **أنشئ:** `src/smartops/adapters/history/archiver.py` + `tests/test_archiver.py`.
- **المطلوب:** كتابة Parquet مقسّم بالتاريخ والنظام والتقرير، ودالة استعلام DuckDB بسيطة تقارن فترتين.
- **القبول:** `pytest tests/test_archiver.py -q` أخضر.
- **ممنوع:** رفع أي بيانات فعلية للمستودع.

## S-07 — الويب آب (تم ✅)
- **الهدف:** غرفة القيادة لمستخدم غير تقني.
- **اقرأ:** `src/smartops/api/app.py` فقط (نقاط API هي العقد).
- **أنشئ:** `web/` (صفحات ثابتة أو Vite) + ربطها بخدمة FastAPI.
- **المطلوب:** لوحة الحالة، قائمة التشغيلات، تفاصيل تشغيل بخطواته وأحداثه، الحوادث، الملفات، وزر تشغيل/إعادة محاولة. عربي وواضح، وبلا مصطلحات تقنية في نص الواجهة.
- **القبول:** الشاشات تعمل على بيانات `platform.selfcheck` بدون أي تعديل في الخلفية.

## S-08 — مدير الوكلاء (وضع تحليل فقط) (تم ✅)
- **الهدف:** تشغيل Codex/Claude CLI وتسجيل كل شيء.
- **اقرأ:** `src/smartops/ports/agents.py`، `src/smartops/storage/repositories.py` (`AgentRunRepository` فقط).
- **أنشئ:** `src/smartops/adapters/agents/cli_runner.py` + `tests/test_agent_runner.py` (بعملية وهمية، بدون استدعاء حقيقي).
- **المطلوب:** تنفيذ `AgentRunnerPort`، بث المخرجات، مهلة زمنية، تسجيل التوكنز، واحترام `AgentMode.ANALYZE` (ممنوع أي تعديل ملفات في هذا الوضع).
- **القبول:** `pytest tests/test_agent_runner.py -q` أخضر، ويثبت أن وضع التحليل لا يكتب أي ملف.
- **ممنوع:** تفعيل وضع التنفيذ أو التجربة في هذه الحزمة.

## S-09 — قنوات الإنذار (تم ✅)
- **الهدف:** تنفيذ `NotifierPort` (سجل محلي + Webhook اختياري).
- **اقرأ:** `src/smartops/ports/notify.py`.
- **أنشئ:** `src/smartops/adapters/notify/local.py` + `tests/test_notifier.py`.
- **القبول:** `pytest tests/test_notifier.py -q` أخضر.

## S-10 — حزمة الحادثة (تم ✅)
- **الهدف:** تجميع الأدلة في مجلد واحد عند فتح أي حادثة.
- **اقرأ:** `src/smartops/domain/models.py` (`Incident`)، `src/smartops/storage/repositories.py` (`IncidentRepository`).
- **أنشئ:** `src/smartops/adapters/incidents/pack.py` + `tests/test_incident_pack.py`.
- **المطلوب:** ملخص، الخطأ، الخطوات، الأحداث، الملفات المتوقعة مقابل الفعلية، وحوادث مشابهة عبر `find_by_signature`، ويُكتب المسار في `incident.pack_path`.
- **القبول:** `pytest tests/test_incident_pack.py -q` أخضر.

---

## الترتيب الموصى به (منفَّذ بالكامل ✅)

`S-01 → S-02 → S-03 → S-04` (نواة الجمع تشتغل فعليًا) ثم `S-10 → S-09 → S-05 → S-07` (رؤية وتحكم) ثم `S-06 → S-08`.
كل الحزم العشر منفَّذة ومختبَرة (97 اختبارًا خضراء) — راجع `docs/EXECUTION_PLAN.md` قسم 5 لحالة كل مرحلة، وقسم "الخطوة التالية" أدناه لما تبقّى.

## الخطوة التالية: التركيب لا الحزم الجديدة

كل حزمة أنشأت محوّلًا مستقلًا خلف عقده (ports/) دون ربطه تلقائيًا بـ
`services.py` — بالتصميم، حتى تبقى النواة مستقلة عن أي محوّل بعينه.
المتبقي ليس حزمة Sonnet جديدة بل **تركيب واعٍ يقرره Opus**: أي محوّلات
تُفعَّل فعليًا في `main.py`/الإنتاج (Playwright حقيقي؟ Webhook حقيقي؟
أي نموذج وكيل؟)، وهذا قرار تشغيلي/أمني يحتاج نفس مستوى الحرص المعماري
الأصلي، فلا يُترك لـ Sonnet وحده.

## متى نوقف Sonnet ونصعّد

- المهمة تحتاج تعديل عقد أو جدول قاعدة بيانات.
- ظهرت حالة تزامن أو استكمال جديدة.
- فشل الاختبار مرتين بنفس السبب.
- قرار يخص الأمان أو الصلاحيات أو حذف بيانات.
