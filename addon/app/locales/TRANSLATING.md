# Translation Guide

Battery Sentinel Plus uses a simple JSON translation system. Each language is a single JSON file in this directory.

## How to add a new language

1. Copy `en.json` to a new file named with the two-letter [ISO 639-1](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes) language code. For example: `fr.json` for French, `de.json` for German.
2. Translate the values on the right side of each `"key": "value"` pair. **Do not change the keys** (the left side).
3. For strings with `{variable}` placeholders, keep the placeholder in the translated string -- the app fills in the real value at runtime. You can reorder placeholders within a sentence as needed for your language.
4. Open a pull request on [GitHub](https://github.com/smcneece/battery-sentinel) with your new file.

## Partial translations

Missing keys automatically fall back to English, so a partial translation is perfectly fine to submit -- any string you haven't translated yet will show in English until someone fills it in.

## Testing your translation

1. Copy your locale file to `addon/app/locales/` on your HA install.
2. Open Battery Sentinel Plus, go to Settings > General, and select your language.
3. The page will reload in your chosen language.

## Notes

- The `en.json` file is the source of truth. If a new feature adds strings to `en.json`, existing translations can be updated by PR.
- Language codes should match what `navigator.language` returns in browsers -- the first two characters, lowercase. For example `pt` for Portuguese (not `pt-BR`).
- To add a new language to the selector, open a PR that includes both the JSON file and a one-line addition to the `_availableLanguages` array in `index.html`.
