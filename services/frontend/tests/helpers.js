import { expect } from '@playwright/test'

export async function openChatConfiguration (page) {
  const dialog = page.getByRole('dialog', { name: 'Chat preferences' })
  if (!await dialog.isVisible()) await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
  await expect(dialog).toBeVisible()
}

export async function closeChatConfiguration (page) {
  const dialog = page.getByRole('dialog', { name: 'Chat preferences' })
  if (await dialog.count()) await dialog.getByRole('button', { name: 'Close chat preferences', exact: true }).click()
  await expect(dialog).toBeHidden()
}
