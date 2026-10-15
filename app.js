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
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ query: q }),
            signal: controller.signal
        });
        
        const data = await response.json().catch(() => null);
        
        if (!response.ok) {
            const message = data && typeof data.error === 'string' ? data.error : `تعذر الفحص (HTTP ${response.status})`;
            out.innerHTML = `<div class="card error">${escapeHtml(message)}</div>`;
            return;
        }
        
        if (!data) {
            out.innerHTML = '<div class="card error">استجابة الخادم فارغة أو غير متوافقة.</div>';
            return;
        }

        // قراءة البيانات بالطريقة التي تتوافق مع استجابة app.py الفعلية
        const queryText = data.query || q;
        const results = Array.isArray(data.results) ? data.results : [];
        
        const resultsHtml = results.length > 0 ? results.map(x => `
            <li>
                <b>${escapeHtml(x.term || x.word || '')}</b>: ${escapeHtml(x.meaning || x.description || JSON.stringify(x))}
            </li>
        `).join('') : '<li>لا توجد نتائج مطابقة لهذه الدورة.</li>';

        out.innerHTML = `
            <div class="card">
                <h2>نتيجة البحث عن: ${escapeHtml(queryText)}</h2>
                <div class="grid">
                    <div class="box">
                        <b>الحالة</b><br>
                        <span class="badge">SUCCESS</span>
                    </div>
                </div>
            </div>
            <div class="card">
                <h3>النتائج والملاحظات</h3>
                <ul>${resultsHtml}</ul>
            </div>
        `;
        
    } catch (error) {
        const message = error.name === 'AbortError' ? 'تجاوز البحث مهلة 30 ثانية. تحقق من الخادم ثم أعد المحاولة.' : 'تعذر الاتصال بمحرك البحث. تحقق من الاتصال وسجل الخادم.';
        out.innerHTML = `<div class="card error">${escapeHtml(message)}</div>`;
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