import StudyWorkspace from './StudyWorkspace.jsx'
import CourseWorkspace from './CourseWorkspace.jsx'
import FlashcardWorkspace from './FlashcardWorkspace.jsx'
import { BrandMark, ThemeToggle, WorkspaceHeader } from './Appearance.jsx'
import RemoteGpuControls, { REMOTE_ENGINES, effectiveBackend } from './RemoteGpu.jsx'
import { ACTIVE_JOB_STATUSES, chooseActiveVideoJob, resolveResumeWorkspace } from './jobSession.js'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Search,
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
  Download,
  ExternalLink,
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
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Settings2,
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
        role="switch" aria-label={label}
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
  const [course, setCourse] = useState(null)
  const [courseTab, setCourseTab] = useState('hub')
  const [studyCourse, setStudyCourse] = useState(null)
  const [newCourseName, setNewCourseName] = useState('')
  const apiBase = project?.apiBase || (project ? '/api/projects/' + project.id : '')
  const workspaceKey = project ? project.id + (project.videoId ? ':' + project.videoId : '') : ''
  // 'dashboard' (proje seç/oluştur) | 'hub' (seçili proje için modül seç) | 'video' (bugüne kadarki tüm akış)
  const [view, setView] = useState('dashboard')
  useEffect(() => { const open = () => { setStudyCourse(null); setView('study') }; window.addEventListener('open-study', open); return () => window.removeEventListener('open-study', open) }, [])
  const [stage, setStage] = useState('source')
  const [selectedSections, setSelectedSections] = useState(new Set())
  const [selectedSlide, setSelectedSlide] = useState(null)
  const [draft, setDraft] = useState(null)
  const [projectQuery, setProjectQuery] = useState('')
  const [sectionQuery, setSectionQuery] = useState('')
  const [slideQuery, setSlideQuery] = useState('')
  const [deckQuery, setDeckQuery] = useState('')
  const [savingSlides, setSavingSlides] = useState(false)
  const draftCache = useRef(new Map())
  const saveInFlight = useRef(false)
  const matches = (value, query) => String(value || '').toLocaleLowerCase('tr-TR').includes(query.trim().toLocaleLowerCase('tr-TR'))
  const projectLabel = (id) => (id || '').replace(/[_-]+/g, ' ')
  const draftKey = (id, index) => 'ders-studio-draft:' + id + ':' + index
  const draftDirty = Boolean(draft && project?.slides[selectedSlide] && JSON.stringify(draft) !== JSON.stringify(project.slides[selectedSlide]))
  function updateDraft(next) {
    setDraft(next)
    const key = draftKey(workspaceKey, selectedSlide)
    const value = { base: JSON.stringify(project.slides[selectedSlide]), draft: next }
    draftCache.current.set(key, value)
    try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* In-memory draft remains available. */ }
  }
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

  const rememberActiveJob = (job, location = {}) => {
    patchSession({
      activeJob: job,
      projectId: course?.id || project?.id || null,
      videoId: project?.videoId || null,
      view: location.view || view,
      stage: location.stage || stage,
    })
    setActiveJob(job)
  }
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
    coquiParallelWorkers: 1,
    chatterboxParallelWorkers: 1,
    ttsBackend: 'local', remoteTtsConcurrency: 2,
  })

  useEffect(() => {
    setBootstrapError('')
    api('/api/bootstrap')
      .then(async (data) => {
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
          coquiParallelWorkers: s.coqui_parallel_workers || 1,
          chatterboxParallelWorkers: s.chatterbox_parallel_workers || 1,
          ttsBackend: s.tts_backend || 'local',
          remoteTtsConcurrency: s.remote_tts_concurrency || 2,
        }))

        // Sayfa yenilense (veya sekme yeniden açılsa) bile, açık projeyi ve arka
        // planda süren bir işi kaybetmemek için son oturumu geri yükle. İş sunucu
        // tarafında bir Python thread'i olarak zaten devam ediyor olabilir — burada
        // sadece arayüzü ona yeniden bağlıyoruz.
        const session = loadSession()
        const resumeParams = new URLSearchParams(window.location.search)
        const requestedJobId = resumeParams.get('resumeJob')
        let sessionJob = requestedJobId ? { id: requestedJobId, type: 'video' } : session.activeJob
        let resumeJobState = null
        let terminalJobState = null
        if (sessionJob) {
          try {
            resumeJobState = await api(`/api/jobs/${sessionJob.id}`)
            if (!ACTIVE_JOB_STATUSES.has(resumeJobState.status)) {
              terminalJobState = resumeJobState
              resumeJobState = null
              sessionJob = null
            }
          } catch {
            sessionJob = null
          }
        }
        if (!sessionJob) {
          try {
            const active = await api('/api/jobs/active?kind=video')
            const matching = chooseActiveVideoJob(active.jobs, session)
            if (matching) {
              sessionJob = { id: matching.id, type: 'video' }
              resumeJobState = matching
            }
          } catch {
            // Eski API sürümünde aktif iş endpoint'i olmayabilir; kayıtlı oturumla devam et.
          }
        }
        const resumeWorkspace = resolveResumeWorkspace(session, resumeJobState, window.location.search)
        const restoreProjectId = resumeWorkspace.projectId
        const restoreVideoId = resumeWorkspace.videoId
        if (restoreProjectId) {
          api(`/api/projects/${restoreProjectId}`)
            .then(async (projectData) => {
              if (projectData.kind === 'course') {
                setCourse(projectData)
                if (restoreVideoId && (session.view === 'video' || sessionJob)) {
                  projectData = await api('/api/projects/' + projectData.id + '/videos/' + restoreVideoId)
                } else {
                  setView(session.view === 'study' ? 'study' : session.view === 'dashboard' ? 'dashboard' : 'course')
                  sessionRestoredRef.current = true
                  return
                }
              }
              setProject(projectData)
              if (projectData.narrationSettings) setLlm(current => ({ ...current, ...projectData.narrationSettings, apiKey: '' }))
              if (projectData.videoSettings) setVideo(current => ({ ...current, ...projectData.videoSettings, elevenlabsKey: '' }))
              setSelectedSections(new Set(projectData.sections.map((_, index) => index)))
              setSelectedSlide(projectData.slides.length ? Math.min(Math.max(0, Number(session.selectedSlide) || 0), projectData.slides.length - 1) : null)
              if (sessionJob?.type === 'video') {
                setStage('video')
                setView('video')
              } else {
                if (session.stage) setStage(session.stage)
                // Eski oturumlarda (bu özellik eklenmeden önce) view kaydı yok —
                // aktif bir proje varsa geri döndüğümüzde doğrudan video modülüne dön.
                setView(['dashboard', 'hub', 'anki', 'video', 'course', 'study'].includes(session.view) ? session.view : 'video')
              }
              if (sessionJob) {
                const attachJob = (job) => {
                    if (ACTIVE_JOB_STATUSES.has(job.status)) {
                      setActiveJob(sessionJob)
                      setJobState(job)
                      patchSession({
                        activeJob: sessionJob,
                        projectId: restoreProjectId,
                        videoId: restoreVideoId || null,
                        view: 'video',
                        stage: 'video',
                      })
                      if (requestedJobId) window.history.replaceState({}, '', window.location.pathname)
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
                  }
                if (resumeJobState) {
                  attachJob(resumeJobState)
                  sessionRestoredRef.current = true
                } else {
                  api(`/api/jobs/${sessionJob.id}`)
                    .then(attachJob)
                    .catch(() => patchSession({ activeJob: null }))
                    .finally(() => { sessionRestoredRef.current = true })
                }
              } else {
                if (terminalJobState?.status === 'failed') {
                  setToast({ type: 'error', text: terminalJobState.error || terminalJobState.message })
                }
                patchSession({ activeJob: null })
                sessionRestoredRef.current = true
              }
            })
            .catch(() => {
              patchSession({ projectId: null, activeJob: null })
              sessionRestoredRef.current = true
            })
        } else {
          if (session.view === 'study') setView('study')
          sessionRestoredRef.current = true
        }
      })
      .catch((error) => setBootstrapError(error.message))
  }, [bootstrapRetry])

  useEffect(() => {
    if (sessionRestoredRef.current && (project?.id || course?.id)) patchSession({ projectId: course?.id || project.id, videoId: project?.videoId || null })
  }, [workspaceKey, course?.id])

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
    const base = project.slides[selectedSlide]
    const key = draftKey(workspaceKey, selectedSlide)
    let saved = draftCache.current.get(key)
    if (!saved) { try { saved = JSON.parse(localStorage.getItem(key)) } catch { /* No stored draft. */ } }
    setDraft(structuredClone(saved?.base === JSON.stringify(base) ? saved.draft : base))
  }, [selectedSlide, workspaceKey, project?.slides])

  useEffect(() => {
    if (sessionRestoredRef.current) patchSession({ selectedSlide })
  }, [selectedSlide])

  useEffect(() => {
    setSectionQuery('')
    setSlideQuery('')
    setDeckQuery('')
  }, [workspaceKey])

  useEffect(() => {
    const unload = (event) => { if (draftDirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', unload)
    return () => window.removeEventListener('beforeunload', unload)
  }, [draftDirty])

  useEffect(() => {
    const keydown = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && view === 'video' && stage === 'script') {
        event.preventDefault()
        if (draftDirty && !activeJob && !saveInFlight.current) saveDraft()
      }
      if (event.key === 'Escape') {
        setShowCost(false); setShowQueue(false); setShowPronunciation(false)
      }
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  })


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
    api(`${apiBase}/render-estimate?provider=${encodeURIComponent(video.ttsProvider)}`)
      .then(setRenderEstimate)
      .catch(() => setRenderEstimate(null))
  }, [workspaceKey, project?.slides?.length, project?.assets?.canonicalSegmentCount, video.ttsProvider])

  const refreshChapters = () => {
    if (!project?.id) return
    api(`${apiBase}/chapters`).then(setChapters).catch(() => setChapters(null))
  }

  useEffect(() => {
    if (!project?.id || !project?.slides?.length) {
      setChapters(null)
      return
    }
    refreshChapters()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceKey, project?.slides?.length, project?.assets?.canonicalSegmentCount])

  const exportChapters = async () => {
    if (!project || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Bölümler hazırlanıyor' })
    try {
      const response = await api(`${apiBase}/chapters/export`, { method: 'POST' })
      rememberActiveJob({ id: response.jobId, type: 'chapters' }, { view: 'video', stage: 'video' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const refreshSnapshots = () => {
    if (!project?.id) return
    api(`${apiBase}/snapshots`)
      .then(({ snapshots: list }) => setSnapshots(list))
      .catch(() => setSnapshots([]))
  }

  useEffect(() => {
    refreshSnapshots()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceKey])

  const restoreSnapshotVersion = async (filename) => {
    if (!project || activeJob) return
    try {
      const data = await api(`${apiBase}/snapshots/${encodeURIComponent(filename)}/restore`, {
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
    }
  }, [view, workspaceKey])

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
            coquiParallelWorkers: video.coquiParallelWorkers,
            chatterboxParallelWorkers: video.chatterboxParallelWorkers,
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
        rememberActiveJob({ id: response.jobId, type: 'flashcards' })
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
    loadFlashcardDecks()
  }

  const deleteFlashcardDeck = async (deckId) => {
    if (!project || !window.confirm('Deste ve kartları kalıcı olarak silinecek. Devam edilsin mi?')) return
    try {
      await api(`/api/projects/${project.id}/flashcards/decks/${deckId}`, { method: 'DELETE' })
      setFlashcardDecks((current) => current.filter((d) => d.id !== deckId))
      if (activeDeck?.id === deckId) closeFlashcardDeck()
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const goToCardSource = (card) => {
    if (card.sourceSlideIndex == null) return
    setView('video')
    setStage('script')
    setSelectedSlide(card.sourceSlideIndex)
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
              api(`${apiBase}/chapters`).then(setChapters).catch(() => {})
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
              api(apiBase)
                .then((data) => setProject((current) => current ? { ...current, assets: data.assets } : current))
                .catch(() => {})
            }
          }
          setActiveJob(null)
        } else if (job.status === 'failed') {
          setToast({ type: 'error', text: job.error })
          if (activeJob.type === 'flashcards') setFlashcardBusy(false)
          setActiveJob(null)
        } else if (job.status === 'cancelled') {
          setToast({ type: 'success', text: 'Render iptal edildi. Mevcut video korunuyor.' })
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


  useEffect(() => {
    if (view !== 'video' || stage !== 'script' || selectedSlide === null) return
    document.getElementById(`slide-${selectedSlide}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [view, stage, selectedSlide])

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
    if (activeJob) { setToast({ type: 'error', text: 'Devam eden üretim tamamlandıktan sonra başka bir çalışma açabilirsin.' }); return }
    setBusy(true)
    try {
      const data = await api(`/api/projects/${id}`)
      if (data.kind !== 'course') setCourse(null)
      applyProject(data)
      setToast({ type: 'success', text: 'Proje yüklendi.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  const applyProject = (data) => {
    if (data.kind === 'course') {
      setCourse(data); setCourseTab('hub'); setProject(null); setView('course'); return
    }
    setProject(data)
    if (data.narrationSettings) setLlm(current => ({ ...current, ...data.narrationSettings, apiKey: '' }))
    if (data.videoSettings) setVideo(current => ({ ...current, ...data.videoSettings, elevenlabsKey: '' }))
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
    if (activeJob) { setToast({ type: 'error', text: 'Devam eden üretim tamamlandıktan sonra başka bir çalışma açabilirsin.' }); return }
    setBusy(true)
    try {
      const data = await api(`/api/projects/${id}`)
      if (data.kind !== 'course') setCourse(null)
      applyProject(data)
      setView(data.kind === 'course' ? 'course' : 'hub')
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

  const persistSlides = async (slides, message, savedDraftKey = null) => {
    if (!project || saveInFlight.current || activeJob) return false
    saveInFlight.current = true
    setSavingSlides(true)
    const projectId = project.id
    try {
      const data = await api(`${apiBase}/slides`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slides }),
      })
      // Reindex drafts with their original slide objects when slides move or are inserted.
      // A changed or deleted slide must never receive another slide's stale draft.
      const pending = []
      project.slides.forEach((original, index) => {
        const key = draftKey(workspaceKey, index)
        let stored = draftCache.current.get(key)
        if (!stored) { try { stored = JSON.parse(localStorage.getItem(key)) } catch { /* No local draft. */ } }
        const newIndex = slides.indexOf(original)
        if (key !== savedDraftKey && newIndex >= 0 && stored?.base === JSON.stringify(original)) {
          pending.push([draftKey(workspaceKey, newIndex), { ...stored, base: JSON.stringify((data.slides || slides)[newIndex]) }])
        }
        draftCache.current.delete(key)
        try { localStorage.removeItem(key) } catch { /* In-memory storage remains available. */ }
      })
      pending.forEach(([key, value]) => {
        draftCache.current.set(key, value)
        try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* In-memory draft remains available. */ }
      })
      setProject((current) => current?.id === projectId && current?.videoId === project.videoId ? {
        ...current, slides: data.slides || slides, quality: data.quality, assets: data.assets || current.assets,
      } : current)
      if (message) setToast({ type: 'success', text: message })
      return true
    } catch (error) {
      setToast({ type: 'error', text: (savedDraftKey ? 'Kaydedilemedi. Değişikliklerin taslakta duruyor. ' : 'Kaydedilemedi. Kayıtlı slaytlar değiştirilmedi. ') + error.message })
      return false
    } finally {
      saveInFlight.current = false
      setSavingSlides(false)
    }
  }

  const saveDraft = () => {
    if (selectedSlide === null || !draft || savingSlides) return
    const next = [...project.slides]
    next[selectedSlide] = { ...draft, bullets: draft.bullets.filter(Boolean), manuallyEdited: true }
    return persistSlides(next, 'Slayt kaydedildi.', draftKey(workspaceKey, selectedSlide))
  }

  const addSlide = async () => {
    if (saveInFlight.current || !project) return
    const at = selectedSlide === null ? project.slides.length : selectedSlide + 1
    const next = [...project.slides]
    next.splice(at, 0, {
      title: 'Yeni slayt', bullets: [], code: null, narration: '', level: 'topic',
      sourceSectionIds: [], sourceTitles: [], manuallyEdited: true,
    })
    if (await persistSlides(next, 'Yeni slayt eklendi.')) setSelectedSlide(at)
  }

  const regenerateSlide = async () => {
    if (selectedSlide === null || !project || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Yeniden üretim hazırlanıyor' })
    try {
      const response = await api(`${apiBase}/slides/${selectedSlide}/regenerate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: llm.provider, apiKey: llm.apiKey, style: llm.style,
          agentCommand: llm.agentCommand, geminiModel: llm.geminiModel,
          openaiEndpoint: llm.openaiEndpoint, openaiModel: llm.openaiModel, timeout: llm.timeout,
        }),
      })
      rememberActiveJob({ id: response.jobId, type: 'regenerate' }, { view: 'video', stage: 'script' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const deleteSlide = async (index) => {
    if (saveInFlight.current || !window.confirm('“' + (project.slides[index].title || 'Başlıksız slayt') + '” silinsin mi?')) return
    const next = project.slides.filter((_, slideIndex) => slideIndex !== index)
    if (await persistSlides(next, 'Slayt silindi.')) setSelectedSlide(next.length ? Math.min(index, next.length - 1) : null)
  }

  const moveSlide = async (index, delta) => {
    if (saveInFlight.current) return
    const target = index + delta
    if (target < 0 || target >= project.slides.length) return
    const next = [...project.slides]
    ;[next[index], next[target]] = [next[target], next[index]]
    if (await persistSlides(next)) setSelectedSlide(target)
  }

  const dropSlide = async (targetIndex) => {
    if (saveInFlight.current) return
    if (draggedSlide === null || draggedSlide === targetIndex) return
    const next = [...project.slides]
    const [moved] = next.splice(draggedSlide, 1)
    next.splice(targetIndex, 0, moved)
    setDraggedSlide(null)
    if (await persistSlides(next, 'Slayt sırası güncellendi.')) setSelectedSlide(targetIndex)
  }

  const generate = async () => {
    if (!project || !selectedSections.size || !effectiveSelectedCount || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Hazırlanıyor' })
    try {
      const response = await api(`${apiBase}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...llm,
          sectionIndexes: [...selectedSections].sort((a, b) => a - b),
          insertAfter: llm.insertMode === 'after' ? selectedSlide : null,
        }),
      })
      rememberActiveJob({ id: response.jobId, type: 'script' }, { view: 'video', stage: 'script' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const resetGenerationProgress = async () => {
    if (!project || activeJob) return
    try {
      const data = await api(`${apiBase}/generation/reset`, { method: 'POST' })
      setProject((current) => ({ ...current, generation: data.generation }))
      setToast({ type: 'success', text: 'Üretim işaretleri sıfırlandı; mevcut slaytlar korunuyor.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const refreshQuality = async () => {
    if (!project || activeJob) return
    try {
      const data = await api(`${apiBase}/quality`, { method: 'POST' })
      setProject((current) => current ? { ...current, quality: data.quality } : current)
      setToast({ type: 'success', text: 'Anlatı kalite denetimi yenilendi.' })
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const preview = async () => {
    if (!project) return
    try {
      const data = await api(`${apiBase}/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: selectedSlide || 0, slide: draft, theme: video.theme }),
      })
      setPreviewUrl(data.url)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const render = async (forceAudioRegeneration = false) => {
    if (!project?.slides.length || activeJob) return
    setJobState({ status: 'queued', progress: 0, message: 'Render hazırlanıyor' })
    try {
      const response = await api(`${apiBase}/render`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...video, ttsBackend: effectiveBackend(video), forceAudioRegeneration }),
      })
      const nextJob = { id: response.jobId, type: 'video' }
      rememberActiveJob(nextJob, { view: 'video', stage: 'video' })
    } catch (error) {
      setJobState(null)
      setToast({ type: 'error', text: error.message })
    }
  }

  const cancelRender = async () => {
    if (activeJob?.type !== 'video' || jobState?.status === 'cancelling') return
    try {
      const job = await api(`/api/jobs/${activeJob.id}/cancel`, { method: 'POST' })
      setJobState(job)
    } catch (error) {
      setToast({ type: 'error', text: error.message })
    }
  }

  const renderSourceIngest = () => (
    <section className="panel source-ingest">
      <div className="section-heading">
        <span className="kicker">YENİ KAYNAK</span>
        <h2>Kaynağını ekle.</h2>
        <p>PDF, PowerPoint veya Markdown dosyandan bir ders projesi oluştur.</p>
      </div>
      <label
        className={`drop-zone ${isDropping ? 'is-dropping' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setIsDropping(true) }}
        onDragLeave={() => setIsDropping(false)}
        onDrop={(event) => { event.preventDefault(); setIsDropping(false); uploadSource(event.dataTransfer.files[0]) }}
      >
        <input aria-label="Kaynak dosyası seç" disabled={busy} type="file" accept=".md,.pptx,.pdf" onChange={(event) => uploadSource(event.target.files[0])} />
        <span className="upload-orbit"><UploadCloud size={28} /></span>
        <strong>{busy ? 'Dosya işleniyor…' : 'Dosyayı buraya bırak'}</strong>
        <small>veya seçmek için tıkla · en fazla 100 MB</small>
      </label>
      <div className="path-divider"><span>ya da bu bilgisayardaki yolu kullan</span></div>
      <div className="path-input">
        <input aria-label="Kaynak dosyasının bilgisayardaki yolu" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="D:\\dersler\\konu.pdf" />
        <button className="button ghost" onClick={parsePath} disabled={!sourcePath.trim() || busy}>Ayrıştır</button>
      </div>
      <details className="options-disclosure">
        <summary><Settings2 size={16} /> PDF seçenekleri{(pageMode || visionEnrich || extractDiagrams) && <span className="memory-pill">Özel ayar</span>}<ChevronRight size={16} className="disclosure-chevron" /></summary>
      <Toggle
        wide
        checked={pageMode}
        onChange={setPageMode}
        label="Sayfaları birebir slayt olarak kullan (yalnızca PDF)"
        hint={pageMode
          ? 'PDF sayfasının görünümü korunur; yalnızca sesli anlatım hazırlanır. Bu seçeneği PDF dosyalarıyla kullan.'
          : 'İçerik, seçtiğin slayt temasıyla yeniden düzenlenir.'}
      />
      <Toggle
        wide
        checked={visionEnrich}
        onChange={setVisionEnrich}
        label="Görsel anlama kullan (yalnızca PDF)"
        hint={visionEnrich
          ? 'Görsel ağırlıklı sayfalar Gemini ile açıklanır. Sayfa başına ek API maliyeti oluşabilir.'
          : 'Kapalıyken görsel-ağırlıklı sayfalar normal modda atlanır, sayfa modunda ise metinsiz bırakılır.'}
      />
      {visionEnrich && !bootstrap.keysConfigured.gemini && (
        <label className="field wide">
          <span>Gemini API anahtarı (sadece görsel anlama için)</span>
          <input type="password" value={visionApiKey} onChange={(e) => setVisionApiKey(e.target.value)} placeholder="Gemini API anahtarın" />
        </label>
      )}
      <Toggle
        wide
        checked={extractDiagrams}
        onChange={setExtractDiagrams}
        label="PDF içindeki görselleri kullan"
        hint={extractDiagrams
          ? 'PDF içindeki mevcut görseller slayta eklenir. Ücretsizdir; yapay zeka kullanmaz.'
          : 'Kapalıyken slaytlar her zamanki gibi sadece metinle (başlık/madde) oluşturulur.'}
      />
      </details>
    </section>
  )

  const renderSource = () => (
    <div className="stage-grid source-grid">
      {project?.videoId ? <section className="panel course-source-summary"><span className="kicker">BU VİDEONUN KAYNAKLARI</span><h3>{project.name}</h3><span className="memory-pill">{project.pageMode ? "PDF üzerinden anlatım · 1 sayfa = 1 slayt" : "Yeni slaytlar oluştur"}</span><p>Kaynaklar aşağıdaki sırayla işlenir. İstersen sağdaki bölüm listesinden kapsamı daraltabilirsin.</p><ol className="course-source-order">{project.sourceNames.map((name,index) => <li key={index}><span>{index + 1}</span><strong>{name}</strong></li>)}</ol><p>Başka kaynaklarla çalışmak için ders alanından yeni bir video oluştur. Bu videonun anlatısı ve çıktıları ayrı saklanır.</p><button className="button ghost" onClick={() => { setCourseTab('sources'); setView('course') }}><ArrowLeft size={16} /> Dersin kaynaklarına dön</button></section> : renderSourceIngest()}

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
        {project && <label className="search-field"><Search size={16} /><input aria-label="Bölümlerde ara" placeholder="Bölümlerde ara…" value={sectionQuery} onChange={(e) => setSectionQuery(e.target.value)} /></label>}
        {project && !project.sections.some((section) => matches(section.title + ' ' + section.text, sectionQuery)) && <p className="list-empty">Bu aramayla eşleşen bölüm yok.</p>}
        {!project ? <EmptyState icon={BookOpen} title="İçerik burada şekillenecek">Bir kaynak eklediğinde başlık yapısını ve her bölümün uzunluğunu burada göreceksin.</EmptyState> : (
          <div className="section-list">
            {project.sections.map((section, index) => {
              if (!matches(section.title + ' ' + section.text, sectionQuery)) return null
              const selected = selectedSections.has(index)
              const completed = completedSections.has(index)
              return <button key={`${section.title}-${index}`} aria-pressed={selected} className={`section-row ${selected ? 'selected' : ''} ${completed ? 'completed' : ''}`} onClick={() => toggleSection(index)}>
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
      <details className="panel generation-console" key={project?.id} open={!project?.slides.length || activeJob?.type === 'script' || undefined}>
        <summary><WandSparkles size={20} /><span>Yeni anlatı üret<small>{selectedSections.size} bölüm seçili · Sağlayıcı, üslup ve süre ayarları</small></span><ChevronRight size={18} className="disclosure-chevron" /></summary>
        <div className="provider-tabs">
          {[['agent', 'Claude Agent'], ['gemini', 'Gemini API'], ['openai', 'OpenAI uyumlu']].map(([id, label]) => <button key={id} className={llm.provider === id ? 'active' : ''} onClick={() => setLlm({ ...llm, provider: id, apiKey: '' })}>{label}</button>)}
        </div>
        <div className="form-grid">{renderProviderFields()}
          <label className="field"><span>Bir LLM çağrısındaki kaynak bölümü</span><input disabled={llm.singleRequest} type="number" min="1" max="20" value={llm.maxSections} onChange={(e) => setLlm({ ...llm, maxSections: Number(e.target.value) })} /><small>{project?.pageMode ? 'Birlikte okunacak PDF sayfası sayısı. Her sayfa yine ayrı slayt olur; başlangıç için 4 sayfa uygundur.' : 'Üretilen slayt sayısı değil; PDF/PPT içinden aynı çağrıya konan bölüm sayısıdır.'}</small></label>
          <label className="field"><span>Timeout</span><div className="input-suffix"><input type="number" min="60" step="60" value={llm.timeout} onChange={(e) => setLlm({ ...llm, timeout: Number(e.target.value) })} /><b>sn</b></div></label>
          <label className="field wide"><span>Üslup yönlendirmesi</span><textarea rows="2" value={llm.style} onChange={(e) => setLlm({ ...llm, style: e.target.value })} placeholder="Örn. kavramsal, sakin, klinik örneklerle…" /></label>
          <label className="field"><span>Yeni slaytların yeri</span><select value={llm.insertMode} onChange={(e) => setLlm({ ...llm, insertMode: e.target.value })}><option value="append">Listenin sonu</option><option value="after">Seçili slayttan sonra</option></select></label>
          <Toggle checked={llm.singleRequest} disabled={!oneShotSafe} onChange={(value) => setLlm({ ...llm, singleRequest: value })} label="Tek istekte gönder" hint={oneShotSafe ? 'En fazla 12 bölüm / 24.000 karakter' : `${selectedSections.size} bölüm ve ${selectedSourceCharacters.toLocaleString('tr-TR')} karakter: güvenli sınırın üzerinde`} />
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
          <div className="session-note wide"><Zap size={14} /><span>{llm.singleRequest ? 'Seçili bölümlerin tamamı tek model çağrısına gider. Parça ve oturum sürdürme ayarları bu çağrıda kullanılmaz.' : `Seçili kaynaklar en fazla ${llm.maxSections} bölümlük çağrılara ayrılır. ${llm.provider === 'agent' && llm.reuseSession ? 'Claude çağrıları aynı oturumda sürer.' : 'Her çağrıya önceki anlatımların sınırlı bir özeti de eklenir; geçmişin tamamı gönderilmez.'}`}</span></div>
        </div>
        <button className="button primary generate-button" onClick={generate} disabled={!project || !selectedSections.size || !effectiveSelectedCount || Boolean(activeJob)}><WandSparkles size={17} /> {activeJob?.type === 'script' ? 'Üretiliyor…' : effectiveSelectedCount ? `${effectiveSelectedCount} kalan bölümden üret` : 'Seçimde bekleyen bölüm yok'}</button>
        <ProgressStrip job={activeJob?.type === 'script' || jobState?.kind === 'script' ? jobState : null} />
      </details>

      <section className="panel slide-rail">
        <div className="panel-toolbar"><div><span className="kicker">AKIŞ</span><h3>{project?.slides.length || 0} slayt</h3></div><button className="icon-button" onClick={addSlide} disabled={savingSlides || Boolean(activeJob)} title="Yeni slayt"><Plus size={18} /></button></div>
        <label className="search-field"><Search size={16} /><input aria-label="Slaytlarda ara" placeholder="Slaytlarda ara…" value={slideQuery} onChange={(e) => setSlideQuery(e.target.value)} /></label>
        {project?.slides.length > 0 && !project.slides.some((slide) => matches(slide.title + ' ' + slide.narration, slideQuery)) && <p className="list-empty">Eşleşen slayt yok.</p>}
        {project?.slides.length > 0 && <div className={`quality-card ${quality?.status || 'unknown'}`}>
          <span className="quality-icon">{quality?.status === 'passed' ? <ShieldCheck size={19} /> : <TriangleAlert size={19} />}</span>
          <span>
            <strong>{quality?.status === 'passed' ? 'Anlatı temiz' : quality?.status === 'blocked' ? 'Düzeltme gerekli' : 'Gözden geçir'}</strong>
            <small>{quality?.score ?? '—'}/100 · {quality?.errorCount || 0} kritik · {quality?.warningCount || 0} uyarı</small>
          </span>
          <button type="button" onClick={refreshQuality} disabled={Boolean(activeJob)} aria-label="Kalite denetimini yenile"><RefreshCw size={13} /></button>
        </div>}
        {snapshots.length > 0 && <details className="snapshot-disclosure"><summary>Önceki sürümler ({snapshots.length})<ChevronRight size={14} className="disclosure-chevron" /></summary><div className="chapter-list snapshot-list">
          {snapshots.map((snap) => (
            <div key={snap.filename} className="chapter-item">
              <span>{SNAPSHOT_REASON_LABELS[snap.reason] || snap.reason} <small>({snap.slideCount ?? '?'} slayt, {new Date(snap.savedAt).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })})</small></span>
              <a onClick={(event) => { event.preventDefault(); restoreSnapshotVersion(snap.filename) }} href="#restore">Geri Yükle</a>
            </div>
          ))}
        </div></details>}
        {!project?.slides.length ? <EmptyState icon={Layers3} title="Anlatı henüz boş">Seçili bölümlerden ilk slayt setini üret.</EmptyState> : <div className="slide-list">
          {project.slides.map((slide, index) => !matches(slide.title + ' ' + slide.narration, slideQuery) ? null : <article
            key={`${slide.title}-${index}`}
            id={`slide-${index}`}
            className={`slide-card ${selectedSlide === index ? 'active' : ''}`}
            tabIndex={0}
            aria-label={`Slayt ${index + 1}: ${slide.title || 'Başlıksız'}`}
            aria-current={selectedSlide === index ? 'true' : undefined}
            onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); setSelectedSlide(index) } }}
            draggable={!savingSlides && !activeJob}
            onDragStart={() => setDraggedSlide(index)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => dropSlide(index)}
            onClick={() => setSelectedSlide(index)}
          >
            <GripVertical size={16} className="grip" />
            <span className="slide-number">{String(index + 1).padStart(2, '0')}</span>
            <span className="slide-copy"><strong>{slide.title || 'Başlıksız'}</strong><small>{slide.level === 'chapter' ? 'Bölüm kapağı' : `${slide.bullets.length} madde`}</small></span>
            <span className="slide-actions">
              <button onClick={(event) => { event.stopPropagation(); moveSlide(index, -1) }} disabled={savingSlides || Boolean(activeJob) || index === 0} aria-label="Yukarı taşı"><ArrowUp size={14} /></button>
              <button onClick={(event) => { event.stopPropagation(); moveSlide(index, 1) }} disabled={savingSlides || Boolean(activeJob) || index === project.slides.length - 1} aria-label="Aşağı taşı"><ArrowDown size={14} /></button>
              <button className="danger" onClick={(event) => { event.stopPropagation(); deleteSlide(index) }} disabled={savingSlides || Boolean(activeJob)} aria-label="Sil"><Trash2 size={14} /></button>
            </span>
          </article>)}
        </div>}
      </section>

      <section className="panel editor-panel">
        <div className="panel-toolbar"><div><span className="kicker">DÜZENLEYİCİ</span><h3>{draft ? `Slayt ${(selectedSlide || 0) + 1}` : 'Bir slayt seç'}</h3>{draft && <span className={`draft-state ${draftDirty ? 'dirty' : ''}`}>{savingSlides ? 'Kaydediliyor…' : draftDirty ? 'Kaydedilmemiş taslak' : 'Tüm değişiklikler kaydedildi'}</span>}</div>{draft && <button className="button primary compact" disabled={!draftDirty || savingSlides || Boolean(activeJob)} title="Kaydet (Ctrl+S)" onClick={saveDraft}><Save size={15} /> Kaydet</button>}</div>
        {!draft ? <EmptyState icon={Settings2} title="Ayrıntıları düzenle">Akıştan bir slayt seçerek başlık, maddeler ve anlatım metnini değiştirebilirsin.</EmptyState> : <fieldset className="editor-form" disabled={savingSlides || Boolean(activeJob)}>
          {draft.sourceSectionIds?.length > 0 ? <div className="source-row">
            <span className="source-label"><Layers3 size={13} /> Kaynak: {draft.sourceTitles?.join(', ') || `${draft.sourceSectionIds.length} bölüm`}</span>
            <button type="button" className="button ghost compact" onClick={regenerateSlide} disabled={Boolean(activeJob)}>
              <RefreshCw size={13} /> Bu slaytı yeniden üret
            </button>
          </div> : <div className="source-row muted"><span className="source-label">Elle eklendi · kaynağı yok</span></div>}
          <ProgressStrip job={activeJob?.type === 'regenerate' || jobState?.kind === 'regenerate' ? jobState : null} />
          <div className="editor-heading-fields">
          <label className="field"><span>Başlık</span><input value={draft.title} onChange={(e) => updateDraft({ ...draft, title: e.target.value })} /></label>
          <label className="field"><span>Tür</span><select value={draft.level} onChange={(e) => updateDraft({ ...draft, level: e.target.value })}><option value="topic">Konu slaytı</option><option value="chapter">Bölüm kapağı</option></select></label>
          </div>
          <label className="field"><span>Tam anlatım metni</span><textarea rows="11" value={draft.narration} onChange={(e) => updateDraft({ ...draft, narration: e.target.value })} /></label>
          <label className="field"><span>Maddeler <em>satır başına bir tane</em></span><textarea rows="5" value={draft.bullets.join('\n')} onChange={(e) => updateDraft({ ...draft, bullets: e.target.value.split('\n') })} /></label>
          <details className="editor-code" key={selectedSlide} open={Boolean(draft.code) || undefined}><summary>Kod örneği <span>İsteğe bağlı</span><ChevronRight size={14} className="disclosure-chevron" /></summary>
          <label className="field"><span>Kod</span><textarea className="code-input" rows="5" value={draft.code || ''} onChange={(e) => updateDraft({ ...draft, code: e.target.value || null })} placeholder="İsteğe bağlı" /></label>
          </details>
          <div className="editor-meta"><Clock3 size={14} /><span>Yaklaşık {Math.max(1, Math.round((draft.narration || '').split(/\s+/).length / 2.2))} sn anlatım</span></div>
          {selectedQualityIssues.length > 0 && <div className="quality-issues">
            <strong><TriangleAlert size={14} /> Bu slaytta {selectedQualityIssues.length} bulgu</strong>
            {selectedQualityIssues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}>{issue.message}</div>)}
          </div>}
        </fieldset>}
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
        {project?.outputs?.video && <video className="result-video" controls src={`${apiBase}/output/video`} />}
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
          {REMOTE_ENGINES.includes(video.ttsProvider) && <RemoteGpuControls video={video} setVideo={setVideo} api={api} />}
          {video.ttsProvider === 'elevenlabs' && <label className="field"><span>ElevenLabs API anahtarı</span><input type="password" value={video.elevenlabsKey} onChange={(e) => setVideo({ ...video, elevenlabsKey: e.target.value })} /></label>}
          {video.ttsProvider === 'coqui' && effectiveBackend(video) === 'local' && (
            <label className="field wide">
              <span>Paralel model sayısı</span>
              <select
                value={video.coquiParallelWorkers}
                onChange={(e) => setVideo({ ...video, coquiParallelWorkers: Number(e.target.value) })}
              >
                <option value={1}>Kapalı — tek tek üret (en stabil)</option>
                <option value={2}>2 paralel model (~4GB VRAM gerekir, ölçülen ~1.85x hız)</option>
                <option value={3}>3 paralel model (~6GB VRAM gerekir, ölçülen ~2.7x hız)</option>
              </select>
              <small>
                Her worker kendi model kopyasını belleğe yükler. VRAM'i az olan bir bilgisayarda
                yüksek bir değer sistemin çökmesine (mavi ekran) yol açabilir — emin değilsen kapalı bırak.
                Yetersiz bellekte yakalanabilir bir hata (CUDA belleği doldu) alınırsa sistem otomatik
                olarak daha az worker'la yeniden dener.
              </small>
            </label>
          )}
          {video.ttsProvider === 'chatterbox' && (
            <label className="field wide">
              <span>Paralel GPU model sayısı</span>
              <select
                value={video.chatterboxParallelWorkers}
                onChange={(e) => setVideo({ ...video, chatterboxParallelWorkers: Number(e.target.value) })}
              >
                <option value={1}>1 model — sıralı üretim (en stabil)</option>
                <option value={2}>2 paralel model — slaytlar iki GPU worker’ına bölünür</option>
              </select>
              <small>İlk model yüklenmesi beklenir; sonrasında worker'lar kendi slaytlarını sırayla üretir. RTX 4060 8 GB için iki model güvenli üst sınırdır. CUDA belleği yetmezse sistem otomatik olarak tek modele iner.</small>
            </label>
          )}
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
        <div className="render-actions">
          <button className="button primary render-button" onClick={() => render(false)} disabled={!project?.slides.length || Boolean(activeJob) || qualityBlocked}><Clapperboard size={17} /> {activeJob?.type === 'video' ? 'Render sürüyor…' : project?.outputs?.video ? 'Videoyu güncelle' : 'Videoyu oluştur'}</button>
          {activeJob?.type === 'video' && <button type="button" className="button danger" onClick={cancelRender} disabled={jobState?.status === 'cancelling'}><X size={16} /> {jobState?.status === 'cancelling' ? 'İptal ediliyor…' : 'Renderı iptal et'}</button>}
          {project?.outputs?.video && !activeJob && <button type="button" className="button ghost" onClick={() => render(true)} disabled={qualityBlocked}><RefreshCw size={16} /> Seçili ses modeliyle yeniden oluştur</button>}
        </div>
        <ProgressStrip job={activeJob?.type === 'video' || jobState?.kind === 'video' ? jobState : null} />
        {project?.outputs?.video && <div className="output-actions"><a className="button ghost" href={`${apiBase}/output/video`}><MonitorPlay size={16} /> Videoyu aç</a>{bootstrap.canOpenFolder && <button className="button ghost" onClick={() => api(`${apiBase}/open-output`, { method: 'POST' })}><FolderOpen size={16} /> Klasörü aç</button>}</div>}
        {project?.slides?.length > 0 && <div className="export-row">
          <span className="source-label"><FileText size={13} /> Çalışma materyali (ücretsiz, anında)</span>
          <div className="export-links">
            <a className="button ghost compact" href={`${apiBase}/export/pdf?theme=${encodeURIComponent(video.theme)}`}>Slaytlar (.pdf)</a>
            <a className="button ghost compact" href={`${apiBase}/export/notes`}>Ders Notu (.md)</a>
            <a className="button ghost compact" href={`${apiBase}/export/transcript`}>Transkript (.txt)</a>
            <a className="button ghost compact" href={`${apiBase}/export/anki`}>Anki (.tsv)</a>
            <a className="button ghost compact" href={`${apiBase}/export/quiz`}>Kendini Test Et (.md)</a>
            <a className="button ghost compact" href={`${apiBase}/export/srt`}>Altyazı (.srt)</a>
            <a className="button ghost compact" href={`${apiBase}/export/vtt`}>Altyazı (.vtt)</a>
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
                <a href={`${apiBase}/chapters/download/${c.videoFile}`}>MP4</a>
                <a href={`${apiBase}/chapters/download/${c.audioFile}`}>MP3</a>
              </div>
            ))}
            {chapters.youtubeChaptersReady && <a className="button ghost compact" href={`${apiBase}/chapters/download/youtube-chapters.txt`}>YouTube Chapters (.txt)</a>}
          </div>}
        </div>}
      </section>
    </div>
  )

  if (!bootstrap) return <div className="app-loading">{bootstrapError ? <><span className="brand-mark error-mark"><CircleAlert /></span><h1>Yerel servis bağlantısı yok</h1><p>{bootstrapError}</p><button className="button primary" onClick={() => setBootstrapRetry((value) => value + 1)}><RefreshCw size={16} /> Yeniden bağlan</button></> : <><BrandMark size={64} /><h1>Kavra</h1><p>Çalışma alanın hazırlanıyor…</p><LoaderCircle className="spin" /></>}</div>

  if (view === 'study') return <StudyWorkspace initialCourse={studyCourse} onClose={() => setView('dashboard')} onOpenCourse={id => openProjectHub(id)} />

  if (view === 'course' && course) return <CourseWorkspace key={course.id} initialCourse={course}
    onStudy={data => { setStudyCourse(data); setView('study') }}
    bootstrap={bootstrap} api={api} llm={llm} setLlm={setLlm} initialTab={courseTab} activeVideoJob={Boolean(activeJob)}
    onClose={() => { setView('dashboard'); api('/api/bootstrap').then(setBootstrap).catch(() => {}) }}
    onOpenVideo={async (data) => {
      if (activeJob && data.apiBase !== apiBase) throw new Error('Devam eden video işleminin tamamlanmasını bekle.')
      applyProject(data); setCourseTab('videos'); setStage(data.slides.length ? 'script' : 'source'); setView('video')
    }} />

  if (view === 'dashboard') {
    return (
      <div className="dashboard-shell">

        <WorkspaceHeader onHome={() => setView('dashboard')} current="Çalışma alanı" />
        <div className="dashboard-hero">
          <div className="home-intro"><div><span className="kicker">ÇALIŞMA ALANIN</span><h1>Bir kaynaktan, birçok öğrenme yolu.</h1><p>Derslerini düzenle, anlatımlı videolar hazırla ve kartlarla bilgini tazele. Kaldığın yerden devam et ya da yeni bir kaynak ekle.</p></div>
          <div className="hero-summary"><div><strong>{bootstrap.projects.length}</strong><span>proje</span></div><div><strong>{bootstrap.projects.reduce((sum, item) => sum + item.slides, 0)}</strong><span>slayt</span></div></div></div>
        </div>
        <button className="study-home-entry" onClick={() => { setStudyCourse(null); setView('study') }}><Clock3 size={28}/><span><strong>Çalışma takibi</strong><small>Görevlerini planla, odak süreni tut ve ilerlemeni gör.</small></span><ArrowRight size={21}/></button>
        <div className="dashboard-grid">
          <section className="panel dashboard-projects">
            <div className="panel-toolbar"><div><span className="kicker">KİTAPLIĞIN</span><h3>Projelerin</h3></div><span className="memory-pill">{bootstrap.projects.length} proje</span></div>
            <label className="search-field"><Search size={17} /><input aria-label="Projelerde ara" placeholder="Proje adıyla ara…" value={projectQuery} onChange={(e) => setProjectQuery(e.target.value)} /></label>
            <div className="dashboard-project-list">
              {bootstrap.projects.length === 0 && (
                <EmptyState icon={FolderOpen} title="Henüz proje yok">Yeni kaynak alanından ilk dosyanı ekleyerek başla.</EmptyState>
              )}
              {bootstrap.projects.length > 0 && !bootstrap.projects.some((item) => matches(item.name || projectLabel(item.id), projectQuery)) && <p className="list-empty">Bu adla bir proje bulunamadı.</p>}
              {bootstrap.projects.filter((item) => matches(item.name || projectLabel(item.id), projectQuery)).map((item) => (
                <button key={item.id} className="dashboard-project-card" onClick={() => openProjectHub(item.id)} disabled={busy}>
                  <FolderOpen size={20} />
                  <span className="dashboard-project-card-main"><strong title={item.name || item.id}>{item.name || projectLabel(item.id)}</strong><small>{item.kind === 'course' ? `${item.sourceCount} kaynak · ${item.videoCount} video çalışması` : `${item.sections} bölüm · ${item.slides} slayt · Eski proje`}</small></span>
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          </section>
          <section className="panel course-new-project"><div><span className="kicker">YENİ DERS</span><h2>Bir ders, tüm kaynakların.</h2></div><p>Dersine bir ad ver. Ardından haftalık PDF, PowerPoint veya Markdown dosyalarını aynı projeye ekle.</p>
            <form onSubmit={async event => {
              event.preventDefault(); if (busy || !newCourseName.trim() || activeJob) return; setBusy(true)
              try { const data = await api('/api/projects', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:newCourseName}) }); applyProject(data); setNewCourseName(''); setBootstrap(await api('/api/bootstrap')) }
              catch(error){ setToast({type:'error',text:error.message}) } finally { setBusy(false) }
            }}><label className="field"><span>Ders adı</span><input maxLength={160} value={newCourseName} onChange={e => setNewCourseName(e.target.value)} placeholder="Örn. Biyoloji · Güz dönemi" /></label><button style={{marginTop:18,width:'100%'}} className="button primary" disabled={busy || Boolean(activeJob) || !newCourseName.trim()}><Plus size={17} /> Ders projesi oluştur</button></form>
            <small className="health-hint">Her kaynak için ayrı video veya birkaç kaynağı kapsayan bir kart destesi oluşturabilirsin.</small>
          </section>
        </div>
        {toast && <div role={toast.type === 'error' ? 'alert' : 'status'} className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button aria-label="Bildirimi kapat" onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'hub') {
    return (
      <div className="dashboard-shell">

        <WorkspaceHeader onHome={() => setView('dashboard')} projectName={project?.id} onProject={() => setView(course ? 'course' : 'hub')} current="Genel bakış" />
        <div className="dashboard-hero">
          <span className="kicker">PROJE</span>
          <h1>{projectLabel(project?.id)}</h1>
          <p>Kaynağın hazır. İçeriğini bir derse dönüştür veya tekrar kartlarınla çalış.</p>
          <div className="project-facts"><span>{project?.sections.length || 0} kaynak bölümü</span><span>{project?.slides.length || 0} slayt</span><span>{project?.outputs?.video ? 'Video hazır' : 'Video henüz üretilmedi'}</span></div>
        </div>
        <div className="hub-modules">
          <button className="hub-module-card" onClick={() => { setStage(project?.slides.length ? 'script' : 'source'); setView('video') }}>
            <Clapperboard size={30} />
            <strong>Dersini hazırla</strong>
            <small>{project?.outputs?.video ? 'Video hazır — düzenlemeye devam et' : project?.slides.length ? 'Anlatı hazır, render bekliyor' : 'Henüz başlanmadı'}</small>
            <span className="module-action">Ders stüdyosunu aç <ArrowRight size={16} /></span>
          </button>
          <button className="hub-module-card" onClick={() => setView('anki')}>
            <Layers3 size={30} />
            <strong>Kartlarla öğren</strong>
            <small>Destelerini düzenle, zamanı gelen kartları çalış ve öğrenme ilerlemeni takip et.</small>
            <span className="module-action">Flashcard destelerine git <ArrowRight size={16} /></span>
          </button>
        </div>
        {toast && <div role={toast.type === 'error' ? 'alert' : 'status'} className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button aria-label="Bildirimi kapat" onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'anki' && !activeDeck) {
    return (
      <div className="dashboard-shell">

        <WorkspaceHeader onHome={() => setView('dashboard')} projectName={project?.id} onProject={() => setView(course ? 'course' : 'hub')} current="Flashcard" />
        <div className="dashboard-hero">
          <span className="kicker">FLASHCARD ÇALIŞMA</span>
          <h1>{projectLabel(project?.id)}</h1>
          <p>Bir deste seç, tekrarlarına başla. Kendi kartlarını ücretsiz ekleyebilir ya da dersinden yeni desteler oluşturabilirsin.</p>
        </div>
        <div className="anki-overview">
          <details className="panel deck-create" open>
          <summary><Plus size={19} /> Yeni deste oluştur<ChevronRight size={16} className="disclosure-chevron" /></summary>
          <div className="deck-create-content">
          <div className="provider-tabs compact">
            <button className={deckKind === 'static' ? 'active' : ''} onClick={() => setDeckKind('static')}>Dersten · ücretsiz</button>
            <button className={deckKind === 'llm' ? 'active' : ''} onClick={() => setDeckKind('llm')}>Yapay Zeka ile Üret</button>
          </div>
          <div className="deck-create-row">
            <input aria-label="Yeni deste adı" value={newDeckName} onChange={(e) => setNewDeckName(e.target.value)} placeholder="Yeni deste adı, ör. Sınav Öncesi" />
            <button className="button primary" onClick={createFlashcardDeck} disabled={!newDeckName.trim() || flashcardBusy || !project?.slides.length}>
              <Plus size={16} /> {activeJob?.type === 'flashcards' ? 'Oluşturuluyor…' : deckKind === 'llm' ? 'Yapay Zeka ile Oluştur' : 'Dersten deste oluştur'}
            </button>
          </div>
          {deckKind === 'static' ? (
            <small className="health-hint">Dersindeki başlık ve açıklamalardan kartlar oluşturur. Ücretsizdir; ek bir yapay zeka çağrısı yapmaz.</small>
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
          <button className="button ghost" disabled={flashcardBusy || !newDeckName.trim()} onClick={async () => {
            setFlashcardBusy(true)
            try {
              const deck = await api(`/api/projects/${project.id}/flashcards/blank`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newDeckName }),
              })
              setActiveDeck(deck)
              setNewDeckName('')
            } catch (error) { setToast({ type: 'error', text: error.message }) }
            finally { setFlashcardBusy(false) }
          }}><Plus size={16} /> Boş deste oluştur · elle ekle / içe aktar</button>
          </div></details>
          <section className="panel deck-library">
          <div className="panel-toolbar"><div><span className="kicker">TEKRAR ZAMANI</span><h3>Destelerin</h3></div><span className="memory-pill">{flashcardDecks.reduce((sum, deck) => sum + deck.summary.dueCount, 0)} kart sırada</span></div>
          <label className="search-field"><Search size={16} /><input aria-label="Destelerde ara" value={deckQuery} onChange={(e) => setDeckQuery(e.target.value)} placeholder="Deste adıyla ara…" /></label>
          <div className="deck-list">
            {flashcardBusy && flashcardDecks.length === 0 && <small className="health-hint">Yükleniyor…</small>}
            {!flashcardBusy && flashcardDecks.length === 0 && (
              <EmptyState icon={Layers3} title="Henüz deste yok">Yeni deste oluştur alanından ilk desteni ekle.</EmptyState>
            )}
            {flashcardDecks.length > 0 && !flashcardDecks.some((deck) => matches(deck.name, deckQuery)) && <p className="list-empty">Eşleşen deste yok.</p>}
            {flashcardDecks.filter((deck) => matches(deck.name, deckQuery)).map((deck) => (
              <div key={deck.id} className="deck-card">
                <button className="deck-open" disabled={flashcardBusy} onClick={() => openFlashcardDeck(deck.id)}>
                <Layers3 size={24} />
                <span className="deck-card-main">
                  <strong>{deck.name}</strong>
                  <small>{deck.kind === 'llm' ? 'YZ destekli' : 'Statik'} · {deck.summary.totalCards} kart · {deck.summary.dueCount} bugün sırada</small>
                </span>
                <ChevronRight size={17} /></button>
                <button className="icon-button danger" aria-label={`${deck.name} destesini sil`} title="Desteyi sil" onClick={(e) => { e.stopPropagation(); deleteFlashcardDeck(deck.id) }}><Trash2 size={14} /></button>
              </div>
            ))}
          </div></section>
        </div>
        {toast && <div role={toast.type === 'error' ? 'alert' : 'status'} className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button aria-label="Bildirimi kapat" onClick={() => setToast(null)}><X size={16} /></button></div>}
      </div>
    )
  }

  if (view === 'anki' && activeDeck) {
    return <FlashcardWorkspace key={activeDeck.id} projectId={project.id} initialDeck={activeDeck}
      api={api} onClose={closeFlashcardDeck} onSource={goToCardSource} />
  }

  return (
    <div className="app-shell">

      <aside className="sidebar">
        <div className="brand"><BrandMark size={39} /><span><strong>Kavra</strong><small>Oku. Dinle. Kavra.</small></span></div>
        <nav className="sidebar-links" aria-label="Çalışma alanları"><button onClick={() => { setView('dashboard'); api('/api/bootstrap').then(setBootstrap).catch(() => {}) }}><FolderOpen size={17} /> Projeler</button><button onClick={() => { setCourseTab('hub'); setView(course ? 'course' : 'hub') }}><BookOpen size={17} /> Projeye genel bakış</button><button onClick={() => { if (course) { setCourseTab('decks'); setView('course') } else setView('anki') }} disabled={!project}><Layers3 size={17} /> Flashcard</button></nav>
        <nav className="step-nav" aria-label="Üretim adımları">
          {steps.map((item) => {
            const Icon = item.icon
            const active = stage === item.id
            const done = (item.id === 'source' && project) || (item.id === 'script' && project?.slides.length) || (item.id === 'video' && project?.outputs?.video)
            return <button key={item.id} aria-label={item.label} aria-current={active ? 'step' : undefined} className={`${active ? 'active' : ''} ${done ? 'done' : ''}`} onClick={() => setStage(item.id)} disabled={item.id !== 'source' && !project}>
              <span className="nav-number">{done ? <Check size={14} /> : item.eyebrow}</span><Icon size={18} /><span>{item.label}</span><ChevronRight size={15} />
            </button>
          })}
        </nav>
        <div className="project-card">
          <div className="project-card-heading"><span>AKTİF PROJE</span><span>{project?.outputs?.video ? <Check size={14} /> : null}</span></div>
          {project ? <><strong title={project.name || project.id}>{project.name || projectLabel(project.id)}</strong><small>{project.sections.length} bölüm · {project.slides.length} slayt</small></> : <p>Yeni bir kaynak ekleyerek başla.</p>}
        </div>
        {bootstrap.projects.length > 0 && <label className="project-picker"><span>Son projeler</span><select value={project?.id || ''} disabled={Boolean(activeJob)} onChange={(event) => event.target.value && loadProject(event.target.value)}><option value="">Proje seç…</option>{bootstrap.projects.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>}
        <div className="local-badge"><span className="status-dot" /><span><strong>Yerel ve hazır</strong><small>Veriler bu bilgisayarda</small></span></div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div><span className="topbar-eyebrow">{steps.find((item) => item.id === stage)?.eyebrow} / DERS HAZIRLAMA</span><h1>{stage === 'source' ? 'İçeriği seç ve yapılandır' : stage === 'script' ? 'Anlatıyı tasarla' : 'Dersi yayına hazırla'}</h1></div>
          <div className="topbar-meta"><button className="button quiet compact" onClick={() => { setCourseTab('hub'); setView(course ? 'course' : 'hub') }}><ArrowLeft size={15} /> Proje</button><button className="icon-button" title="Kullanım / maliyet" onClick={() => setShowCost(true)}><Wallet size={18} /></button><button className="icon-button" title="Toplu kuyruk" onClick={() => setShowQueue(true)}><ListOrdered size={18} /></button><button className="icon-button" title="Telaffuz sözlüğü" onClick={() => setShowPronunciation(true)}><Settings2 size={18} /></button><ThemeToggle /></div>
        </header>
        <div className="stage-content">
          {stage === 'source' && renderSource()}
          {stage === 'script' && renderScript()}
          {stage === 'video' && renderVideo()}
        </div>
        <footer className="stage-footer">
          <button className="button quiet" disabled={stage === 'source'} onClick={() => setStage(stage === 'video' ? 'script' : 'source')}><ArrowLeft size={16} /> Önceki adım</button>
          <span>{draftDirty ? 'Düzenlediğin slaytı Kaydet ile projene işle.' : 'Projelerin bu bilgisayarda saklanır.'}</span>
          <button className="button quiet" disabled={stage === 'video' || (stage === 'source' && !project)} onClick={() => setStage(stage === 'source' ? 'script' : 'video')}>Sonraki adım <ArrowRight size={16} /></button>
        </footer>
      </main>

      {toast && <div role={toast.type === 'error' ? 'alert' : 'status'} className={`toast ${toast.type}`}><span>{toast.type === 'success' ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}</span><p>{toast.text}</p><button aria-label="Bildirimi kapat" onClick={() => setToast(null)}><X size={16} /></button></div>}

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
