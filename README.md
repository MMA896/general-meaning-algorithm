# خوارزمية البحث عن المراد / المعنى العام

## نطاق هذه الحزمة
هذه نسخة أولية ذات فحص نصي وصفي محدود. لا تدّعي استخراج دلالات قرآنية أو إثبات علاقات لغوية. لا تعرض نتائج قرآنية موثقة قبل توفير ملف المصدر الأصلي والتحقق من ترخيصه وبنيته ومطابقة سجلاته.

## متطلبات التشغيل
- Python 3.10+
- تثبيت المتطلبات: `pip install -r requirements.txt`
- ضبط `RESEARCH_PASSWORD` و`SECRET_KEY` في بيئة التشغيل. لا تستخدم كلمة مرور افتراضية.
- `RESEARCH_USER` اختياري، وافتراضيه `researcher`.
- `COOKIE_SECURE=1` مناسب للنشر خلف HTTPS.

## مصدر corpus
المسار المتوقع في هذه النسخة هو `data/qac_morphology_0.4.txt`. الملف غير مضمن في هذه الحزمة. المصدر المشار إليه في المشروع: https://corpus.quran.com/download/
لا يُعتبر وجود الملف وحده تحققًا من النتائج؛ يجب فحص تنسيقه وحقوله وتوثيق الإصدار.

## قواعد المنهج
- Candidate ≠ Fact
- NotFound ≠ False
- TemporalOrder ≠ Causality
- No effect without comparison
- No provenance ⇒ No evidential status
- Discovery data ≠ independent test data

## اختبارات
بعد تثبيت المتطلبات، شغّل: `python -m unittest -v test_app.py`
افحص صياغة JavaScript باستخدام: `node --check static/app.js`

## حدود النشر
قاعدة البيانات SQLite تُنشأ بجانب التطبيق. على استضافة لا تضمن قرصًا دائمًا، قد تضيع سجلات التشغيل عند إعادة إنشاء البيئة أو نشر جديد. للاستخدام الموثوق طويل الأمد، انقل السجلات إلى قاعدة بيانات دائمة مثل PostgreSQL مع نسخ احتياطي.
