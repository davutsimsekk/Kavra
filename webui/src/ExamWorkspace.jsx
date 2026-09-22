import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, ClipboardList, Download, FileText, LoaderCircle, Plus, Search, UploadCloud } from 'lucide-react'
import { WorkspaceHeader } from './Appearance.jsx'
import './ExamWorkspace.css'
import { normalizeAttempt, questionStatus, attemptSummary, finishAttempt, restartAttempt } from './examState.mjs'

const post = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
const readLocal = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback } }
const storeLocal = (key,value) => { try { localStorage.setItem(key,JSON.stringify(value)) } catch {} }

function ExamPlayer({exam,base,onClose}) {
  const key='ders-exam-attempt:'+base+':'+exam.id
  const [attempt,setAttempt]=useState(()=>normalizeAttempt(exam,readLocal(key,null)))
  const [filter,setFilter]=useState('all')
  const [showAnswers,setShowAnswers]=useState(false)
  const [exportFormat,setExportFormat]=useState('pdf')
  useEffect(()=>storeLocal(key,attempt),[key,attempt])
  const {answered,correct,wrong,unanswered,totalMcq}=attemptSummary(exam,attempt)
  const visibleQuestions=exam.questions.filter(q=>filter==='all'||(filter==='flagged'?attempt.flags.includes(q.id):questionStatus(q,attempt)===filter))
  function finish() {
    if(unanswered && !window.confirm(unanswered+' soru boş. Sınavı bitirmek istiyor musun?'))return
    setAttempt(a=>finishAttempt(exam,a));setShowAnswers(true);setFilter('all')
  }
  return <div className="dashboard-shell exam-player">
    <div className="exam-player-toolbar"><button className="button quiet" onClick={onClose}><ArrowLeft size={17}/> Sınavlar</button><div><label className="exam-export-format"><span>Dosya biçimi</span><select aria-label="Sınav indirme biçimi" value={exportFormat} onChange={e=>setExportFormat(e.target.value)}><option value="pdf">PDF</option><option value="docx">Word (.docx)</option><option value="md">Markdown (.md)</option></select></label><a className="button ghost" href={base+'/'+exam.id+'/export?format='+exportFormat}><Download size={16}/> Soruları indir</a><a className="button ghost" href={base+'/'+exam.id+'/export?answers=true&format='+exportFormat}>Cevap anahtarını indir</a></div></div>
    <div className="dashboard-hero"><span className="kicker">DENEME SINAVI</span><h1>{exam.name}</h1><p>{exam.questions.length} soru · {exam.settings.mcqCount} şıklı · {exam.settings.classicCount} klasik</p><small>{exam.sourceNames.join(' · ')}</small>{exam.referenceNames.length>0 && <small>Biçim örneği: {exam.referenceNames.join(' · ')}</small>}</div>
    <section className="panel exam-session"><div><strong>{answered}/{exam.questions.length} cevaplandı</strong><small>Cevapların bu tarayıcıda otomatik saklanır.</small></div>
      {!attempt.finished ? <button className="button primary" onClick={finish}>Sınavı bitir ve cevapları gör</button> :
      <><span>{totalMcq>0 ? 'Şıklı sorular: '+correct+'/'+totalMcq+' doğru' : 'Örnek cevaplarla değerlendirme'}</span><button className="button ghost" onClick={()=>setShowAnswers(v=>!v)}>{showAnswers?'Cevapları gizle':'Cevapları göster'}</button><button className="button quiet" onClick={()=>{setAttempt(a=>({...a,finished:false}));setShowAnswers(false);setFilter('all')}}>Cevapları düzenle</button><button className="button ghost" onClick={()=>{if(window.confirm('Yeni deneme başlatılsın mı? Mevcut cevaplar temizlenir; sonuç geçmişin korunur.')){setAttempt(a=>restartAttempt(a));setShowAnswers(false);setFilter('all')}}}>Yeniden çöz</button></>}
    </section>
    {attempt.finished && exam.settings.classicCount>0 && <p className="exam-note">Klasik cevaplar otomatik puanlanmaz. Örnek cevap ve değerlendirme ölçütleriyle kendi yanıtını karşılaştır.</p>}
    <section className="panel exam-navigation">
      <div className="exam-filters" role="group" aria-label="Soru filtresi">{[['all','Tümü'],['unanswered','Boş ('+unanswered+')'],['flagged','İşaretli ('+attempt.flags.length+')'],...(attempt.finished?[['wrong','Yanlış ('+wrong+')']]:[])].map(([id,label])=><button className={'button '+(filter===id?'primary':'ghost')} aria-pressed={filter===id} key={id} onClick={()=>setFilter(id)}>{label}</button>)}</div>
      <nav className="exam-question-map" aria-label="Sorulara git">{exam.questions.map((q,i)=><button key={q.id} title={attempt.flags.includes(q.id)?'İşaretli':''} className={questionStatus(q,attempt)} aria-label={'Soru '+(i+1)+(attempt.flags.includes(q.id)?' · işaretli':'')} onClick={()=>{setFilter('all');requestAnimationFrame(()=>document.getElementById('exam-question-'+q.id)?.scrollIntoView({behavior:'smooth',block:'start'}))}}>{i+1}{attempt.flags.includes(q.id)?' ★':''}</button>)}</nav>
      <small>Şıklı sonuçlar otomatik hesaplanır; klasik yanıtlar bu puana dahil değildir.</small>
      {attempt.history.length>0&&<details><summary>Deneme geçmişi ({attempt.history.length})</summary><ul>{[...attempt.history].reverse().map((h,i)=><li key={i}>{new Date(h.at).toLocaleString('tr-TR')} · {h.correct}/{h.totalMcq} şıklı doğru · {h.answered} cevaplandı</li>)}</ul></details>}
    </section>
    {exam.quality?.warnings?.length>0&&<p className="exam-note">{exam.quality.warnings.join(' ')}</p>}
    {!visibleQuestions.length&&<p className="exam-note">Bu filtrede soru yok.</p>}
    {visibleQuestions.map(q=><article id={'exam-question-'+q.id} className="panel exam-question" key={q.id}>
      <div className="exam-question-heading"><span className="memory-pill">Soru {exam.questions.indexOf(q)+1}</span><small>{q.type==='mcq'?'Çoktan seçmeli':'Klasik'}</small><button className="button quiet compact" aria-pressed={attempt.flags.includes(q.id)} onClick={()=>setAttempt(a=>({...a,flags:a.flags.includes(q.id)?a.flags.filter(id=>id!==q.id):[...a.flags,q.id]}))}>{attempt.flags.includes(q.id)?'★ İşaretli':'☆ Sonra bak'}</button></div><h2>{q.prompt}</h2>
      {q.type==='mcq'?<fieldset disabled={attempt.finished}><legend className="sr-only">{exam.questions.indexOf(q)+1}. soru için cevabın</legend>{q.options.map((option,j)=><label key={j} className={'exam-option'+(attempt.answers[q.id]===j?' selected':'')+(showAnswers && q.correctOption===j?' correct':'')}><input type="radio" name={'question-'+q.id} checked={attempt.answers[q.id]===j} onChange={()=>setAttempt(a=>({...a,answers:{...a.answers,[q.id]:j}}))}/><b>{String.fromCharCode(65+j)}</b><span>{option}</span>{showAnswers&&q.correctOption===j&&<Check size={17}/>}</label>)}</fieldset>:
      <label className="field"><span>Cevabın</span><textarea rows="5" disabled={attempt.finished} value={attempt.answers[q.id]||''} onChange={e=>setAttempt(a=>({...a,answers:{...a.answers,[q.id]:e.target.value}}))} placeholder="Cevabını buraya yaz…"/></label>}
      {showAnswers&&<div className="exam-answer"><strong>{q.type==='mcq'?'Doğru cevap: '+String.fromCharCode(65+q.correctOption):'Örnek cevap'}</strong><p>{q.answer}</p><p>{q.explanation}</p>{q.rubric.length>0&&<><strong>Değerlendirme ölçütleri</strong><ul>{q.rubric.map((r,j)=><li key={j}>{r}</li>)}</ul></>}{q.evidence?.length>0&&<details className="exam-evidence"><summary>Kaynak dayanaklarını göster</summary>{q.evidence.map((item,k)=><blockquote key={k}><p>{item.quote}</p><cite>{exam.sourceNames[exam.sourceIds.indexOf(item.sourceId)]}</cite></blockquote>)}<small>Alıntının kaynakta bulunduğu doğrulandı; bu kontrol sorunun tüm yorumlarının doğru olduğunu garanti etmez.</small></details>}<small>İlgili kaynaklar: {q.sourceIds.map(id=>exam.sourceNames[exam.sourceIds.indexOf(id)]).join(' · ')}</small></div>}
    </article>)}
  </div>
}

export default function ExamWorkspace({course,api,bootstrap,llm,onClose,onSources,onHome}) {
  const base='/api/projects/'+course.id+'/exams'
  const referenceBase='/api/projects/'+course.id+'/exam-references'
  const draftKey='ders-exam-draft:'+course.id
  const [savedDraft]=useState(()=>readLocal(draftKey,{}))
  const [exams,setExams]=useState([])
  const [libraryQuery,setLibraryQuery]=useState('')
  const [references,setReferences]=useState([])
  const [selected,setSelected]=useState(()=>Array.isArray(savedDraft.selected)?savedDraft.selected.filter(id=>course.sources.some(s=>s.id===id)):course.sources.length?[course.sources[0].id]:[])
  const [selectedRefs,setSelectedRefs]=useState(()=>Array.isArray(savedDraft.selectedRefs)?savedDraft.selectedRefs:[])
  const [creating,setCreating]=useState(savedDraft.creating===true)
  const [exam,setExam]=useState(null)
  const [query,setQuery]=useState('')
  const [name,setName]=useState(savedDraft.name||'')
  const [config,setConfig]=useState({kind:'mcq',count:10,mcqCount:5,choiceCount:4,difficulty:'medium',focus:'',...savedDraft.config})
  const [model,setModel]=useState(()=>({...llm,provider:'gemini',...savedDraft.model,apiKey:''}))
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [estimate,setEstimate]=useState(null)
  const [estimateError,setEstimateError]=useState('')
  const jobKey='ders-exam-job:'+course.id
  const [jobId,setJobId]=useState(()=>readLocal(jobKey,null))
  const [jobStatus,setJobStatus]=useState('')
  const actionLock=useRef(false)
  const locked=Boolean(busy||jobId)
  useEffect(()=>{
    const safeModel={provider:model.provider,geminiModel:model.geminiModel,openaiModel:model.openaiModel,openaiEndpoint:model.openaiEndpoint,agentCommand:model.agentCommand}
    storeLocal(draftKey,{selected,selectedRefs,creating,name,config,model:safeModel})
  },[draftKey,selected,selectedRefs,creating,name,config,model])
  const payload={...config,name,sourceIds:selected,referenceIds:selectedRefs,...model}
  async function refresh() {
    const [a,b]=await Promise.all([api(base),api(referenceBase)])
    setExams(a.exams);setReferences(b.references)
  }
  useEffect(()=>{refresh().catch(e=>setMessage(e.message))},[base])
  useEffect(()=>{
    if(!creating||!selected.length){setEstimate(null);setEstimateError('');return}
    let cancelled=false;setEstimate(null)
    const timer=setTimeout(()=>{api(base+'/estimate',post({...config,sourceIds:selected,referenceIds:selectedRefs}))
      .then(data=>{if(!cancelled){setEstimate(data);setEstimateError('')}})
      .catch(e=>{if(!cancelled)setEstimateError(e.message)})},250)
    return ()=>{cancelled=true;clearTimeout(timer)}
  },[creating,base,JSON.stringify(config),selected.join(','),selectedRefs.join(',')])
  useEffect(()=>{
    if(!jobId)return
    let cancelled=false
    const poll=async()=>{
      try {
        const job=await api('/api/jobs/'+jobId)
        if(cancelled)return
        setJobStatus(job.message||'Sınav hazırlanıyor…')
        if(job.status==='complete'||job.status==='failed'){
          storeLocal(jobKey,null);setJobId(null)
          if(job.status==='failed')setMessage(job.error||'Sınav oluşturulamadı.')
          else {setExam(job.result.exam);setCreating(false);setName('');refresh().catch(e=>setMessage(e.message))}
        }
      }catch(e){if(!cancelled){
        if(e.message.includes('İş bulunamadı')){storeLocal(jobKey,null);setJobId(null);setMessage('Önceki işlem kaydı bulunamadı. Sınav kitaplığını kontrol edebilir veya yeniden oluşturabilirsin.');refresh().catch(()=>{})}
        else setJobStatus('İşlem durumu yeniden kontrol edilecek. '+e.message)
      }}
    }
    poll();const timer=setInterval(poll,1500)
    return()=>{cancelled=true;clearInterval(timer)}
  },[jobId,base])
  useEffect(()=>{
    const warn=e=>{if(busy){e.preventDefault();e.returnValue=''}}
    window.addEventListener('beforeunload',warn)
    return()=>window.removeEventListener('beforeunload',warn)
  },[busy])
  function toggle(id,setter){setter(items=>items.includes(id)?items.filter(i=>i!==id):[...items,id])}
  async function upload(files){
    if(actionLock.current||locked)return
    actionLock.current=true;setMessage('')
    const errors=[]
    try{
      for(const file of files){
        setBusy(file.name+' okunuyor…')
        try{const form=new FormData();form.append('file',file);const ref=await api(referenceBase,{method:'POST',body:form});setSelectedRefs(ids=>[...ids,ref.id]);await refresh()}
        catch(e){errors.push(file.name+': '+e.message)}
      }
      if(errors.length)setMessage(errors.join('\n'))
    }finally{setBusy('');actionLock.current=false}
  }
  async function generate(){
    if(actionLock.current||locked||!estimate)return
    actionLock.current=true;setBusy('Sınav başlatılıyor…');setMessage('')
    try{const result=await api(base,post(payload));storeLocal(jobKey,result.jobId);setJobId(result.jobId);setJobStatus('Sınav hazırlanıyor…')}
    catch(e){setMessage(e.message)}
    finally{setBusy('');actionLock.current=false}
  }
  if(exam)return <ExamPlayer key={exam.id} exam={exam} base={base} onClose={()=>setExam(null)}/>
  return <div className="dashboard-shell exam-shell">
    <WorkspaceHeader onHome={()=>{if(!busy)onHome()}} projectName={course.name} onProject={()=>{if(!busy)onClose()}} current="Sınav hazırla"/>
    <div className="dashboard-hero"><span className="kicker">{course.name}</span><h1>Sınav hazırla</h1><p>Ders kaynaklarından yeni sorular üret. İstersen çıkmış sınavlarla aynı üslup ve yapıda pratik yap.</p><div className="project-facts"><span>{exams.length} sınav</span><span>{course.sources.length} ders kaynağı</span></div></div>
    <div className="course-module-toolbar"><button className="button quiet" disabled={Boolean(busy)} onClick={onClose}><ArrowLeft size={16}/> Projeye genel bakış</button><button className="button primary" disabled={locked} onClick={()=>setCreating(v=>!v)}><Plus size={16}/>{creating?'Oluşturmayı kapat':'Yeni sınav oluştur'}</button></div>
    {message&&<p className="exam-error" role="alert">{message}</p>}
    {jobId&&<div className="panel exam-job" role="status"><LoaderCircle className="spin" size={22}/><div><strong>{jobStatus}</strong><small>Başka ekrana geçsen de işlem sürer; bu derse dönünce sonucu görebilirsin.</small></div></div>}
    {creating&&<div className="course-layout exam-create-layout">
      <section className="panel"><div className="panel-toolbar"><div><span className="kicker">SINAVIN KAPSAMI</span><h3>Ders kaynakları</h3></div><span className="memory-pill">{selected.length} seçili</span></div>
        <label className="search-field"><Search size={16}/><input aria-label="Sınav kaynaklarında ara" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Kaynak adıyla ara…"/></label>
        <div className="course-selection-tools"><button disabled={locked} onClick={()=>setSelected(course.sources.map(s=>s.id))}>Tümünü seç</button><button disabled={locked} onClick={()=>setSelected([])}>Seçimi temizle</button></div>
        <div className="course-source-list">{course.sources.filter(s=>s.name.toLocaleLowerCase('tr-TR').includes(query.toLocaleLowerCase('tr-TR'))).map(s=><div className={'course-source-row'+(selected.includes(s.id)?' selected':'')} key={s.id}><label><input type="checkbox" disabled={locked} checked={selected.includes(s.id)} onChange={()=>toggle(s.id,setSelected)}/><FileText size={19}/><span><strong>{s.name}</strong><small>{s.sectionCount} bölüm</small></span></label></div>)}</div>
        {!course.sources.length&&<p className="list-empty">Önce dersine kaynak ekle.</p>}<div className="exam-panel-footer"><button className="button ghost" disabled={Boolean(busy)} onClick={onSources}>Kaynak kitaplığını aç</button></div>
      </section>
      <section className="panel"><div className="panel-toolbar"><h3>Sınavı yapılandır</h3></div><div className="course-create-body">
        <label className="field"><span>Sınav adı <em>isteğe bağlı</em></span><input maxLength="160" disabled={locked} value={name} onChange={e=>setName(e.target.value)} placeholder="Örn. Vize provası · Hafta 1–5"/></label>
        <div className="form-grid">
          <label className="field"><span>Soru türü</span><select disabled={locked} value={config.kind} onChange={e=>setConfig(c=>({...c,kind:e.target.value,count:e.target.value==='mixed'?Math.max(2,c.count):c.count,mcqCount:Math.max(1,Math.min(c.mcqCount,Math.max(2,c.count)-1))}))}><option value="mcq">Çoktan seçmeli</option><option value="classic">Klasik</option><option value="mixed">Karma</option></select></label>
          <label className="field"><span>Toplam soru sayısı</span><input disabled={locked} type="number" min={config.kind==='mixed'?2:1} max="40" value={config.count} onChange={e=>setConfig(c=>({...c,count:Number(e.target.value),mcqCount:Math.max(1,Math.min(c.mcqCount,Number(e.target.value)-1))}))}/></label>
          {config.kind==='mixed'&&<label className="field"><span>Şıklı soru sayısı</span><input disabled={locked} type="number" min="1" max={config.count-1} value={config.mcqCount} onChange={e=>setConfig(c=>({...c,mcqCount:Number(e.target.value)}))}/><small>Kalan {Math.max(0,config.count-config.mcqCount)} soru klasik.</small></label>}
          {config.kind!=='classic'&&<label className="field"><span>Soru başına şık</span><select disabled={locked} value={config.choiceCount} onChange={e=>setConfig(c=>({...c,choiceCount:Number(e.target.value)}))}>{[3,4,5].map(n=><option key={n} value={n}>{n} şık</option>)}</select></label>}
          <label className="field"><span>Zorluk</span><select disabled={locked} value={config.difficulty} onChange={e=>setConfig(c=>({...c,difficulty:e.target.value}))}><option value="easy">Kolay</option><option value="medium">Orta</option><option value="hard">Zor</option><option value="reference">Çıkmış sınava benzer</option></select></label>
        </div>
        <label className="field"><span>Odak / özel talimat</span><textarea disabled={locked} maxLength="2000" rows="2" value={config.focus} onChange={e=>setConfig(c=>({...c,focus:e.target.value}))} placeholder="Örn. yorum soruları ağırlıklı olsun; ilk iki haftaya odaklan."/></label>
        <div className="exam-references"><h4>Çıkmış sınavlar <span>isteğe bağlı</span></h4><p>Seçtiğin örneklerin soru dilini ve yapısını izler; aynı soruları kopyalamadan ders kaynaklarından yeni sorular üretir.</p>
          <label className="exam-upload"><UploadCloud size={18}/><span>{busy||'Çıkmış sınav ekle'}</span><input aria-label="Çıkmış sınav dosyaları" type="file" multiple accept=".pdf,.txt,.md,.pptx" disabled={locked} onChange={e=>{upload(Array.from(e.target.files));e.target.value=''}}/></label>
          <small>Metin içeren PDF, TXT, Markdown, PowerPoint · en fazla 20 MB. Taranmış PDF için önce OCR gerekir. Yükleme ücretli çağrı yapmaz.</small>
          {references.map(r=><label className="course-checkbox" key={r.id}><input type="checkbox" disabled={locked} checked={selectedRefs.includes(r.id)} onChange={()=>toggle(r.id,setSelectedRefs)}/><span>{r.name}<small>{r.characters.toLocaleString('tr-TR')} karakter</small></span></label>)}
        </div>
        <details className="exam-model-options"><summary>Yapay zeka ayarları · {model.provider==='gemini'?'Gemini':model.provider==='agent'?'Claude':'OpenAI uyumlu'}</summary>
          <div className="course-llm"><label className="field"><span>Sağlayıcı</span><select disabled={locked} value={model.provider} onChange={e=>setModel(m=>({...m,provider:e.target.value,apiKey:''}))}><option value="gemini">Gemini</option><option value="agent">Claude Agent</option><option value="openai">OpenAI uyumlu</option></select></label>
          {model.provider==='agent'?<label className="field"><span>Agent komutu</span><input disabled={locked} value={model.agentCommand} onChange={e=>setModel(m=>({...m,agentCommand:e.target.value}))}/></label>:<>
            {model.provider==='openai'&&<label className="field"><span>API adresi</span><input disabled={locked} value={model.openaiEndpoint} onChange={e=>setModel(m=>({...m,openaiEndpoint:e.target.value}))}/></label>}
            <label className="field"><span>Model</span><input disabled={locked} value={model.provider==='gemini'?model.geminiModel:model.openaiModel} onChange={e=>setModel(m=>({...m,[m.provider==='gemini'?'geminiModel':'openaiModel']:e.target.value}))}/></label>
            <label className="field"><span>API anahtarı {bootstrap.keysConfigured[model.provider]?'(kayıtlı)':''}</span><input disabled={locked} type="password" autoComplete="off" value={model.apiKey} placeholder="Kayıtlı anahtarı kullanmak için boş bırak" onChange={e=>setModel(m=>({...m,apiKey:e.target.value}))}/></label>
          </>}</div>
        </details>
        {estimate&&<small>{estimate.sourceCount} ders kaynağı · {estimate.referenceCount} çıkmış sınav · {estimate.characters.toLocaleString('tr-TR')} karakter. Bir üretim isteği; biçim/sayı düzeltmesi gerekirse bir ek istek. Sağlayıcının hata tekrarları ayrıca uygulanabilir.</small>}
        {estimate?.warnings?.map((warning,index)=><p className="exam-note" key={index}>{warning}</p>)}
        {estimateError&&<p className="exam-error" role="alert">{estimateError}</p>}
        <button className="button primary" disabled={locked||!estimate||!selected.length||Boolean(estimateError)} onClick={generate}>{locked?<LoaderCircle size={17} className="spin"/>:<ClipboardList size={17}/>}Sınav oluştur<ArrowRight size={16}/></button>
      </div></section>
    </div>}
    <section className="panel exam-library"><div className="panel-toolbar"><h3>Sınavların</h3><span className="memory-pill">{exams.length} sınav</span></div>
      {!exams.length&&<p className="list-empty">Henüz sınav yok. Yeni sınav oluştur ile ders kaynaklarını seçerek başla.</p>}
      <label className="search-field"><Search size={16}/><input aria-label="Sınavlarda ara" placeholder="Sınav adıyla ara…" value={libraryQuery} onChange={event=>setLibraryQuery(event.target.value)}/></label>
      {libraryQuery&&!exams.some(e=>e.name.toLocaleLowerCase('tr-TR').includes(libraryQuery.toLocaleLowerCase('tr-TR')))&&<p className="list-empty">Eşleşen sınav yok.</p>}
      {exams.filter(e=>e.name.toLocaleLowerCase('tr-TR').includes(libraryQuery.toLocaleLowerCase('tr-TR'))).map(e=><article className="course-result" key={e.id}><ClipboardList size={22}/><div><h4>{e.name}</h4><p>{e.sourceNames.join(' · ')}</p><small>{e.settings.count} soru · {e.settings.mcqCount} şıklı · {e.settings.classicCount} klasik{e.referenceNames.length?' · Çıkmış sınav örnekli':''}</small></div><button className="button ghost" disabled={Boolean(busy)} onClick={async()=>{try{setExam(await api(base+'/'+e.id))}catch(error){setMessage(error.message)}}}>Sınavı aç</button></article>)}
    </section>
  </div>
}
