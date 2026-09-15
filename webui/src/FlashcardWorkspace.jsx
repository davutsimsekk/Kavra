import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, BookOpen, Search, Plus, Settings2, BarChart3, Download, Upload, Flag, Undo2, Pencil, Check, X, Clock3 } from 'lucide-react'
import './FlashcardWorkspace.css'
import { ThemeToggle } from './Appearance.jsx'

const stateOf = (c) => ['learning', 'relearning', 'review'].includes(c.state) ? c.state : (c.lastReviewedAt != null || c.repetitions > 0 ? 'review' : 'new')
const stateLabels = { new: 'Yeni', learning: 'Öğreniliyor', relearning: 'Yeniden öğreniliyor', review: 'Tekrar' }
const ratings = [['again', 'Tekrar'], ['hard', 'Zor'], ['good', 'İyi'], ['easy', 'Kolay']]
const tagsFrom = (text) => text.trim().split(/\s+/).filter(Boolean)
function duration(seconds) {
  if (seconds <= 0) return '0 sn'
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sn`
  if (seconds < 3600) return `${Math.round(seconds / 60)} dk`
  if (seconds < 86400) return `${+(seconds / 3600).toFixed(1)} sa`
  return `${+(seconds / 86400).toFixed(1)} gün`
}
const date = (seconds) => new Date(seconds * 1000).toLocaleString('tr-TR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export default function FlashcardWorkspace({ projectId, initialDeck, onClose, onSource, api }) {
  const [deck, setDeck] = useState(initialDeck)
  const [mode, setMode] = useState('overview')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [revealed, setRevealed] = useState(false)
  const [sessionCount, setSessionCount] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [tagFilter, setTagFilter] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState(new Set())
  const [bulkTag, setBulkTag] = useState('')
  const [editor, setEditor] = useState(null)
  const [settings, setSettings] = useState(null)
  const [importText, setImportText] = useState('')
  const [now, setNow] = useState(Date.now() / 1000)
  const inFlight = useRef(false)
  const shownAt = useRef(Date.now())
  const answerRef = useRef(null)
  const base = `/api/projects/${projectId}/flashcards/decks/${deck.id}`
  const study = deck.study || {}
  const card = study.nextCard
  const stats = deck.stats || {}
  const pageSize = 40

  function accept(next) {
    setNow(Date.now() / 1000)
    setDeck(next)
    setSelected((current) => new Set([...current].filter((id) => next.cards.some((c) => c.id === id))))
  }
  async function mutate(path, body, method = 'POST') {
    if (inFlight.current) return null
    inFlight.current = true
    setBusy(true)
    setMessage(null)
    try {
      let result = await api(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!Array.isArray(result.cards)) result = await api(base)
      accept(result)
      return result
    } catch (error) {
      setMessage({ error: true, text: error.message })
      return null
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  async function refresh() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    try { accept(await api(base)) } catch (error) { setMessage({ error: true, text: error.message }) }
    finally { inFlight.current = false; setBusy(false) }
  }
  async function rate(rating) {
    if (!card || !revealed) return
    const result = await mutate('/study/review', { cardId: card.id, rating, expectedVersion: card.reviewVersion || 0, seconds: Math.min(300, (Date.now() - shownAt.current) / 1000) })
    if (result) { setSessionCount((n) => n + 1); setRevealed(false) }
  }
  async function undo() {
    if (!study.undoId) return
    const result = await mutate('/study/undo', { eventId: study.undoId })
    if (result) { setSessionCount((n) => Math.max(0, n - 1)); setRevealed(false); setMode('study') }
  }
  async function bulk(action, ids = [...selected]) {
    const result = await mutate('/bulk', { ids, action, tags: tagsFrom(bulkTag) })
    if (result) {
      setSelected(new Set())
      if (mode !== 'study') setMessage({ text: `${ids.length} kart güncellendi.` })
    }
  }
  function openEditor(value = null) {
    setEditor(value ? { ...value, tags: (value.tags || []).join(' ') } : { front: '', back: '', tags: '', cardType: 'basic' })
    setMode('editor')
  }
  async function saveCard(event) {
    event.preventDefault()
    const body = { ...editor, tags: tagsFrom(editor.tags) }
    const result = editor.id
      ? await mutate(`/cards/${editor.id}`, { front: editor.front, back: editor.back, tags: body.tags }, 'PATCH')
      : await mutate('/notes', body)
    if (result) { setMode('browse'); setEditor(null); setMessage({ text: 'Kart kaydedildi; mevcut tekrar takvimi korundu.' }) }
  }
  async function saveSettings(event) {
    event.preventDefault()
    const { name, learningSteps, relearningSteps, ...values } = settings
    const parseSteps = (v) => v.trim().split(/\s+/).filter(Boolean).map((n) => Number(n.replace(',', '.')))
    const result = await mutate('/options', { name, options: { ...values, learningSteps: parseSteps(learningSteps), relearningSteps: parseSteps(relearningSteps) } }, 'PATCH')
    if (result) setMessage({ text: 'Deste ayarları kaydedildi. Mevcut vade tarihleri korunur; yeni süreler sonraki değerlendirmede uygulanır.' })
  }
  function showSettings() {
    setSettings({ ...deck.options, name: deck.name, learningSteps: deck.options.learningSteps.join(' '), relearningSteps: deck.options.relearningSteps.join(' ') })
    setMode('settings')
  }
  async function start() {
    setMessage(null)
    await refresh()
    setSessionCount(0)
    setRevealed(false)
    setMode('study')
  }

  useEffect(() => { setRevealed(false); shownAt.current = Date.now() }, [card?.id, card?.reviewVersion, mode])
  useEffect(() => { if (mode === 'study' && card && !revealed) answerRef.current?.focus() }, [mode, card?.id, card?.reviewVersion, revealed])
  useEffect(() => {
    if (!['study', 'overview'].includes(mode) || card || !study.nextLearningAt) return
    const interval = setInterval(() => setNow(Date.now() / 1000), 1000)
    const timer = setTimeout(refresh, Math.min(30000, Math.max(1000, (study.nextLearningAt - Date.now() / 1000) * 1000 + 200)))
    return () => { clearInterval(interval); clearTimeout(timer) }
  }, [mode, card?.id, study.nextLearningAt, deck])
  useEffect(() => {
    if (mode !== 'study') return
    const handler = (event) => {
      if (event.repeat || inFlight.current || event.target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return
      if (event.altKey || event.metaKey || (event.ctrlKey && event.key.toLowerCase() !== 'z')) return
      const key = event.key.toLowerCase()
      if (key === 'z') { event.preventDefault(); undo(); return }
      if (event.key === 'Escape') { event.preventDefault(); setMode('overview'); return }
      if (!card) return
      if (key === 'e') { event.preventDefault(); openEditor(card); return }
      if (key === 'b') { event.preventDefault(); bulk('bury', [card.id]); return }
      if (event.code === 'Space' || event.key === 'Enter') {
        // Do not hijack keyboard activation of navigation / action buttons.
        if (event.target.tagName === 'BUTTON' && event.target.dataset.studyAnswer !== 'true') return
        event.preventDefault()
        if (revealed) rate('good'); else setRevealed(true)
      } else if (revealed && /^[1-4]$/.test(key)) { event.preventDefault(); rate(ratings[Number(key) - 1][0]) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const allTags = useMemo(() => [...new Set(deck.cards.flatMap((c) => c.tags || []))].sort(), [deck.cards])
  const filtered = useMemo(() => {
    const term = query.toLocaleLowerCase('tr-TR')
    const timestamp = Date.now() / 1000
    const dueIds = new Set(study.queueIds || [])
    return deck.cards.filter((c) => {
      const state = stateOf(c)
      const matches = filter === 'all' || (filter === 'due' && dueIds.has(c.id)) ||
        (filter === 'suspended' && c.suspended) || (filter === 'buried' && c.buriedUntil > timestamp) ||
        (filter === 'flagged' && c.flagged) || (filter === 'new' && state === 'new') ||
        (filter === 'learning' && ['learning', 'relearning'].includes(state)) || (filter === 'review' && state === 'review')
      return matches && (!tagFilter || c.tags?.includes(tagFilter)) &&
        (!term || [c.front, c.back, ...(c.tags || [])].join(' ').toLocaleLowerCase('tr-TR').includes(term))
    })
  }, [deck.cards, query, filter, tagFilter, study.queueIds])
  useEffect(() => { setPage(0) }, [query, filter, tagFilter])
  const maxPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1)
  const currentPage = Math.min(page, maxPage)
  const visible = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize)
  const countTiles = <div className="fc-counts">
    {[['new', study.newCount || 0, 'Yeni'], ['learning', study.learningCount || 0, 'Öğrenme'], ['review', study.reviewCount || 0, 'Tekrar']].map(([kind, count, label]) =>
      <div key={kind} className={kind}><strong>{count}</strong><span>{label}</span></div>)}
  </div>

  return <div className="fc-workspace">
    <header className="fc-header">
      <button className="button quiet" disabled={busy} onClick={onClose}><ArrowLeft size={16} /> Desteler</button>
      <div className="fc-title"><span className="kicker">ÇALIŞMA MASASI</span><h1>{deck.name}</h1><span>{deck.cards.length} kart · Bu cihazda saklanır</span></div>
      <button className="button ghost" disabled={busy} onClick={() => openEditor()}><Plus size={16} /> Kart ekle</button>
      <ThemeToggle />
    </header>
    <nav className="fc-nav" aria-label="Deste bölümleri">
      {[['overview', BookOpen, 'Deste'], ['browse', Search, 'Kartlar'], ['stats', BarChart3, 'İstatistikler']].map(([id, Icon, label]) =>
        <button key={id} disabled={busy} aria-current={mode === id ? 'page' : undefined} className={mode === id ? 'active' : ''} onClick={() => setMode(id)}><Icon size={16} /> {label}</button>)}
      <button disabled={busy} className={mode === 'settings' ? 'active' : ''} onClick={showSettings}><Settings2 size={16} /> Seçenekler</button>
      <button disabled={busy} className={mode === 'import' ? 'active' : ''} onClick={() => setMode('import')}><Upload size={16} /> İçe aktar</button>
      <a href={base + '/export'}><Download size={16} /> Anki'ye aktar</a>
    </nav>
    {message && <div className={`fc-message ${message.error ? 'error' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}<button aria-label="Mesajı kapat" onClick={() => setMessage(null)}><X size={16} /></button></div>}

    {mode === 'overview' && <main className="fc-home">
      <section className="fc-panel fc-study-start"><span className="kicker">BUGÜNKÜ PROGRAMIN</span><h2>Az az, düzenli tekrar.</h2><p>Önce zamanı gelen öğrenme kartları, ardından tekrarlar ve yeni kartlar.</p>{countTiles}
        <button className="button primary fc-start" disabled={busy || !study.readyCount} onClick={start}><BookOpen size={18} /> {study.readyCount ? 'Şimdi çalış' : 'Şimdilik tamamlandı'}</button>
        {!study.readyCount && study.nextLearningAt && <p><Clock3 size={14} /> Sonraki öğrenme: {date(study.nextLearningAt)}</p>}
        {!study.readyCount && !study.nextLearningAt && <p>Günlük sınırlar içindeki kartlarını tamamladın. Diğer kartları Kartlar sekmesinde inceleyebilirsin.</p>}
        <button className="button quiet" disabled={busy} onClick={refresh}>Programı yenile</button>
        <small>Günlük yeni kart sınırı: {deck.options.newPerDay} · Tekrar sınırı: {deck.options.reviewsPerDay}</small>
      </section>
      <aside className="fc-panel fc-today"><span className="kicker">BUGÜN</span><strong>{stats.todayCount || 0}</strong><p>değerlendirme tamamlandı</p>
        <div><span>Yeni çalışılan</span><b>{stats.todayNew || 0}</b></div><div><span>Çalışma süresi</span><b>{duration(stats.seconds || 0)}</b></div>
        <div><span>Öğrenmede bekleyen</span><b>{deck.summary.learningCount || 0}</b></div><div><span>Bugün ertelenen</span><b>{deck.summary.buriedCards || 0}</b></div>
        <div><span>Askıya alınan</span><b>{deck.summary.suspendedCards || 0}</b></div>
        <button className="button ghost" disabled={busy || !study.undoId} onClick={undo}><Undo2 size={15} /> Son değerlendirmeyi geri al</button>
      </aside>
      <div className="fc-help"><strong>Çalışırken</strong><span>Boşluk: cevabı aç / İyi</span><span>1–4: değerlendir</span><span>Z: geri al</span><span>B: bugün ertele</span><span>E: düzenle</span><span>Esc: desteye dön</span></div>
    </main>}

    {mode === 'study' && <main className="fc-review">
      <div className="fc-review-top">{countTiles}<span>{sessionCount} değerlendirme · {study.readyCount || 0} sırada</span></div>
      {card ? <>
        <article className="fc-question" aria-label="Çalışma kartı"><div className="fc-card-meta"><span>{stateLabels[stateOf(card)]} · {card.kind === 'cloze' ? 'Boşluk doldurma' : 'Temel kart'}</span><button className={`button quiet ${card.flagged ? 'fc-flagged' : ''}`} aria-label={card.flagged ? 'İşareti kaldır' : 'Kartı işaretle'} disabled={busy} onClick={() => bulk(card.flagged ? 'unflag' : 'flag', [card.id])}><Flag size={17} /></button></div>
          <p className="fc-front">{card.front}</p>
          {revealed && <div className="fc-answer" aria-live="polite"><span className="kicker">CEVAP</span><p>{card.back}</p></div>}
          {card.tags?.length > 0 && <div className="fc-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
        </article>
        {!revealed ? <button ref={answerRef} data-study-answer="true" className="button primary fc-reveal" disabled={busy} onClick={() => setRevealed(true)}>Cevabı göster <kbd>Boşluk</kbd></button> :
          <div className="fc-ratings">{ratings.map(([id, label], index) => <button key={id} disabled={busy} className={`button rating-${id}`} onClick={() => rate(id)}><small>{duration(card.intervals[id])}</small><span>{label} <kbd>{index + 1}</kbd></span></button>)}</div>}
        <div className="fc-review-tools"><button className="button quiet" disabled={busy} onClick={() => openEditor(card)}><Pencil size={15} /> Düzenle</button><button className="button quiet" disabled={busy} onClick={() => bulk('bury', [card.id])}>Bugün ertele</button><button className="button quiet" disabled={busy} onClick={() => bulk('suspend', [card.id])}>Askıya al</button>{card.sourceSlideIndex != null && <button className="button quiet" onClick={() => onSource(card)}>Kaynağa git</button>}</div>
      </> : <section className="fc-panel fc-finished" aria-live="polite"><Check size={38} /><h2>{study.nextLearningAt ? 'Kısa bir mola.' : 'Bugünkü program tamamlandı.'}</h2><p>Bu oturumda {sessionCount} değerlendirme yaptın.</p>{study.nextLearningAt && <p>Öğrenme kartı {duration(Math.max(0, study.nextLearningAt - now))} sonra hazır olacak; ekran otomatik yenilenir.</p>}<button className="button ghost" disabled={busy} onClick={refresh}>Şimdi kontrol et</button></section>}
      <div className="fc-review-tools"><button className="button ghost" disabled={busy || !study.undoId} onClick={undo}><Undo2 size={15} /> Geri al <kbd>Z</kbd></button><button className="button quiet" disabled={busy} onClick={() => setMode('overview')}>Oturumu bitir</button></div>
    </main>}

    {mode === 'browse' && <main className="fc-panel fc-browser">
      <div className="fc-browser-tools"><label className="fc-search"><Search size={17} /><input aria-label="Kartlarda ara" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Soru, cevap veya etikette ara…" /></label>
        <select aria-label="Kart durumu" value={filter} onChange={(e) => setFilter(e.target.value)}>{[['all','Tüm kartlar'],['due','Şimdi çalışılabilir'],['new','Yeni'],['learning','Öğreniliyor'],['review','Tekrar'],['flagged','İşaretli'],['buried','Bugün ertelenen'],['suspended','Askıda']].map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select aria-label="Etikete göre filtrele" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}><option value="">Tüm etiketler</option>{allTags.map((tag) => <option key={tag}>{tag}</option>)}</select>
      </div>
      <div className="fc-bulk"><span>{selected.size ? `${selected.size} kart seçili` : `${filtered.length} kart`}</span>
        <select aria-label="Seçili kartlara işlem" value="" disabled={busy || !selected.size} onChange={(e) => bulk(e.target.value)}><option value="">Toplu işlem…</option><option value="bury">Bugün ertele</option><option value="unbury">Ertelemeyi kaldır</option><option value="suspend">Askıya al</option><option value="resume">Aktif et</option><option value="flag">İşaretle</option><option value="unflag">İşareti kaldır</option></select>
        <input aria-label="Toplu etiketler" value={bulkTag} onChange={(e) => setBulkTag(e.target.value)} placeholder="etiket1 etiket2" />
        <button className="button ghost compact" disabled={busy || !selected.size || !bulkTag.trim()} onClick={() => bulk('tag')}>Etiket ekle</button>
        <button className="button quiet compact" disabled={busy || !selected.size || !bulkTag.trim()} onClick={() => bulk('untag')}>Etiket kaldır</button>
        {selected.size > 0 && <button className="button quiet compact" onClick={() => setSelected(new Set())}>Seçimi temizle</button>}
      </div>
      <div className="fc-table-wrap"><table className="fc-table"><thead><tr><th><input type="checkbox" aria-label="Bu sayfadaki kartları seç" checked={visible.length > 0 && visible.every((c) => selected.has(c.id))} onChange={(e) => setSelected((old) => { const next = new Set(old); visible.forEach((c) => e.target.checked ? next.add(c.id) : next.delete(c.id)); return next })} /></th><th>Soru / etiket</th><th>Durum</th><th>Sonraki tekrar</th><th>İşlem</th></tr></thead><tbody>
        {visible.map((c) => <tr key={c.id} className={c.suspended ? 'is-suspended' : ''}><td><input type="checkbox" aria-label={`Kart seç: ${c.front.slice(0, 60)}`} checked={selected.has(c.id)} onChange={(e) => setSelected((old) => { const next = new Set(old); e.target.checked ? next.add(c.id) : next.delete(c.id); return next })} /></td><td><button className="fc-card-link" onClick={() => openEditor(c)}>{c.flagged && <Flag size={12} />} {c.front}</button><div className="fc-tags">{(c.tags || []).map((t) => <span key={t}>{t}</span>)}</div></td><td>{c.suspended ? 'Askıda' : c.buriedUntil > now ? 'Ertelendi' : stateLabels[stateOf(c)]}</td><td>{stateOf(c) === 'new' ? 'Yeni kart' : date(c.dueAt)}</td><td><button className="button quiet compact" aria-label={`Düzenle: ${c.front.slice(0, 60)}`} onClick={() => openEditor(c)}><Pencil size={15} /></button></td></tr>)}
      </tbody></table></div>
      {!filtered.length && <p className="fc-empty">Bu filtreye uyan kart yok.</p>}
      <div className="fc-pagination"><span>{filtered.length} sonuç · Sayfa {currentPage + 1}/{maxPage + 1}</span><button className="button quiet" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Önceki</button><button className="button quiet" disabled={currentPage === maxPage} onClick={() => setPage(currentPage + 1)}>Sonraki</button></div>
    </main>}

    {mode === 'editor' && editor && <form className="fc-panel fc-form" onSubmit={saveCard}><h2>{editor.id ? 'Kartı düzenle' : 'Yeni kart ekle'}</h2>
      {!editor.id && <label>Kart türü<select value={editor.cardType} onChange={(e) => setEditor({ ...editor, cardType: e.target.value })}><option value="basic">Temel (soru → cevap)</option><option value="reverse">Temel + ters kart (iki yön)</option><option value="cloze">Boşluk doldurma</option></select></label>}
      {editor.cardType === 'cloze' && <p>Gizlenecek metni <code>{'{{c1::cevap}}'}</code> ile işaretle. İpucu için <code>{'{{c1::cevap::ipucu}}'}</code> kullan. c1, c2… her farklı numara için bir kart oluşturulur.</p>}
      <label>{editor.cardType === 'cloze' ? 'Boşluklu metin' : 'Ön yüz / soru'}<textarea autoFocus required rows={5} value={editor.front} onChange={(e) => setEditor({ ...editor, front: e.target.value })} /></label>
      <label>{editor.cardType === 'cloze' ? 'Ek açıklama (isteğe bağlı)' : 'Arka yüz / cevap'}<textarea required={editor.cardType !== 'cloze'} rows={5} value={editor.back} onChange={(e) => setEditor({ ...editor, back: e.target.value })} /></label>
      <label>Etiketler<input value={editor.tags} onChange={(e) => setEditor({ ...editor, tags: e.target.value })} placeholder="konu::bellek sınav zor" /></label>
      {editor.id && <p>Aralık: {editor.intervalDays || 0} gün · Unutma: {editor.lapses || 0} · Son çalışma: {editor.lastReviewedAt ? date(editor.lastReviewedAt) : 'Henüz çalışılmadı'}</p>}
      <div className="fc-form-actions"><button className="button primary" disabled={busy} type="submit"><Check size={16} /> Kaydet</button><button className="button ghost" type="button" disabled={busy} onClick={() => setMode('browse')}>Vazgeç</button>
        {editor.sourceSlideIndex != null && <button className="button quiet" type="button" onClick={() => onSource(editor)}>Kaynağa git</button>}
        {editor.id && <button className="button quiet danger" type="button" disabled={busy} onClick={async () => { if (window.confirm('Bu kart kalıcı olarak silinecek. Devam edilsin mi?')) { if (await mutate(`/cards/${editor.id}`, {}, 'DELETE')) setMode('browse') } }}>Kartı sil</button>}
      </div>
    </form>}

    {mode === 'settings' && settings && <form className="fc-panel fc-form" onSubmit={saveSettings}><h2>Deste seçenekleri</h2><p>Öğrenme adımları dakika cinsindedir. Yeni ayarlar mevcut vade tarihlerini değiştirmez.</p>
      <label>Deste adı<input required maxLength={150} value={settings.name} onChange={(e) => setSettings({ ...settings, name: e.target.value })} /></label>
      <div className="fc-form-grid">{[['newPerDay','Günlük yeni kart',0,9999],['reviewsPerDay','Günlük tekrar',0,9999],['graduatingDays','Öğrenme sonrası aralık (gün)',1,36500],['easyDays','Kolay aralığı (gün)',1,36500],['maxIntervalDays','En uzun aralık (gün)',1,36500],['dayStartsAt','Yeni gün başlangıcı (saat)',0,23]].map(([key,label,min,max]) =>
        <label key={key}>{label}<input type="number" required min={min} max={max} value={settings[key]} onChange={(e) => setSettings({ ...settings, [key]: Number(e.target.value) })} /></label>)}
        <label>Öğrenme adımları (dk)<input required value={settings.learningSteps} onChange={(e) => setSettings({ ...settings, learningSteps: e.target.value })} placeholder="1 10" /></label>
        <label>Yeniden öğrenme adımları (dk)<input required value={settings.relearningSteps} onChange={(e) => setSettings({ ...settings, relearningSteps: e.target.value })} placeholder="10" /></label>
      </div>
      <label className="fc-checkbox"><input type="checkbox" checked={settings.burySiblings} onChange={(e) => setSettings({ ...settings, burySiblings: e.target.checked })} /> Aynı kaynak / nottan gelen kardeş kartları ertesi güne bırak</label>
      <small>Günlük limit dolsa da başlamış öğrenme adımları devam eder. Gün başlangıcı bilgisayarın yerel saatine göredir. Zamanlayıcı Anki tarzı öğrenme adımları ve SM-2 temelli tekrar aralıkları kullanır; FSRS değildir.</small>
      <button className="button primary" disabled={busy} type="submit">Seçenekleri kaydet</button>
      {deck.kind === 'static' && <button className="button ghost" disabled={busy} type="button" onClick={async () => {
        if (window.confirm('Kartlar güncel kaynak slaytlarından oluşturulacak. Elle eklenen/düzenlenen kartlar ve eşleşen kartların geçmişi korunur. Devam edilsin mi?')) {
          const result = await mutate('/generate', {})
          if (result) setMessage({ text: 'Deste güncel kaynaktan yeniden oluşturuldu.' })
        }
      }}>Kaynaktan yeniden oluştur</button>}
    </form>}

    {mode === 'stats' && <main className="fc-statistics">
      <div className="fc-metrics">{[[stats.todayCount || 0,'Bugünkü değerlendirme'],[stats.totalReviews || 0,'Kayıtlı değerlendirme'],[stats.matureCards || 0,'21+ gün aralıklı kart'],[duration(stats.seconds || 0),'Bugünkü süre']].map(([value,label]) => <div className="fc-panel" key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
      <div className="fc-chart-grid">{[[stats.history || [],'Son 7 çalışma günü'],[stats.forecast || [],'Gelecek 7 günün vadeleri']].map(([data,title]) => <section className="fc-panel" key={title}><h2>{title}</h2><div className="fc-bars">{data.map((d) => <div key={d.day} className="fc-bar-column"><b>{d.count}</b><div className="fc-bar-track"><div style={{ height: `${d.count ? Math.max(3, 100 * d.count / Math.max(1, ...data.map((v) => v.count))) : 0}%` }} /></div><small>{d.day.slice(8)}.{d.day.slice(5,7)}</small></div>)}</div></section>)}</div>
      <section className="fc-panel"><h2>Bugünkü yanıtlar</h2><div className="fc-rating-stats">{ratings.map(([id,label]) => <div className={`rating-${id}`} key={id}><strong>{stats.ratings?.[id] || 0}</strong><span>{label}</span></div>)}</div><p>Geçmiş, bu sürümden itibaren kaydedilen değerlendirmeleri kapsar. Eski kartların vade ve tekrar bilgileri korunur. Tahmin yeni kartları içermez; yanıtlarına göre değişir.</p></section>
    </main>}

    {mode === 'import' && <section className="fc-panel fc-form"><h2>Metinden kart aktar</h2><p>Her satır: soru, sekme, cevap, isteğe bağlı sekme ve etiketler. UTF-8 .tsv veya .txt dosyası seçebilir ya da bir tablodan yapıştırabilirsin. Aynı soru–cevap çifti yeniden eklenmez.</p>
      <input aria-label="Kart dosyası seç" type="file" accept=".tsv,.txt" onChange={async (e) => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 2000000) { setMessage({ error: true, text: 'Dosya en fazla 2 MB olabilir.' }); return } setImportText(await file.text()) }} />
      <textarea aria-label="İçe aktarılacak kartlar" rows={12} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder={'Pointer nedir?\tBellek adresi tutan değişken.\tprogramlama'} />
      <small>Bu aktarım temel kart içindir; .apkg, medya ve Anki çalışma geçmişi aktarılmaz. Metin düz yazı olarak gösterilir.</small>
      <button className="button primary" disabled={busy || !importText.trim()} onClick={async () => { const result = await mutate('/import', { text: importText }); if (result) { setImportText(''); setMessage({ text: `${result.importResult.added} kart eklendi; ${result.importResult.skipped} tekrar atlandı.` }); setMode('browse') } }}><Upload size={16} /> Kartları içe aktar</button>
    </section>}
    <footer className="fc-footer">Öğrenme ve kart yönetimi tamamen yerel çalışır; ek API çağrısı yapmaz.</footer>
  </div>
}
