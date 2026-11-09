import manifest from './brand-data.json' with { type: 'json' }

/** Display identity only. Never use these values for storage, APIs or app IDs. */
export const brand = Object.freeze({ ...manifest, colors: Object.freeze({ ...manifest.colors }), logo: `/brand/logo.svg?v=${manifest.logoVersion}` })
export const BRAND = brand
