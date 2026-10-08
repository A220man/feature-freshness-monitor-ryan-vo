import {createRoot} from 'react-dom/client'
import {FreshnessApp, type AppConfig} from '../src/FreshnessApp'
declare global {interface Window {fixtureConfig:AppConfig}}
const root=document.getElementById('root')
if(!root)throw new Error('Application root is missing')
createRoot(root).render(<FreshnessApp config={window.fixtureConfig}/>)
