import os,re,sqlite3,secrets
from datetime import datetime,timezone
from functools import wraps
from flask import Flask,render_template,request,redirect,url_for,session,jsonify,g
BASE_DIR=os.path.dirname(os.path.abspath(__file__)); DB_PATH=os.path.join(BASE_DIR,'research.db'); CORPUS_PATH=os.path.join(BASE_DIR,'data','qac_morphology_0.4.txt')
app=Flask(__name__); app.secret_key=os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','1')=='1')
USER=os.environ.get('RESEARCH_USER','researcher'); PASSWORD=os.environ.get('RESEARCH_PASSWORD','change-me')
D=re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
def norm(s): return re.sub(r'\s+',' ',D.sub('',s or '').replace('ـ','').replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ى','ي')).strip()
def db():
    if 'db' not in g:g.db=sqlite3.connect(DB_PATH);g.db.row_factory=sqlite3.Row
    return g.db
@app.teardown_appcontext
def close(_=None):
    c=g.pop('db',None)
    if c:c.close()
def init():
    c=sqlite3.connect(DB_PATH);c.executescript('''CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,created_at TEXT,query_text TEXT,normalized TEXT,classification TEXT,status TEXT);CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,run_id INTEGER,axis TEXT,value TEXT,detail TEXT);CREATE TABLE IF NOT EXISTS discoveries(id INTEGER PRIMARY KEY,run_id INTEGER,kind TEXT,payload TEXT);''');c.commit();c.close()
init()
def required(f):
    @wraps(f)
    def w(*a,**k):
        return f(*a,**k) if session.get('authenticated') else redirect(url_for('login',next=request.path))
    return w
def corpus(): return {'loaded':os.path.exists(CORPUS_PATH),'records':sum(1 for _ in open(CORPUS_PATH,encoding='utf-8')) if os.path.exists(CORPUS_PATH) else 0}
def scan(q):
    n=norm(q); words=n.split(); chars=[x for x in n if not x.isspace()]
    obs=[{'axis':'الكلمات','value':len(words),'detail':'عدد الوحدات النصية في المدخل.'},{'axis':'الحروف','value':len(chars),'detail':f'عدد الحروف غير الفراغية؛ الفريد منها {len(set(chars))}.'},{'axis':'السياق','value':'NOT_TESTED','detail':'يلزم سياق مصدر.'},{'axis':'الآثار','value':'NOT_TESTED','detail':'لا أثر بلا مقارنة.'},{'axis':'الغياب','value':'CHECK','detail':'عدم العثور ليس نفيًا.'},{'axis':'التتبع','value':'PROVENANCE_REQUIRED','detail':'لا دليل بلا تتبع مصدر.'},{'axis':'السببية','value':'NOT_TESTED','detail':'الترتيب الزمني لا يثبت السببية.'}]
    return {'normalized':n,'words':words,'observations':obs,'discoveries':([{'kind':'CHARACTER_DIVERSITY','payload':f'ملاحظة وصفية: {len(set(chars))} حروف مميزة.'}] if len(set(chars))>1 else [])}
@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST' and secrets.compare_digest(request.form.get('username',''),USER) and secrets.compare_digest(request.form.get('password',''),PASSWORD):session['authenticated']=True;return redirect(request.args.get('next') or '/')
    return render_template('login.html',error='بيانات الدخول غير صحيحة.' if request.method=='POST' else None)
@app.get('/logout')
def logout():session.clear();return redirect('/login')
@app.get('/')
@required
def home():return render_template('index.html',corpus=corpus())
@app.get('/health')
def health():return jsonify(status='ok',corpus=corpus())
@app.post('/api/search')
@required
def search():
    q=(request.json or {}).get('query','').strip()
    if not q:return jsonify(error='اكتب المدخل أولًا.'),400
    s=scan(q); c=corpus(); cls='SOURCE' if c['loaded'] else 'REFERENT'; status='NOT_TESTED' if c['loaded'] else 'UNRESOLVED'; now=datetime.now(timezone.utc).isoformat()
    cur=db().execute('INSERT INTO runs(created_at,query_text,normalized,classification,status) VALUES(?,?,?,?,?)',(now,q,s['normalized'],cls,status));rid=cur.lastrowid
    for x in s['observations']:db().execute('INSERT INTO observations(run_id,axis,value,detail) VALUES(?,?,?,?)',(rid,x['axis'],str(x['value']),x['detail']))
    for x in s['discoveries']:db().execute('INSERT INTO discoveries(run_id,kind,payload) VALUES(?,?,?)',(rid,x['kind'],x['payload']))
    db().commit();return jsonify(run_id=rid,classification=cls,status=status,corpus=c,scan=s,method_rules=['المرشح ليس حقيقة.','عدم العثور ليس نفيًا.','الترتيب الزمني ليس سببية.','لا أثر بلا مقارنة.','لا دليل بلا تتبع مصدر.','الاكتشاف ليس اختبارًا مستقلًا.'])
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')))
