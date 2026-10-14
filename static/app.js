
async function runSearch() {
  const input = document.getElementById('q');
  const out = document.getElementById('out');

  if (!input || !out) {
    console.error('تعذر العثور على عناصر البحث في الصفحة.');
    return;
  }

  const q = input.value.trim();

  if (!q) {
    out.innerHTML = '<div class="card">اكتب المدخل أولًا.</div>';
    return;
  }

  out.innerHTML = '<div class="card">جارٍ الفحص، يرجى الانتظار...</div>';

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);

  try {
    const response = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q }),
      signal: controller.signal
    });

    const data = await response.json().catch(() => null);

    if (!response.ok) {
      const message =
        data && typeof data.error === 'string'
          ? data.error
          : `تعذر الفحص (HTTP ${response.status})`;

      out.innerHTML =
        `<div class="card error">${escapeHtml(message)}</div>`;
      return;
    }

    if (!data || !data.scan || !Array.isArray(data.scan.observations)
        || !Array.isArray(data.scan.discoveries)) {
      out.innerHTML =
        '<div class="card error">استجابة الخادم غير مكتملة أو غير متوافقة مع الواجهة.</div>';
      return;
    }

    const observations = data.scan.observations
      .map(x => `<li><b>${escapeHtml(x.axis ?? '')}</b>: ${
        escapeHtml(x.value ?? '')
      } — ${escapeHtml(x.detail ?? '')}</li>`)
      .join('');

    const discoveries = data.scan.discoveries
      .map(x => `<li><b>${escapeHtml(x.kind ?? '')}</b>: ${
        escapeHtml(x.payload ?? '')
      }</li>`)
      .join('');

    const rules = Array.isArray(data.method_rules)
      ? data.method_rules
          .map(x => `<li>${escapeHtml(x)}</li>`)
          .join('')
      : '<li>لم يرسل الخادم قواعد التحقق.</li>';

    const runId = Number.isFinite(Number(data.run_id))
      ? String(data.run_id).padStart(3, '0')
      : 'غير متاح';

    out.innerHTML = `
      <div class="card">
        <h2>RUN-${runId}</h2>
        <div class="grid">
          <div class="box">
            <b>التصنيف</b><br>
            <span class="badge">${escapeHtml(data.classification ?? 'غير محدد')}</span>
          </div>
          <div class="box">
            <b>الحالة</b><br>
            <span class="badge">UNRESOLVED</span>
          </div>
          <div class="box">
            <b>بعد التطبيع</b><br>
            ${escapeHtml(data.scan.normalized ?? '')}
          </div>
        </div>
      </div>
      <div class="card">
        <h3>الملاحظات الأولية</h3>
        <ul>${observations || '<li>لا توجد ملاحظات.</li>'}</ul>
      </div>
      <div class="card">
        <h3>الاكتشافات</h3>
        <ul>${discoveries || '<li>لا يوجد اكتشاف جديد في هذه الدورة.</li>'}</ul>
      </div>
      <div class="card">
        <h3>قواعد التحقق</h3>
        <ul>${rules}</ul>
      </div>`;
  } catch (error) {
    const message = error.name === 'AbortError'
      ? 'تجاوز البحث مهلة 30 ثانية. تحقق من الخادم ثم أعد المحاولة.'
      : 'تعذر الاتصال بمحرك البحث. تحقق من الاتصال وسجل الخادم.';

    out.innerHTML =
      `<div class="card error">${escapeHtml(message)}</div>`;

    console.error('Search request failed:', error);
  } finally {
    clearTimeout(timeout);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[c]);
}
