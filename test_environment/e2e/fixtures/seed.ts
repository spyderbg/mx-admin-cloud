/**
 * TypeScript-side description of the fixture test_environment/testenv_seed.py produces from the
 * real content/ directory. Single source of truth for specs, so a category/item id or
 * count never gets hardcoded twice — see test_environment/testenvlib/paths.py for the Python side
 * (KEPT_CATEGORY_ROWS / KEPT_ITEM_ROWS) that actually builds the tree this describes.
 *
 * Two corrections versus docs/test_environment/implementation_plan.md, confirmed by
 * running the real backend against the real stub (see backend/src/services/content/
 * providers/file_system.py item_images(), lines 1678-1724):
 *
 * 1. `fruits/apricot` has no directory at all in the real content/ (not an "empty
 *    directory" as the plan assumed) — it exists only as a row in items_ids.csv/items_en.csv.
 * 2. `item_images()` falls back to the first (sorted) variant as the main image whenever
 *    no dedicated "{item_folder}.{ext}" file exists — so `drinks/water` and `drinks/coffee`
 *    DO show a selected main image (the fallback), not "no main image" as the plan assumed.
 *    Only items with zero image variants at all (apricot, avocado) have a genuinely empty
 *    image state.
 */

export const FIXTURE = {
  categories: {
    atSchool: { id: 'C_AtSchool', folder: 'at_school', nameEn: 'at school', itemCount: 2 },
    drinks: { id: 'C_Drinks', folder: 'drinks', nameEn: 'drinks', nameBg: 'напитки', itemCount: 2 },
    atHome: { id: 'C_AtHome', folder: 'at_home', nameEn: 'at home', itemCount: 0 },
    // Dashboard/list item count is a directory count, not a CSV row count — apricot has
    // no directory, so this category shows 1, even though 2 items are registered in CSV
    // (both apricot and avocado appear in the category's item list/grid).
    fruits: { id: 'C_Fruits', folder: 'fruits', nameEn: 'fruits', itemCount: 1 },
  },
  /**
   * Dashboard card order is NOT categories_ids.csv row order — list_categories()
   * (file_system.py) does `sorted(root.iterdir(), key=lambda x: x.name)`, i.e.
   * alphabetical by on-disk folder name. Confirmed by running dashboard.spec.ts against
   * the real app: root CSV order is [at_school, drinks, at_home, fruits], but the
   * dashboard renders [at_home, at_school, drinks, fruits].
   */
  dashboardOrder: ['C_AtHome', 'C_AtSchool', 'C_Drinks', 'C_Fruits'] as const,
  dashboardNameOrder: ['at home', 'at school', 'drinks', 'fruits'] as const,

  items: {
    coffee: {
      id: 'I_Coffee', folder: 'coffee', category: 'C_Drinks',
      variantCount: 1, hasMainFallback: true, voiceLanguages: ['en', 'fr', 'es', 'bg'],
    },
    water: {
      id: 'I_Water', folder: 'water', category: 'C_Drinks',
      variantCount: 2, hasMainFallback: true, voiceLanguages: ['en', 'fr', 'es', 'bg'],
    },
    apricot: {
      id: 'I_Apricot', folder: 'apricot', category: 'C_Fruits',
      variantCount: 0, hasMainFallback: false, voiceLanguages: [],
    },
    avocado: {
      id: 'I_Avocado', folder: 'avocado', category: 'C_Fruits',
      variantCount: 0, hasMainFallback: false, voiceLanguages: ['en', 'fr', 'es', 'bg'],
    },
    pencil: {
      id: 'I_Pencil', folder: 'pencil', category: 'C_AtSchool',
      variantCount: 1, hasMainFallback: false, voiceLanguages: ['en', 'fr', 'es', 'bg'],
    },
    waterBottle: {
      id: 'I_WaterBottle', folder: 'water_bottle', category: 'C_AtSchool',
      variantCount: 1, hasMainFallback: true,
      // Real content quirk: only 2 of the 4 supported languages have a voice file.
      voiceLanguages: ['en', 'bg'],
    },
  },

  /** Root files a spec should never see surfaced as content (drinks/ stray legacy files). */
  drinksStrayFiles: ['names_export.py', 'images.csv', 'images_names.csv', 'asd_drinks.png', 'asd_drinks_01.png', 'drinks_old.png'],

  /** Orphan file: no categories_it.csv, "it" not in SUPPORTED_LANGUAGES, never read by the app. */
  orphanLanguage: 'it',
} as const
