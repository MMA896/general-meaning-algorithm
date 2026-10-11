import os, re, sqlite3, secrets, json, urllib.request
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'research.db')
CORPUS_PATH = os.path.join(BASE_DIR, 'data', 'quranic-corpus-morphology-0.4.txt')
CORPUS_URL = 'https://raw.githubusercontent.com/bnjasim/quranic-corpus/master/quranic-corpus-morphology-0.4.txt'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','1') == '1')
RESEARCH_USER = os.environ.get('RESEARCH_USER','researcher')
RESEARCH_PASSWORD = os.environ.get('RESEARCH_PASSWORD','change-me')
DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')


def normalize(text):
    text = DIACRITICS.sub('', text or '').replace('ـ','')
    return re.sub(r'\s+', ' ', text.replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ى','ي')).strip()


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc=None):
    c = g.pop('db', None)
    if c: c.close()


def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS corpus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS units (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      surah INTEGER NOT NULL, verse INTEGER NOT NULL, word INTEGER NOT NULL, segment INTEGER NOT NULL,
      surface TEXT NOT NULL, normalized TEXT NOT NULL, tag TEXT, features TEXT,
      pos TEXT, lemma TEXT, root TEXT,
      UNIQUE(surah,verse,word,segment)
    );
    CREATE INDEX IF NOT EXISTS idx_units_norm ON units(normalized);
    CREATE INDEX IF NOT EXISTS idx_units_lemma ON units(lemma);
    CREATE INDEX IF NOT EXISTS idx_units_root ON units(root);
    CREATE INDEX IF NOT EXISTS idx_units_pos ON units(pos);
    CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, query_text TEXT, mode TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS discoveries (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, kind TEXT, payload TEXT, created_at TEXT);
    ''')
    c.commit(); c.close()


def ensure_corpus():
    if os.path.exists(CORPUS_PATH) and os.path.getsize(CORPUS_PATH) > 1000000:
        return True, 'present'
    try:
        os.makedirs(os.path.dirname(CORPUS_PATH), exist_ok=True)
        urllib.request.urlretrieve(CORPUS_URL, CORPUS_PATH)
        return True, 'downloaded'
    except Exception as e:
        return False, str(e)


def parse_location(loc):
    nums = re.findall(r'\d+', loc)
    if len(nums) >= 4:
        return tuple(map(int, nums[:4]))
    return None


def parse_features(features):
    out = {}
    for p in features.split('|'):
        if ':' in p:
            k,v=p.split(':',1); out[k]=v
    return out


def import_corpus(force=False):
    ok, state = ensure_corpus()
    if not ok: return {'ok':False,'error':state}
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    old = c.execute("SELECT value FROM corpus_meta WHERE key='source_size'").fetchone()
    size = str(os.path.getsize(CORPUS_PATH))
    if old and old['value'] == size and not force:
        return {'ok':True,'state':'already_imported','count':c.execute('SELECT COUNT(*) FROM units').fetchone()[0]}
    if force or not old:
        c.execute('DELETE FROM units')
    inserted=0
    with open(CORPUS_PATH,encoding='utf-8',errors='replace') as f:
        for line in f:
            if not line or line.startswith('#') or line.startswith('LOCATION') or '\t' not in line: continue
            parts=line.rstrip('\n').split('\t')
            if len(parts)<4: continue
            loc, surface, tag, features = parts[0], parts[1], parts[2], parts[3]
            p=parse_location(loc)
            if not p: continue
            surah,verse,word,segment=p
            fs=parse_features(features)
            pos=fs.get('POS')
            lemma=fs.get('LEM')
            root=fs.get('ROOT')
            c.execute('INSERT OR REPLACE INTO units(surah,verse,word,segment,surface,normalized,tag,features,pos,lemma,root) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (surah,verse,word,segment,surface,normalize(surface),tag,features,pos,lemma,root))
            inserted += 1
    c.execute("INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source_size',?)",(size,))
    c.execute("INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source','Quranic Arabic Corpus morphology v0.4')")
    c.execute("INSERT OR REPLACE INTO corpus_meta(key,value) VALUES('source_url',?)",(CORPUS_URL,))
    c.commit(); c.close()
    return {'ok':True,'state':'imported','count':inserted}


def ensure_index():
    with app.app_context():
        c=db()
        count=c.execute('SELECT COUNT(*) FROM units').fetchone()[0]
        if count==0: return import_corpus()
        return {'ok':True,'state':'ready','count':count}


def classify(query):
    nq=normalize(query)
    c=db()
    if c.execute('SELECT 1 FROM units WHERE normalized=? LIMIT 1',(nq,)).fetchone(): return 'SOURCE'
    if c.execute('SELECT 1 FROM units WHERE lemma=? LIMIT 1',(nq,)).fetchone(): return 'SOURCE_LEMMA'
    return 'REFERENT'


def search_units(query, mode='auto'):
    c=db(); nq=normalize(query)
    if mode=='exact': rows=c.execute('SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
    elif mode=='lemma': rows=c.execute('SELECT * FROM units WHERE lemma=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
    elif mode=='root': rows=c.execute('SELECT * FROM units WHERE root=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
    elif mode=='character': rows=c.execute('SELECT * FROM units WHERE normalized LIKE ? ORDER BY surah,verse,word,segment',(f'%{nq}%',)).fetchall()
    else:
        rows=c.execute('SELECT * FROM units WHERE normalized=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
        if not rows: rows=c.execute('SELECT * FROM units WHERE lemma=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
        if not rows: rows=c.execute('SELECT * FROM units WHERE root=? ORDER BY surah,verse,word,segment',(nq,)).fetchall()
    return rows


def contexts(rows, limit=200):
    c=db(); out=[]
    for r in rows[:limit]:
        prev=c.execute('SELECT * FROM units WHERE surah=? AND verse=? AND word=? AND segment=?',(r['surah'],r['verse'],r['word']-1,1)).fetchone()
        nxt=c.execute('SELECT * FROM units WHERE surah=? AND verse=? AND word=? AND segment=?',(r['surah'],r['verse'],r['word']+1,1)).fetchone()
        out.append({'location':f"{r['surah']}:{r['verse']}:{r['word']}:{r['segment']}",'surface':r['surface'],'normalized':r['normalized'],'lemma':r['lemma'],'root':r['root'],'pos':r['pos'],'previous':dict(prev) if prev else None,'next':dict(nxt) if nxt else None})
    return out


def summarize(rows):
    forms={}; nxt={}; prev={}; pos={}
    c=db()
    for r in rows:
        forms[r['surface']]=forms.get(r['surface'],0)+1
        if r['pos']: pos[r['pos']]=pos.get(r['pos'],0)+1
        p=c.execute('SELECT surface FROM units WHERE surah=? AND verse=? AND word=? AND segment=1',(r['surah'],r['verse'],r['word']-1)).fetchone()
        n=c.execute('SELECT surface FROM units WHERE surah=? AND verse=? AND word=? AND segment=1',(r['surah'],r['verse'],r['word']+1)).fetchone()
        if p: prev[p['surface']]=prev.get(p['surface'],0)+1
        if n: nxt[n['surface']]=nxt.get(n['surface'],0)+1
    return {'forms':sorted(forms.items(),key=lambda x:-x[1])[:30],'previous':sorted(prev.items(),key=lambda x:-x[1])[:20],'next':sorted(nxt.items(),key=lambda x:-x[1])[:20],'pos':sorted(pos.items(),key=lambda x:-x[1])}


def open_scan(query, rows):
    chars=[x for x in normalize(query) if not x.isspace()]
    discoveries=[]
    if len(set(chars))>1: discoveries.append({'kind':'CHARACTER_DIVERSITY','payload':f'{len(set(chars))} حروف مميزة في المدخل.'})
    if rows: discoveries.append({'kind':'CORPUS_MATCH','payload':f'تم العثور على {len(rows)} موضعًا ضمن طبقة المطابقة الحالية.'})
    return discoveries

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        if secrets.compare_digest(request.form.get('username',''),RESEARCH_USER) and secrets.compare_digest(request.form.get('password',''),RESEARCH_PASSWORD):
            session['authenticated']=True; return redirect(url_for('index'))
        return render_template('login.html',error='بيانات الدخول غير صحيحة.')
    return render_template('login.html',error=None)

@app.get('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.get('/')
def index():
    if not session.get('authenticated'): return redirect(url_for('login'))
    return render_template('index.html')

@app.post('/api/search')
def api_search():
    if not session.get('authenticated'): return jsonify({'error':'غير مصرح'}),401
    q=(request.json or {}).get('query','').strip(); mode=(request.json or {}).get('mode','auto')
    if not q:return jsonify({'error':'اكتب المدخل أولًا.'}),400
    state=ensure_index()
    if not state.get('ok'): return jsonify({'error':'لم تتوفر بيانات المصدر بعد.','detail':state.get('error')}),503
    rows=search_units(q,mode)
    cls=classify(q); summary=summarize(rows); ctx=contexts(rows)
    now=datetime.now(timezone.utc).isoformat()
    c=db(); cur=c.execute('INSERT INTO runs(created_at,query_text,mode,status) VALUES(?,?,?,?)',(now,q,mode,'COMPLETED')); rid=cur.lastrowid
    ds=open_scan(q,rows)
    for d in ds:c.execute('INSERT INTO discoveries(run_id,kind,payload,created_at) VALUES(?,?,?,?)',(rid,d['kind'],d['payload'],now))
    c.commit()
    return jsonify({'run_id':rid,'classification':cls,'mode':mode,'corpus':{'source':'Quranic Arabic Corpus morphology v0.4','url':CORPUS_URL,'units':c.execute('SELECT COUNT(*) FROM units').fetchone()[0]},'count':len(rows),'summary':summary,'contexts':ctx,'discoveries':ds,'method_rules':['المرشح ليس حقيقة.','عدم العثور ليس نفيًا.','الترتيب الزمني ليس سببية.','لا أثر بلا مقارنة.','لا دليل بلا تتبع مصدر.']})

@app.post('/api/admin/import')
def api_import():
    if not session.get('authenticated'): return jsonify({'error':'غير مصرح'}),401
    return jsonify(import_corpus(force=bool((request.json or {}).get('force'))))

@app.get('/health')
def health(): return jsonify({'status':'ok','corpus_exists':os.path.exists(CORPUS_PATH),'db_units':sqlite3.connect(DB_PATH).execute('SELECT COUNT(*) FROM units').fetchone()[0]})

with app.app_context():
    init_db()

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')),debug=False)
