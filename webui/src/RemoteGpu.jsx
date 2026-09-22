import { useEffect, useState } from 'react'
import { Check, Cloud, LoaderCircle, Plug, Save, Trash2 } from 'lucide-react'
import './RemoteGpu.css'

// Yalnızca bu motorlar uzak GPU sunucusunda çalışır (bkz. app/tts/remote.py).
export const REMOTE_ENGINES = ['coqui', 'piper']

// Sağlayıcı uzağa uygun değilse (örn. Edge seçildi) kayıtlı "remote" tercihi API'ye gönderilmez.
export const effectiveBackend = (video) =>
  REMOTE_ENGINES.includes(video.ttsProvider) && video.ttsBackend === 'remote' ? 'remote' : 'local'

const ENGINE_LABELS = { coqui: 'XTTS v2', piper: 'Piper' }
const PROFILE_LABELS = { pc: 'Bu bilgisayar', colab: 'Colab' }
const PROFILE_HINTS = {
  pc: 'Kendi ekran kartın, Tailscale üzerinden. Sabit adres/token; run_tts_server.bat çalışıyor ve Tailscale bağlıyken hazırdır.',
  colab: 'Colab not defterinden gelen adres ve token. Oturum kapanınca yenisiyle güncelle.',
}
const jsonOptions = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

function ProfileCard({ id, profile, isActive, api, onChanged, onActivate }) {
  const [url, setUrl] = useState(profile.url)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState('')
  const [test, setTest] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { setUrl(profile.url) }, [profile.url])

  const run = async (name, work) => {
    setBusy(name); setError('')
    try { await work() } catch (e) { setError(e.message) } finally { setBusy('') }
  }
  const runTest = () => run('test', async () => setTest(await api(`/api/remote-tts/${id}/test`, { method: 'POST' })))
  const save = () => run('save', async () => {
    onChanged(await api(`/api/remote-tts/${id}`, jsonOptions('PUT', { url, token })))
    setToken(''); setTest(null)
  })
  const clear = () => run('clear', async () => {
    onChanged(await api(`/api/remote-tts/${id}`, { method: 'DELETE' })); setUrl(''); setToken(''); setTest(null)
  })
  const dirty = url.trim() !== profile.url || token.trim() !== ''

  return <div className={`remote-gpu-card${isActive ? ' active' : ''}`}>
    <div className="remote-gpu-head">
      <label className="remote-gpu-radio">
        <input type="radio" name="remote-gpu-active" checked={isActive} onChange={() => onActivate(id)} />
        <strong>{PROFILE_LABELS[id]}</strong>
      </label>
      {profile.tokenSet && <small className="remote-gpu-saved"><Check size={12} /> kayıtlı</small>}
    </div>
    <small className="remote-gpu-hint">{PROFILE_HINTS[id]}</small>
    <label className="field">
      <span>Adres</span>
      <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://….trycloudflare.com" autoComplete="off" spellCheck="false" />
    </label>
    <label className="field">
      <span>Token</span>
      <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off"
        placeholder={profile.tokenSet ? '•••••••• kayıtlı — değiştirmek için yaz' : 'sunucunun yazdırdığı token'} />
    </label>
    <div className="remote-gpu-actions">
      <button type="button" className="button primary" onClick={save} disabled={!!busy || !dirty || !url.trim()}>
        {busy === 'save' ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} Kaydet
      </button>
      <button type="button" className="button ghost" onClick={runTest} disabled={!!busy || !profile.tokenSet || dirty}>
        {busy === 'test' ? <LoaderCircle className="spin" size={15} /> : <Plug size={15} />} Bağlantıyı dene
      </button>
      <button type="button" className="button ghost" onClick={clear} disabled={!!busy || (!profile.url && !profile.tokenSet)} title="Adresi ve token'ı sil">
        <Trash2 size={15} />
      </button>
    </div>
    {error && <small className="health-missing">{error}</small>}
    {test && (test.ok
      ? <small className="health-hint remote-gpu-ok">
          Bağlı — {test.gpu || 'GPU görünmüyor'} · {test.latencyMs} ms ·{' '}
          {REMOTE_ENGINES.map((e) => `${ENGINE_LABELS[e]} ${test.engines?.[e]?.available ? '✓' : '✗'}`).join(' · ')}
        </small>
      : <small className="health-missing">{test.error}</small>)}
  </div>
}

export default function RemoteGpuControls({ video, setVideo, api }) {
  const [state, setState] = useState(null)
  const remote = video.ttsBackend === 'remote'

  useEffect(() => { api('/api/remote-tts').then(setState).catch(() => {}) }, [])

  const activate = async (profile) => setState(await api('/api/remote-tts/active', jsonOptions('POST', { profile })))

  return <>
    <label className="field wide">
      <span>Çalıştırma yeri</span>
      <select value={remote ? 'remote' : 'local'} onChange={(e) => setVideo({ ...video, ttsBackend: e.target.value })}>
        <option value="local">Bu bilgisayar</option>
        <option value="remote">Uzak GPU (Colab / Tailscale)</option>
      </select>
      <small>
        {remote
          ? 'Ses, aşağıda seçtiğin GPU kaynağında üretilir; anlatı metinleri internet üzerinden oraya gönderilir.'
          : 'Ses bu bilgisayarda üretilir.'}
      </small>
    </label>
    {remote && state && <div className="remote-gpu">
      <div className="remote-gpu-title"><Cloud size={16} /><strong>GPU kaynağı</strong></div>
      {['pc', 'colab'].map((id) => (
        <ProfileCard key={id} id={id} profile={state.profiles[id]} isActive={state.active === id}
          api={api} onChanged={setState} onActivate={activate} />
      ))}
      <label className="field">
        <span>Aynı anda gönderilen slayt</span>
        <select value={video.remoteTtsConcurrency} onChange={(e) => setVideo({ ...video, remoteTtsConcurrency: Number(e.target.value) })}>
          {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}{n === 1 ? ' — sıralı' : ''}</option>)}
        </select>
        <small>Seçili GPU kaynağındaki model kopyası sayısıyla eşleşmesi en hızlısıdır (Colab'da varsayılan 2).</small>
      </label>
      {video.ttsProvider === 'piper' && <small className="health-hint">Piper CPU'da zaten hızlıdır; uzağa göndermek genellikle avantaj sağlamaz.</small>}
      <small className="health-hint">Aktif GPU kapanırsa (Colab oturumu bitti, PC uyudu…) diğerine geçip renderı yeniden başlat; biten sesler korunur.</small>
    </div>}
  </>
}
