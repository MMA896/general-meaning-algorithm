# GMA — Corpus Search Engine next

هذه النسخة تربط واجهة البحث بطبقة فهرسة فعلية لبيانات Quranic Arabic Corpus morphology v0.4.

- لا تُعدّل ملف corpus الأصلي.
- المصدر: Quranic Arabic Corpus morphology v0.4.
- الرابط المصدر محفوظ في التطبيق.
- البحث يدعم auto/exact/lemma/root/character.
- النتائج تتضمن الإحداثيات والسابق واللاحق وlemma/root/POS.
- النتائج الوصفية لا تُعامل كدليل دلالي.

للتشغيل: `python app.py` أو `gunicorn app:app`.
عند غياب corpus يحاول التطبيق جلب النسخة الأصلية غير المعدلة من مستودع عام يحوي الملف.
