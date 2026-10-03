# خوارزمية البحث عن المراد / المعنى العام

هذه مجلد تشغيل تجريبي مرتب للمشروع، جاهز للوضع في مستودع GitHub ثم نشره على Render.

## مهم
ملف `data/source_units.jsonl` يحتوي حاليًا بيانات DEMO فقط، وليس corpus موثقًا للقرآن الكريم والسنة الصحيحة. لذلك هذه النسخة لا تدعي نتائج بحث دلالية نهائية.

## التشغيل
- تثبيت المتطلبات من `requirements.txt`
- ضبط `RESEARCH_USER` و`RESEARCH_PASSWORD` و`SECRET_KEY`
- تشغيل `gunicorn app:app` في الاستضافة
- اختبار `/health`

## Render
يوجد `render.yaml` جاهز، مع كلمة مرور تملأها من إعدادات الخدمة لأن المفتاح `RESEARCH_PASSWORD` سري.
