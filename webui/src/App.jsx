import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
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
  Image as ImageIcon,
  Layers3,
  ListOrdered,
  Wallet,
  LoaderCircle,
  Mic2,
  MonitorPlay,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Settings2,
  Sparkles,
  Trash2,
  TriangleAlert,
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

const SNAPSHOT_REASON_LABELS = {
  'before-regenerate': 'yeniden üretim öncesi',
  'before-restore': 'geri yükleme öncesi',
}

const QUEUE_STATUS_LABELS = {
  queued: 'Sırada',
  parsing: 'Ayrıştırılıyor',
  generating: 'Anlatı üretiliyor',
  rendering: 'Render ediliyor',
  complete: 'Tamamlandı',
  failed: 'Başarısız',
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

function Toggle({ checked, onChange, label, hint, disabled = false, wide = false }) {
  return (
    <label className={`toggle-row${wide ? ' wide' : ''}`}>
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
  // 'dashboard' (proje seç/oluştur) | 'hub' (seçili proje için modül seç) | 'video' (bugüne kadarki tüm akış)
  const [view, setView] = useState('dashboard')
  const [stage, setStage] = useState('source')
  const [selectedSections, setSelectedSections] = useState(new Set())
  const [selectedSlide, setSelectedSlide] = useState(null)
  const [draft, setDraft] = useState(null)
  const [sourcePath, setSourcePath] = useState('')
  const [pageMode, setPageMode] = useState(false)
  const [visionEnrich, setVisionEnrich] = useState(false)
  const [visionApiKey, setVisionApiKey] = useState('')
  const [extractDiagrams, setExtractDiagrams] = useState(false)
  const [isDropping, setIsDropping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)
  const [activeJob, setActiveJob] = useState(null)
  const [jobState, setJobState] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [voices, setVoices] = useState([])
  const [renderEstimate, setRenderEstimate] = useState(null)
  const [chapters, setChapters] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [showPronunciation, setShowPronunciation] = useState(false)
  const [showQueue, setShowQueue] = useState(false)
  const [showCost, setShowCost] = useState(false)
  const [costSummary, setCostSummary] = useState(null)
  const [queueItems, setQueueItems] = useState([])
  const [queuePath, setQueuePath] = useState('')
  const [queueBusy, setQueueBusy] = useState(false)
  const [flashcardDecks, setFlashcardDecks] = useState([])
  const [activeDeck, setActiveDeck] = useState(null)
  const [newDeckName, setNewDeckName] = useState('')
  const [deckKind, setDeckKind] = useState('static')
  const [deckCount, setDeckCount] = useState('')
  const [deckFocusPrompt, setDeckFocusPrompt] = useState('')
  const [flashcardBusy, setFlashcardBusy] = useState(false)
  const [reviewQueue, setReviewQueue] = useState([])
  const [reviewCard, setReviewCard] = useState(null)
  const [reviewRevealed, setReviewRevealed] = useState(false)
  const [reviewedCount, setReviewedCount] = useState(0)
  const [pronunciationEntries, setPronunciationEntries] = useState([])
  const [newTerm, setNewTerm] = useState('')
  const [newPhonetic, setNewPhonetic] = useState('')
  const [previewText, setPreviewText] = useState('switch yapısını burada anlatıyoruz')
  const [previewResult, setPreviewResult] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [draggedSlide, setDraggedSlide] = useState(null)
  // Kaydedilmiş oturum (proje/aşama/aktif iş) geri yüklenene kadar aşağıdaki
  // patchSession efektlerinin state'in başlangıç değerleriyle (ör. stage='source')
  // localStorage'ı ezmesini engeller — aksi halde asenkron restore daha kayıtlı
  // değeri okuyamadan üzerine yazılırdı.
  const sessionRestoredRef = useRef(false)

  const [llm, setLlm] = useState({
    provider: 'agent', apiKey: '', geminiModel: '', openaiModel: '', openaiEndpoint: '',
    agentCommand: '', reuseSession: true, maxSections: 4, timeout: 900,
    singleRequest: false, resumeCompleted: true, style: '', insertMode: 'append',
    durationLimitEnabled: false, targetDurationMinutes: 60,
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
              // Eski oturumlarda (bu özellik eklenmeden önce) view kaydı yok —
              // aktif bir proje varsa geri döndüğümüzde doğrudan video modülüne
              // (kaldığı yere) dönmek, panele atmaktan daha az sürpriz olur.
              setView(session.view === 'hub' ? 'hub' : session.view === 'anki' ? 'anki' : 'video')
              if (session.activeJob) {
                api(`/api/jobs/${session.activeJob.id}`)
                  .then((job) => {
                    if (job.status === 'running' || job.status === 'queued') {
                      setActiveJob(session.activeJob)
                      setJobState(job)
                    } else {
                      // Uygulama/API en son bu iş çalışırken kapanmış olabilir —
                      // PersistentJobStore böyle işleri anlaşılır bir hataya çevirir
                      // (bkz. studio_web/job_store.py); kullanıcıya sessizce
                      // kaybolmasın diye burada gösteriyoruz.
                      if (job.status === 'failed') {
                        setToast({ type: 'error', text: job.error || job.message })
                      }
                      patchSession({ activeJob: null })
                    }
                  })
                  .catch(() => patchSession({ activeJob: null }))
                  .finally(() => { sessionRestoredRef.current = true })
              } else {
                sessionRestoredRef.current = true
              }
            })
            .catch(() => {
              patchSession({ projectId: null, activeJob: null })
              sessionRestoredRef.current = true
            })
        } else {
          sessionRestoredRef.current = true
        }
      })
      .catch((error) => setBootstrapError(error.message))
  }, [bootstrapRetry])

  useEffect(() => {
    if (sessionRestoredRef.current && project?.id) patchSession({ projectId: project.id })
  }, [project?.id])

  useEffect(() => {
    if (sessionRestoredRef.current) patchSession({ stage })
  }, [stage])

  useEffect(() => {
    if (sessionRestoredRef.current) patchSession({ view })
  }, [view])

  useEffect(() => {
    if (sessionRestoredRef.current) patchSession({ activeJob: activeJob || null })
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
    if (!project?.id || !project?.slides?.length) {
      setRenderEstimate(null)
      return
    }
    api(`/api/projects/${project.id}/render-estimate?provider=${encodeURIComponent(video.ttsProvider)}`)
      .then(setRenderEstimate)
      .catch(() => setRenderEstimate(null))
  }, [project?.id, project?.slides?.length, project?.assets?.canonicalSegmentCount, video.ttsProvider])

  const refreshChapters = () => {
    if (!project?.id) return
    api(`/api/projects/${project.id}/chapters`).then(setChapters).catch(() => setChapters(null))
  }

  useEffect(() => {
    if (!project?.id || !project?.slides?.length) {
      setChapters(null)
      return
    }
    refreshChapters()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.slides?.length, project?.assets?.canonicalSegmentCount])

  const exportChapters = async () => {
    if (!project || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Bölümler hazırlanıyor' })
    try {
      const response = await api(`/api/projects/${project.id}/chapters/export`, { method: 'POST' })
      setActiveJob({ id: response.jobId, type: 'chapters' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const refreshSnapshots = () => {
    if (!project?.id) return
    api(`/api/projects/${project.id}/snapshots`)
      .then(({ snapshots: list }) => setSnapshots(list))
      .catch(() => setSnapshots([]))
  }

  useEffect(() => {
    refreshSnapshots()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id])

  const restoreSnapshotVersion = async (filename) => {
    if (!project || activeJob) return
    try {
      const data = await api(`/api/projects/${project.id}/snapshots/${encodeURIComponent(filename)}/restore`, {
        method: 'POST',
      })
      setProject((current) => current ? { ...current, slides: data.slides, quality: data.quality, assets: data.assets } : current)
      setSnapshots(data.snapshots)
      setToast({ type: 'success', text: 'Önceki sürüm geri yüklendi (mevcut hâl de otomatik saklandı).' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const loadPronunciation = () => {
    api('/api/pronunciation').then(({ entries }) => setPronunciationEntries(entries)).catch(() => {})
  }

  useEffect(() => {
    if (showPronunciation) loadPronunciation()
  }, [showPronunciation])

  const refreshQueue = () => {
    api('/api/queue').then(({ items }) => setQueueItems(items)).catch(() => {})
  }

  useEffect(() => {
    if (!showQueue) return
    refreshQueue()
    const timer = setInterval(refreshQueue, 2000)
    return () => clearInterval(timer)
  }, [showQueue])

  useEffect(() => {
    if (showCost) api('/api/cost-summary').then(setCostSummary).catch(() => {})
  }, [showCost])

  useEffect(() => {
    if (view === 'anki' && project) loadFlashcardDecks()
    if (view !== 'anki') {
      setActiveDeck(null)
      endReviewSession()
    }
  }, [view, project?.id])

  const addToQueue = async () => {
    if (!queuePath.trim()) return
    setQueueBusy(true)
    try {
      await api('/api/queue', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sourcePath: queuePath.trim(),
          llmSettings: llm,
          videoSettings: {
            ttsProvider: video.ttsProvider, voice: video.voice, rate: video.rate,
            elevenlabsKey: video.elevenlabsKey, subtitles: video.subtitles,
            fadeTransitions: video.fadeTransitions, kenBurns: video.kenBurns,
            theme: video.theme,
          },
        }),
      })
      setQueuePath('')
      setToast({ type: 'success', text: 'Kuyruğa eklendi.' })
      refreshQueue()
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setQueueBusy(false)
    }
  }

  const removeQueueItem = async (id) => {
    try {
      await api(`/api/queue/${id}`, { method: 'DELETE' })
      refreshQueue()
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const loadFlashcardDecks = async () => {
    if (!project) return
    setFlashcardBusy(true)
    try {
      const data = await api(`/api/projects/${project.id}/flashcards/decks`)
      setFlashcardDecks(data.decks)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setFlashcardBusy(false)
    }
  }

  const createFlashcardDeck = async () => {
    if (!project || !newDeckName.trim()) return
    setFlashcardBusy(true)
    if (deckKind === 'llm') {
      try {
        const response = await api(`/api/projects/${project.id}/flashcards/decks`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: newDeckName.trim(), kind: 'llm',
            provider: llm.provider, apiKey: llm.apiKey, agentCommand: llm.agentCommand,
            geminiModel: llm.geminiModel, openaiEndpoint: llm.openaiEndpoint,
            openaiModel: llm.openaiModel, timeout: llm.timeout,
            count: deckCount.trim() ? Number(deckCount) : null,
            focusPrompt: deckFocusPrompt.trim(),
          }),
        })
        setActiveJob({ id: response.jobId, type: 'flashcards' })
      } catch (error) {
        setToast({ type: 'error', text: error.message })
        setFlashcardBusy(false)
      }
      return
    }
    try {
      const deck = await api(`/api/projects/${project.id}/flashcards/decks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newDeckName.trim(), kind: 'static' }),
      })
      setNewDeckName('')
      setFlashcardDecks((current) => [...current, deck])
      setActiveDeck(deck)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setFlashcardBusy(false)
    }
  }

  const openFlashcardDeck = async (deckId) => {
    setFlashcardBusy(true)
    try {
      const deck = await api(`/api/projects/${project.id}/flashcards/decks/${deckId}`)
      setActiveDeck(deck)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setFlashcardBusy(false)
    }
  }

  const closeFlashcardDeck = () => {
    setActiveDeck(null)
    endReviewSession()
    loadFlashcardDecks()
  }

  const regenerateActiveDeck = async () => {
    if (!project || !activeDeck) return
    setFlashcardBusy(true)
    try {
      const deck = await api(`/api/projects/${project.id}/flashcards/decks/${activeDeck.id}/generate`, { method: 'POST' })
      setActiveDeck(deck)
      setToast({ type: 'success', text: 'Deste güncel slaytlardan yeniden oluşturuldu.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setFlashcardBusy(false)
    }
  }

  const deleteFlashcardDeck = async (deckId) => {
    if (!project) return
    try {
      await api(`/api/projects/${project.id}/flashcards/decks/${deckId}`, { method: 'DELETE' })
      setFlashcardDecks((current) => current.filter((d) => d.id !== deckId))
      if (activeDeck?.id === deckId) closeFlashcardDeck()
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const startReviewSession = () => {
    if (!activeDeck) return
    const now = Date.now() / 1000
    const due = activeDeck.cards
      .filter((c) => !c.suspended && c.dueAt <= now)
      .sort((a, b) => a.dueAt - b.dueAt)
    if (!due.length) return
    setReviewQueue(due.slice(1))
    setReviewCard(due[0])
    setReviewRevealed(false)
    setReviewedCount(0)
  }

  const endReviewSession = () => {
    setReviewQueue([])
    setReviewCard(null)
    setReviewRevealed(false)
  }

  const submitReviewRating = async (rating) => {
    if (!project || !activeDeck || !reviewCard) return
    try {
      const data = await api(`/api/projects/${project.id}/flashcards/decks/${activeDeck.id}/cards/${reviewCard.id}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating }),
      })
      setActiveDeck((current) => ({
        ...current,
        cards: current.cards.map((c) => (c.id === data.card.id ? data.card : c)),
        summary: data.summary,
      }))
      setReviewedCount((count) => count + 1)
      if (reviewQueue.length) {
        setReviewCard(reviewQueue[0])
        setReviewQueue(reviewQueue.slice(1))
        setReviewRevealed(false)
      } else {
        endReviewSession()
      }
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const toggleCardSuspend = async (card) => {
    if (!project || !activeDeck) return
    try {
      const data = await api(`/api/projects/${project.id}/flashcards/decks/${activeDeck.id}/cards/${card.id}/suspend`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suspended: !card.suspended }),
      })
      setActiveDeck((current) => ({
        ...current,
        cards: current.cards.map((c) => (c.id === data.card.id ? data.card : c)),
        summary: data.summary,
      }))
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const savePronunciationOverrides = async (overrides, successMessage) => {
    try {
      const data = await api('/api/pronunciation', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ overrides }),
      })
      setPronunciationEntries(data.entries)
      if (successMessage) setToast({ type: 'success', text: successMessage })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const addOrUpdatePronunciationTerm = () => {
    const term = newTerm.trim().toLowerCase()
    const phonetic = newPhonetic.trim()
    if (!term || !phonetic) return
    const overrides = {}
    pronunciationEntries.filter((e) => e.isOverride).forEach((e) => { overrides[e.term] = e.phonetic })
    overrides[term] = phonetic
    savePronunciationOverrides(overrides, `"${term}" kaydedildi.`)
    setNewTerm(''); setNewPhonetic('')
  }

  const removePronunciationOverride = (term) => {
    const overrides = {}
    pronunciationEntries.filter((e) => e.isOverride && e.term !== term).forEach((e) => { overrides[e.term] = e.phonetic })
    savePronunciationOverrides(overrides, `"${term}" için özel telaffuz kaldırıldı.`)
  }

  const runPronunciationPreview = async () => {
    if (!previewText.trim() || previewBusy) return
    setPreviewBusy(true)
    try {
      const data = await api('/api/pronunciation/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: previewText }),
      })
      setPreviewResult(data)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setPreviewBusy(false)
    }
  }

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
            quality: job.result.quality || current.quality,
          } : current)
        }
        if (job.status === 'complete') {
          if (activeJob.type === 'script') {
            setProject((current) => ({
              ...current,
              slides: job.result.slides,
              generation: job.result.generation || current.generation,
              quality: job.result.quality || current.quality,
            }))
            const skipped = job.result.skippedSectionCount || 0
            setToast({ type: 'success', text: skipped
              ? `${job.result.generatedCount} yeni slayt üretildi; daha önce biten ${skipped} bölüm atlandı.`
              : `${job.result.generatedCount} yeni slayt üretildi.` })
          } else if (activeJob.type === 'regenerate') {
            setProject((current) => current ? {
              ...current,
              slides: job.result.slides,
              quality: job.result.quality || current.quality,
              assets: job.result.assets || current.assets,
            } : current)
            setToast({ type: 'success', text: 'Slayt yeniden üretildi.' })
            refreshSnapshots()
          } else if (activeJob.type === 'chapters') {
            setToast({ type: 'success', text: `${job.result.chapters.length} bölüm dosyası hazır.` })
            if (project?.id) {
              api(`/api/projects/${project.id}/chapters`).then(setChapters).catch(() => {})
            }
          } else if (activeJob.type === 'flashcards') {
            const deck = job.result.deck
            setFlashcardDecks((current) => [...current, deck])
            setActiveDeck(deck)
            setNewDeckName('')
            setDeckCount('')
            setDeckFocusPrompt('')
            setFlashcardBusy(false)
            setToast({ type: 'success', text: `"${deck.name}" destesi ${deck.cards.length} kartla oluşturuldu.` })
          } else {
            setProject((current) => ({ ...current, outputs: { video: true, audio: true } }))
            setToast({ type: 'success', text: 'Video ve ses çıktısı hazır.' })
            // Render bittiğinde ses/segment sayıları değişti; sağlık kartının
            // güncel kalması için proje verisini (assets denetimi dahil) tazele.
            if (project?.id) {
              api(`/api/projects/${project.id}`)
                .then((data) => setProject((current) => current ? { ...current, assets: data.assets } : current))
                .catch(() => {})
            }
          }
          setActiveJob(null)
        } else if (job.status === 'failed') {
          setToast({ type: 'error', text: job.error })
          if (activeJob.type === 'flashcards') setFlashcardBusy(false)
          setActiveJob(null)
        }
      } catch (error) {
        if (!stopped) {
          const message = `Yerel API bağlantısı koptu: ${error.message}`
          setJobState({ status: 'failed', kind: activeJob.type, progress: 0, error: message })
          setToast({ type: 'error', text: message })
          if (activeJob.type === 'flashcards') setFlashcardBusy(false)
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
  const quality = project?.quality
  const qualityBlocked = Boolean(project?.slides?.length && quality && !quality.renderAllowed)
  const assets = project?.assets
  const selectedQualityIssues = selectedSlide === null
    ? []
    : (quality?.issues || []).filter((issue) => issue.slideIndex === selectedSlide)

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

  // Paneldeyken (henüz bir proje seçilmemişken) yeni bir kaynak ayrıştırılırsa
  // doğrudan o projenin hub'ına geç; video modülünün KENDİ Kaynak adımından
  // (view zaten 'video') tetiklenmişse mevcut davranış (aynı ekranda kal) korunur.
  const goToHubIfComingFromDashboard = () => {
    setView((current) => (current === 'dashboard' ? 'hub' : current))
  }

  const openProjectHub = async (id) => {
    setBusy(true)
    try {
      const data = await api(`/api/projects/${id}`)
      applyProject(data)
      setView('hub')
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  const uploadSource = async (file) => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    form.append('page_mode', pageMode ? 'true' : 'false')
    form.append('vision_enrich', visionEnrich ? 'true' : 'false')
    form.append('vision_api_key', visionApiKey.trim())
    form.append('extract_diagrams', extractDiagrams ? 'true' : 'false')
    setBusy(true)
    try {
      const data = await api('/api/source/upload', { method: 'POST', body: form })
      applyProject(data)
      goToHubIfComingFromDashboard()
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
        body: JSON.stringify({
          path: sourcePath.trim(), pageMode, visionEnrich, visionApiKey: visionApiKey.trim(), extractDiagrams,
        }),
      })
      applyProject(data)
      goToHubIfComingFromDashboard()
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
      const data = await api(`/api/projects/${project.id}/slides`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slides }),
      })
      setProject((current) => current ? {
        ...current,
        quality: data.quality,
        assets: data.assets || current.assets,
      } : current)
      if (message) setToast({ type: 'success', text: message })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const saveDraft = () => {
    if (selectedSlide === null || !draft) return
    const next = [...project.slides]
    next[selectedSlide] = { ...draft, bullets: draft.bullets.filter(Boolean), manuallyEdited: true }
    persistSlides(next, 'Slayt kaydedildi.')
  }

  const addSlide = () => {
    const at = selectedSlide === null ? project.slides.length : selectedSlide + 1
    const next = [...project.slides]
    next.splice(at, 0, {
      title: 'Yeni slayt', bullets: [], code: null, narration: '', level: 'topic',
      sourceSectionIds: [], sourceTitles: [], manuallyEdited: true,
    })
    setSelectedSlide(at)
    persistSlides(next, 'Yeni slayt eklendi.')
  }

  const regenerateSlide = async () => {
    if (selectedSlide === null || !project || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Yeniden üretim hazırlanıyor' })
    try {
      const response = await api(`/api/projects/${project.id}/slides/${selectedSlide}/regenerate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: llm.provider, apiKey: llm.apiKey, style: llm.style,
          agentCommand: llm.agentCommand, geminiModel: llm.geminiModel,
          openaiEndpoint: llm.openaiEndpoint, openaiModel: llm.openaiModel, timeout: llm.timeout,
        }),
      })
      setActiveJob({ id: response.jobId, type: 'regenerate' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
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

  const refreshQuality = async () => {
    if (!project || activeJob) return
    try {
      const data = await api(`/api/projects/${project.id}/quality`, { method: 'POST' })
      setProject((current) => current ? { ...current, quality: data.quality } : current)
      setToast({ type: 'success', text: 'Anlatı kalite denetimi yenilendi.' })
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

  const renderSourceIngest = () => (
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
      <Toggle
        wide
        checked={pageMode}
        onChange={setPageMode}
        label="Sayfaları birebir slayt olarak kullan (yalnızca PDF)"
        hint={pageMode
          ? 'Her PDF sayfası olduğu gibi bir slayt görseli olur (bizim tema/başlık/madde tasarımımız kullanılmaz); yapay zeka sadece o sayfa için anlatım metni yazar. PDF olmayan bir dosyada bu seçenek hataya yol açar.'
          : 'Kapalıyken içerik her zamanki gibi kendi slayt tasarımımızla (tema, başlık, madde) yeniden oluşturulur.'}
      />
      <Toggle
        wide
        checked={visionEnrich}
        onChange={setVisionEnrich}
        label="Görsel anlama kullan (yalnızca PDF)"
        hint={visionEnrich
          ? 'Metni çok az/hiç olmayan sayfalar (muhtemelen diyagram/ekran görüntüsü) Gemini\'ye gönderilip bir açıklama alınır — anlatım sağlayıcın ne olursa olsun (multimodal olması gerekmez), o metni kullanır. Sayfa/görsel başına ek bir API çağrısı, dolayısıyla ek maliyet demektir.'
          : 'Kapalıyken görsel-ağırlıklı sayfalar normal modda atlanır, sayfa modunda ise metinsiz bırakılır.'}
      />
      {visionEnrich && !bootstrap.keysConfigured.gemini && (
        <label className="field wide">
          <span>Gemini API anahtarı (sadece görsel anlama için)</span>
          <input type="password" value={visionApiKey} onChange={(e) => setVisionApiKey(e.target.value)} placeholder="Anlatım sağlayıcından bağımsız, ücretsiz alınabilir" />
        </label>
      )}
      <Toggle
        wide
        checked={extractDiagrams}
        onChange={setExtractDiagrams}
        label="Diyagram/görsel çıkar (yalnızca PDF)"
        hint={extractDiagrams
          ? 'Sayfadaki gerçek, gömülü bir görsel (diyagram/grafik) varsa olduğu gibi çıkarılıp o sayfanın slaydına, maddelerin yanına yerleştirilir — hiçbir şey üretilmez/yapay zekaya çizdirilmez, kaynaktaki piksellerin birebir kopyası kullanılır. Ücretsiz, API gerektirmez.'
          : 'Kapalıyken slaytlar her zamanki gibi sadece metinle (başlık/madde) oluşturulur.'}
      />
    </section>
  )

  const renderSource = () => (
    <div className="stage-grid source-grid">
      {renderSourceIngest()}

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
          <label className="field"><span>Bir LLM çağrısındaki kaynak bölümü</span><input disabled={llm.singleRequest || project?.pageMode} type="number" min="1" max="20" value={project?.pageMode ? 1 : llm.maxSections} onChange={(e) => setLlm({ ...llm, maxSections: Number(e.target.value) })} /><small>{project?.pageMode ? 'Sayfa modunda her çağrı tam olarak 1 sayfa alır (değiştirilemez).' : 'Üretilen slayt sayısı değil; PDF/PPT içinden aynı çağrıya konan bölüm sayısıdır.'}</small></label>
          <label className="field"><span>Timeout</span><div className="input-suffix"><input type="number" min="60" step="60" value={llm.timeout} onChange={(e) => setLlm({ ...llm, timeout: Number(e.target.value) })} /><b>sn</b></div></label>
          <label className="field wide"><span>Üslup yönlendirmesi</span><textarea rows="2" value={llm.style} onChange={(e) => setLlm({ ...llm, style: e.target.value })} placeholder="Örn. kavramsal, sakin, klinik örneklerle…" /></label>
          <label className="field"><span>Yeni slaytların yeri</span><select value={llm.insertMode} onChange={(e) => setLlm({ ...llm, insertMode: e.target.value })}><option value="append">Listenin sonu</option><option value="after">Seçili slayttan sonra</option></select></label>
          <Toggle checked={llm.singleRequest} disabled={!oneShotSafe || project?.pageMode} onChange={(value) => setLlm({ ...llm, singleRequest: value })} label="Tek istekte gönder" hint={project?.pageMode ? 'Sayfa modunda kullanılamaz — her sayfa ayrı işlenmeli.' : oneShotSafe ? 'En fazla 12 bölüm / 24.000 karakter' : `${selectedSections.size} bölüm ve ${selectedSourceCharacters.toLocaleString('tr-TR')} karakter: güvenli sınırın üzerinde`} />
          {project?.pageMode && (
            <div className="session-note wide"><ImageIcon size={14} /><span>Sayfa modu aktif: her slaytın görseli, kaynak PDF sayfasının kendisi olacak (bizim tema tasarımımız kullanılmayacak). Yapay zeka sadece o sayfa için anlatım metni üretiyor.</span></div>
          )}
          <Toggle
            wide
            checked={llm.durationLimitEnabled}
            onChange={(value) => setLlm({ ...llm, durationLimitEnabled: value })}
            label="Süre hedefi kullan"
            hint={llm.durationLimitEnabled
              ? 'Her parçaya, kaynaktaki payıyla orantılı bir süre/kelime bütçesi verilir (ör. kaynağın %5\'ini oluşturan bölüme hedefin ~%5\'i düşer).'
              : 'Kapalıyken mevcut davranış aynen sürer: parçalar süre sınırı olmadan, olabildiğince öğretici anlatılır.'}
          />
          <label className="field"><span>Hedef toplam süre</span><div className="input-suffix"><input disabled={!llm.durationLimitEnabled} type="number" min="1" max="1000" value={llm.targetDurationMinutes} onChange={(e) => setLlm({ ...llm, targetDurationMinutes: Number(e.target.value) })} /><b>dk</b></div><small>{llm.durationLimitEnabled ? `≈ ${Math.round(llm.targetDurationMinutes * 132).toLocaleString('tr-TR')} kelimelik toplam anlatım bütçesi` : 'Süre hedefi kapalı'}</small></label>
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
        {project?.slides.length > 0 && <div className={`quality-card ${quality?.status || 'unknown'}`}>
          <span className="quality-icon">{quality?.status === 'passed' ? <ShieldCheck size={19} /> : <TriangleAlert size={19} />}</span>
          <span>
            <strong>{quality?.status === 'passed' ? 'Anlatı temiz' : quality?.status === 'blocked' ? 'Düzeltme gerekli' : 'Gözden geçir'}</strong>
            <small>{quality?.score ?? '—'}/100 · {quality?.errorCount || 0} kritik · {quality?.warningCount || 0} uyarı</small>
          </span>
          <button type="button" onClick={refreshQuality} disabled={Boolean(activeJob)} aria-label="Kalite denetimini yenile"><RefreshCw size={13} /></button>
        </div>}
        {snapshots.length > 0 && <div className="chapter-list snapshot-list">
          {snapshots.map((snap) => (
            <div key={snap.filename} className="chapter-item">
              <span>{SNAPSHOT_REASON_LABELS[snap.reason] || snap.reason} <small>({snap.slideCount ?? '?'} slayt, {new Date(snap.savedAt).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })})</small></span>
              <a onClick={(event) => { event.preventDefault(); restoreSnapshotVersion(snap.filename) }} href="#restore">Geri Yükle</a>
            </div>
          ))}
        </div>}
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
          {draft.sourceSectionIds?.length > 0 ? <div className="source-row">
            <span className="source-label"><Layers3 size={13} /> Kaynak: {draft.sourceTitles?.join(', ') || `${draft.sourceSectionIds.length} bölüm`}</span>
            <button type="button" className="button ghost compact" onClick={regenerateSlide} disabled={Boolean(activeJob)}>
              <RefreshCw size={13} /> Bu slaytı yeniden üret
            </button>
          </div> : <div className="source-row muted"><span className="source-label">Elle eklendi · kaynağı yok</span></div>}
          <ProgressStrip job={activeJob?.type === 'regenerate' || jobState?.kind === 'regenerate' ? jobState : null} />
          <label className="field"><span>Başlık</span><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
          <label className="field"><span>Tür</span><select value={draft.level} onChange={(e) => setDraft({ ...draft, level: e.target.value })}><option value="topic">Konu slaytı</option><option value="chapter">Bölüm kapağı</option></select></label>
          <label className="field"><span>Maddeler <em>satır başına bir tane</em></span><textarea rows="5" value={draft.bullets.join('\n')} onChange={(e) => setDraft({ ...draft, bullets: e.target.value.split('\n') })} /></label>
          <label className="field"><span>Kod</span><textarea className="code-input" rows="5" value={draft.code || ''} onChange={(e) => setDraft({ ...draft, code: e.target.value || null })} placeholder="İsteğe bağlı" /></label>
          <label className="field"><span>Tam anlatım metni</span><textarea rows="11" value={draft.narration} onChange={(e) => setDraft({ ...draft, narration: e.target.value })} /></label>
          <div className="editor-meta"><Clock3 size={14} /><span>Yaklaşık {Math.max(1, Math.round((draft.narration || '').split(/\s+/).length / 2.2))} sn anlatım</span></div>
          {selectedQualityIssues.length > 0 && <div className="quality-issues">
            <strong><TriangleAlert size={14} /> Bu slaytta {selectedQualityIssues.length} bulgu</strong>
            {selectedQualityIssues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}>{issue.message}</div>)}
          </div>}
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
        {assets && assets.slideCount > 0 && <div className="health-card">
          <div className="health-row">
            <span>Ses</span>
            <strong>{assets.canonicalAudioCount}/{assets.slideCount} hazır</strong>
            {assets.missingAudioCount > 0 && <small className="health-missing">{assets.missingAudioCount} eksik</small>}
          </div>
          <div className="health-row">
            <span>Video segmenti</span>
            <strong>{assets.canonicalSegmentCount}/{assets.slideCount} hazır</strong>
            {assets.missingSegmentCount > 0 && <small className="health-missing">{assets.missingSegmentCount} eksik</small>}
          </div>
          {assets.orphanCount > 0 && <div className="health-row"><span>Eski dosya</span><small className="health-missing">{assets.orphanCount} karantinada</small></div>}
          <small className="health-hint">
            {assets.missingSegmentCount === 0 && assets.slideCount > 0
              ? 'Tüm slaytlar render edilmiş; tekrar render sadece değişenleri günceller.'
              : 'Render başladığında hazır olanlar atlanır, yalnızca eksikler üretilir.'}
          </small>
        </div>}
        <div className="editor-form">
          <label className="field"><span>Sağlayıcı</span><select value={video.ttsProvider} onChange={(e) => setVideo({ ...video, ttsProvider: e.target.value })}>{bootstrap.ttsProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.id} — {provider.label.split('(')[0]}</option>)}</select></label>
          <label className="field"><span>Ses</span><select value={video.voice} onChange={(e) => setVideo({ ...video, voice: e.target.value })}>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}</select></label>
          <label className="field"><span>Konuşma hızı</span><select value={video.rate} onChange={(e) => setVideo({ ...video, rate: e.target.value })}>{['-20%', '-10%', '+0%', '+10%', '+20%'].map((rate) => <option key={rate}>{rate}</option>)}</select></label>
          {video.ttsProvider === 'elevenlabs' && <label className="field"><span>ElevenLabs API anahtarı</span><input type="password" value={video.elevenlabsKey} onChange={(e) => setVideo({ ...video, elevenlabsKey: e.target.value })} /></label>}
        </div>
        {renderEstimate && <div className={`estimate-card ${renderEstimate.diskWarning ? 'warning' : ''}`}>
          <div className="health-row"><span>Tahmini süre</span><strong>{renderEstimate.estimatedMinutes} dk ({renderEstimate.wordsTotal} kelime)</strong></div>
          <div className="health-row"><span>Bu render'da üretilecek</span><strong>{renderEstimate.slidesToRender} slayt</strong>{renderEstimate.slidesReusable > 0 && <small className="health-hint">({renderEstimate.slidesReusable} zaten hazır, atlanacak)</small>}</div>
          {renderEstimate.estimatedDiskMb > 0 && <div className="health-row">
            <span>Tahmini disk</span>
            <strong>~{renderEstimate.estimatedDiskMb} MB</strong>
            {renderEstimate.freeDiskMb != null && <small className={renderEstimate.diskWarning ? 'health-missing' : 'health-hint'}>{renderEstimate.freeDiskMb.toLocaleString('tr-TR')} MB boş</small>}
          </div>}
          <small className={renderEstimate.providerIsLocal ? 'health-hint' : 'health-missing'}>{renderEstimate.providerCostNote}</small>
        </div>}
        {qualityBlocked && <div className="render-gate"><TriangleAlert size={16} /><span>Ücretli veya ağır ses üretimi başlamadan önce kritik anlatı sorunlarını düzelt.</span></div>}
        <button className="button primary render-button" onClick={render} disabled={!project?.slides.length || Boolean(activeJob) || qualityBlocked}><Clapperboard size={17} /> {activeJob?.type === 'video' ? 'Render sürüyor…' : 'Videoyu oluştur'}</button>
        <ProgressStrip job={activeJob?.type === 'video' || jobState?.kind === 'video' ? jobState : null} />
        {project?.outputs?.video && <div className="output-actions"><a className="button ghost" href={`/api/projects/${project.id}/output/video`}><MonitorPlay size={16} /> Videoyu aç</a><button className="button ghost" onClick={() => api(`/api/projects/${project.id}/open-output`, { method: 'POST' })}><FolderOpen size={16} /> Klasörü aç</button></div>}
        {project?.slides?.length > 0 && <div className="export-row">
          <span className="source-label"><FileText size={13} /> Çalışma materyali (ücretsiz, anında)</span>
          <div className="export-links">
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/notes`}>Ders Notu (.md)</a>
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/transcript`}>Transkript (.txt)</a>
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/anki`}>Anki (.tsv)</a>
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/quiz`}>Kendini Test Et (.md)</a>
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/srt`}>Altyazı (.srt)</a>
            <a className="button ghost compact" href={`/api/projects/${project.id}/export/vtt`}>Altyazı (.vtt)</a>
          </div>
          <small>Altyazı dosyaları videodaki yakılmış altyazıdan bağımsız, ayrıca YouTube'a yüklenebilir. Render'dan önce kaba tahmini, render'dan sonra gerçek zamanlamayla üretilir.</small>
        </div>}
        {chapters?.chapters?.length > 1 && <div className="export-row">
          <span className="source-label"><Layers3 size={13} /> Bölüm bazlı çıktı ({chapters.chapters.length} bölüm) — uzun dersi YouTube'a ayrı ayrı yüklemek için</span>
          {!chapters.allRendered && <small className="health-missing">{chapters.missingCount} slayt henüz render edilmemiş — önce yukarıdan tam render tamamla.</small>}
          <button type="button" className="button ghost compact" onClick={exportChapters} disabled={!chapters.allRendered || Boolean(activeJob)}>
            <Clapperboard size={13} /> {activeJob?.type === 'chapters' ? 'Hazırlanıyor…' : 'Bölüm Videolarını Oluştur'}
          </button>
          <ProgressStrip job={activeJob?.type === 'chapters' || jobState?.kind === 'chapters' ? jobState : null} />
          {chapters.chapters.some((c) => c.exported) && <div className="chapter-list">
            {chapters.chapters.filter((c) => c.exported).map((c) => (
              <div key={c.index} className="chapter-item">
                <span>{c.index}. {c.title} <small>({c.slideCount} slayt)</small></span>
                <a href={`/api/projects/${project.id}/chapters/download/${c.videoFile}`}>MP4</a>
                <a href={`/api/projects/${project.id}/chapters/download/${c.audioFile}`}>MP3</a>
              </div>
            ))}
            {chapters.youtubeChaptersReady && <a className="button ghost compact" href={`/api/projects/${project.id}/chapters/download/youtube-chapters.txt`}>YouTube Chapters (.txt)</a>}
          </div>}
        </div>}
      </section>
    </div>
  )

  if (!bootstrap) return <div className="app-loading"><AmbientBackdrop />{bootstrapError ? <><span className="brand-mark error-mark"><CircleAlert /></span><h1>Yerel servis bağlantısı yok</h1><p>{bootstrapError}</p><button className="button primary" onClick={() => setBootstrapRetry((value) => value + 1)}><RefreshCw size={16} /> Yeniden bağlan</button></> : <><span className="brand-mark"><Sparkles /></span><h1>Ders Stüdyosu</h1><p>Çalışma alanın hazırlanıyor…</p><LoaderCircle className="spin" /></>}</div>

  if (view === 'dashboard') {
    return (
      <div className="dashboard-shell">
        <AmbientBackdrop />
        <div className="dashboard-hero">
          <span className="brand-mark"><Sparkles size={22} /></span>
          <h1>Ders Stüdyosu</h1>
          <p>Bir kaynak seç ya da yeni bir tane ekle; sonra ne yapmak istediğine karar ver.</p>
        </div>
        <div className="dashboard-grid">
          <section className="panel dashboard-projects">
            <div className="panel-toolbar"><div><span className="kicker">PROJELER</span><h3>Var olan kaynaklar</h3></div></div>
            <div className="dashboard-project-list">
              {bootstrap.projects.length === 0 && (
                <EmptyState icon={FolderOpen} title="Henüz proje yok">Sağdan yeni bir kaynak ekleyerek başla.</EmptyState>
              )}
              {bootstrap.projects.map((item) => (
                <button key={item.id} className="dashboard-project-card" onClick={() => openProjectHub(item.id)} disabled={busy}>
                  <FolderOpen size={20} />
                  <span className="dashboard-project-card-main"><strong>{item.id}</strong><small>{item.sections} bölüm · {item.slides} slayt</small></span>
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          </section>
          <div className="dashboard-upload">{renderSourceIngest()}</div>
        </div>
        {toast && <div className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'hub') {
    const hubCompletion = project ? (project.outputs?.video ? 100 : project.slides.length ? 66 : 20) : 0
    return (
      <div className="dashboard-shell">
        <AmbientBackdrop />
        <button className="button quiet hub-back" onClick={() => setView('dashboard')}><ArrowLeft size={16} /> Panele dön</button>
        <div className="dashboard-hero">
          <span className="kicker">PROJE</span>
          <h1>{project?.id}</h1>
          <p>{project?.sections.length || 0} bölüm · {project?.slides.length || 0} slayt — ne yapmak istiyorsun?</p>
        </div>
        <div className="hub-modules">
          <button className="hub-module-card" onClick={() => setView('video')}>
            <Clapperboard size={30} />
            <strong>Video Üretimi</strong>
            <small>{project?.outputs?.video ? 'Video hazır — düzenlemeye devam et' : project?.slides.length ? 'Anlatı hazır, render bekliyor' : 'Henüz başlanmadı'}</small>
            <div className="mini-progress"><span style={{ width: `${hubCompletion}%` }} /></div>
          </button>
          <button className="hub-module-card" onClick={() => setView('anki')} disabled={!project?.slides.length}>
            <Layers3 size={30} />
            <strong>Flashcard Çalışma</strong>
            <small>{project?.slides.length ? 'Kaynaktan üretilen kartlarla aralıklı tekrar yap' : 'Önce Video Üretimi\'nden bir anlatı üret'}</small>
          </button>
        </div>
        {toast && <div className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'anki' && !activeDeck) {
    return (
      <div className="dashboard-shell">
        <AmbientBackdrop />
        <button className="button quiet hub-back" onClick={() => setView('hub')}><ArrowLeft size={16} /> Panele dön</button>
        <div className="dashboard-hero">
          <span className="kicker">FLASHCARD ÇALIŞMA</span>
          <h1>{project?.id}</h1>
          <p>Anki'deki gibi, aynı kaynaktan birden fazla deste tutabilirsin — ör. farklı sınavlar için statik bir deste, belirli bir konuya odaklanan yapay zeka destekli başka bir deste.</p>
        </div>
        <div className="anki-overview">
          <div className="provider-tabs compact">
            <button className={deckKind === 'static' ? 'active' : ''} onClick={() => setDeckKind('static')}>Statik</button>
            <button className={deckKind === 'llm' ? 'active' : ''} onClick={() => setDeckKind('llm')}>Yapay Zeka ile Üret</button>
          </div>
          <div className="deck-create-row">
            <input value={newDeckName} onChange={(e) => setNewDeckName(e.target.value)} placeholder="Yeni deste adı, ör. Sınav Öncesi" />
            <button className="button primary" onClick={createFlashcardDeck} disabled={!newDeckName.trim() || flashcardBusy}>
              <Plus size={16} /> {activeJob?.type === 'flashcards' ? 'Oluşturuluyor…' : deckKind === 'llm' ? 'Yapay Zeka ile Oluştur' : 'Statik Deste Oluştur'}
            </button>
          </div>
          {deckKind === 'static' ? (
            <small className="health-hint">Mevcut anlatıdan anında, ücretsiz ve deterministik olarak üretilir — LLM çağrısı yapılmaz.</small>
          ) : (
            <div className="deck-llm-options">
              <div className="provider-tabs compact">
                {[['agent', 'Claude Agent'], ['gemini', 'Gemini API'], ['openai', 'OpenAI uyumlu']].map(([id, label]) => (
                  <button key={id} className={llm.provider === id ? 'active' : ''} onClick={() => setLlm({ ...llm, provider: id })}>{label}</button>
                ))}
              </div>
              <div className="form-grid">
                {llm.provider === 'agent' && (
                  <label className="field wide"><span>Agent komutu</span><input value={llm.agentCommand} onChange={(e) => setLlm({ ...llm, agentCommand: e.target.value })} /></label>
                )}
                {llm.provider === 'gemini' && <>
                  <label className="field"><span>Model</span><select value={llm.geminiModel} onChange={(e) => setLlm({ ...llm, geminiModel: e.target.value })}>{bootstrap.models.gemini.map((model) => <option key={model}>{model}</option>)}</select></label>
                  <label className="field"><span>API anahtarı {bootstrap.keysConfigured.gemini && <em>kayıtlı</em>}</span><input type="password" value={llm.apiKey} onChange={(e) => setLlm({ ...llm, apiKey: e.target.value })} placeholder="Yeni anahtar girmek zorunda değilsin" /></label>
                </>}
                {llm.provider === 'openai' && <>
                  <label className="field wide"><span>Endpoint / Base URL</span><input value={llm.openaiEndpoint} onChange={(e) => setLlm({ ...llm, openaiEndpoint: e.target.value })} /></label>
                  <label className="field"><span>Model kimliği</span><input value={llm.openaiModel} onChange={(e) => setLlm({ ...llm, openaiModel: e.target.value })} /></label>
                  <label className="field"><span>API anahtarı {bootstrap.keysConfigured.openai && <em>kayıtlı</em>}</span><input type="password" value={llm.apiKey} onChange={(e) => setLlm({ ...llm, apiKey: e.target.value })} placeholder="sk-…" /></label>
                </>}
                <label className="field"><span>Kart sayısı</span><input type="number" min="1" max="60" value={deckCount} onChange={(e) => setDeckCount(e.target.value)} placeholder="Model karar versin" /></label>
                <label className="field wide"><span>Odak / özel talimat (opsiyonel)</span><textarea rows="2" value={deckFocusPrompt} onChange={(e) => setDeckFocusPrompt(e.target.value)} placeholder="Örn. pointer'lara ve bellek yönetimine daha çok odaklan" /></label>
              </div>
              <ProgressStrip job={activeJob?.type === 'flashcards' || jobState?.kind === 'flashcards' ? jobState : null} />
            </div>
          )}
          <div className="deck-list">
            {flashcardBusy && flashcardDecks.length === 0 && <small className="health-hint">Yükleniyor…</small>}
            {!flashcardBusy && flashcardDecks.length === 0 && (
              <EmptyState icon={Layers3} title="Henüz deste yok">Yukarıdan ilk desteni oluştur.</EmptyState>
            )}
            {flashcardDecks.map((deck) => (
              <div key={deck.id} className="deck-card" onClick={() => openFlashcardDeck(deck.id)}>
                <Layers3 size={20} />
                <span className="deck-card-main">
                  <strong>{deck.name}</strong>
                  <small>{deck.kind === 'llm' ? 'YZ destekli' : 'Statik'} · {deck.summary.totalCards} kart · {deck.summary.dueCount} bugün sırada</small>
                </span>
                <button className="icon-button danger" title="Desteyi sil" onClick={(e) => { e.stopPropagation(); deleteFlashcardDeck(deck.id) }}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        </div>
        {toast && <div className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'anki' && activeDeck) {
    return (
      <div className="dashboard-shell">
        <AmbientBackdrop />
        <button className="button quiet hub-back" onClick={reviewCard ? endReviewSession : closeFlashcardDeck}><ArrowLeft size={16} /> {reviewCard ? 'Desteye dön' : 'Destelere dön'}</button>
        {!reviewCard ? (
          <>
            <div className="dashboard-hero">
              <span className="kicker">DESTE</span>
              <h1>{activeDeck.name}</h1>
              <p>Aralıklı tekrar (SM-2 — Anki'nin de temel aldığı algoritma) ile çalış; kartlar ne kadar iyi hatırladığına göre otomatik programlanır.</p>
            </div>
            <div className="anki-overview">
              <div className="anki-stats">
                <div className="anki-stat"><strong>{activeDeck.summary?.dueCount ?? 0}</strong><small>bugün sırada</small></div>
                <div className="anki-stat"><strong>{activeDeck.summary?.newCount ?? 0}</strong><small>yeni</small></div>
                <div className="anki-stat"><strong>{activeDeck.summary?.totalCards ?? 0}</strong><small>toplam kart</small></div>
                <div className="anki-stat"><strong>{activeDeck.summary?.suspendedCards ?? 0}</strong><small>askıda</small></div>
              </div>
              <div className="anki-actions">
                <button className="button primary" onClick={startReviewSession} disabled={!activeDeck.summary?.dueCount || flashcardBusy}>
                  <WandSparkles size={16} /> Çalışmaya başla ({activeDeck.summary?.dueCount ?? 0})
                </button>
                {activeDeck.kind === 'static' && (
                  <button className="button ghost" onClick={regenerateActiveDeck} disabled={flashcardBusy}>
                    <RefreshCw size={16} /> Kaynaktan yeniden oluştur
                  </button>
                )}
              </div>
              <div className="anki-card-list">
                {activeDeck.cards.map((card) => (
                  <div key={card.id} className={`anki-card-row ${card.suspended ? 'suspended' : ''}`}>
                    <span className="anki-card-kind">{card.kind === 'cloze' ? 'Boşluk' : 'Temel'}</span>
                    <span className="anki-card-front">{card.front}</span>
                    <small>{card.repetitions === 0 ? 'yeni' : `${card.repetitions} tekrar`}</small>
                    <button className="button quiet compact" onClick={() => toggleCardSuspend(card)}>
                      {card.suspended ? 'Aktif et' : 'Askıya al'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="review-session">
            <div className="review-progress"><small>{reviewedCount} incelendi · {reviewQueue.length + 1} kart kaldı</small></div>
            <div className="review-card">
              <span className="review-card-kind">{reviewCard.kind === 'cloze' ? 'Boşluk Doldurma' : 'Temel Kart'}</span>
              <p className="review-front">{reviewCard.front}</p>
              {reviewRevealed && <><hr /><p className="review-back">{reviewCard.back}</p></>}
            </div>
            {!reviewRevealed ? (
              <button className="button primary review-reveal" onClick={() => setReviewRevealed(true)}>Cevabı Göster</button>
            ) : (
              <div className="review-ratings">
                <button className="button rating-again" onClick={() => submitReviewRating('again')}>Tekrar</button>
                <button className="button rating-hard" onClick={() => submitReviewRating('hard')}>Zor</button>
                <button className="button rating-good" onClick={() => submitReviewRating('good')}>İyi</button>
                <button className="button rating-easy" onClick={() => submitReviewRating('easy')}>Kolay</button>
              </div>
            )}
            <button className="button quiet" onClick={endReviewSession}>Oturumu bitir</button>
          </div>
        )}
        {toast && <div className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

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
          <div className="topbar-meta"><button className="button quiet compact" onClick={() => setView('hub')}><ArrowLeft size={15} /> Panele dön</button><span><span className="status-dot" /> Pipeline bağlı</span><button className="icon-button" title="Kullanım / maliyet" onClick={() => setShowCost(true)}><Wallet size={18} /></button><button className="icon-button" title="Toplu kuyruk" onClick={() => setShowQueue(true)}><ListOrdered size={18} /></button><button className="icon-button" title="Telaffuz sözlüğü" onClick={() => setShowPronunciation(true)}><Settings2 size={18} /></button></div>
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

      {showPronunciation && <div className="modal-backdrop" onClick={() => setShowPronunciation(false)}>
        <div className="modal-card" onClick={(event) => event.stopPropagation()}>
          <div className="modal-header">
            <div><strong>Telaffuz Sözlüğü</strong><small>Türkçe TTS'in İngilizce/kod terimlerini yanlış okumasını düzeltir — tüm projelerde geçerlidir.</small></div>
            <button className="icon-button" onClick={() => setShowPronunciation(false)}><X size={18} /></button>
          </div>
          <div className="modal-body">
            <div className="pronunciation-preview">
              <label className="field wide"><span>Hızlı önizleme (bir cümle yaz, nasıl seslendirileceğini dinle)</span>
                <textarea rows="2" value={previewText} onChange={(e) => setPreviewText(e.target.value)} />
              </label>
              <button className="button ghost compact" onClick={runPronunciationPreview} disabled={previewBusy || !previewText.trim()}>
                {previewBusy ? 'Üretiliyor…' : 'Dinle (Edge-TTS)'}
              </button>
              {previewResult && <div className="preview-result">
                <small>Seslendirilecek metin: “{previewResult.normalizedText}”</small>
                <audio controls src={previewResult.audioUrl} />
              </div>}
            </div>
            <div className="pronunciation-add">
              <label className="field"><span>Terim</span><input value={newTerm} onChange={(e) => setNewTerm(e.target.value)} placeholder="ör. kernel" /></label>
              <label className="field"><span>Fonetik Türkçe yazım</span><input value={newPhonetic} onChange={(e) => setNewPhonetic(e.target.value)} placeholder="ör. körnıl" /></label>
              <button className="button primary compact" onClick={addOrUpdatePronunciationTerm} disabled={!newTerm.trim() || !newPhonetic.trim()}><Plus size={14} /> Ekle / Güncelle</button>
            </div>
            <div className="pronunciation-list">
              {pronunciationEntries.map((entry) => (
                <div key={entry.term} className={`pronunciation-row ${entry.isOverride ? 'override' : ''}`}>
                  <span className="pronunciation-term">{entry.term}</span>
                  <span className="pronunciation-arrow">→</span>
                  <span className="pronunciation-phonetic">{entry.phonetic}</span>
                  {entry.isOverride ? (
                    <button className="icon-button danger" title="Özel telaffuzu kaldır" onClick={() => removePronunciationOverride(entry.term)}><Trash2 size={14} /></button>
                  ) : <small className="health-hint">varsayılan</small>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>}

      {showQueue && <div className="modal-backdrop" onClick={() => setShowQueue(false)}>
        <div className="modal-card" onClick={(event) => event.stopPropagation()}>
          <div className="modal-header">
            <div><strong>Toplu Kuyruk</strong><small>Birden fazla PDF/PPTX/MD dosyasını sıraya koy — şu anki Üretim ve Stüdyo ayarlarınla, gözetimsiz olarak sırayla ayrıştırılıp üretilir ve render edilir.</small></div>
            <button className="icon-button" onClick={() => setShowQueue(false)}><X size={18} /></button>
          </div>
          <div className="modal-body">
            <div className="path-input">
              <input value={queuePath} onChange={(e) => setQueuePath(e.target.value)} placeholder="D:\\dersler\\konu.pdf" />
              <button className="button ghost" onClick={addToQueue} disabled={!queuePath.trim() || queueBusy}>Kuyruğa Ekle</button>
            </div>
            <small className="health-hint">Eklendiği andaki Üretim ayarları (sağlayıcı, üslup vb.) ve Stüdyo ayarları (ses, tema) anlık görüntü olarak alınır; kuyruk işlenirken bu ayarları değiştirsen bile bu öğeyi etkilemez.</small>
            <div className="queue-list">
              {queueItems.length === 0 && <small className="health-hint">Kuyrukta öğe yok.</small>}
              {queueItems.map((item) => (
                <div key={item.id} className={`queue-item queue-item-${item.status}`}>
                  <span className="queue-item-main">
                    <strong>{item.projectName}</strong>
                    <small>{item.stage}{item.error ? ` — ${item.error}` : ''}</small>
                  </span>
                  <span className={`queue-status-badge queue-status-${item.status}`}>{QUEUE_STATUS_LABELS[item.status] || item.status}</span>
                  {['queued', 'complete', 'failed'].includes(item.status) && (
                    <button className="icon-button danger" title="Kuyruktan kaldır" onClick={() => removeQueueItem(item.id)}><Trash2 size={14} /></button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>}

      {showCost && <div className="modal-backdrop" onClick={() => setShowCost(false)}>
        <div className="modal-card" onClick={(event) => event.stopPropagation()}>
          <div className="modal-header">
            <div><strong>Kullanım / Maliyet</strong><small>Tüm projeler genelinde. Sadece kendi maliyetini bildiren sağlayıcılar (Claude Agent CLI) için gerçek $ gösterilir — diğerleri için ASLA tahmin üretilmez, sadece kullanım miktarı gösterilir.</small></div>
            <button className="icon-button" onClick={() => setShowCost(false)}><X size={18} /></button>
          </div>
          <div className="modal-body">
            {!costSummary && <small className="health-hint">Yükleniyor…</small>}
            {costSummary && <>
              <div className="cost-total">
                <strong>${costSummary.totalUsd.toFixed(4)}</strong>
                <small>bilinen toplam maliyet{costSummary.hasUnknownCostProvider ? ' (bazı sağlayıcılar maliyetini bildirmiyor, aşağıda ayrıca işaretli)' : ''}</small>
              </div>
              <div className="cost-provider-list">
                {Object.keys(costSummary.byProvider).length === 0 && <small className="health-hint">Henüz kayıtlı kullanım yok.</small>}
                {Object.entries(costSummary.byProvider).map(([provider, data]) => (
                  <div key={provider} className="cost-provider-row">
                    <strong>{provider}</strong>
                    <span>{data.hasUnknownCost ? 'maliyet bilinmiyor — kendi hesabından kontrol et' : `$${data.usd.toFixed(4)}`}</span>
                    <small>{data.words > 0 ? `${data.words.toLocaleString('tr-TR')} kelime` : ''}{data.requests > 1 ? ` · ${data.requests} istek` : ''}</small>
                  </div>
                ))}
              </div>
            </>}
          </div>
        </div>
      </div>}
    </div>
  )
}
