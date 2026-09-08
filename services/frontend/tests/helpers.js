import { expect } from '@playwright/test'

export async function openChatConfiguration (page) {
  const toggle = page.getByRole('button', { name: 'Chat configuration', exact: true })
  await expect(toggle).toBeVisible()
  if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click()
}
