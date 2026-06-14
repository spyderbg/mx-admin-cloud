/** All language codes known to the application (mirrors backend LANG_MAP). */
const LANG_CODES = new Set(['en', 'de', 'fr', 'it', 'es', 'pl', 'uk', 'ro', 'nl', 'bg'])

/**
 * A multilingual string with typed property access per language code.
 *
 * Mirrors the Python `LocalizedString` in `backend/src/core/localization.py`.
 *
 * Property getters cover all LANG_MAP codes and fall back to English when a
 * translation is absent. Dynamic access via `get(lang)` throws for unknown codes.
 *
 * Deserializes transparently from the JSON wire format:
 *   `{"en": "coffee", "fr": "café", "es": "café", "bg": "кафе"}`
 *
 * @example
 *   const ls = new LocalizedString({ en: 'coffee', fr: 'café' })
 *   ls.en // 'coffee'
 *   ls.fr // 'café'
 *   ls.es // 'coffee' ← falls back to English
 *   ls.get('jp') // throws Error: 'jp' is not a supported language code
 */
export class LocalizedString {
    private readonly _data: Record<string, string>

    constructor(data: Record<string, string>) {
        this._data = { ...data }
    }

    // ------------------------------------------------------------------
    // Typed getters for every LANG_MAP code
    // ------------------------------------------------------------------

    get en(): string { return this._data['en'] ?? '' }
    get de(): string { return this._data['de'] || this._data['en'] || '' }
    get fr(): string { return this._data['fr'] || this._data['en'] || '' }
    get it(): string { return this._data['it'] || this._data['en'] || '' }
    get es(): string { return this._data['es'] || this._data['en'] || '' }
    get pl(): string { return this._data['pl'] || this._data['en'] || '' }
    get uk(): string { return this._data['uk'] || this._data['en'] || '' }
    get ro(): string { return this._data['ro'] || this._data['en'] || '' }
    get nl(): string { return this._data['nl'] || this._data['en'] || '' }
    get bg(): string { return this._data['bg'] || this._data['en'] || '' }

    // ------------------------------------------------------------------
    // Dynamic access helpers
    // ------------------------------------------------------------------

    /** Return the value for `lang`, falling back to English. Throws for unknown codes. */
    get(lang: string, fallback = ''): string {
        if (!LANG_CODES.has(lang)) {
            throw new Error(
                `'${lang}' is not a supported language code. ` +
                `Supported: ${[...LANG_CODES].sort().join(', ')}`
            )
        }
        return this._data[lang] || this._data['en'] || fallback
    }

    /** Raw access without English fallback; returns `undefined` when not set. */
    getRaw(lang: string): string | undefined {
        return this._data[lang]
    }

    // ------------------------------------------------------------------
    // Serialization
    // ------------------------------------------------------------------

    toDict(): Record<string, string> {
        return { ...this._data }
    }

    /** Called automatically by `JSON.stringify`. */
    toJSON(): Record<string, string> {
        return this.toDict()
    }

    toString(): string {
        return this._data['en'] ?? ''
    }

    // ------------------------------------------------------------------
    // Factory
    // ------------------------------------------------------------------

    /** Construct from a plain JSON object (the wire format from the API). */
    static fromJSON(data: Record<string, string>): LocalizedString {
        return new LocalizedString(data)
    }
}
