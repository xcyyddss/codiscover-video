const { test, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');

test('demo workbench completes discovery, chat, feedback and memory flows on desktop and mobile', async ({ page, request }) => {
  // Never mutate a live/paid-mode service by accidentally using the wrong port.
  const status = await (await request.get('/api/status')).json();
  expect(status.mode).toBe('demo');
  expect(status.llm_mode).toBe('demo');
  const errors = [];
  page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(`${message.text()} (${message.location().url || 'page'})`); });
  const failedRequests = [];
  page.on('requestfailed', req => failedRequests.push(`${req.method()} ${req.url()}: ${req.failure()?.errorText}`));

  await page.goto('/');
  await expect(page).toHaveTitle(/CoDiscover Video/);
  await expect(page.locator('#mode-badge')).toHaveText('演示数据模式');
  const title = `下班后安静放松，带一点自然观察 · UI验收 ${Date.now()}`;
  await page.getByRole('button', { name: '新建体验目标', exact: true }).click();
  await page.locator('#goal-text').fill(title);
  await page.locator('#goal-context').fill('工作日晚上独自吃饭，想低认知负担地放松十分钟');
  const goalResponse = page.waitForResponse(r => r.url().endsWith('/api/goals') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存目标', exact: true }).click();
  expect((await goalResponse).ok()).toBe(true);
  await expect(page.locator('#goal-dialog')).not.toBeVisible();
  await expect(page.locator('#current-goal-title')).toHaveText(title);

  await page.locator('#presence-toggle').click();
  await expect(page.locator('#presence-label')).toHaveText('我已离线');
  const jobResponse = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.locator('#start-from-companion').click();
  expect((await jobResponse).ok()).toBe(true);
  await expect(page.locator('#tab-explore')).toBeVisible();
  const job = page.locator('#job-list .job-row').filter({ hasText: title }).first();
  await expect(job).toContainText('已完成', { timeout: 45000 });
  await job.getByRole('button', { name: '执行详情 →' }).click();
  await expect(page.locator('#job-detail')).toContainText('已完成');
  await expect(page.locator('#job-detail .event-list li')).not.toHaveCount(0);
  await page.locator('#job-dialog').getByRole('button', { name: '关闭', exact: true }).click();

  await page.locator('[data-tab="companion"]').click();
  await page.locator('#refresh-recommendations').click();
  await expect(page.locator('#recommendations .video-card').first()).toBeVisible();
  expect(await page.locator('#recommendations .video-card').count()).toBeGreaterThanOrEqual(2);
  const videoTitle = await page.locator('#recommendations .video-card h3').first().textContent();
  await page.locator('#recommendations .video-card').first().getByRole('button', { name: '查看证据', exact: true }).click();
  await expect(page.locator('#video-detail')).toContainText('原始内容证据');
  await expect(page.locator('#video-detail .segment-row').first()).toBeVisible();
  await page.locator('#video-dialog').getByRole('button', { name: '关闭', exact: true }).click();

  const message = '我喜欢安静的自然观察，不喜欢高刺激和学习压力，请根据已保存的内容给我建议。';
  await page.locator('#chat-input').fill(message);
  const chatResponse = page.waitForResponse(r => r.url().endsWith('/api/chat') && r.request().method() === 'POST');
  await page.locator('#chat-submit').click();
  expect((await chatResponse).ok()).toBe(true);
  await expect(page.locator('#messages .pending')).toHaveCount(0);
  await expect(page.locator('#messages')).toContainText(message);
  await expect(page.locator('#messages .assistant').last()).not.toHaveClass(/error/);
  await expect(page.locator('#intent-content')).toContainText('安静');

  await page.locator('#recommendations .video-card').first().getByRole('button', { name: '记录体验', exact: true }).click();
  await page.locator('#feedback-form label').filter({ has: page.locator('input[name="rating"][value="5"]') }).click();
  await page.locator('#feedback-note').fill('安静、慢节奏的观察让我放松，愿意继续试试类似内容。');
  const feedbackResponse = page.waitForResponse(r => r.url().endsWith('/api/feedback') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存反馈', exact: true }).click();
  expect((await feedbackResponse).ok()).toBe(true);
  await expect(page.locator('#feedback-dialog')).not.toBeVisible();

  await page.locator('[data-tab="library"]').click();
  await expect(page.locator('#library-videos .video-card').first()).toBeVisible();
  await page.locator('#library-query').fill(videoTitle);
  await page.locator('#library-search-form').getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page.locator('#library-summary')).toContainText(videoTitle);
  await expect(page.locator('#library-videos .video-card h3').first()).toHaveText(videoTitle);
  await page.locator('#import-from-library').click();
  await expect(page.locator('#import-hint')).toContainText('demo-rain');
  await page.locator('#import-id').fill('demo-cook');
  await page.getByRole('button', { name: '读取并保存', exact: true }).click();
  await expect(page.locator('#feedback-dialog')).toBeVisible();
  await expect(page.locator('#feedback-video-title')).toHaveText('下班后的两人晚餐');
  await page.locator('#feedback-dialog').getByRole('button', { name: '关闭', exact: true }).click();

  await page.locator('[data-tab="explore"]').click();
  await expect(page.locator('#connection-details')).toContainText('本地规则演示');
  await expect(page.locator('#auth-panel')).not.toBeVisible();
  await expect.poll(async () => Number(await page.locator('#stat-feedback').textContent())).toBeGreaterThan(0);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#export-memory').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^codiscover-video-memory-.*\.json$/);
  const exported = JSON.parse(await fs.readFile(await download.path(), 'utf8'));
  expect(JSON.stringify(exported)).toContain(title);
  expect(JSON.stringify(exported)).not.toMatch(/sessdata|bili_jct|DEEPSEEK_API_KEY/i);

  await page.locator('[data-tab="companion"]').click();
  await page.locator('#presence-toggle').click();
  await expect(page.locator('#presence-label')).toHaveText('我在线');
  await expect(page.locator('#toast')).not.toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: path.resolve('data/ui-smoke.png'), fullPage: true });

  expect(failedRequests).toEqual([]);
  expect(errors).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const tab of ['companion', 'library', 'explore']) {
    await page.locator(`[data-tab="${tab}"]`).click();
    await expect(page.locator(`#tab-${tab}`)).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      viewport: window.innerWidth,
      body: document.body.scrollWidth,
      document: document.documentElement.scrollWidth,
    }));
    expect(dimensions.body, `${tab}: body horizontal overflow`).toBeLessThanOrEqual(dimensions.viewport);
    expect(dimensions.document, `${tab}: document horizontal overflow`).toBeLessThanOrEqual(dimensions.viewport);
  }
  await page.locator('[data-tab="companion"]').click();
  await page.screenshot({ path: path.resolve('data/ui-smoke-mobile.png'), fullPage: true });
  // Produce a clean presentation copy through the same user-facing edit flow.
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#edit-goal').click();
  await page.locator('#goal-text').fill('下班后的十分钟：安静陪伴与轻度好奇');
  await page.getByRole('button', { name: '保存目标', exact: true }).click();
  await expect(page.locator('#goal-dialog')).not.toBeVisible();
  await expect(page.locator('#current-goal-title')).toHaveText('下班后的十分钟：安静陪伴与轻度好奇');
  await expect(page.locator('#toast')).not.toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: path.resolve('data/ui-preview.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.resolve('data/ui-preview-mobile.png'), fullPage: true });
  expect(failedRequests).toEqual([]);
  expect(errors).toEqual([]);
});
