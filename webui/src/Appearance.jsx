import { createContext, useContext, useEffect, useState } from 'react'
import { Sun, Moon, Monitor, ChevronRight } from 'lucide-react'
import markUrl from './assets/kavra-mark.png'

const Appearance = createContext(null)
const THEME_KEY = 'ders-studio-appearance'
export function AppearanceProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try { const value = localStorage.getItem(THEME_KEY); return ['light', 'dark', 'system'].includes(value) ? value : 'light' }
    catch { return 'light' }
  })
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const resolved = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme
      document.documentElement.dataset.theme = resolved
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#111816' : '#f6f7f9')
    }
    apply()
    try { localStorage.setItem(THEME_KEY, theme) } catch { /* Theme still works without storage. */ }
    media.addEventListener('change', apply)
    return () => media.removeEventListener('change', apply)
  }, [theme])
  return <Appearance.Provider value={{ theme, setTheme }}>{children}</Appearance.Provider>
}
export function BrandMark({ size = 39 }) {
  return <span className="brand-mark logo" style={{ '--mark': `${size}px` }}><img src={markUrl} alt="" draggable="false" /></span>
}
export function ThemeToggle() {
  const { theme, setTheme } = useContext(Appearance)
  return <div className="appearance-control" role="group" aria-label="Görünüm teması">
    {[['light', Sun, 'Açık tema'], ['dark', Moon, 'Koyu tema'], ['system', Monitor, 'Sistem teması']].map(([id, Icon, label]) =>
      <button key={id} type="button" aria-label={label} title={label} aria-pressed={theme === id} onClick={() => setTheme(id)}><Icon size={17} /></button>)}
  </div>
}
export function WorkspaceHeader({ onHome, projectName, onProject, current }) {
  return <header className="workspace-header">
    <button className="workspace-brand" onClick={onHome} aria-label="Kavra · Projeler">
      <BrandMark size={40} /><strong>Kavra<span>Oku. Dinle. Kavra.</span></strong>
    </button>
    <nav className="breadcrumbs" aria-label="Konum">
      <button onClick={onHome}>Projeler</button>
      {projectName && <><ChevronRight size={14} /><button onClick={onProject || onHome} title={projectName}>{projectName.replace(/[_-]+/g, ' ')}</button></>}
      {current && <><ChevronRight size={14} /><span aria-current="page">{current}</span></>}
    </nav>
    <ThemeToggle />
  </header>
}
