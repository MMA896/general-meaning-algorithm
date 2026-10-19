import os, re, sqlite3, secrets, json, urllib.request, hashlib, threading, time
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE, "research.db"))
CORPUS_PATH = os.path.join(BASE, "data", "quranic-corpus-morphology-0.4.txt")
CORPUS_URL = "https://raw.githubusercontent.com/bnjasim/quranic-corpus/master/quranic-corpus-morphology-0.4.txt"
VERSION = "2.1.4"
DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1")
USER = os.environ.get("RESEARCH_USER", "researcher")
PASSWORD = os.environ.get("RESEARCH_PASSWORD", "change-me")

# Shared cross-worker status/lock files prevent multiple Gunicorn workers from
# downloading and rebuilding the same corpus at the same time.
IMPORT_LOCK_PATH = os.environ.get("INDEX_LOCK_PATH", os.path.join(BASE, ".index_import.lock"))
IMPORT_STATUS_PATH = os.environ.get("INDEX_STATUS_PATH", os.path.join(BASE, ".index_import_status.json"))
_IMPORT_THREAD = None
_IMPORT_THREAD_GUARD = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_import_status():
    try:
        with open(IMPORT_STATUS_PATH, "r", encoding="utf-8") as f:
            status = json.load(f)
        if isinstance(status, dict):
            return status
    except (OSError, ValueError, TypeError):
        pass
    return {"state": "idle"}


def _write_import_status(state, **details):
    payload = {"state": state, "updated_at": _now_iso(), **details}
    try:
        parent = os.path.dirname(IMPORT_STATUS_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp = f"{IMPORT_STATUS_PATH}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp, IMPORT_STATUS_PATH)
    except OSError:
        # Status telemetry must never stop indexing/search itself.
        pass
    return payload


def _status_age_seconds(status):
    """Return status age, or None if its timestamp cannot be parsed."""
    raw = status.get("updated_at") or status.get("started_at")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return None


def _pid_is_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, OSError, TypeError):
        return False


def _recover_stale_import_status(status=None):
    """Recover an abandoned 'starting/running' flag instead of freezing indexing forever.

    The status and lock live on the service filesystem, while a worker/thread may
    be restarted independently. A stale status must not be treated as proof that
    a real importer is still running.
    """
    status = dict(status or _read_import_status())
    state = status.get("state")
    if state not in {"starting", "running"}:
        return status

    age = _status_age_seconds(status)
    limit = 60 if state == "starting" else 600
    if age is not None and age <= limit:
        return status

    thread = _IMPORT_THREAD
    if thread is not None and thread.is_alive():
        # A real local thread is alive; correct an out-of-date label only.
        if state == "starting":
            return _write_import_status(
                "running",
                started_at=status.get("started_at") or status.get("updated_at") or _now_iso(),
                message="عملية الفهرسة تعمل في الخلفية.",
                recovered_label=True,
            )
        return status

    # If another live worker owns the lock, let it finish. Otherwise remove the
    # abandoned lock. The same-process lock can be stale when its thread died.
    try:
        lock_lines = Path(IMPORT_LOCK_PATH).read_text(encoding="utf-8").splitlines()
        lock_pid = int(lock_lines[0]) if lock_lines else -1
    except (OSError, ValueError):
        lock_pid = None

    if lock_pid is not None and lock_pid != os.getpid() and _pid_is_alive(lock_pid):
        return status

    try:
        if os.path.exists(IMPORT_LOCK_PATH):
            os.unlink(IMPORT_LOCK_PATH)
    except OSError:
        pass

    return _write_import_status(
        "idle",
        recovered_from=state,
        previous_updated_at=status.get("updated_at"),
        message="اكتُشفت محاولة فهرسة متوقفة؛ ستُعاد المحاولة عند الطلب التالي.",
    )


def _claim_import_lock():
    parent = os.path.dirname(IMPORT_LOCK_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(IMPORT_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()}\n{time.time()}\n")
            return True
        except FileExistsError:
            try:
                raw = Path(IMPORT_LOCK_PATH).read_text(encoding="utf-8").splitlines()
                pid = int(raw[0]) if raw else -1
                try:
                    os.kill(pid, 0)
                    alive = True
                except PermissionError:
                    alive = True
                except (ProcessLookupError, ValueError, OSError):
                    alive = False
                if alive:
                    return False
                os.unlink(IMPORT_LOCK_PATH)
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                try:
                    age = time.time() - os.path.getmtime(IMPORT_LOCK_PATH)
                    if age < 900:
                        return False
                    os.unlink(IMPORT_LOCK_PATH)
                except OSError:
                    continue
    return False


def _release_import_lock():
    try:
        raw = Path(IMPORT_LOCK_PATH).read_text(encoding="utf-8").splitlines()
        if raw and int(raw[0]) == os.getpid():
            os.unlink(IMPORT_LOCK_PATH)
    except (OSError, ValueError):
        pass


def _import_worker(force=False):
    try:
        _write_import_status("running", started_at=_now_iso(), message="جارٍ جلب المصدر وفهرسته.")
        result = import_corpus(force=force)
        if result.get("ok"):
            _write_import_status("ready", finished_at=_now_iso(), count=result.get("count", 0),
                                 result_state=result.get("state", "imported"), error=None)
        else:
            _write_import_status("error", finished_at=_now_iso(), error=result.get("error", "فشل الاستيراد"),
                                 detail=result.get("detail"), result=result)
    except Exception as exc:
        _write_import_status("error", finished_at=_now_iso(), error="تعذر تجهيز فهرس المصدر.", detail=str(exc))
    finally:
        _release_import_lock()


def _start_import_background(force=False):
    global _IMPORT_THREAD
    if app.config.get("TESTING"):
        return {"state": "idle", "message": "وضع الاختبار؛ لم يبدأ الاستيراد."}
    with _IMPORT_THREAD_GUARD:
        if _IMPORT_THREAD is not None and _IMPORT_THREAD.is_alive():
            return {"state": "running", "message": "الفهرسة جارية بالفعل."}
        _recover_stale_import_status()
        if not _claim_import_lock():
            current = _read_import_status()
            return {"state": current.get("state", "running"), "message": "الفهرسة جارية بالفعل."}
        _write_import_status("starting", started_at=_now_iso(), message="سيبدأ تجهيز الفهرس في الخلفية.")
        thread = threading.Thread(target=_import_worker, args=(force,), daemon=True,
                                  name="quran-corpus-indexer")
        _IMPORT_THREAD = thread
        try:
            thread.start()
        except Exception as exc:
            _release_import_lock()
            _write_import_status("error", finished_at=_now_iso(), error="تعذر بدء الفهرسة.", detail=str(exc))
            return {"state": "error", "error": str(exc)}
    return {"state": "starting", "message": "بدأ تجهيز الفهرس في الخلفية."}


# Quranic Arabic Corpus v0.4 stores FORM, LEM and ROOT in Buckwalter
# transliteration, not Arabic script. Convert these fields before indexing.
BUCKWALTER_TO_ARABIC = str.maketrans({
    "'":"ء", ">":"أ", "&":"ؤ", "<":"إ", "}":"ئ", "A":"ا", "b":"ب",
    "p":"ة", "t":"ت", "v":"ث", "j":"ج", "H":"ح", "x":"خ", "d":"د",
    "*":"ذ", "r":"ر", "z":"ز", "s":"س", "$":"ش", "S":"ص", "D":"ض",
    "T":"ط", "Z":"ظ", "E":"ع", "g":"غ", "_":"ـ", "f":"ف", "q":"ق",
    "k":"ك", "l":"ل", "m":"م", "n":"ن", "h":"ه", "w":"و", "Y":"ى",
    "y":"ي", "F":"ً", "N":"ٌ", "K":"ٍ", "a":"َ", "u":"ُ", "i":"ِ",
    "~":"ّ", "o":"ْ", "^":"ٓ", "#":"ٔ", "`":"ٰ", "{":"ٱ", ":":"ۜ",
    "@":"۟", '"':"۠", "[":"ۢ", ";":"ۣ", ",":"ۥ", ".":"ۦ", "!":"ۨ",
    "-":"۪", "+":"۫", "%":"۬", "]":"ۭ",
})
PARSER_VERSION = "buckwalter-ar-v1"

def buckwalter_to_arabic(value):
    return (value or "").translate(BUCKWALTER_TO_ARABIC)

def normalize(s):
    s = DIACRITICS.sub("", (s or "")).replace("ـ", "")
    return re.sub(r"\s+", " ", s.translate(str.maketrans({"أ":"ا","إ":"ا","آ":"ا","ٱ":"ا","ى":"ي"}))).strip()

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout=30000")
    return g.db

@app.teardown_appcontext
def close_db(_e=None):
    c = g.pop("db", None)
    if c: c.close()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c=sqlite3.connect(DB_PATH)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS corpus_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS units(
      id INTEGER PRIMARY KEY AUTOINCREMENT,surah INTEGER NOT NULL,verse INTEGER NOT NULL,
      word INTEGER NOT NULL,segment INTEGER NOT NULL,surface TEXT NOT NULL,normalized TEXT NOT NULL,
      tag TEXT,features TEXT,pos TEXT,lemma TEXT,root TEXT,UNIQUE(surah,verse,word,segment));
    CREATE INDEX IF NOT EXISTS idx_norm ON units(normalized);
    CREATE INDEX IF NOT EXISTS idx_lemma ON units(lemma);
    CREATE INDEX IF NOT EXISTS idx_root ON units(root);
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT,query_text TEXT,normalized TEXT,mode TEXT,status TEXT,match_count INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS discoveries(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER,kind TEXT,payload TEXT,created_at TEXT);
    """)
    # Safe schema migration: add columns missing from older installations.
    cols={r[1] for r in c.execute("PRAGMA table_info(runs)")}
    for name, decl in [("normalized","TEXT"),("match_count","INTEGER DEFAULT 0")]:
        if name not in cols: c.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
    c.commit(); c.close()

def ensure_corpus():
    if os.path.exists(CORPUS_PATH) and os.path.getsize(CORPUS_PATH)>100000:
        return True, "local"
    os.makedirs(os.path.dirname(CORPUS_PATH),exist_ok=True)
    try:
        req=urllib.request.Request(CORPUS_URL,headers={"User-Agent":"GeneralMeaningResearch/2.0"})
        with urllib.request.urlopen(req,timeout=35) as resp, open(CORPUS_PATH,"wb") as out:
            out.write(resp.read())
        if os.path.getsize(CORPUS_PATH)<100000:
            raise ValueError("حجم ملف المصدر أقل من المتوقع")
        return True,"downloaded"
    except Exception as e:
        try:
            if os.path.exists(CORPUS_PATH): os.remove(CORPUS_PATH)
        except OSError: pass
        return False,str(e)

def parse_loc(loc):
    nums=re.findall(r"\d+",loc)
    return tuple(map(int,nums[:4])) if len(nums)>=4 else None

def parse_features(s):
    d={}
    for part in s.split("|"):
        if ":" in part:
            k,v=part.split(":",1); d[k]=v
    return d

def import_corpus(force=False):
    ok, state = ensure_corpus()
    if not ok:
        return {"ok": False, "error": "تعذر جلب ملف المصدر القرآني", "detail": state}
    size = str(os.path.getsize(CORPUS_PATH))
    c = sqlite3.connect(DB_PATH, timeout=60)
    inserted = 0
    try:
        c.execute("PRAGMA busy_timeout=60000")
        old = c.execute("SELECT value FROM corpus_meta WHERE key='source_size'").fetchone()
        parser = c.execute("SELECT value FROM corpus_meta WHERE key='parser_version'").fetchone()
        count = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        if old and old[0] == size and parser and parser[0] == PARSER_VERSION and count and not force:
            return {"ok": True, "state": "already_imported", "count": count}

        # The deletion and insertions are one transaction. If parsing fails,
        # the previous index remains intact instead of being left half-empty.
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM units")
        with open(CORPUS_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip() or line.startswith("#") or "\t" not in line:
                    continue
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 4:
                    continue
                loc, surface, tag, features = parts[:4]
                coords = parse_loc(loc)
                if not coords or not surface:
                    continue
                surah, verse, word, segment = coords
                ft = parse_features(features)
                surface_ar = buckwalter_to_arabic(surface)
                lemma_ar = normalize(buckwalter_to_arabic(ft.get("LEM")))
                root_ar = normalize(buckwalter_to_arabic(ft.get("ROOT")))
                c.execute(
                    "INSERT OR REPLACE INTO units(surah,verse,word,segment,surface,normalized,tag,features,pos,lemma,root) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (surah, verse, word, segment, surface_ar, normalize(surface_ar), tag, features,
                     ft.get("POS"), lemma_ar, root_ar))
                inserted += 1
        if inserted < 10000:
            c.rollback()
            return {"ok": False, "error": "ملف المصدر لم ينتج عددًا كافيًا من الوحدات؛ لم يُعتمد الاستيراد", "parsed": inserted}
        c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_size',?)", (size,))
        c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_name','Quranic Arabic Corpus morphology v0.4')")
        c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_url',?)", (CORPUS_URL,))
        c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('parser_version',?)", (PARSER_VERSION,))
        c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('imported_at',?)", (_now_iso(),))
        c.commit()
        return {"ok": True, "state": "imported", "count": inserted, "source": CORPUS_URL}
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def ensure_index():
    c = db()
    count = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    parser = c.execute("SELECT value FROM corpus_meta WHERE key='parser_version'").fetchone()
    index_current = bool(count and parser and parser[0] == PARSER_VERSION)
    if index_current:
        return {"ok": True, "state": "ready", "count": count}
    status = _recover_stale_import_status(_read_import_status())
    if status.get("state") not in {"starting", "running"}:
        _start_import_background(force=bool(count))
        status = _read_import_status()
    state = status.get("state", "starting")
    if state == "error":
        return {"ok": False, "status_code": 503, "code": "INDEX_UNAVAILABLE",
                "state": state, "error": "تعذر تجهيز فهرس المصدر. أعد المحاولة بعد التحقق من اتصال المصدر.",
                "detail": status.get("detail") or status.get("error")}
    return {"ok": False, "status_code": 503, "code": "INDEXING", "state": state,
            "error": "يجري تجهيز فهرس المصدر أو تحديث طريقة فهرسته. سيعمل البحث تلقائيًا بعد اكتماله.",
            "detail": status.get("message")}

def authenticated():
    return bool(session.get("authenticated"))

def login_required_json():
    if not authenticated(): return jsonify({"error":"انتهت الجلسة أو يلزم تسجيل الدخول"}),401
    return None

def classify(q, rows):
    # A zero-result search is not evidence that the query is a referent.
    if rows:
        return "SOURCE_MATCH"
    return "NO_MATCH"

def search_units(q,mode="auto",limit=5000):
    c=db(); nq=normalize(q)
    if mode=="character":
        # Search each supplied character in normalized surface; bounded for safety.
        sql="SELECT * FROM units WHERE normalized LIKE ? ORDER BY surah,verse,word,segment LIMIT ?"
        return c.execute(sql,("%"+nq+"%",limit)).fetchall()
    if mode=="lemma":
        sql="SELECT * FROM units WHERE lemma=? OR normalized=? ORDER BY surah,verse,word,segment LIMIT ?"
        args=(nq,nq,limit)
    elif mode=="root":
        sql="SELECT * FROM units WHERE root=? ORDER BY surah,verse,word,segment LIMIT ?"
        args=(nq,limit)
    elif mode=="exact": sql="SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment LIMIT ?"; args=(nq,limit)
    else:
        rows=c.execute("SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment LIMIT ?",(nq,limit)).fetchall()
        if rows: return rows
        rows=c.execute("SELECT * FROM units WHERE lemma=? ORDER BY surah,verse,word,segment LIMIT ?",(nq,limit)).fetchall()
        if rows: return rows
        rows=c.execute("SELECT * FROM units WHERE root=? ORDER BY surah,verse,word,segment LIMIT ?",(nq,limit)).fetchall()
        if rows: return rows
        return c.execute("SELECT * FROM units WHERE normalized LIKE ? ORDER BY surah,verse,word,segment LIMIT ?",("%"+nq+"%",limit)).fetchall()
    return c.execute(sql,args).fetchall()

def neighbors(row):
    c=db()
    out={}
    for label,word in (("previous",row["word"]-1),("next",row["word"]+1)):
        x=c.execute("SELECT surface,normalized,lemma,root,pos FROM units WHERE surah=? AND verse=? AND word=? ORDER BY segment LIMIT 1",
                     (row["surah"],row["verse"],word)).fetchone()
        out[label]=dict(x) if x else None
    return out

def analyze(q,mode="auto"):
    state=ensure_index()
    if not state.get("ok"):
        return {"ok": False, "status_code": state.get("status_code", 503),
                "code": state.get("code", "INDEX_UNAVAILABLE"), "state": state.get("state"),
                "error": state.get("error"), "detail": state.get("detail")}
    rows=search_units(q,mode)
    total=len(rows)
    forms={}; roots={}; lemmas={}; pos={}; prev={}; nxt={}; letters={}
    for r in rows:
        forms[r["surface"]]=forms.get(r["surface"],0)+1
        if r["root"]: roots[r["root"]]=roots.get(r["root"],0)+1
        if r["lemma"]: lemmas[r["lemma"]]=lemmas.get(r["lemma"],0)+1
        if r["pos"]: pos[r["pos"]]=pos.get(r["pos"],0)+1
        for ch in normalize(r["surface"]).replace(" ",""): letters[ch]=letters.get(ch,0)+1
        n=neighbors(r)
        if n["previous"]: prev[n["previous"]["surface"]]=prev.get(n["previous"]["surface"],0)+1
        if n["next"]: nxt[n["next"]["surface"]]=nxt.get(n["next"]["surface"],0)+1
    contexts=[]
    for r in rows[:250]:
        contexts.append({"location":f"{r['surah']}:{r['verse']}:{r['word']}:{r['segment']}",
          "surah":r["surah"],"verse":r["verse"],"word":r["word"],"segment":r["segment"],
          "surface":r["surface"],"normalized":r["normalized"],"lemma":r["lemma"],"root":r["root"],
          "pos":r["pos"],"tag":r["tag"],**neighbors(r)})
    now=datetime.now(timezone.utc).isoformat()
    c=db()
    cur=c.execute("INSERT INTO runs(created_at,query_text,normalized,mode,status,match_count) VALUES(?,?,?,?,?,?)",
                  (now,q,normalize(q),mode,"COMPLETED" if total else "NO_MATCH",total))
    rid=cur.lastrowid
    rules=["المطابقة اللغوية لا تثبت وحدها علاقة دلالية.","عدم العثور ليس نفيًا.","الترابط الإحصائي لا يثبت السببية.","كل استنتاج يحتاج سياقًا ومقارنة ومصدرًا."]
    discoveries=[
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
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")))
