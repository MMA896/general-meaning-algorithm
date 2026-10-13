import os, re, sqlite3, secrets, json, urllib.request, hashlib
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE, "research.db"))
CORPUS_PATH = os.path.join(BASE, "data", "quranic-corpus-morphology-0.4.txt")
CORPUS_URL = "https://raw.githubusercontent.com/bnjasim/quranic-corpus/master/quranic-corpus-morphology-0.4.txt"
VERSION = "2.0.0"
DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1")
USER = os.environ.get("RESEARCH_USER", "researcher")
PASSWORD = os.environ.get("RESEARCH_PASSWORD", "change-me")

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
    ok,state=ensure_corpus()
    if not ok: return {"ok":False,"error":"تعذر جلب ملف المصدر القرآني","detail":state}
    size=str(os.path.getsize(CORPUS_PATH))
    c=sqlite3.connect(DB_PATH,timeout=60)
    old=c.execute("SELECT value FROM corpus_meta WHERE key='source_size'").fetchone()
    count=c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    if old and old[0]==size and count and not force:
        c.close(); return {"ok":True,"state":"already_imported","count":count}
    c.execute("DELETE FROM units")
    inserted=0
    with open(CORPUS_PATH,encoding="utf-8",errors="replace") as f:
        for line in f:
            if not line.strip() or line.startswith("#") or "\t" not in line: continue
            p=line.rstrip("\r\n").split("\t")
            if len(p)<4: continue
            loc,surface,tag,features=p[:4]
            coords=parse_loc(loc)
            if not coords or not surface: continue
            surah,verse,word,segment=coords
            ft=parse_features(features)
            c.execute("""INSERT OR REPLACE INTO units(surah,verse,word,segment,surface,normalized,tag,features,pos,lemma,root)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (surah,verse,word,segment,surface,normalize(surface),tag,features,ft.get("POS"),ft.get("LEM"),ft.get("ROOT")))
            inserted+=1
    if inserted<10000:
        c.rollback(); c.close()
        return {"ok":False,"error":"ملف المصدر لم ينتج عددًا كافيًا من الوحدات؛ لم يُعتمد الاستيراد","parsed":inserted}
    c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_size',?)",(size,))
    c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_name','Quranic Arabic Corpus morphology v0.4')")
    c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('source_url',?)",(CORPUS_URL,))
    c.execute("INSERT OR REPLACE INTO corpus_meta VALUES('imported_at',?)",(datetime.now(timezone.utc).isoformat(),))
    c.commit(); c.close()
    return {"ok":True,"state":"imported","count":inserted,"source":CORPUS_URL}

def ensure_index():
    c=db()
    count=c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    if count: return {"ok":True,"state":"ready","count":count}
    return import_corpus()

def authenticated():
    return bool(session.get("authenticated"))

def login_required_json():
    if not authenticated(): return jsonify({"error":"انتهت الجلسة أو يلزم تسجيل الدخول"}),401
    return None

def classify(q, rows):
    if rows: return "SOURCE_MATCH"
    return "REFERENT_CANDIDATE"

def search_units(q,mode="auto",limit=5000):
    c=db(); nq=normalize(q)
    if mode=="character":
        # Search each supplied character in normalized surface; bounded for safety.
        sql="SELECT * FROM units WHERE normalized LIKE ? ORDER BY surah,verse,word,segment LIMIT ?"
        return c.execute(sql,("%"+nq+"%",limit)).fetchall()
    if mode=="lemma": sql="SELECT * FROM units WHERE lemma=? OR lower(lemma)=lower(?) ORDER BY surah,verse,word,segment LIMIT ?"; args=(q,q,limit)
    elif mode=="root": sql="SELECT * FROM units WHERE root=? OR lower(root)=lower(?) ORDER BY surah,verse,word,segment LIMIT ?"; args=(q,q,limit)
    elif mode=="exact": sql="SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment LIMIT ?"; args=(nq,limit)
    else:
        rows=c.execute("SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment LIMIT ?",(nq,limit)).fetchall()
        if rows: return rows
        rows=c.execute("SELECT * FROM units WHERE lemma=? OR lower(lemma)=lower(?) ORDER BY surah,verse,word,segment LIMIT ?",(q,q,limit)).fetchall()
        if rows: return rows
        return c.execute("SELECT * FROM units WHERE root=? OR lower(root)=lower(?) ORDER BY surah,verse,word,segment LIMIT ?",(q,q,limit)).fetchall()
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
    if not state.get("ok"): return {"ok":False,"status":503,"error":state.get("error"),"detail":state.get("detail")}
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
        return render_template("login.html",error="اسم المستخدم أو كلمة المرور غير صحيحين.")
    return render_template("login.html",error=None)

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
    result=analyze(q,mode)
    status=result.pop("status_code",None)
    if not result.get("ok"): return jsonify(result),503
    return jsonify(result)

@app.post("/api/admin/import")
def api_import():
    denied=login_required_json()
    if denied: return denied
    payload=request.get_json(silent=True) or {}
    return jsonify(import_corpus(force=bool(payload.get("force"))))

@app.get("/api/history")
def api_history():
    denied=login_required_json()
    if denied: return denied
    rows=db().execute("SELECT id,created_at,query_text,normalized,mode,status,match_count FROM runs ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify({"runs":[dict(r) for r in rows]})

@app.get("/health")
def health():
    c=sqlite3.connect(DB_PATH)
    try: count=c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    except sqlite3.Error: count=0
    c.close()
    return jsonify({"status":"ok","version":VERSION,"corpus_file_present":os.path.exists(CORPUS_PATH),
                    "indexed_units":count,"corpus_source":CORPUS_URL})

with app.app_context(): init_db()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")))
