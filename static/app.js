'use strict';

async function runSearch() {
  const input = document.getElementById('q');
  const out = document.getElementById('out');
  const button = document.querySelector('button[onclick="runSearch()"]');
  if (!input || !out) {
    console.error('تعذر العثور على عناصر البحث في الصفحة.');
    return;
  }

  const q = input.value.trim();
  if (!q) {
    out.innerHTML = '<div class="card">اكتب المدخل أولًا.</div>';
    input.focus();
    return;
  }

  if (button) { button.disabled = true; button.textContent = 'جارٍ الفحص…'; }
  out.innerHTML = '<div class="card">جارٍ الفحص، يرجى الانتظار...</div>';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);

  try {
    const response = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify({ query: q }),
      signal: controller.signal
    });
    const data = await response.json().catch(() => null);

    if (!response.ok) {
      const message = data && typeof data.error === 'string'
        ? data.error
        : `تعذر الفحص (HTTP ${response.status})`;
      out.innerHTML = `<div class="card error"><b>تعذر إكمال البحث</b><p>${escapeHtml(message)}</p>${data && data.code ? `<p>رمز الحالة: ${escapeHtml(data.code)}</p>` : ''}</div>`;
      return;
    }

    // The API returns a flat object: contexts, summary, discoveries, method_rules, limits.
    // Do not expect a non-existent data.scan object.
    if (!data || data.ok !== true || !Array.isArray(data.contexts) || !data.summary || !Array.isArray(data.discoveries)) {
      console.error('Unexpected /api/search response:', data);
      out.innerHTML = '<div class="card error"><b>استجابة الخادم غير متوافقة مع الواجهة.</b><p>وصل رد من الخادم، لكن حقوله لا تطابق عقد الواجهة الحالي. افتح أدوات المطور أو أرسل سجل الطلب للدعم.</p></div>';
      return;
    }

    const listPairs = (items, emptyText = 'لا توجد بيانات.') => {
      if (!Array.isArray(items) || !items.length) return `<li>${escapeHtml(emptyText)}</li>`;
      return items.slice(0, 40).map(item => {
        if (Array.isArray(item)) return `<li><b>${escapeHtml(item[0] ?? '')}</b>: ${escapeHtml(item[1] ?? '')}</li>`;
        return `<li>${escapeHtml(item)}</li>`;
      }).join('');
    };
    const discoveries = data.discoveries.map(x => `<li><b>${escapeHtml(x.kind ?? '')}</b>: ${escapeHtml(x.payload ?? '')}</li>`).join('');
    const contexts = data.contexts.slice(0, 30).map(x => `
      <tr><td>${escapeHtml(x.location ?? '')}</td><td>${escapeHtml(x.surface ?? '')}</td><td>${escapeHtml(x.lemma ?? '')}</td><td>${escapeHtml(x.root ?? '')}</td><td>${escapeHtml(x.pos ?? '')}</td><td>${escapeHtml(x.tag ?? '')}</td></tr>`).join('');
    const rules = Array.isArray(data.method_rules) ? data.method_rules.map(x => `<li>${escapeHtml(x)}</li>`).join('') : '<li>لم يرسل الخادم قواعد التحقق.</li>';
    const limits = Array.isArray(data.limits) ? data.limits.map(x => `<li>${escapeHtml(x)}</li>`).join('') : '<li>لا توجد ملاحظات حدودية.</li>';
    const statusLabel = data.status === 'MATCHED' ? 'عُثر على مطابقات' : 'لم تُعثر على مطابقات بهذا النمط';
    const summary = data.summary;

    out.innerHTML = `
      <div class="card">
        <h2>نتيجة البحث: ${escapeHtml(data.query ?? q)}</h2>
        <div class="grid">
          <div class="box"><b>رقم العملية</b><br><span class="badge">RUN-${escapeHtml(data.run_id ?? '—')}</span></div>
          <div class="box"><b>الحالة</b><br><span class="badge">${escapeHtml(statusLabel)}</span></div>
          <div class="box"><b>التصنيف</b><br><span class="badge">${escapeHtml(data.classification ?? 'غير محدد')}</span></div>
          <div class="box"><b>عدد المطابقات</b><br><strong>${escapeHtml(data.count ?? 0)}</strong></div>
          <div class="box"><b>بعد التطبيع</b><br>${escapeHtml(data.normalized ?? '')}</div>
          <div class="box"><b>وحدات المصدر المفهرسة</b><br>${escapeHtml(data.source?.units ?? 'غير متاح')}</div>
        </div>
        <p class="muted">المصدر: ${escapeHtml(data.source?.name ?? 'غير محدد')} — نمط البحث: ${escapeHtml(data.mode ?? 'auto')}</p>
      </div>
      <div class="card"><h3>ملخص الصيغ</h3><ul>${listPairs(summary.forms)}</ul></div>
      <div class="grid">
        <div class="card"><h3>الجذور</h3><ul>${listPairs(summary.roots)}</ul></div>
        <div class="card"><h3>اللّمّات</h3><ul>${listPairs(summary.lemmas)}</ul></div>
        <div class="card"><h3>أقسام الكلام</h3><ul>${listPairs(summary.parts_of_speech)}</ul></div>
        <div class="card"><h3>الكلمات السابقة</h3><ul>${listPairs(summary.previous_words)}</ul></div>
        <div class="card"><h3>الكلمات التالية</h3><ul>${listPairs(summary.next_words)}</ul></div>
        <div class="card"><h3>تكرار الحروف</h3><ul>${listPairs(summary.letter_counts)}</ul></div>
      </div>
      <div class="card"><h3>المواضع السياقية (أول ${Math.min(data.contexts.length, 30)} موضعًا)</h3>
        ${contexts ? `<div style="overflow-x:auto"><table><thead><tr><th>الموضع</th><th>الصيغة</th><th>اللّمّة</th><th>الجذر</th><th>القسم</th><th>الوسم</th></tr></thead><tbody>${contexts}</tbody></table></div>` : '<p>لا توجد مواضع مطابقة.</p>'}
      </div>
      <div class="card"><h3>الاكتشافات الوصفية</h3><ul>${discoveries || '<li>لا توجد اكتشافات.</li>'}</ul></div>
      <div class="card"><h3>قواعد التحقق</h3><ul>${rules}</ul></div>
      <div class="card"><h3>حدود النتائج</h3><ul>${limits}</ul></div>`;
  } catch (error) {
    const message = error.name === 'AbortError'
      ? 'تجاوز البحث مهلة 30 ثانية. تحقق من سجلات الخادم ثم أعد المحاولة.'
      : 'تعذر الاتصال بمحرك البحث. تحقق من الاتصال وسجل الخادم.';
    out.innerHTML = `<div class="card error">${escapeHtml(message)}</div>`;
    console.error('Search request failed:', error);
  } finally {
    clearTimeout(timeout);
    if (button) { button.disabled = false; button.textContent = 'ابدأ المسح'; }
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}
