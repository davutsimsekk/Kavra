import { useEffect, useRef, useState } from 'react'
import { Clock3, ArrowDown, ArrowUp, ArrowRight, ArrowLeft, FolderOpen, BookOpen, ClipboardList, Check, ChevronRight, Clapperboard, Download, FileText, Layers3, LoaderCircle, Plus, Search, Settings2, Trash2, UploadCloud, X } from 'lucide-react'
import { WorkspaceHeader } from './Appearance.jsx'
import ExamWorkspace from './ExamWorkspace.jsx'
import FlashcardWorkspace from './FlashcardWorkspace.jsx'
import './CourseWorkspace.css'

const post = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export default function CourseWorkspace({ initialCourse, bootstrap, api, llm, setLlm, onClose, onOpenVideo, initialTab = 'hub', activeVideoJob, onStudy }) {
  const [course, setCourse] = useState(initialCourse)
  const [tab, setTab] = useState(initialTab)
  const [creating, setCreating] = useState(false)
  const [outputQuery, setOutputQuery] = useState('')
  function navigate(next) { if (busy) return; if (next === 'study') { onStudy(course); return; } setTab(next); setCreating(false); setName(''); setOutputQuery('') }
  const [selected, setSelected] = useState(() => initialCourse.sources.length ? [initialCourse.sources[0].id] : [])
  const [query, setQuery] = useState('')
  const [name, setName] = useState('')
  const [kind, setKind] = useState('static')
  const [count, setCount] = useState('30')
  const [focus, setFocus] = useState('')
  const [examCount, setExamCount] = useState(0)
  const [decks, setDecks] = useState([])
  const [deck, setDeck] = useState(null)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState(null)
  const [failures, setFailures] = useState([])
  const [source, setSource] = useState(null)
  const [sourceIndex, setSourceIndex] = useState(null)
  const [presentationMode, setPresentationMode] = useState('pdf')
  const [diagrams, setDiagrams] = useState(false)
  const [vision, setVision] = useState(false)
  const [visionKey, setVisionKey] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [estimate, setEstimate] = useState(null)
  const [estimateError, setEstimateError] = useState('')
  const jobKey = 'ders-course-deck-job:' + course.id
  const [jobId, setJobId] = useState(() => { try { return localStorage.getItem('ders-course-deck-job:' + initialCourse.id) } catch { return null } })
  const [jobStatus, setJobStatus] = useState('')
  const importLock = useRef(false)
  const base = '/api/projects/' + course.id
  const selectedSources = selected.map(id => course.sources.find(s => s.id === id)).filter(Boolean)
  const allPdf = selectedSources.length > 0 && selectedSources.every(source => source.type === 'pdf')
  const effectivePresentationMode = allPdf ? presentationMode : 'generated'
  const locked = Boolean(busy || jobId)
  const modules = [
    { id: 'videos', icon: Clapperboard, title: 'Dersini hazırla', description: 'Kaynaklarından video dersler hazırla; anlatılarını, seslerini ve videolarını düzenle.', action: 'Ders stüdyosunu aç', count: course.videos.length + ' video çalışması' },
    { id: 'decks', icon: Layers3, title: 'Kartlarla öğren', description: 'Bir deste seç, tekrarlarına başla. İstediğin kaynaklardan yeni kart desteleri oluştur.', action: 'Flashcard destelerine git', count: decks.length + ' deste' },
    { id: 'exams', icon: ClipboardList, title: 'Sınav hazırla', description: 'Seçtiğin kaynaklardan şıklı veya klasik bir deneme oluştur. Çıkmış sınavlarla aynı tarzda çalış.', action: 'Sınav alanını aç', count: examCount + ' sınav' },
    { id: 'study', icon: Clock3, title: 'Çalışmanı takip et', description: 'Görevlerini ve alt görevlerini planla. Pomodoro ile odaklan, dersine ayırdığın süreyi takip et.', action: 'Çalışma alanını aç', count: 'Görevler · Süre · Plan' },
    { id: 'sources', icon: FolderOpen, title: 'Kaynaklarını yönet', description: 'Dersinin PDF, PowerPoint ve notlarını ekle; tüm araçlarda aynı kaynakları kullan.', action: 'Kaynak kitaplığını aç', count: course.sources.length + ' kaynak' },
  ]
  const activeModule = modules.find(module => module.id === tab)
  const matches = (text) => text.toLocaleLowerCase('tr-TR').includes(query.trim().toLocaleLowerCase('tr-TR'))

  async function refresh() {
    const [next, cards, tests] = await Promise.all([api(base), api(base + '/flashcards/decks'), api(base + '/exams')])
    setCourse(next); setDecks(cards.decks); setExamCount(tests.exams.length)
  }
  useEffect(() => { refresh().catch(error => setMessage({ error: true, text: error.message })) }, [base])
  useEffect(() => {
    const warn = event => { if (busy) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [busy])
  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    const poll = async () => {
      try {
        const job = await api('/api/jobs/' + jobId)
        if (cancelled) return
        setJobStatus(job.message || 'Kartlar hazırlanıyor…')
        if (job.status === 'complete' || job.status === 'failed') {
          try { localStorage.removeItem(jobKey) } catch {}
          setJobId(null)
          if (job.status === 'failed') setMessage({ error: true, text: job.error || 'Kart üretimi tamamlanamadı.' })
          else { setDeck(job.result.deck); setName(''); refresh().catch(() => {}) }
        }
      } catch (error) { if (!cancelled) setJobStatus('İşlemle bağlantı kurulamadı; yeniden deneniyor. ' + error.message) }
    }
    poll(); const timer = setInterval(poll, 1200)
    return () => { cancelled = true; clearInterval(timer) }
  }, [jobId, base])
  useEffect(() => {
    if (tab !== 'decks' || kind !== 'llm' || !selected.length) { setEstimate(null); setEstimateError(''); return }
    let cancelled = false
    setEstimate(null); setEstimateError('')
    api(base + '/flashcards/source-estimate', post({ sourceIds: selected }))
      .then(data => { if (!cancelled) setEstimate(data) })
      .catch(error => { if (!cancelled) setEstimateError(error.message) })
    return () => { cancelled = true }
  }, [tab, kind, selected.join(','), base])
  function toggle(id) { setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]) }
  function move(index, delta) {
    const next = [...selected], target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setSelected(next)
  }
  async function upload(files) {
    if (importLock.current || !files.length) return
    importLock.current = true; setFailures([]); setMessage(null)
    const errors = [], added = []
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        setBusy((i + 1) + '/' + files.length + ' · ' + file.name + ' yükleniyor')
        const form = new FormData(); form.append('file', file)
        const pdf = file.name.toLowerCase().endsWith('.pdf')
        form.append('extract_diagrams', String(pdf && diagrams))
        form.append('vision_enrich', String(pdf && vision)); form.append('vision_api_key', visionKey)
        try {
          const result = await api(base + '/sources/upload', { method: 'POST', body: form })
          setCourse(result.project); added.push(result.source.id)
        } catch (error) { errors.push({ name: file.name, message: error.message }) }
      }
      setSelected(current => current.length ? current : added.slice(0, 1))
      setFailures(errors)
      if (added.length) setMessage({ text: added.length + ' kaynak dersine eklendi.' })
    } finally { setBusy(''); importLock.current = false }
  }
  async function addPath() {
    if (importLock.current) return
    importLock.current = true; setBusy('Kaynak ekleniyor…')
    try {
      const result = await api(base + '/sources/path', post({ path: sourcePath, visionEnrich: vision, visionApiKey: visionKey, extractDiagrams: diagrams }))
      setCourse(result.project); setSelected(current => current.length ? current : [result.source.id]); setSourcePath('')
    } catch (error) { setMessage({ error: true, text: error.message }) }
    finally { setBusy(''); importLock.current = false }
  }
  async function createOutput() {
    if (locked || !selected.length) return
    setBusy(tab === 'videos' ? 'Video alanı hazırlanıyor…' : 'Deste oluşturuluyor…'); setMessage(null)
    try {
      const outputName = name.trim() || selectedSources.map(s => s.name.replace(/\.[^.]+$/, '')).join(' + ')
      if (tab === 'videos') {
        const video = await api(base + '/videos', post({ name: outputName, sourceIds: selected, presentationMode: effectivePresentationMode }))
        await onOpenVideo(video)
      } else {
        const result = await api(base + '/flashcards/decks', post({ name: outputName, sourceIds: selected, kind, ...llm, count: count ? Number(count) : null, focusPrompt: focus }))
        if (result.jobId) {
          setJobId(result.jobId); setJobStatus('Kartlar hazırlanıyor…')
          try { localStorage.setItem(jobKey, result.jobId) } catch {}
        } else { setDeck(result); setName(''); await refresh() }
      }
    } catch (error) { setMessage({ error: true, text: error.message }) }
    finally { setBusy('') }
  }
  async function openVideo(item) {
    if (locked) return
    setBusy('Video açılıyor…')
    try { await onOpenVideo(await api(base + '/videos/' + item.id)) }
    catch (error) { setMessage({ error: true, text: error.message }) }
    finally { setBusy('') }
  }
  async function openSource(id, index = null) {
    try { setSource(await api(base + '/sources/' + id)); setSourceIndex(index) }
    catch (error) { setMessage({ error: true, text: error.message }) }
  }
  const sourceViewer = source && <div className="modal-backdrop" onClick={() => setSource(null)}>
    <section className="modal-card course-source-viewer" role="dialog" aria-modal="true" aria-label={source.name} onClick={event => event.stopPropagation()} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setSource(null) } }}>
      <div className="modal-header"><div><strong>{source.name}</strong><small>{source.sectionCount} bölüm</small></div><button autoFocus className="icon-button" aria-label="Kaynağı kapat" onClick={() => setSource(null)}><X size={18} /></button></div>
      <div className="modal-body"><a className="button ghost" href={base + '/sources/' + source.id + '/file'}><Download size={16} /> Kaynak dosyasını indir</a>
        {(sourceIndex == null ? source.sections : source.sections.slice(sourceIndex, sourceIndex + 1)).map((section, index) => <article key={index}><h3>{section.title}</h3><p>{section.text}</p>{section.code_blocks?.map((code, i) => <pre key={i}>{code}</pre>)}</article>)}
      </div>
    </section>
  </div>

  if (tab === 'exams') return <ExamWorkspace course={course} api={api} bootstrap={bootstrap} llm={llm} onClose={() => { navigate('hub'); refresh().catch(() => {}) }} onSources={() => navigate('sources')} onHome={onClose} />
  if (deck) return <><FlashcardWorkspace key={deck.id} projectId={course.id} initialDeck={deck} api={api} onClose={() => { setDeck(null); setCreating(false); refresh().catch(() => {}) }} onSource={card => card.sourceId && openSource(card.sourceId, card.sectionIndex)} />{sourceViewer}</>
  return <div className="dashboard-shell course-shell">
    <WorkspaceHeader onHome={() => { if (busy) setMessage({error:true,text:'Dosya işlemi tamamlanınca proje listesine dönebilirsin.'}); else onClose() }} projectName={course.name} onProject={() => navigate('hub')} current={activeModule?.title || 'Genel bakış'} />
    <div className="dashboard-hero"><span className="kicker">{tab === 'hub' ? 'DERS PROJESİ' : course.name}</span><h1>{tab === 'hub' ? course.name : activeModule?.title}</h1><p>{tab === 'hub' ? 'Bugün nasıl çalışmak istersin? Bir çalışma alanı seç, dersine kaldığın yerden devam et.' : activeModule?.description}</p>
      <div className="project-facts"><span>{course.sources.length} kaynak</span><span>{course.videos.length} video çalışması</span><span>{decks.length} deste</span></div>
    </div>
    {message && <div className={'fc-message course-message' + (message.error ? ' error' : '')} role={message.error ? 'alert' : 'status'}><span>{message.text}</span><button aria-label="Mesajı kapat" onClick={() => setMessage(null)}><X size={16} /></button></div>}
    {failures.length > 0 && <div className="course-upload-errors" role="alert"><strong>Bazı dosyalar eklenemedi; diğerleri kaydedildi.</strong>{failures.map((item,i) => <p key={i}>{item.name}: {item.message}</p>)}</div>}
    {tab === 'hub' ? <div className="hub-modules course-hub-modules">
      {modules.map(module => { const Icon = module.icon; return <button key={module.id} className="hub-module-card" onClick={() => navigate(module.id)}>
        <Icon size={30} /><strong>{module.title}</strong><small>{module.description}</small><span className="memory-pill">{module.count}</span><span className="module-action">{module.action}<ArrowRight size={16} /></span>
      </button> })}
    </div> : <>
    <div className="course-module-toolbar">
      <button className="button quiet" disabled={Boolean(busy)} onClick={() => navigate('hub')}><ArrowLeft size={16} /> Projeye genel bakış</button>
      {tab !== 'sources' && <button className={'button ' + (creating ? 'ghost' : 'primary')} disabled={locked} onClick={() => setCreating(value => !value)}>{creating ? <X size={16} /> : <Plus size={16} />}{creating ? 'Oluşturmayı kapat' : tab === 'videos' ? 'Yeni video oluştur' : 'Yeni deste oluştur'}</button>}
    </div>
    {jobId && !creating && <p className="course-job-status" role="status"><LoaderCircle size={16} className="spin" />{jobStatus}</p>}
    <div className={'course-layout' + (!creating || tab === 'sources' ? ' course-library-layout' : '')}>
      {(tab === 'sources' || creating) && <section className="panel course-sources">
        <div className="panel-toolbar"><div><span className="kicker">KAYNAK KİTAPLIĞI</span><h3>Ders dosyaları</h3></div><span className="memory-pill">{selected.length} seçili</span></div>
        <label className="course-upload" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (!locked) upload(Array.from(e.dataTransfer.files)) }}>
          <input aria-label="Derse kaynak dosyaları ekle" type="file" multiple accept=".pdf,.pptx,.md" disabled={locked} onChange={e => { upload(Array.from(e.target.files)); e.target.value = '' }} />
          {busy ? <LoaderCircle size={23} className="spin" /> : <UploadCloud size={23} />}<strong>{busy || 'Dosyaları sürükle veya seç'}</strong><small>Birden fazla PDF, PowerPoint veya Markdown · dosya başına 100 MB</small>
        </label>
        <details className="options-disclosure"><summary><Settings2 size={15} /> Yükleme seçenekleri<ChevronRight className="disclosure-chevron" size={15} /></summary>
          <label className="course-checkbox"><input type="checkbox" checked={diagrams} disabled={locked} onChange={e => setDiagrams(e.target.checked)} /> PDF içindeki görselleri kullan · ücretsiz</label>
          <label className="course-checkbox"><input type="checkbox" checked={vision} disabled={locked} onChange={e => setVision(e.target.checked)} /> Görselleri Gemini ile açıkla · ek API maliyeti</label>
          {vision && <label className="field"><span>Gemini API anahtarı {bootstrap.keysConfigured.gemini ? '(kayıtlı)' : ''}</span><input type="password" value={visionKey} onChange={e => setVisionKey(e.target.value)} /></label>}
          <div className="course-path"><input aria-label="Derse eklenecek dosya yolu" value={sourcePath} onChange={e => setSourcePath(e.target.value)} placeholder="Bilgisayardaki dosyanın yolu" /><button className="button ghost" disabled={locked || !sourcePath.trim()} onClick={addPath}>Ekle</button></div>
          <small>Bu seçenekler sonraki yüklemelerde uygulanır. Dosya seçerek eklediğinde PDF seçenekleri diğer dosya türlerine uygulanmaz.</small>
        </details>
        <label className="search-field"><Search size={16} /><input aria-label="Ders kaynaklarında ara" placeholder="Kaynak adıyla ara…" value={query} onChange={e => setQuery(e.target.value)} /></label>
        {course.sources.length > 0 && <div className="course-selection-tools"><button disabled={locked} onClick={() => setSelected(course.sources.map(s => s.id))}>Tümünü seç</button><button disabled={locked} onClick={() => setSelected([])}>Seçimi temizle</button></div>}
        <div className="course-source-list">
          {!course.sources.length && <div className="empty-state"><BookOpen size={28} /><h3>İlk ders kaynağını ekle</h3><p>Örneğin, 10 haftalık PDF dosyalarını tek seferde yükleyebilirsin.</p></div>}
          {course.sources.filter(s => matches(s.name)).map(item => <div key={item.id} className={'course-source-row' + (selected.includes(item.id) ? ' selected' : '')}>
            <label><input type="checkbox" disabled={locked} checked={selected.includes(item.id)} onChange={() => toggle(item.id)} /><FileText size={20} /><span><strong>{item.name}</strong><small>{item.type.toUpperCase()} · {item.sectionCount} bölüm</small></span></label>
            <button className="icon-button" title={item.name + ' kaynağını incele'} aria-label={item.name + ' kaynağını incele'} onClick={() => openSource(item.id)}><ChevronRight size={17} /></button>
          </div>)}
          {course.sources.length > 0 && !course.sources.some(s => matches(s.name)) && <p className="list-empty">Eşleşen kaynak bulunamadı.</p>}
        </div>
      </section>}
      {tab !== 'sources' && <div className="course-output-area">
        {creating && <section className="panel course-create">
          <div className="panel-toolbar"><div><span className="kicker">SEÇİLİ KAYNAKLARDAN ÜRET</span><h3>{tab === 'videos' ? 'Yeni video çalışması' : 'Yeni tekrar destesi'}</h3></div><span className="memory-pill">{selected.length} kaynak</span></div>
          <div className="course-create-body">
            <p>{tab === 'videos' ? 'Tek bir PDF için ayrı bir video oluşturabilirsin. Ardışık konuları birleştirmek için birden fazla kaynak seç ve aşağıda sırala.' : 'Bir haftayı veya sınava dahil tüm kaynakları seç. Kart oluşturmak için önce video ya da anlatı üretmen gerekmez.'}</p>
            {selectedSources.length > 0 ? <ol className="course-source-order">{selectedSources.map((item,index) => <li key={item.id}><span>{index + 1}</span><strong>{item.name}</strong>{selectedSources.length > 1 && <div><button aria-label={item.name + ' kaynağını yukarı taşı'} disabled={locked || index === 0} onClick={() => move(index,-1)}><ArrowUp size={14} /></button><button aria-label={item.name + ' kaynağını aşağı taşı'} disabled={locked || index === selected.length - 1} onClick={() => move(index,1)}><ArrowDown size={14} /></button></div>}</li>)}</ol> : <div className="course-selection-empty">Başlamak için kaynak kitaplığından en az bir dosya seç.</div>}
            <label className="field"><span>{tab === 'videos' ? 'Video adı' : 'Deste adı'} <em>isteğe bağlı</em></span><input value={name} disabled={locked} onChange={e => setName(e.target.value)} placeholder={tab === 'videos' ? 'Örn. Hafta 1 · Hücre biyolojisi' : 'Örn. Vize · Hafta 1–5'} /></label>
            {tab === 'videos' && <fieldset className="course-presentation" disabled={locked}>
              <legend>Video nasıl görünsün?</legend>
              <label className={'course-mode-option' + (effectivePresentationMode === 'pdf' ? ' selected' : '')}>
                <input type="radio" name="presentationMode" value="pdf" checked={effectivePresentationMode === 'pdf'} disabled={!allPdf || locked} onChange={() => setPresentationMode('pdf')} />
                <span><strong>PDF üzerinden anlat</strong><small>Sayfaların tasarımı aynen kalır. 20 sayfa → 20 slayt; her sayfa için anlatım hazırlanır.</small></span>
              </label>
              <label className={'course-mode-option' + (effectivePresentationMode === 'generated' ? ' selected' : '')}>
                <input type="radio" name="presentationMode" value="generated" checked={effectivePresentationMode === 'generated'} onChange={() => setPresentationMode('generated')} />
                <span><strong>Yeni slaytlar oluştur</strong><small>Kaynak metninden başlıklar ve maddelerle yeni slaytlar hazırlanır. Slayt sayısı değişebilir.</small></span>
              </label>
              <small>{!allPdf ? 'PDF üzerinden anlatım için yalnızca PDF dosyaları seç.' : 'Tüm sayfalar sırayla korunur; sonraki adımda yalnızca belirli sayfaları da seçebilirsin.'} Aynı kaynaklarla farklı biçimde yeni bir video açabilirsin.</small>
            </fieldset>}
            {tab === 'decks' && <><div className="provider-tabs compact"><button className={kind === 'static' ? 'active' : ''} onClick={() => setKind('static')} disabled={locked}>Kaynak kartları · ücretsiz</button><button className={kind === 'llm' ? 'active' : ''} onClick={() => setKind('llm')} disabled={locked}>Yapay zeka ile sorular</button></div>
              {kind === 'static' ? <small>Kaynak bölümlerinden başlık ve açıklama kartları oluşturur. Kısa sınav soruları için yapay zeka seçeneğini kullanabilirsin.</small> : <div className="course-llm">
                <div className="provider-tabs compact">{[['agent','Claude Agent'],['gemini','Gemini'],['openai','OpenAI uyumlu']].map(([id,label]) => <button key={id} className={llm.provider === id ? 'active' : ''} onClick={() => setLlm({...llm,provider:id,apiKey:''})} disabled={locked}>{label}</button>)}</div>
                {llm.provider === 'agent' ? <label className="field"><span>Agent komutu</span><input value={llm.agentCommand} onChange={e => setLlm({...llm,agentCommand:e.target.value})} /></label> : <>
                  {llm.provider === 'openai' && <label className="field"><span>API adresi</span><input value={llm.openaiEndpoint} onChange={e => setLlm({...llm,openaiEndpoint:e.target.value})} /></label>}
                  <label className="field"><span>Model</span>{llm.provider === 'gemini' ? <select value={llm.geminiModel} onChange={e => setLlm({...llm,geminiModel:e.target.value})}>{bootstrap.models.gemini.map(model => <option key={model}>{model}</option>)}</select> : <input value={llm.openaiModel} onChange={e => setLlm({...llm,openaiModel:e.target.value})} />}</label>
                  <label className="field"><span>API anahtarı {bootstrap.keysConfigured[llm.provider] ? '(kayıtlı)' : ''}</span><input type="password" value={llm.apiKey} onChange={e => setLlm({...llm,apiKey:e.target.value})} /></label>
                </>}
                <label className="field"><span>Toplam kart hedefi</span><input type="number" min={estimate?.requests || 1} max="60" value={count} onChange={e => setCount(e.target.value)} /></label>
                <label className="field"><span>Odaklanılacak konular</span><textarea rows="2" value={focus} onChange={e => setFocus(e.target.value)} placeholder="Örn. yalnızca tanımlar ve karşılaştırmalar" /></label>
                {estimate && <small>{estimate.characters.toLocaleString('tr-TR')} karakter · yaklaşık {estimate.requests} üretim çağrısı. Yanıt biçiminin düzeltilmesi gerekirse ek çağrı yapılabilir. Sağlayıcına göre ücret oluşabilir.</small>}
                {estimateError && <p className="course-error">{estimateError}</p>}
              </div>}
            </>}
            <button className="button primary" disabled={locked || !selected.length || (tab === 'videos' && activeVideoJob) || (tab === 'decks' && kind === 'llm' && (!estimate || Boolean(estimateError)))} onClick={createOutput}>
              {locked ? <LoaderCircle size={17} className="spin" /> : tab === 'videos' ? <Clapperboard size={17} /> : <Layers3 size={17} />}
              {jobId ? 'Kartlar hazırlanıyor…' : tab === 'videos' ? 'Video alanını oluştur' : 'Desteyi oluştur'}<ArrowRight size={16} />
            </button>
            {tab === 'videos' && <small>Bu adım ücretli üretim başlatmaz. Anlatı ve ses ayarlarını sonraki ekranda seçersin; diğer videoların korunur.</small>}
            {activeVideoJob && <small>Bir video işlemi sürüyor. Yeni video açmak için tamamlanmasını bekle.</small>}
            {jobId && <p role="status">{jobStatus}</p>}
            {tab === 'decks' && <button className="button quiet" disabled={locked} onClick={async () => { try { setDeck(await api(base + '/flashcards/blank', post({name:name.trim() || 'Yeni deste'}))) } catch(error){setMessage({error:true,text:error.message})} }}><Plus size={16} /> Boş deste aç · elle ekle / içe aktar</button>}
          </div>
        </section>}
        <section className="panel course-results"><div className="panel-toolbar"><h3>{tab === 'videos' ? 'Video çalışmaların' : 'Kart destelerin'}</h3><span className="memory-pill">{tab === 'videos' ? course.videos.length + ' video' : decks.reduce((sum, item) => sum + (item.study?.readyCount ?? item.summary.dueCount), 0) + ' kart sırada'}</span></div>
          <label className="search-field"><Search size={16} /><input aria-label={tab === 'videos' ? 'Videolarda ara' : 'Destelerde ara'} value={outputQuery} onChange={event => setOutputQuery(event.target.value)} placeholder={tab === 'videos' ? 'Video adıyla ara…' : 'Deste adıyla ara…'} /></label>
          {outputQuery && !(tab === 'videos' ? course.videos : decks).some(item => item.name.toLocaleLowerCase('tr-TR').includes(outputQuery.toLocaleLowerCase('tr-TR'))) && <p className="list-empty">Eşleşen çalışma bulunamadı.</p>}
          {tab === 'videos' ? course.videos.length ? course.videos.filter(item => item.name.toLocaleLowerCase('tr-TR').includes(outputQuery.toLocaleLowerCase('tr-TR'))).map(item => <article className="course-result" key={item.id}><Clapperboard size={22} /><div><h4>{item.name}</h4><p>{item.sourceNames.join(' → ')}</p><small>{item.slideCount} slayt · {item.videoReady ? 'Video hazır' : item.slideCount ? 'Anlatı hazır' : 'Anlatı bekliyor'}</small></div><div className="course-result-actions">{item.videoReady && <a className="button ghost compact" href={base + '/videos/' + item.id + '/output/video'}>Videoyu aç</a>}<button className="button ghost compact" disabled={locked || Boolean(activeVideoJob)} onClick={() => openVideo(item)}>Düzenle</button></div></article>) : <p className="list-empty">Henüz video çalışması yok. Yeni video oluştur ile kaynaklarını seçerek başla.</p> :
          decks.length ? decks.filter(item => item.name.toLocaleLowerCase('tr-TR').includes(outputQuery.toLocaleLowerCase('tr-TR'))).map(item => <article className="course-result" key={item.id}><Layers3 size={22} /><div><h4>{item.name}</h4><p>{item.sourceNames?.join(' · ') || 'Elle oluşturulan deste'}</p><small>{item.summary.totalCards} kart · {item.study?.readyCount ?? item.summary.dueCount} bugün sırada</small></div><button className="button ghost compact" disabled={locked} onClick={async () => {try {setDeck(await api(base + '/flashcards/decks/' + item.id))}catch(error){setMessage({error:true,text:error.message})}}}>Çalış</button><button className="icon-button danger" aria-label={item.name + ' destesini sil'} disabled={locked} onClick={async () => {
            if (!window.confirm('“' + item.name + '” destesi ve çalışma geçmişi silinsin mi?')) return
            try { await api(base + '/flashcards/decks/' + item.id, {method:'DELETE'}); await refresh() } catch(error){setMessage({error:true,text:error.message})}
          }}><Trash2 size={16} /></button></article>) : <p className="list-empty">Henüz deste yok. Yeni deste oluştur ile kaynaklarını seç veya boş bir deste aç.</p>}
        </section>
      </div>}
    </div>
    </>}
    {sourceViewer}
  </div>
}
