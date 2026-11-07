import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig } from '@playwright/test'
import { EMAIL, PASSWORD } from './e2e/constants'

const folder = join(tmpdir(), 'coolcandle-e2e')

const ENV = {
  COOLCANDLE_SECRET_KEY: 'n0Yl2Yc0yTPqXzvCkq1h1w2pCq9lUeJ8mZrQ7mJ3p9o=',
  COOLCANDLE_DATABASE_URL: `sqlite+aiosqlite:///${join(folder, 'e2e.db')}`,
  COOLCANDLE_COOKIE_SECURE: 'false',
  COOLCANDLE_DATA_DIR: join(folder, 'data'),
  COOLCANDLE_MARKET_SOURCE: 'sim',
  COOLCANDLE_SIM_SPEED: '4',
}

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  workers: 1,
  use: { baseURL: 'http://localhost:5173', screenshot: 'only-on-failure' },
  webServer: [
    {
      // A clean database each run, then the app. The seed prints the authenticator secret.
      command: [
        `rm -rf ${folder} && mkdir -p ${folder}`,
        `uv run python ../frontend/e2e/seed.py ${EMAIL} ${PASSWORD} > ${join(folder, 'secret')}`,
        'uv run uvicorn coolcandle.main:create_app --factory --port 8000',
      ].join(' && '),
      cwd: '../backend',
      env: ENV,
      url: 'http://localhost:8000/api/health',
      reuseExistingServer: false,
    },
    { command: 'npm run dev', url: 'http://localhost:5173', reuseExistingServer: false },
  ],
})
