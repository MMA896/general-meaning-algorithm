{"kind":"MATCH_COUNT","payload":f"عدد الوحدات المطابقة في النمط المحدد: {total}."},
      {"kind":"UNIQUE_FORMS","payload":f"عدد الصيغ السطحية المختلفة: {len(forms)}."},
      {"kind":"ROOT_COVERAGE","payload":f"عدد الجذور المسجلة بين النتائج: {len(roots)}."},
      {"kind":"CONTEXT_SAMPLE","payload":f"عُرضت حتى {len(contexts)} مواضع سياقية من أصل {total}."},
      {"kind":"INTERPRETATION_LIMIT","payload":"المخرجات وصفية آلية؛ لا تُعد حكمًا لغويًا نهائيًا أو تفسيرًا شرعيًا."}
    ]
    for d in discoveries:
        c.execute("INSERT INTO discoveries(run_id,kind,payload,created_at) VALUES(?,?,?,?)",(rid,d["kind"],d["payload"],now))
    c.commit()
    meta=c.execute("SELECT value FROM corpus_meta WHERE key='imported_at'").fetchone()
    return {"ok":True,"run_id":rid,"created_at":now,"query":q,"normalized":normalize(q),"mode":mode,
      "classification":classify(q,rows),"status":"MATCHED" if total else "UNRESOLVED",
      "source":{"name":"Quranic Arabic Corpus morphology v0.4","url":CORPUS_URL,
        "imported_at":meta[0] if meta else None,"units":state.get("count") or c.execute("SELECT COUNT(*) FROM units").fetchone()[0]},
      "count":total,"displayed_contexts":len(contexts),
      "summary":{"forms":sorted(forms.items(),key=lambda x:(-x[1],x[0]))[:40],
        "roots":sorted(roots.items(),key=lambda x:(-x[1],x[0]))[:40],
        "lemmas":sorted(lemmas.items(),key=lambda x:(-x[1],x[0]))[:40],
        "parts_of_speech":sorted(pos.items(),key=lambda x:(-x[1],x[0])),
        "previous_words":sorted(prev.items(),key=lambda x:(-x[1],x[0]))[:25],
        "next_words":sorted(nxt.items(),key=lambda x:(-x[1],x[0]))[:25],
        "letter_counts":sorted(letters.items(),key=lambda x:(-x[1],x[0]))},
      "contexts":contexts,"discoveries":discoveries,"method_rules":rules,
      "limits":["هذا المصدر طبقة صرفية/موضعية وليس نص الآيات الكامل؛ لذلك لا يعرض نص الآية كاملًا.","قد تختلف نتائج الجذر/اللمّة بحسب ترميز المصدر.","النتيجة الصفرية لا تثبت الغياب من القرآن؛ فقد يكون النمط أو التطبيع غير مناسب."]}

@app.get("/login")
@app.route("/login",methods=["POST"])
def login():
    if request.method=="POST":
        if secrets.compare_digest(request.form.get("username",""),USER) and secrets.compare_digest(request.form.get("password",""),PASSWORD):
            session.clear(); session["authenticated"]=True
            return redirect(url_for("index"))
        return render_template("login.html",error="اسم المستخدم أو كلمة المرور غير صحيحين.", version=VERSION)
    return render_template("login.html",error=None,version=VERSION)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
def index():
    if not authenticated(): return redirect(url_for("login"))
    return render_template("index.html",version=VERSION)

@app.post("/api/search")
def api_search():
    denied=login_required_json()
    if denied: return denied
    payload=request.get_json(silent=True) or {}
    q=(payload.get("query") or "").strip()
    mode=payload.get("mode","auto")
    if not q: return jsonify({"error":"اكتب كلمة أو عبارة أولًا."}),400
    if mode not in {"auto","exact","lemma","root","character"}: return jsonify({"error":"نمط البحث غير صالح."}),400
    result = analyze(q, mode)
    status_code = int(result.pop("status_code", 200))
    if not result.get("ok"):
        return jsonify(result), status_code
    return jsonify(result)

@app.post("/api/admin/import")
def api_import():
    denied = login_required_json()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force"))
    count = db().execute("SELECT COUNT(*) FROM units").fetchone()[0]
    if count and not force:
        return jsonify({"ok": True, "state": "already_imported", "count": count})
    status = _start_import_background(force=force)
    return jsonify({"ok": True, "state": status.get("state", "starting"),
                    "message": status.get("message", "بدأ تجهيز الفهرس في الخلفية.")}), 202


@app.get("/api/admin/import/status")
def api_import_status():
    denied = login_required_json()
    if denied:
        return denied
    c = db()
    count = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    parser = c.execute("SELECT value FROM corpus_meta WHERE key='parser_version'").fetchone()
    index_current = bool(count and parser and parser[0] == PARSER_VERSION)
    status = _recover_stale_import_status(_read_import_status())
    if index_current:
        status = {**status, "state": "ready", "count": count}
    elif not app.config.get("TESTING") and status.get("state") not in {"starting", "running"}:
        _start_import_background(force=bool(count))
        status = _read_import_status()
    return jsonify({**status, "index_ready": index_current, "indexed_units": count,
                    "parser_version": parser[0] if parser else None})

@app.get("/api/history")
def api_history():
    denied=login_required_json()
    if denied: return denied
    rows=db().execute("SELECT id,created_at,query_text,normalized,mode,status,match_count FROM runs ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify({"runs":[dict(r) for r in rows]})

@app.get("/health")
def health():
    c = sqlite3.connect(DB_PATH, timeout=10)
    try:
        count = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        parser = c.execute("SELECT value FROM corpus_meta WHERE key='parser_version'").fetchone()
    except sqlite3.Error:
        count, parser = 0, None
    finally:
        c.close()
    index_current = bool(count and parser and parser[0] == PARSER_VERSION)
    status = _recover_stale_import_status(_read_import_status())
    if index_current:
        status = {**status, "state": "ready", "count": count}
    elif not app.config.get("TESTING") and status.get("state") not in {"starting", "running"}:
        _start_import_background(force=bool(count))
        status = _read_import_status()
    return jsonify({"status": "ok", "version": VERSION, "corpus_file_present": os.path.exists(CORPUS_PATH),
                    "index_ready": index_current, "indexed_units": count,
                    "import_state": status.get("state", "idle"),
                    "import_error": status.get("detail") or status.get("error"),
                    "import_message": status.get("message"),
                    "import_updated_at": status.get("updated_at"),
                    "import_started_at": status.get("started_at"),
                    "import_recovered_from": status.get("recovered_from"),
                    "corpus_source": CORPUS_URL, "parser_version": parser[0] if parser else None})

with app.app_context(): init_db()
