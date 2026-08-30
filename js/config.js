// Site configuration. Loaded before js/map.js and js/compare.js.
//
// MAPBOX_TOKEN must be a PUBLIC token — it starts with "pk." and is visible to
// anyone who views source, which is fine and expected for a public token so
// long as it is URL-restricted to this site's domain in the Mapbox account
// (Account -> Tokens -> the token -> URL restrictions).
//
// NEVER put a secret "sk." token here. Secret tokens cannot be URL-restricted,
// carry account-management scopes, and are for server-side/CLI use only
// (uploading styles, reading account data). Keep those out of this repo.
//
// A public token needs only the default scopes: styles:tiles, styles:read,
// fonts:read. The styles:list / fonts:list scopes are for enumerating an
// account's styles and are not used for rendering.
//
// If this is left empty the maps fall back to CARTO's keyless tiles, so the
// site still builds and renders — just on the rate-limited basemap.
window.PCGF_CONFIG = {
  MAPBOX_TOKEN: "pk.eyJ1Ijoic2NvdHRncnViZXIiLCJhIjoiY210ZngzdXdqMHpnbDJ5cHQ1NWdwcm45OCJ9.rLX-CrCt3RimnjdZwlLJ_Q",
};
