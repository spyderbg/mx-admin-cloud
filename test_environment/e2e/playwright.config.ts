import { defineConfig, devices } from '@playwright/test'
import { AUTH_STATE_PATH } from './fixtures/testEnv'

/**
 * Every root CSV in the fixture is row-index aligned (_set_csv_row_aligned,
 * backend/src/services/content/providers/file_system.py:674) — a half-completed write
 * under a concurrent spec misaligns every category below it, and the *next* spec fails
 * with an error that looks unrelated. fullyParallel/workers/retries are load-bearing,
 * not perf tuning: a retry would run against state the first attempt already mutated,
 * masking corruption instead of absorbing flake. See implementation plan §5 and risk #5.
 */
export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,

  globalSetup: './global-setup.ts',

  reporter: [
    ['list'],
    ['html', { outputFolder: '../../.testenv/artifacts/html-report', open: 'never' }],
    ['json', { outputFile: '../../.testenv/artifacts/results.json' }],
  ],

  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:48001',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // The app drives <audio> playback (VoiceCardsGrid / ItemDetailsView togglePlay);
    // Playwright's synthetic click is not always treated as a user gesture by Chromium's
    // default autoplay policy.
    launchOptions: {
      args: ['--autoplay-policy=no-user-gesture-required'],
    },
  },

  // No webServer: the stack is externally managed by `python scripts/testenv.py up`, so
  // it can stay up all day while specs iterate against it in milliseconds.

  projects: [
    {
      name: 'setup',
      testMatch: 'auth.setup.ts',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chromium',
      testMatch: 'specs/**/*.spec.ts',
      use: {
        ...devices['Desktop Chrome'],
        storageState: AUTH_STATE_PATH,
      },
      dependencies: ['setup'],
    },
  ],
})
