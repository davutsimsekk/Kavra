import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { StudyProvider } from './StudyProvider.jsx'
import { AppearanceProvider } from './Appearance.jsx'
import './styles.css'
import './workspace.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppearanceProvider><StudyProvider><App /></StudyProvider></AppearanceProvider>
  </StrictMode>,
)

// Telefonda "Ana ekrana ekle" ile tarayıcı çubuğu olmadan, kendi ikonuyla açılan bir
// "uygulama" deneyimi için — bkz. webui/public/manifest.webmanifest + sw.js. Sadece HTTPS
// ya da localhost'ta (secure context) çalışır; Tailscale'in `tailscale serve` ile verdiği
// HTTPS bunu zaten sağlıyor (bkz. DOCKER.md §8).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
