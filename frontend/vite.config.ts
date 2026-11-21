import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

const proxy = { '/api': 'http://127.0.0.1:8000' }
export default defineConfig({ base: './', plugins: [svelte()], server: { proxy }, preview: { proxy } })
