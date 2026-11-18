import { defineConfig, loadEnv } from 'vite'; import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => ({plugins:[react()],server:{proxy:{'/api':loadEnv(mode, '.', '').VITE_API_PROXY || 'http://localhost:8000'}}}));
