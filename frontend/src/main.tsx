/// <reference types="vite/client" />
import './styles.css'
import {createRoot} from 'react-dom/client'
import {FreshnessApp} from './FreshnessApp'
const root=document.getElementById('root')
if(!root)throw new Error('Application root is missing')
createRoot(root).render(<FreshnessApp config={{
 issuer:import.meta.env.VITE_KEYCLOAK_ISSUER || '',
 clientId:import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'freshness-ui',
 redirectUri:window.location.origin+'/callback',
 apiBase:import.meta.env.VITE_API_BASE || '/api',
}}/>)
