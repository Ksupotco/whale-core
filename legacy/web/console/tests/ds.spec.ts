import { expect, test } from '@playwright/test';

// 设计系统自检（dev 画廊页 /ds.html；不进部署构建）。

test.beforeEach(async ({ page }) => {
  await page.goto('/ds.html');
});

test('gallery renders all core components', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Whale Core 设计系统自检' })).toBeVisible();
  await expect(page.getByRole('button', { name: '保存配置' }).first()).toBeVisible();
  await expect(page.getByText('已吊销')).toBeVisible();
  await expect(page.getByLabel('模型名称')).toBeVisible();
  await expect(page.getByRole('switch', { name: '启用流式输出' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await expect(page.getByText('会话 cookie 为 HttpOnly')).toBeVisible();
});

test('FocusGrid: focus, switch, collapse, restore', async ({ page }) => {
  const cards = page.locator('.focus-card');
  await expect(cards).toHaveCount(4);
  await expect(page.locator('.focus-grid')).not.toHaveClass(/has-focus/);
  // 点击聚焦
  await cards.first().click();
  await expect(page.locator('.focus-grid')).toHaveClass(/has-focus/);
  await expect(cards.first()).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByLabel('高级设置草稿')).toBeVisible();
  // 点另一张 → 切换聚焦
  await cards.nth(1).click();
  await expect(cards.nth(1)).toHaveAttribute('aria-expanded', 'true');
  await expect(cards.first()).toHaveAttribute('aria-expanded', 'false');
  // 再点当前聚焦卡（非控件区）→ 恢复平级
  await cards.nth(1).click();
  await expect(page.locator('.focus-grid')).not.toHaveClass(/has-focus/);
});

test('FocusGrid: inner controls never collapse, input survives', async ({ page }) => {
  const cards = page.locator('.focus-card');
  await cards.first().click();
  // 输入内容
  await page.getByLabel('高级设置草稿').fill('温度 0.7 待保存');
  // 点内部控件（思考强度档位）→ 不折叠（作用域限定到聚焦卡片）
  const focusedCard = page.getByRole('region', { name: '回复模型（快）' });
  await focusedCard.getByRole('radio', { name: 'High' }).click();
  await expect(cards.first()).toHaveAttribute('aria-expanded', 'true');
  // 收起（明确按钮）→ 再展开 → 输入还在
  await focusedCard.getByRole('button', { name: '收起' }).click();
  await expect(page.locator('.focus-grid')).not.toHaveClass(/has-focus/);
  await cards.first().click();
  await expect(page.getByLabel('高级设置草稿')).toHaveValue('温度 0.7 待保存');
});

test('theme toggle switches dark tokens', async ({ page }) => {
  const bgLight = await page.evaluate(
    () => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
  );
  await page.getByRole('button', { name: '切换深色' }).click();
  const bgDark = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
  );
  expect(bgDark).not.toBe(bgLight);
  expect(bgDark).toBe('#14151c');
  await expect(page.getByRole('button', { name: '切换浅色' })).toBeVisible();
});

test('gallery fits narrow screens without overflow', async ({ page }) => {
  for (const w of [360, 768, 1280]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.goto('/ds.html');
    await page.waitForTimeout(200);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `${w}px`,
    ).toBeTruthy();
  }
  // 窄屏聚焦：单列原地展开
  await page.setViewportSize({ width: 360, height: 900 });
  await page.locator('.focus-card').first().click();
  await expect(page.getByLabel('高级设置草稿')).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
});
