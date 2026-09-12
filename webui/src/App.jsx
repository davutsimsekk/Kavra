import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clapperboard,
  Clock3,
  Copy,
  FileText,
  FolderOpen,
  GripVertical,
  Layers3,
  LoaderCircle,
  Mic2,
  MonitorPlay,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Sparkles,
  Trash2,
  UploadCloud,
  WandSparkles,
  X,
  Zap,
} from 'lucide-react'

const steps = [
  { id: 'source', label: 'Kaynak', eyebrow: '01', icon: FileText },
  { id: 'script', label: 'Anlatı', eyebrow: '02', icon: Layers3 },
  { id: 'video', label: 'Stüdyo', eyebrow: '03', icon: Clapperboard },
]

const themeSwatches = {
  auto: ['#77f2c2', '#6e78ff'],
  white: ['#ffffff', '#e7edf7'],
  notebook: ['#f7faff', '#d9e8ff'],
  midnight: ['#0a111f', '#315078'],
  warm: ['#fff3d9', '#d87455'],
  mint: ['#e1fff5', '#2ea888'],
  aurora: ['#46389f', '#13a4a6'],
}

const ThreeBackdrop = lazy(() => import('./ThreeBackdrop.jsx'))

function AmbientBackdrop({ disabled = false }) {
  if (disabled) return <div className="three-backdrop css-only" aria-hidden="true" />
  return <Suspense fallback={null}><ThreeBackdrop /></Suspense>
}

const SESSION_KEY = 'ders-studio-session'

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') || {}
  } catch {
    return {}
  }
}

function patchSession(patch) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ ...loadSession(), ...patch }))
  } catch {
    // localStorage kullanılamıyor olabilir (gizli sekme vb.) - sessizce yok say
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options)
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = typeof data === 'object' ? data.detail : data
    throw new Error(detail || `HTTP ${response.status}`)
  }
  return data
}

function Toggle({ checked, onChange, label, hint, disabled = false }) {
  return (
    <label className="toggle-row">
      <button
        type="button"
        className={`switch ${checked ? 'is-on' : ''}`}
        onClick={() => onChange(!checked)}
        disabled={disabled}
        role="switch"
        aria-checked={checked}
      >
        <span />
      </button>
      <span><strong>{label}</strong>{hint && <small>{hint}</small>}</span>
    </label>
  )
}

function ProgressStrip({ job }) {
  if (!job) return null
  const failed = job.status === 'failed'
  const complete = job.status === 'complete'
  return (
    <div className={`job-strip ${failed ? 'failed' : ''} ${complete ? 'complete' : ''}`}>
      <div className="job-copy">
        {failed ? <CircleAlert size={17} /> : complete ? <CheckCircle2 size={17} /> : <LoaderCircle className="spin" size={17} />}
        <span>{failed ? job.error : job.message}</span>
        <b>{job.progress || 0}%</b>
      </div>
      <div className="progress-track"><span style={{ width: `${job.progress || 0}%` }} /></div>
    </div>
  )
}

function EmptyState({ icon: Icon, title, children }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={25} /></span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}

export default function App() {
  const [bootstrap, setBootstrap] = useState(null)
  const [bootstrapError, setBootstrapError] = useState('')
  const [bootstrapRetry, setBootstrapRetry] = useState(0)
  const [project, setProject] = useState(null)
  const [stage, setStage] = useState('source')
  const [selectedSections, setSelectedSections] = useState(new Set())
  const [selectedSlide, setSelectedSlide] = useState(null)
  const [draft, setDraft] = useState(null)
  const [sourcePath, setSourcePath] = useState('')
  const [isDropping, setIsDropping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)
  const [activeJob, setActiveJob] = useState(null)
  const [jobState, setJobState] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [voices, setVoices] = useState([])
  const [draggedSlide, setDraggedSlide] = useState(null)

  const [llm, setLlm] = useState({
    provider: 'agent', apiKey: '', geminiModel: '', openaiModel: '', openaiEndpoint: '',
    agentCommand: '', reuseSession: true, maxSections: 4, timeout: 900,
    singleRequest: false, resumeCompleted: true, style: '', insertMode: 'append',
  })
  const [video, setVideo] = useState({
    theme: 'auto', ttsProvider: 'edge', voice: 'tr-TR-AhmetNeural', rate: '+0%',
    subtitles: true, fadeTransitions: true, kenBurns: false, elevenlabsKey: '',
  })

  useEffect(() => {
    setBootstrapError('')
    api('/api/bootstrap')
      .then((data) => {
        setBootstrap(data)
        const s = data.settings
        setLlm((current) => ({
          ...current,
          provider: s.llm_provider || 'agent',
          geminiModel: s.gemini_model || data.models.geminiDefault,
          openaiModel: s.openai_model || data.models.openaiDefault,
          openaiEndpoint: s.openai_endpoint || data.models.openaiEndpoint,
          agentCommand: s.agent_command,
          reuseSession: s.agent_reuse_session,
          maxSections: s.agent_max_sections,
          timeout: s.agent_timeout_sec,
          singleRequest: s.single_request,
        }))
        setVideo((current) => ({
          ...current,
          theme: s.theme_preset,
          ttsProvider: s.tts_provider,
          voice: s.tts_voice,
          rate: s.tts_rate,
          subtitles: s.subtitles,
          fadeTransitions: s.fade_transitions,
          kenBurns: s.ken_burns,
        }))

        // Sayfa yenilense (veya sekme yeniden açılsa) bile, açık projeyi ve arka
        // planda süren bir işi kaybetmemek için son oturumu geri yükle. İş sunucu
        // tarafında bir Python thread'i olarak zaten devam ediyor olabilir — burada
        // sadece arayüzü ona yeniden bağlıyoruz.
        const session = loadSession()
        if (session.projectId) {
          api(`/api/projects/${session.projectId}`)
            .then((projectData) => {
              setProject(projectData)
              setSelectedSections(new Set(projectData.sections.map((_, index) => index)))
              setSelectedSlide(projectData.slides.length ? 0 : null)
              if (session.stage) setStage(session.stage)
              if (session.activeJob) {
                api(`/api/jobs/${session.activeJob.id}`)
                  .then((job) => {
                    if (job.status === 'running' || job.status === 'queued') {
                      setActiveJob(session.activeJob)
                      setJobState(job)
                    } else {
                      patchSession({ activeJob: null })
                    }
                  })
                  .catch(() => patchSession({ activeJob: null }))
              }
            })
            .catch(() => patchSession({ projectId: null, activeJob: null }))
        }
      })
      .catch((error) => setBootstrapError(error.message))
  }, [bootstrapRetry])

  useEffect(() => {
    if (project?.id) patchSession({ projectId: project.id })
  }, [project?.id])

  useEffect(() => {
    patchSession({ stage })
  }, [stage])

  useEffect(() => {
    patchSession({ activeJob: activeJob || null })
  }, [activeJob])

  useEffect(() => {
    if (selectedSlide === null || !project?.slides[selectedSlide]) {
      setDraft(null)
      return
    }
    setDraft(structuredClone(project.slides[selectedSlide]))
  }, [selectedSlide, project?.slides])

  useEffect(() => {
    if (!video.ttsProvider) return
    api(`/api/voices/${video.ttsProvider}`)
      .then(({ voices: nextVoices }) => {
        setVoices(nextVoices)
        if (nextVoices.length && !nextVoices.some((item) => item.id === video.voice)) {
          setVideo((current) => ({ ...current, voice: nextVoices[0].id }))
        }
      })
      .catch((error) => {
        setVoices([])
        setToast({ type: 'error', text: `Sesler alınamadı: ${error.message}` })
      })
  }, [video.ttsProvider])

  useEffect(() => {
    if (!activeJob) return undefined
    let stopped = false
    const poll = async () => {
      try {
        const job = await api(`/api/jobs/${activeJob.id}`)
        if (stopped) return
        setJobState(job)
        if (activeJob.type === 'script' && job.result?.slides) {
          setProject((current) => current ? {
            ...current,
            slides: job.result.slides,
            generation: job.result.generation || current.generation,
          } : current)
        }
        if (job.status === 'complete') {
          if (activeJob.type === 'script') {
            setProject((current) => ({
              ...current,
              slides: job.result.slides,
              generation: job.result.generation || current.generation,
            }))
            const skipped = job.result.skippedSectionCount || 0
            setToast({ type: 'success', text: skipped
              ? `${job.result.generatedCount} yeni slayt üretildi; daha önce biten ${skipped} bölüm atlandı.`
              : `${job.result.generatedCount} yeni slayt üretildi.` })
          } else {
            setProject((current) => ({ ...current, outputs: { video: true, audio: true } }))
            setToast({ type: 'success', text: 'Video ve ses çıktısı hazır.' })
          }
          setActiveJob(null)
        } else if (job.status === 'failed') {
          setToast({ type: 'error', text: job.error })
          setActiveJob(null)
        }
      } catch (error) {
        if (!stopped) {
          const message = `Yerel API bağlantısı koptu: ${error.message}`
          setJobState({ status: 'failed', kind: activeJob.type, progress: 0, error: message })
          setToast({ type: 'error', text: message })
          setActiveJob(null)
        }
      }
    }
    poll()
    const timer = setInterval(poll, 750)
    return () => { stopped = true; clearInterval(timer) }
  }, [activeJob])

  useEffect(() => {
    if (!toast) return undefined
    const timer = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(timer)
  }, [toast])

  const completion = useMemo(() => {
    if (!project) return 0
    if (project.outputs?.video) return 100
    if (project.slides?.length) return 66
    return 33
  }, [project])

  const selectedSourceCharacters = useMemo(() => {
    if (!project) return 0
    return [...selectedSections].reduce((total, index) => {
      const section = project.sections[index]
      if (!section) return total
      return total + section.title.length + section.text.length + section.code_blocks.reduce((sum, block) => sum + block.length, 0)
    }, 0)
  }, [project, selectedSections])
  const oneShotSafe = selectedSections.size <= 12 && selectedSourceCharacters <= 24000
  const completedSections = useMemo(
    () => new Set(project?.generation?.completedIndexes || []),
    [project?.generation?.completedIndexes],
  )
  const selectedRemainingCount = useMemo(
    () => [...selectedSections].filter((index) => !completedSections.has(index)).length,
    [selectedSections, completedSections],
  )
  const effectiveSelectedCount = llm.resumeCompleted ? selectedRemainingCount : selectedSections.size

  useEffect(() => {
    if (!oneShotSafe && llm.singleRequest) {
      setLlm((current) => ({ ...current, singleRequest: false }))
    }
  }, [oneShotSafe, llm.singleRequest])

  const loadProject = async (id) => {
    setBusy(true)
    try {
      const data = await api(`/api/projects/${id}`)
      applyProject(data)
      setToast({ type: 'success', text: 'Proje yüklendi.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  const applyProject = (data) => {
    setProject(data)
    setSelectedSections(new Set(data.sections.map((_, index) => index)))
    setSelectedSlide(data.slides.length ? 0 : null)
    setPreviewUrl('')
  }

  const uploadSource = async (file) => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    setBusy(true)
    try {
      const data = await api('/api/source/upload', { method: 'POST', body: form })
      applyProject(data)
      setToast({ type: 'success', text: `${data.sections.length} bölüm ayrıştırıldı.` })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  const parsePath = async () => {
    if (!sourcePath.trim()) return
    setBusy(true)
    try {
      const data = await api('/api/source/path', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: sourcePath.trim() }),
      })
      applyProject(data)
      setToast({ type: 'success', text: `${data.sections.length} bölüm ayrıştırıldı.` })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  const toggleSection = (index) => {
    setSelectedSections((current) => {
      const next = new Set(current)
      next.has(index) ? next.delete(index) : next.add(index)
      return next
    })
  }

  const persistSlides = async (slides, message) => {
    if (!project) return
    setProject((current) => ({ ...current, slides }))
    try {
      await api(`/api/projects/${project.id}/slides`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slides }),
      })
      if (message) setToast({ type: 'success', text: message })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const saveDraft = () => {
    if (selectedSlide === null || !draft) return
    const next = [...project.slides]
    next[selectedSlide] = { ...draft, bullets: draft.bullets.filter(Boolean) }
    persistSlides(next, 'Slayt kaydedildi.')
  }

  const addSlide = () => {
    const at = selectedSlide === null ? project.slides.length : selectedSlide + 1
    const next = [...project.slides]
    next.splice(at, 0, { title: 'Yeni slayt', bullets: [], code: null, narration: '', level: 'topic' })
    setSelectedSlide(at)
    persistSlides(next, 'Yeni slayt eklendi.')
  }

  const deleteSlide = (index) => {
    const next = project.slides.filter((_, slideIndex) => slideIndex !== index)
    setSelectedSlide(next.length ? Math.min(index, next.length - 1) : null)
    persistSlides(next, 'Slayt silindi.')
  }

  const moveSlide = (index, delta) => {
    const target = index + delta
    if (target < 0 || target >= project.slides.length) return
    const next = [...project.slides]
    ;[next[index], next[target]] = [next[target], next[index]]
    setSelectedSlide(target)
    persistSlides(next)
  }

  const dropSlide = (targetIndex) => {
    if (draggedSlide === null || draggedSlide === targetIndex) return
    const next = [...project.slides]
    const [moved] = next.splice(draggedSlide, 1)
    next.splice(targetIndex, 0, moved)
    setSelectedSlide(targetIndex)
    setDraggedSlide(null)
    persistSlides(next, 'Slayt sırası güncellendi.')
  }

  const generate = async () => {
    if (!project || !selectedSections.size || !effectiveSelectedCount || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Hazırlanıyor' })
    try {
      const response = await api(`/api/projects/${project.id}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...llm,
          sectionIndexes: [...selectedSections].sort((a, b) => a - b),
          insertAfter: llm.insertMode === 'after' ? selectedSlide : null,
        }),
      })
      setActiveJob({ id: response.jobId, type: 'script' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const resetGenerationProgress = async () => {
    if (!project || activeJob) return
    try {
      const data = await api(`/api/projects/${project.id}/generation/reset`, { method: 'POST' })
      setProject((current) => ({ ...current, generation: data.generation }))
      setToast({ type: 'success', text: 'Üretim işaretleri sıfırlandı; mevcut slaytlar korunuyor.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const preview = async () => {
    if (!project) return
    try {
      const data = await api(`/api/projects/${project.id}/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: selectedSlide || 0, slide: draft, theme: video.theme }),
      })
      setPreviewUrl(data.url)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const render = async () => {
    if (!project?.slides.length || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Render hazırlanıyor' })
    try {
      const response = await api(`/api/projects/${project.id}/render`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(video),
      })
      setActiveJob({ id: response.jobId, type: 'video' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const renderSource = () => (
    <div className="stage-grid source-grid">
      <section className="panel source-ingest">
        <div className="section-heading">
          <span className="kicker">MATERYAL GİRİŞİ</span>
          <h2>Dersin hammaddesini içeri al.</h2>
          <p>PDF, PowerPoint veya Markdown dosyasını bırak. Bölümleri senin için ayrıştırıp seçilebilir hale getireceğiz.</p>
        </div>
        <label
          className={`drop-zone ${isDropping ? 'is-dropping' : ''}`}
          onDragOver={(event) => { event.preventDefault(); setIsDropping(true) }}
          onDragLeave={() => setIsDropping(false)}
          onDrop={(event) => { event.preventDefault(); setIsDropping(false); uploadSource(event.dataTransfer.files[0]) }}
        >
          <input type="file" accept=".md,.pptx,.pdf" onChange={(event) => uploadSource(event.target.files[0])} />
          <span className="upload-orbit"><UploadCloud size={28} /></span>
          <strong>{busy ? 'Dosya işleniyor…' : 'Dosyayı buraya bırak'}</strong>
          <small>veya seçmek için tıkla · en fazla 100 MB</small>
        </label>
        <div className="path-divider"><span>ya da bu bilgisayardaki yolu kullan</span></div>
        <div className="path-input">
          <input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="D:\\dersler\\konu.pdf" />
          <button className="button ghost" onClick={parsePath} disabled={!sourcePath.trim() || busy}>Ayrıştır</button>
        </div>
      </section>

      <section className="panel section-library">
        <div className="panel-toolbar">
          <div>
            <span className="kicker">İÇERİK HARİTASI</span>
            <h3>{project ? `${project.sections.length} bölüm bulundu` : 'Henüz kaynak seçilmedi'}</h3>
          </div>
          {project && <div className="selection-tools">
            <button onClick={() => setSelectedSections(new Set(project.sections.map((_, i) => i)))}>Tümünü seç</button>
            <button onClick={() => setSelectedSections(new Set())}>Temizle</button>
            <span>{selectedSections.size}/{project.sections.length}</span>
          </div>}
        </div>
        {!project ? <EmptyState icon={BookOpen} title="İçerik burada şekillenecek">Bir kaynak eklediğinde başlık yapısını ve her bölümün uzunluğunu burada göreceksin.</EmptyState> : (
          <div className="section-list">
            {project.sections.map((section, index) => {
              const selected = selectedSections.has(index)
              const completed = completedSections.has(index)
              return <button key={`${section.title}-${index}`} className={`section-row ${selected ? 'selected' : ''} ${completed ? 'completed' : ''}`} onClick={() => toggleSection(index)}>
                <span className="check-box">{selected && <Check size={14} />}</span>
                <span className="section-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="section-main"><strong>{section.title || 'Başlıksız bölüm'}</strong><small>{completed ? 'Tamamlandı · ' : ''}{section.breadcrumb || `Seviye ${section.level}`}</small></span>
                <span className="char-count">{section.text.length.toLocaleString('tr-TR')} kr</span>
              </button>
            })}
          </div>
        )}
        {project && <div className="panel-footer"><span>{selectedSections.size} bölüm üretime hazır</span><button className="button primary" disabled={!selectedSections.size} onClick={() => setStage('script')}>Anlatıya geç <ArrowRight size={16} /></button></div>}
      </section>
    </div>
  )

  const renderProviderFields = () => {
    if (llm.provider === 'agent') return <>
      <label className="field wide"><span>Agent komutu</span><input value={llm.agentCommand} onChange={(e) => setLlm({ ...llm, agentCommand: e.target.value })} /></label>
      <Toggle
        checked={llm.reuseSession}
        onChange={(value) => setLlm({ ...llm, reuseSession: value })}
        disabled={llm.singleRequest}
        label="Aynı Claude oturumu"
        hint={llm.singleRequest ? 'Tek çağrıda devam oturumuna gerek yok' : 'Her parça --resume ile aynı konuşmayı sürdürür'}
      />
    </>
    if (llm.provider === 'gemini') return <>
      <label className="field"><span>Model</span><select value={llm.geminiModel} onChange={(e) => setLlm({ ...llm, geminiModel: e.target.value })}>{bootstrap.models.gemini.map((model) => <option key={model}>{model}</option>)}</select></label>
      <label className="field"><span>API anahtarı {bootstrap.keysConfigured.gemini && <em>kayıtlı</em>}</span><input type="password" value={llm.apiKey} onChange={(e) => setLlm({ ...llm, apiKey: e.target.value })} placeholder="Yeni anahtar girmek zorunda değilsin" /></label>
    </>
    return <>
      <label className="field wide"><span>Endpoint / Base URL</span><input value={llm.openaiEndpoint} onChange={(e) => setLlm({ ...llm, openaiEndpoint: e.target.value })} /></label>
      <label className="field"><span>Model kimliği</span><input value={llm.openaiModel} onChange={(e) => setLlm({ ...llm, openaiModel: e.target.value })} /></label>
      <label className="field"><span>API anahtarı {bootstrap.keysConfigured.openai && <em>kayıtlı</em>}</span><input type="password" value={llm.apiKey} onChange={(e) => setLlm({ ...llm, apiKey: e.target.value })} placeholder="sk-…" /></label>
    </>
  }

  const renderScript = () => (
    <div className="script-layout">
      <section className="panel generation-console">
        <div className="panel-toolbar">
          <div><span className="kicker">ANLATI MOTORU</span><h3>Üretim ayarları</h3></div>
          <span className="memory-pill"><Zap size={13} /> Ders hafızası açık</span>
        </div>
        <div className="provider-tabs">
          {[['agent', 'Claude Agent'], ['gemini', 'Gemini API'], ['openai', 'OpenAI uyumlu']].map(([id, label]) => <button key={id} className={llm.provider === id ? 'active' : ''} onClick={() => setLlm({ ...llm, provider: id, apiKey: '' })}>{label}</button>)}
        </div>
        <div className="form-grid">{renderProviderFields()}
          <label className="field"><span>Bir LLM çağrısındaki kaynak bölümü</span><input disabled={llm.singleRequest} type="number" min="1" max="20" value={llm.maxSections} onChange={(e) => setLlm({ ...llm, maxSections: Number(e.target.value) })} /><small>Üretilen slayt sayısı değil; PDF/PPT içinden aynı çağrıya konan bölüm sayısıdır.</small></label>
          <label className="field"><span>Timeout</span><div className="input-suffix"><input type="number" min="60" step="60" value={llm.timeout} onChange={(e) => setLlm({ ...llm, timeout: Number(e.target.value) })} /><b>sn</b></div></label>
          <label className="field wide"><span>Üslup yönlendirmesi</span><textarea rows="2" value={llm.style} onChange={(e) => setLlm({ ...llm, style: e.target.value })} placeholder="Örn. kavramsal, sakin, klinik örneklerle…" /></label>
          <label className="field"><span>Yeni slaytların yeri</span><select value={llm.insertMode} onChange={(e) => setLlm({ ...llm, insertMode: e.target.value })}><option value="append">Listenin sonu</option><option value="after">Seçili slayttan sonra</option></select></label>
          <Toggle checked={llm.singleRequest} disabled={!oneShotSafe} onChange={(value) => setLlm({ ...llm, singleRequest: value })} label="Tek istekte gönder" hint={oneShotSafe ? 'En fazla 12 bölüm / 24.000 karakter' : `${selectedSections.size} bölüm ve ${selectedSourceCharacters.toLocaleString('tr-TR')} karakter: güvenli sınırın üzerinde`} />
          <Toggle checked={llm.resumeCompleted} onChange={(value) => setLlm({ ...llm, resumeCompleted: value })} label="Kaldığı yerden devam et" hint={`Tamamlanan ${project?.generation?.completedCount || 0} bölümü tekrar gönderme`} />
          <div className="resume-note wide">
            <CheckCircle2 size={15} />
            <span>
              <strong>{project?.generation?.completedCount || 0}/{project?.generation?.totalCount || 0} kaynak bölümü tamamlandı</strong>
              <small>{project?.generation?.inferred ? 'Eski proje için son üretilen numaralı başlıktan güvenli biçimde çıkarıldı.' : 'Her başarılı LLM çağrısından sonra diske kaydedilir.'}</small>
            </span>
            {(project?.generation?.completedCount || 0) > 0 && <button type="button" onClick={resetGenerationProgress} disabled={Boolean(activeJob)}>İşaretleri sıfırla</button>}
          </div>
          <div className="session-note wide"><Zap size={14} /><span>{llm.singleRequest ? 'Seçili bölümlerin tamamı tek Claude çağrısına gider. Bu durumda tek bir çağrı zaten tek oturumdur; parça ve resume ayarları kullanılmaz.' : `Seçili kaynaklar en fazla ${llm.maxSections} bölümlük çağrılara ayrılır. Claude seçeneği açıksa bu ayrı çağrıların tamamı aynı mantıksal oturumda devam eder.`}</span></div>
        </div>
        <button className="button primary generate-button" onClick={generate} disabled={!project || !selectedSections.size || !effectiveSelectedCount || Boolean(activeJob)}><WandSparkles size={17} /> {activeJob?.type === 'script' ? 'Üretiliyor…' : effectiveSelectedCount ? `${effectiveSelectedCount} kalan bölümden üret` : 'Seçimde bekleyen bölüm yok'}</button>
        <ProgressStrip job={activeJob?.type === 'script' || jobState?.kind === 'script' ? jobState : null} />
      </section>

      <section className="panel slide-rail">
        <div className="panel-toolbar"><div><span className="kicker">AKIŞ</span><h3>{project?.slides.length || 0} slayt</h3></div><button className="icon-button" onClick={addSlide} title="Yeni slayt"><Plus size={18} /></button></div>
        {!project?.slides.length ? <EmptyState icon={Layers3} title="Anlatı henüz boş">Seçili bölümlerden ilk slayt setini üret.</EmptyState> : <div className="slide-list">
          {project.slides.map((slide, index) => <article
            key={`${slide.title}-${index}`}
            className={`slide-card ${selectedSlide === index ? 'active' : ''}`}
            draggable
            onDragStart={() => setDraggedSlide(index)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => dropSlide(index)}
            onClick={() => setSelectedSlide(index)}
          >
            <GripVertical size={16} className="grip" />
            <span className="slide-number">{String(index + 1).padStart(2, '0')}</span>
            <span className="slide-copy"><strong>{slide.title || 'Başlıksız'}</strong><small>{slide.level === 'chapter' ? 'Bölüm kapağı' : `${slide.bullets.length} madde`}</small></span>
            <span className="slide-actions">
              <button onClick={(event) => { event.stopPropagation(); moveSlide(index, -1) }} aria-label="Yukarı taşı"><ArrowUp size={14} /></button>
              <button onClick={(event) => { event.stopPropagation(); moveSlide(index, 1) }} aria-label="Aşağı taşı"><ArrowDown size={14} /></button>
              <button className="danger" onClick={(event) => { event.stopPropagation(); deleteSlide(index) }} aria-label="Sil"><Trash2 size={14} /></button>
            </span>
          </article>)}
        </div>}
      </section>

      <section className="panel editor-panel">
        <div className="panel-toolbar"><div><span className="kicker">DÜZENLEYİCİ</span><h3>{draft ? `Slayt ${(selectedSlide || 0) + 1}` : 'Bir slayt seç'}</h3></div>{draft && <button className="button compact" onClick={saveDraft}><Save size={15} /> Kaydet</button>}</div>
        {!draft ? <EmptyState icon={Settings2} title="Ayrıntıları düzenle">Akıştan bir slayt seçerek başlık, maddeler ve anlatım metnini değiştirebilirsin.</EmptyState> : <div className="editor-form">
          <label className="field"><span>Başlık</span><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
          <label className="field"><span>Tür</span><select value={draft.level} onChange={(e) => setDraft({ ...draft, level: e.target.value })}><option value="topic">Konu slaytı</option><option value="chapter">Bölüm kapağı</option></select></label>
          <label className="field"><span>Maddeler <em>satır başına bir tane</em></span><textarea rows="5" value={draft.bullets.join('\n')} onChange={(e) => setDraft({ ...draft, bullets: e.target.value.split('\n') })} /></label>
          <label className="field"><span>Kod</span><textarea className="code-input" rows="5" value={draft.code || ''} onChange={(e) => setDraft({ ...draft, code: e.target.value || null })} placeholder="İsteğe bağlı" /></label>
          <label className="field"><span>Tam anlatım metni</span><textarea rows="11" value={draft.narration} onChange={(e) => setDraft({ ...draft, narration: e.target.value })} /></label>
          <div className="editor-meta"><Clock3 size={14} /><span>Yaklaşık {Math.max(1, Math.round((draft.narration || '').split(/\s+/).length / 2.2))} sn anlatım</span></div>
        </div>}
      </section>
    </div>
  )

  const renderVideo = () => (
    <div className="video-layout">
      <section className="panel preview-panel">
        <div className="panel-toolbar"><div><span className="kicker">CANVAS</span><h3>Slayt önizleme</h3></div><button className="button compact" onClick={preview} disabled={!project}><RefreshCw size={15} /> Yenile</button></div>
        <div className="preview-canvas">
          {previewUrl ? <img src={previewUrl} alt="Video slaytı önizlemesi" /> : <div className="preview-placeholder"><MonitorPlay size={38} /><span>Temayı seçip önizlemeyi oluştur</span></div>}
          <span className="resolution-badge">1920 × 1080</span>
        </div>
        {project?.outputs?.video && <video className="result-video" controls src={`/api/projects/${project.id}/output/video`} />}
      </section>

      <section className="panel studio-controls">
        <div className="panel-toolbar"><div><span className="kicker">GÖRSEL SİSTEM</span><h3>Tema ve hareket</h3></div></div>
        <div className="theme-grid">
          {bootstrap.themes.map((theme) => <button key={theme.id} className={`theme-card ${video.theme === theme.id ? 'active' : ''}`} onClick={() => setVideo({ ...video, theme: theme.id })}>
            <span className="theme-swatch" style={{ background: `linear-gradient(135deg, ${themeSwatches[theme.id]?.[0]}, ${themeSwatches[theme.id]?.[1]})` }}><span /></span>
            <span><strong>{theme.label}</strong><small>{theme.id === 'auto' ? 'İçeriğe göre değişir' : theme.id}</small></span>
            {video.theme === theme.id && <CheckCircle2 size={17} />}
          </button>)}
        </div>
        <div className="toggle-stack">
          <Toggle checked={video.subtitles} onChange={(value) => setVideo({ ...video, subtitles: value })} label="Akıllı altyazı" hint="Kod slaytlarında otomatik gizlenir" />
          <Toggle checked={video.fadeTransitions} onChange={(value) => setVideo({ ...video, fadeTransitions: value })} label="Yumuşak geçişler" hint="Slaytlar arasında fade" />
          <Toggle checked={video.kenBurns} onChange={(value) => setVideo({ ...video, kenBurns: value })} label="Kamera hareketi" hint="Hafif Ken Burns yakınlaştırması" />
        </div>
      </section>

      <section className="panel voice-panel">
        <div className="panel-toolbar"><div><span className="kicker">SESLENDİRME</span><h3>Ses karakteri</h3></div><Mic2 size={19} /></div>
        <div className="editor-form">
          <label className="field"><span>Sağlayıcı</span><select value={video.ttsProvider} onChange={(e) => setVideo({ ...video, ttsProvider: e.target.value })}>{bootstrap.ttsProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.id} — {provider.label.split('(')[0]}</option>)}</select></label>
          <label className="field"><span>Ses</span><select value={video.voice} onChange={(e) => setVideo({ ...video, voice: e.target.value })}>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}</select></label>
          <label className="field"><span>Konuşma hızı</span><select value={video.rate} onChange={(e) => setVideo({ ...video, rate: e.target.value })}>{['-20%', '-10%', '+0%', '+10%', '+20%'].map((rate) => <option key={rate}>{rate}</option>)}</select></label>
          {video.ttsProvider === 'elevenlabs' && <label className="field"><span>ElevenLabs API anahtarı</span><input type="password" value={video.elevenlabsKey} onChange={(e) => setVideo({ ...video, elevenlabsKey: e.target.value })} /></label>}
        </div>
        <button className="button primary render-button" onClick={render} disabled={!project?.slides.length || Boolean(activeJob)}><Clapperboard size={17} /> {activeJob?.type === 'video' ? 'Render sürüyor…' : 'Videoyu oluştur'}</button>
        <ProgressStrip job={activeJob?.type === 'video' || jobState?.kind === 'video' ? jobState : null} />
        {project?.outputs?.video && <div className="output-actions"><a className="button ghost" href={`/api/projects/${project.id}/output/video`}><MonitorPlay size={16} /> Videoyu aç</a><button className="button ghost" onClick={() => api(`/api/projects/${project.id}/open-output`, { method: 'POST' })}><FolderOpen size={16} /> Klasörü aç</button></div>}
      </section>
    </div>
  )

  if (!bootstrap) return <div className="app-loading"><AmbientBackdrop />{bootstrapError ? <><span className="brand-mark error-mark"><CircleAlert /></span><h1>Yerel servis bağlantısı yok</h1><p>{bootstrapError}</p><button className="button primary" onClick={() => setBootstrapRetry((value) => value + 1)}><RefreshCw size={16} /> Yeniden bağlan</button></> : <><span className="brand-mark"><Sparkles /></span><h1>Ders Stüdyosu</h1><p>Çalışma alanın hazırlanıyor…</p><LoaderCircle className="spin" /></>}</div>

  return (
    <div className="app-shell">
      <AmbientBackdrop disabled={video.ttsProvider === 'coqui'} />
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Sparkles size={20} /></span><span><strong>Ders Stüdyosu</strong><small>LOCAL CREATION SUITE</small></span></div>
        <nav className="step-nav" aria-label="Üretim adımları">
          {steps.map((item) => {
            const Icon = item.icon
            const active = stage === item.id
            const done = (item.id === 'source' && project) || (item.id === 'script' && project?.slides.length) || (item.id === 'video' && project?.outputs?.video)
            return <button key={item.id} className={`${active ? 'active' : ''} ${done ? 'done' : ''}`} onClick={() => setStage(item.id)} disabled={item.id !== 'source' && !project}>
              <span className="nav-number">{done ? <Check size={14} /> : item.eyebrow}</span><Icon size={18} /><span>{item.label}</span><ChevronRight size={15} />
            </button>
          })}
        </nav>
        <div className="project-card">
          <div className="project-card-heading"><span>AKTİF PROJE</span><span>{completion}%</span></div>
          {project ? <><strong>{project.id}</strong><small>{project.sections.length} bölüm · {project.slides.length} slayt</small><div className="mini-progress"><span style={{ width: `${completion}%` }} /></div></> : <p>Yeni bir kaynak ekleyerek başla.</p>}
        </div>
        {bootstrap.projects.length > 0 && <label className="project-picker"><span>Son projeler</span><select value={project?.id || ''} onChange={(event) => event.target.value && loadProject(event.target.value)}><option value="">Proje seç…</option>{bootstrap.projects.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>}
        <div className="local-badge"><span className="status-dot" /><span><strong>Yerel ve hazır</strong><small>Veriler bu bilgisayarda</small></span></div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div><span className="topbar-eyebrow">{steps.find((item) => item.id === stage)?.eyebrow} / PIPELINE</span><h1>{stage === 'source' ? 'İçeriği seç ve yapılandır' : stage === 'script' ? 'Anlatıyı tasarla' : 'Dersi yayına hazırla'}</h1></div>
          <div className="topbar-meta"><span><span className="status-dot" /> Pipeline bağlı</span><button className="icon-button" title="Ayarlar"><Settings2 size={18} /></button></div>
        </header>
        <div className="stage-content">
          {stage === 'source' && renderSource()}
          {stage === 'script' && renderScript()}
          {stage === 'video' && renderVideo()}
        </div>
        <footer className="stage-footer">
          <button className="button quiet" disabled={stage === 'source'} onClick={() => setStage(stage === 'video' ? 'script' : 'source')}><ArrowLeft size={16} /> Önceki adım</button>
          <span>Her değişiklik otomatik olarak proje klasörüne kaydedilir.</span>
          <button className="button quiet" disabled={stage === 'video' || (stage === 'source' && !project)} onClick={() => setStage(stage === 'source' ? 'script' : 'video')}>Sonraki adım <ArrowRight size={16} /></button>
        </footer>
      </main>

      {toast && <div className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button onClick={() => setToast(null)}><X size={16} /></button></div>}
    </div>
  )
}
