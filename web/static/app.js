/* دوال مشتركة لكل صفحات غرفة القيادة: نداءات API، تحويل الحالات لعربي، وبناء عناصر HTML بسيطة. */

const SmartOps = (() => {
  async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail?.message || body.detail || detail;
      } catch (_) {
        /* تجاهل: مفيش تفاصيل إضافية */
      }
      throw new Error(detail || "تعذّر الاتصال بالمنصة");
    }
    return res.json();
  }

  async function postJSON(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      /* بلا محتوى */
    }
    if (!res.ok) {
      const message = body?.detail?.message || body?.detail || "فشل الطلب";
      throw new Error(message);
    }
    return body;
  }

  const RUN_STATUS_LABELS = {
    queued: ["في الانتظار", "gray"],
    running: ["قيد التنفيذ", "blue"],
    waiting: ["معلّق مؤقتًا", "yellow"],
    retrying: ["يعيد المحاولة", "orange"],
    succeeded: ["نجح", "green"],
    failed: ["فشل", "red"],
    cancelled: ["أُلغي", "gray"],
  };

  const STEP_STATUS_LABELS = {
    pending: ["في الانتظار", "gray"],
    running: ["قيد التنفيذ", "blue"],
    waiting: ["معلّق", "yellow"],
    retrying: ["يعيد المحاولة", "orange"],
    succeeded: ["نجحت", "green"],
    failed: ["فشلت", "red"],
    skipped: ["تم تخطّيها", "gray"],
  };

  const VALIDATION_STATUS_LABELS = {
    pending: ["قيد التحقق", "yellow"],
    passed: ["سليم", "green"],
    failed: ["مرفوض", "red"],
  };

  const INCIDENT_STATUS_LABELS = {
    open: ["مفتوحة", "red"],
    diagnosing: ["قيد التشخيص", "orange"],
    fixing: ["قيد الإصلاح", "yellow"],
    resolved: ["تم الحل", "green"],
    escalated: ["مُصعَّدة لشخص", "blue"],
  };

  const SEVERITY_LABELS = {
    debug: ["تفاصيل", "gray"],
    info: ["معلومة", "blue"],
    warning: ["تنبيه", "yellow"],
    error: ["خطأ", "red"],
    critical: ["حرج", "red"],
  };

  const EVENT_TYPE_LABELS = {
    run_created: "تم إنشاء التشغيل",
    run_started: "بدأ التشغيل",
    run_waiting: "التشغيل معلّق مؤقتًا",
    run_resumed: "استؤنف التشغيل",
    run_succeeded: "اكتمل التشغيل بنجاح",
    run_failed: "فشل التشغيل",
    run_cancelled: "أُلغي التشغيل",
    step_started: "بدأت خطوة",
    step_succeeded: "نجحت خطوة",
    step_failed: "فشلت خطوة",
    step_retry_scheduled: "تمت جدولة إعادة محاولة",
    file_downloaded: "تم تنزيل ملف",
    file_validated: "تم التحقق من ملف",
    file_rejected: "تم رفض ملف",
    alert_raised: "تنبيه من المنصة",
    incident_opened: "تم فتح حادثة",
    incident_closed: "تم إغلاق حادثة",
    agent_run_started: "بدأ وكيل الذكاء الاصطناعي",
    agent_run_finished: "انتهى وكيل الذكاء الاصطناعي",
    escalated: "تم التصعيد",
  };

  const ERROR_CLASS_LABELS = {
    transient: "عطل مؤقت",
    rate_limit: "تم تجاوز الحد المسموح مؤقتًا",
    auth: "انتهت صلاحية الجلسة",
    target_not_found: "العنصر المطلوب غير موجود",
    data_quality: "مشكلة في جودة الملف",
    permanent: "خطأ في الإعداد يحتاج تصحيحًا يدويًا",
    internal: "خطأ غير متوقع داخل المنصة",
  };

  function badge(label, color) {
    const span = document.createElement("span");
    span.className = `badge badge-${color}`;
    span.textContent = label;
    return span;
  }

  function statusBadge(map, code) {
    const [label, color] = map[code] || [code, "gray"];
    return badge(label, color);
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("ar-EG", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function shortId(id) {
    if (!id) return "—";
    return id.length > 14 ? `${id.slice(0, 10)}…` : id;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else node.setAttribute(key, value);
    }
    for (const child of children || []) {
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function link(text, href) {
    return el("a", { class: "link", href }, [text]);
  }

  function showError(container, error) {
    container.innerHTML = "";
    container.appendChild(el("div", { class: "error-box", text: error.message || String(error) }));
  }

  function connectEvents(runId, onEvent) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    const socket = new WebSocket(`${proto}://${location.host}/ws/events${query}`);
    socket.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data));
      } catch (_) {
        /* رسالة غير متوقعة، تجاهلها */
      }
    };
    return socket;
  }

  return {
    getJSON,
    postJSON,
    RUN_STATUS_LABELS,
    STEP_STATUS_LABELS,
    VALIDATION_STATUS_LABELS,
    INCIDENT_STATUS_LABELS,
    SEVERITY_LABELS,
    EVENT_TYPE_LABELS,
    ERROR_CLASS_LABELS,
    badge,
    statusBadge,
    formatDate,
    shortId,
    el,
    link,
    showError,
    connectEvents,
  };
})();
