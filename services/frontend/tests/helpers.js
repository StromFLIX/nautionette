import { expect } from '@playwright/test'

export async function openChatConfiguration (page) {
  const panel = page.getByRole('region', { name: 'Chat preferences' })
  if (!await panel.isVisible()) await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
  await expect(panel).toBeVisible()
}

export async function closeChatConfiguration (page) {
  const panel = page.getByRole('region', { name: 'Chat preferences' })
  if (await panel.count()) await panel.getByRole('button', { name: 'Close chat preferences', exact: true }).click()
  await expect(panel).toBeHidden()
}
