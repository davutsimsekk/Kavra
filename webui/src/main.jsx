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
