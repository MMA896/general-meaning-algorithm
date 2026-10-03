import os, re, sqlite3, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'research.db')
DATA_PATH = os.path.join(BASE_DIR, 'data', 'source_units.jsonl')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '1') == '1'

RESEARCH_USER = os.environ.get('RESEARCH_USER', 'researcher')
RESEARCH_PASSWORD = os.environ.get('RESEARCH_PASSWORD', 'change-me')

DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')

def normalize(text: str) -> str:
    text = text or ''
    text = DIACRITICS.sub('', text)
    text = text.replace('ـ', '')
    text = text.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
    text = text.replace('ى', 'ي')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS source_units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,
        surface_form TEXT NOT NULL,
        normalized_form TEXT NOT NULL,
        reference TEXT,
        authenticity_status TEXT NOT NULL DEFAULT 'PROVISIONAL',
        provenance TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_source_norm ON source_units(normalized_form);
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        query_text TEXT NOT NULL,
        classification TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS discoveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    ''')
    conn.commit()
    count = conn.execute('SELECT COUNT(*) FROM source_units').fetchone()[0]
    if count == 0 and os.path.exists(DATA_PATH):
        import json
        rows = []
        with open(DATA_PATH, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                rows.append((item.get('source_type','DEMO'), item['surface_form'], normalize(item['surface_form']), item.get('reference'), item.get('authenticity_status','PROVISIONAL'), item.get('provenance')))
        conn.executemany('INSERT INTO source_units(source_type,surface_form,normalized_form,reference,authenticity_status,provenance) VALUES (?,?,?,?,?,?)', rows)
        conn.commit()
    conn.close()

with app.app_context():
    init_db()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def classify(query: str):
    nq = normalize(query)
    row = db().execute('SELECT * FROM source_units WHERE normalized_form = ? LIMIT 1', (nq,)).fetchone()
    if row:
        return 'SOURCE', dict(row)
    return 'REFERENT', None

def open_scan(query: str):
    nq = normalize(query)
    words = [w for w in re.split(r'\s+', nq) if w]
    chars = [c for c in nq if not c.isspace()]
    unique_chars = sorted(set(chars))
    observations = []
    if words:
        observations.append({'axis':'الكلمات','value':len(words),'detail':'عدد الوحدات النصية في المدخل.'})
    if chars:
        observations.append({'axis':'الحروف','value':len(chars),'detail':f'عدد الحروف غير الفراغية؛ الفريد منها {len(unique_chars)}.'})
    observations.append({'axis':'السياق','value':'UNRESOLVED','detail':'لا يُستنتج المعنى من الشكل وحده؛ يلزم السياق.'})
    observations.append({'axis':'الآثار','value':'COMPARE','detail':'عند وجود تغير تُقارن الحالة قبل/بعد ولا يُثبت الأثر بمجرد التتابع.'})
    observations.append({'axis':'الغياب','value':'CHECK','detail':'عدم الظهور لا يُعد نفيًا إلا بعد تحديد مجال فرصة الظهور.'})
    discoveries = []
    if len(unique_chars) >= 2:
        discoveries.append({'kind':'CHARACTER_DIVERSITY','payload':f'اكتشاف وصفي: {len(unique_chars)} حروف مميزة في المدخل.'})
    if len(words) >= 2:
        discoveries.append({'kind':'PAIR_CANDIDATE','payload':'مرشح علاقة بين وحدات المدخل؛ يحتاج إلى اختبار مستقل.'})
    return {'normalized':nq, 'words':words, 'observations':observations, 'discoveries':discoveries}

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user = request.form.get('username','')
        pw = request.form.get('password','')
        if secrets.compare_digest(user, RESEARCH_USER) and secrets.compare_digest(pw, RESEARCH_PASSWORD):
            session['authenticated'] = True
            return redirect(request.args.get('next') or url_for('index'))
        return render_template('login.html', error='بيانات الدخول غير صحيحة.')
    return render_template('login.html', error=None)

@app.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.get('/')
@login_required
def index():
    return render_template('index.html')

@app.post('/api/search')
@login_required
def api_search():
    query = (request.json or {}).get('query','').strip()
    if not query:
        return jsonify({'error':'اكتب المدخل أولًا.'}), 400
    cls, source = classify(query)
    scan = open_scan(query)
    now = datetime.now(timezone.utc).isoformat()
    cur = db().execute('INSERT INTO runs(created_at,query_text,classification) VALUES (?,?,?)',(now,query,cls))
    run_id = cur.lastrowid
    for d in scan['discoveries']:
        db().execute('INSERT INTO discoveries(run_id,kind,payload,created_at) VALUES (?,?,?,?)',(run_id,d['kind'],d['payload'],now))
    db().commit()
    return jsonify({'run_id':run_id,'classification':cls,'source':source,'scan':scan,'statuses':['UNRESOLVED','NOT_TESTED'],'method_rules':[
        'المرشح ليس حقيقة.',
        'عدم العثور ليس نفيًا.',
        'الترتيب الزمني ليس سببية.',
        'لا أثر بلا مقارنة.',
        'لا دليل بلا تتبع مصدر.'
    ]})

@app.get('/health')
def health():
    return jsonify({'status':'ok'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT','5000'))
    app.run(host='0.0.0.0', port=port, debug=False)
