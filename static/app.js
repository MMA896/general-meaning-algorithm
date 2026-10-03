async function runSearch(){
 const q=document.getElementById('q').value.trim();
 const out=document.getElementById('out');
 if(!q){out.innerHTML='<div class="card">اكتب المدخل أولًا.</div>';return;}
 out.innerHTML='<div class="card">جارٍ الفحص...</div>';
 const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});
 const data=await r.json();
 if(!r.ok){out.innerHTML='<div class="card error">'+(data.error||'تعذر الفحص')+'</div>';return;}
 const obs=data.scan.observations.map(x=>`<li><b>${x.axis}</b>: ${x.value} — ${x.detail}</li>`).join('');
 const ds=data.scan.discoveries.map(x=>`<li><b>${x.kind}</b>: ${x.payload}</li>`).join('');
 out.innerHTML=`<div class="card"><h2>RUN-${String(data.run_id).padStart(3,'0')}</h2><div class="grid"><div class="box"><b>التصنيف</b><br><span class="badge">${data.classification}</span></div><div class="box"><b>الحالة</b><br><span class="badge">UNRESOLVED</span></div><div class="box"><b>بعد التطبيع</b><br>${escapeHtml(data.scan.normalized)}</div></div></div><div class="card"><h3>الملاحظات الأولية</h3><ul>${obs}</ul></div><div class="card"><h3>الاكتشافات</h3><ul>${ds||'<li>لا يوجد اكتشاف جديد في هذه الدورة.</li>'}</ul></div><div class="card"><h3>قواعد التحقق</h3><ul>${data.method_rules.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
