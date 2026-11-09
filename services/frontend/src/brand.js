import manifest from '../public/brand/brand.json' with { type: 'json' }

/** Display identity only. Never use these values for storage, APIs or app IDs. */
export const brand = Object.freeze({ ...manifest, colors: Object.freeze({ ...manifest.colors }), logo: '/brand/logo.svg' })
export const BRAND = brand
