# تشغيل وصيانة Recording Center

اضبط `SMARTOPS_RECORDINGS_DIR` و`SMARTOPS_RECORDINGS_BACKUP_DIR` على مجلدين
خاصين خارج المستودع قبل تسجيل أي نظام حقيقي. لا تضع النسخ الاحتياطية أو ملفات
الجلسة أو HAR أو trace داخل Git أو مساحة مشاركة عامة.

ابدأ المنصة كالمعتاد بـ `python -m smartops serve`. عند تركيب الخدمات تُسوّى
التسجيلات التي توقفت بلا heartbeat إلى `interrupted`؛ لا تُحذف خطواتها أو أدلتها
الجزئية، ويمكن بدء نسخة re-record منها.

تحقق من الحالة بواسطة `python -m smartops doctor` أو `GET /health`. يوضح الفحص
قابلية كتابة مسارات التسجيل والنسخ الاحتياطي وعدد عمال المسجل النشطين.

لإنشاء نسخة خاصة قابلة للاستعادة نفّذ:

```powershell
python -m smartops recordings-backup
```

ينتج ZIP محليًا يحتوي نسخة SQLite متسقة وشجرة الأدلة الخاصة. للاستعادة، أوقف
SmartOps، فك الأرشيف في مساحة خاصة، واستبدل قاعدة SQLite ومجلد التسجيلات معًا،
ثم شغّل `python -m smartops recordings-recover` قبل فتح الواجهة.

الحذف من الواجهة قابل للاسترجاع فقط. الحذف الدائم معطل افتراضيًا. لتفعيله عيّن
`storage.recordings_retention_days` إلى قيمة موجبة و`safety.allow_recording_purge`
إلى `true` في إعداد خاص، ثم نفذ `python -m smartops recordings-purge`. كل حذف
دائم يسجل event، ولا يلمس إلا تسجيلًا موجودًا في سلة المحذوفات وتجاوز مدة
الاحتفاظ.

## Chrome recording exception

تعليمات المستودع تفرض `windows-chrome-launcher` لفتح الروابط التفاعلية لأنها
تضمن سطح مكتب Windows الصحيح. Recording Center لا يستخدم launcher: يحتاج
Playwright إلى إنشاء **Chrome persistent context** مخصص، وربطه مباشرةً للتقاط
DOM والشبكة والتنزيلات والـtrace في نفس الجلسة. الـlauncher يفتح Chrome فقط ولا
يوفر CDP endpoint أو profile مخصصًا يمكن لـPlaywright التحكم فيه؛ دمجه يفقد
التقاط التسجيل وقد يخلط profile الشركة المعتاد بملفات التسجيل.

لذلك هذا استثناء ضيق للتسجيل فقط: `channel="chrome"` عبر Playwright، profile
خاص داخل `recordings_dir/<id>/profile`، ونافذة headed. لا يستعمل Chrome العادي
للمستخدم ولا `Start-Process` أو `chrome.exe` مباشرة. إذا لم يظهر Chrome على
سطح مكتب المستخدم بسبب جلسة خدمة Windows، يفشل التسجيل بوضوح ويحتاج worker
bridge في جلسة المستخدم؛ لا يوجد fallback يلتقط بيانات من Chrome عادي.
