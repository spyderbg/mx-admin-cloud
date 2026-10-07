import type { CategoryFull, Item, Language, Voice } from '../types/viewModels'

function labelFromVoices(voices: Voice[], lang: Language): string | undefined {
  const pick = (code: Language) => voices.find((v) => v.language === code)?.name?.trim()
  const primary = pick(lang)
  if (primary) return primary
  const en = pick('en')
  if (en) return en
  return undefined
}

export function localizedCategoryLabel(category: CategoryFull, lang: Language): string {
  const voiceLabel = labelFromVoices(category.voices, lang)
  if (voiceLabel) return voiceLabel
  if (typeof (category.name as any).get === 'function') {
    return (category.name as any).get(lang, (category.name as any).en)
  }
  const dict = category.name as unknown as Record<string, string>
  return dict[lang] ?? dict['en'] ?? ''
}

export function localizedImageLabel(item: Item, lang: Language): string {
  const voiceLabel = labelFromVoices(item.voices, lang)
  if (voiceLabel) return voiceLabel
  const enLabel = labelFromVoices(item.voices, 'en')
  if (enLabel) return enLabel
  const localizedName = item.localizedName?.get(lang, item.name).trim()
  if (localizedName) return localizedName
  return item.name
}

/** The first segment of category breadcrumbs; unknown `lang` falls back to English. */
export function breadcrumbCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Categories',
    es: 'Categorías',
    fr: 'Catégories',
    bg: 'Категории',
  }
  return labels[lang] ?? labels.en
}

/** Link to the complete category list in the header menu. */
export function allCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'All categories',
    es: 'Todas las categorías',
    fr: 'Toutes les catégories',
    bg: 'Всички категории',
  }
  return labels[lang] ?? labels.en
}

/** Loading state while categories are fetched; unknown `lang` falls back to English. */
export function loadingCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Loading categories...',
    es: 'Cargando categorías...',
    fr: 'Chargement des catégories...',
    bg: 'Зареждане на категориите...',
  }
  return labels[lang] ?? labels.en
}

/** Error shown when the category list fails to load; unknown `lang` falls back to English. */
export function failedToLoadCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Failed to load categories.',
    es: 'No se pudieron cargar las categorías.',
    fr: 'Échec du chargement des catégories.',
    bg: 'Неуспешно зареждане на категориите.',
  }
  return labels[lang] ?? labels.en
}

/** Error shown when no content provider is active; unknown `lang` falls back to English. */
export function noActiveContentProviderLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No active content provider is configured.',
    es: 'No hay ningún proveedor de contenido activo configurado.',
    fr: 'Aucun fournisseur de contenu actif n’est configuré.',
    bg: 'Няма конфигуриран активен доставчик на съдържание.',
  }
  return labels[lang] ?? labels.en
}

/** Error shown when the category list fails to load, naming the cause when no content provider is active. */
export function categoriesLoadErrorLabel(lang: Language, contentUnavailable: boolean): string {
  return contentUnavailable ? noActiveContentProviderLabel(lang) : failedToLoadCategoriesLabel(lang)
}

/** Empty state when no categories exist; unknown `lang` falls back to English. */
export function noCategoriesFoundLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No categories found.',
    es: 'No se encontraron categorías.',
    fr: 'Aucune catégorie trouvée.',
    bg: 'Не са намерени категории.',
  }
  return labels[lang] ?? labels.en
}

/** Header label for the configuration menu; unknown `lang` falls back to English. */
export function configurationLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Configuration',
    es: 'Configuración',
    fr: 'Configuration',
    bg: 'Конфигурация',
  }
  return labels[lang] ?? labels.en
}

/** Loading state while configuration services are fetched; unknown `lang` falls back to English. */
export function loadingServicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Loading services...',
    es: 'Cargando servicios...',
    fr: 'Chargement des services...',
    bg: 'Зареждане на услугите...',
  }
  return labels[lang] ?? labels.en
}

/** Error shown when configuration services fail to load; unknown `lang` falls back to English. */
export function failedToLoadServicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Failed to load services.',
    es: 'No se pudieron cargar los servicios.',
    fr: 'Échec du chargement des services.',
    bg: 'Неуспешно зареждане на услугите.',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder label for sidebar filtering; unknown `lang` falls back to English. */
export function filterLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Filter...',
    es: 'Filtrar...',
    fr: 'Filtrer...',
    bg: 'Филтър...',
  }
  return labels[lang] ?? labels.en
}

/** Label for the items section heading; unknown `lang` falls back to English. */
export function itemsLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Items',
    es: 'Elementos',
    fr: 'Éléments',
    bg: 'Елементи',
  }
  return labels[lang] ?? labels.en
}

/** Button label for adding an item; unknown `lang` falls back to English. */
export function addNewItemLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add New Item',
    es: 'Añadir elemento',
    fr: 'Ajouter un élément',
    bg: 'Добави елемент',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for adding an item; unknown `lang` falls back to English. */
export function addNewItemTitleLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add new item',
    es: 'Añadir elemento',
    fr: 'Ajouter un élément',
    bg: 'Добавяне на елемент',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Item name" field; unknown `lang` falls back to English. */
export function itemNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Item Name',
    es: 'Nombre del elemento',
    fr: "Nom de l'élément",
    bg: 'Име на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Item id" field; unknown `lang` falls back to English. */
export function itemIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Item Id',
    es: 'Id del elemento',
    fr: "Id de l'élément",
    bg: 'Идентификатор на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Label for the current item image; unknown `lang` falls back to English. */
export function currentImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Current Image',
    es: 'Imagen actual',
    fr: 'Image actuelle',
    bg: 'Текущо изображение',
  }
  return labels[lang] ?? labels.en
}

/** Generic description field label; unknown `lang` falls back to English. */
export function descriptionLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Description',
    es: 'Descripción',
    fr: 'Description',
    bg: 'Описание',
  }
  return labels[lang] ?? labels.en
}

/** Label for an item voices card group; unknown `lang` falls back to English. */
export function itemVoicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Item Voices',
    es: 'Voces del elemento',
    fr: "Voix de l'élément",
    bg: 'Гласове на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Label for an images section heading; unknown `lang` falls back to English. */
export function imagesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Images',
    es: 'Imágenes',
    fr: 'Images',
    bg: 'Изображения',
  }
  return labels[lang] ?? labels.en
}

/** Button label for releasing one item; unknown `lang` falls back to English. */
export function releaseItemLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Release Item',
    es: 'Publicar elemento',
    fr: "Publier l'élément",
    bg: 'Публикувай елемент',
  }
  return labels[lang] ?? labels.en
}

/** Button label for adding item images; unknown `lang` falls back to English. */
export function addImagesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add Images',
    es: 'Añadir imágenes',
    fr: 'Ajouter des images',
    bg: 'Добави изображения',
  }
  return labels[lang] ?? labels.en
}

/** Button label for adding one item image; unknown `lang` falls back to English. */
export function addImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add Image',
    es: 'Añadir imagen',
    fr: 'Ajouter une image',
    bg: 'Добави изображение',
  }
  return labels[lang] ?? labels.en
}

/** Instructions for adding an image through the drag-and-drop card. */
export function dropImageHereLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Drop an image here or click to browse.',
    es: 'Suelta una imagen aquí o haz clic para seleccionarla.',
    fr: 'Déposez une image ici ou cliquez pour la parcourir.',
    bg: 'Пуснете изображение тук или щракнете, за да го изберете.',
  }
  return labels[lang] ?? labels.en
}

/** Status shown while a newly selected image is being uploaded. */
export function uploadingImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Uploading image…',
    es: 'Subiendo imagen…',
    fr: "Importation de l’image…",
    bg: 'Качване на изображение…',
  }
  return labels[lang] ?? labels.en
}

/** Validation message for a non-image dropped onto the image upload card. */
export function dropImageFileRequiredLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Please drop an image file.',
    es: 'Suelta un archivo de imagen.',
    fr: 'Déposez un fichier image.',
    bg: 'Пуснете файл с изображение.',
  }
  return labels[lang] ?? labels.en
}

/** Label for the image generator metadata field. */
export function generatorLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Generator',
    es: 'Generador',
    fr: 'Générateur',
    bg: 'Генератор',
  }
  return labels[lang] ?? labels.en
}

/** Label for the image-generation prompt metadata field. */
export function promptLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Prompt',
    es: 'Indicación',
    fr: 'Invite',
    bg: 'Подкана',
  }
  return labels[lang] ?? labels.en
}

/** Description for the first image attached to an item. */
export function mainItemImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Main Item Image',
    es: 'Imagen principal del elemento',
    fr: "Image principale de l’élément",
    bg: 'Основно изображение на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Description for additional images attached to an item. */
export function imageVariantLabel(lang: Language, index: number): string {
  const labels: Record<Language, string> = {
    en: 'Image Variant',
    es: 'Variante de imagen',
    fr: "Variante d’image",
    bg: 'Вариант на изображението',
  }
  return `${labels[lang] ?? labels.en} ${index}`
}

/** Prefix for the file name displayed in image metadata. */
export function sourceFileLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Source file',
    es: 'Archivo de origen',
    fr: 'Fichier source',
    bg: 'Изходен файл',
  }
  return labels[lang] ?? labels.en
}

/** Button label for releasing all item images; unknown `lang` falls back to English. */
export function releaseAllImagesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Release All Images',
    es: 'Publicar todas las imágenes',
    fr: 'Publier toutes les images',
    bg: 'Публикувай всички изображения',
  }
  return labels[lang] ?? labels.en
}

/** Button label for selecting an item image; unknown `lang` falls back to English. */
export function selectImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Select Image',
    es: 'Seleccionar imagen',
    fr: "Sélectionner l'image",
    bg: 'Избери изображение',
  }
  return labels[lang] ?? labels.en
}

/** Label for the currently selected item image; unknown `lang` falls back to English. */
export function selectedLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Selected',
    es: 'Seleccionada',
    fr: 'Sélectionnée',
    bg: 'Избрано',
  }
  return labels[lang] ?? labels.en
}

/** Button label for adding an item voice; unknown `lang` falls back to English. */
export function addVoiceLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add Voice',
    es: 'Añadir voz',
    fr: 'Ajouter une voix',
    bg: 'Добави глас',
  }
  return labels[lang] ?? labels.en
}

/** Button label for releasing all item voices; unknown `lang` falls back to English. */
export function releaseAllVoicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Release All Voices',
    es: 'Publicar todas las voces',
    fr: 'Publier toutes les voix',
    bg: 'Публикувай всички гласове',
  }
  return labels[lang] ?? labels.en
}

/** Button label for regenerating every voice of an item. */
export function regenerateAllVoicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Regenerate Voices',
    es: 'Regenerar voces',
    fr: 'Régénérer les voix',
    bg: 'Генерирай гласовете отново',
  }
  return labels[lang] ?? labels.en
}

export function regenerateAllVoicesConfirmParts(lang: Language): { beforeItemId: string; afterItemId: string } {
  const labels: Record<Language, { beforeItemId: string; afterItemId: string }> = {
    en: { beforeItemId: 'Regenerate all voices for item ', afterItemId: '? A new voice is generated for each language and becomes the main voice; the previous one is kept as a variant in Voices.' },
    es: { beforeItemId: '¿Regenerar todas las voces del elemento ', afterItemId: '? Se genera una nueva voz por idioma y pasa a ser la voz principal; la anterior se conserva como variante en Voces.' },
    fr: { beforeItemId: 'Régénérer toutes les voix de l’élément ', afterItemId: ' ? Une nouvelle voix est générée pour chaque langue et devient la voix principale ; la précédente est conservée comme variante dans Voix.' },
    bg: { beforeItemId: 'Да се генерират ли отново всички гласове за елемент ', afterItemId: '? За всеки език се генерира нов глас и той става основен; предишният се запазва като вариант в „Гласове“.' },
  }
  return labels[lang] ?? labels.en
}

/** Audio play button label; unknown `lang` falls back to English. */
export function playLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Play',
    es: 'Reproducir',
    fr: 'Lire',
    bg: 'Пусни',
  }
  return labels[lang] ?? labels.en
}

/** Audio stop button label; unknown `lang` falls back to English. */
export function stopLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Stop',
    es: 'Detener',
    fr: 'Arrêter',
    bg: 'Спри',
  }
  return labels[lang] ?? labels.en
}

/** Audio missing-state label; unknown `lang` falls back to English. */
export function voiceFileMissingLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Voice file missing',
    es: 'Falta el archivo de voz',
    fr: 'Fichier vocal manquant',
    bg: 'Липсва гласов файл',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for changing an item id; unknown `lang` falls back to English. */
export function changeItemIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Change Item Id',
    es: 'Cambiar id del elemento',
    fr: "Modifier l'id de l'élément",
    bg: 'Промяна на идентификатора',
  }
  return labels[lang] ?? labels.en
}

/** Label for a new item id input; unknown `lang` falls back to English. */
export function newItemIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'New Item Id',
    es: 'Nuevo id del elemento',
    fr: "Nouvel id de l'élément",
    bg: 'Нов идентификатор',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder for an item id input; unknown `lang` falls back to English. */
export function enterItemIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Enter item id',
    es: 'Introduce el id del elemento',
    fr: "Saisir l'id de l'élément",
    bg: 'Въведете идентификатор',
  }
  return labels[lang] ?? labels.en
}

/** Label for an English item-name field; unknown `lang` falls back to English. */
export function itemNameEnglishLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Item name (English)',
    es: 'Nombre del elemento (inglés)',
    fr: "Nom de l'élément (anglais)",
    bg: 'Име на елемента (английски)',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder for the add-item name field; unknown `lang` falls back to English. */
export function enterItemNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Enter item name',
    es: 'Introduce el nombre del elemento',
    fr: "Saisir le nom de l'élément",
    bg: 'Въведете име на елемента',
  }
  return labels[lang] ?? labels.en
}

/** Label for an image-file input; unknown `lang` falls back to English. */
export function imageFileLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Image file',
    es: 'Archivo de imagen',
    fr: 'Fichier image',
    bg: 'Файл с изображение',
  }
  return labels[lang] ?? labels.en
}

/** Label for an audio-file input; unknown `lang` falls back to English. */
export function audioFileLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Audio file',
    es: 'Archivo de audio',
    fr: 'Fichier audio',
    bg: 'Аудио файл',
  }
  return labels[lang] ?? labels.en
}

/** Button label for opening the file picker; unknown `lang` falls back to English. */
export function chooseFileLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Choose file',
    es: 'Elegir archivo',
    fr: 'Choisir un fichier',
    bg: 'Избери файл',
  }
  return labels[lang] ?? labels.en
}

/** Empty selected-file state; unknown `lang` falls back to English. */
export function noFileSelectedLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No file selected',
    es: 'Ningún archivo seleccionado',
    fr: 'Aucun fichier sélectionné',
    bg: 'Няма избран файл',
  }
  return labels[lang] ?? labels.en
}

/** Label for the item files section; unknown `lang` falls back to English. */
export function filesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Files',
    es: 'Archivos',
    fr: 'Fichiers',
    bg: 'Файлове',
  }
  return labels[lang] ?? labels.en
}

/** Label for the item voices section; unknown `lang` falls back to English. */
export function voicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Voices',
    es: 'Voces',
    fr: 'Voix',
    bg: 'Гласове',
  }
  return labels[lang] ?? labels.en
}

/** Label for a language column; unknown `lang` falls back to English. */
export function languageColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Language',
    es: 'Idioma',
    fr: 'Langue',
    bg: 'Език',
  }
  return labels[lang] ?? labels.en
}

/** Label for a word column; unknown `lang` falls back to English. */
export function wordColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Word',
    es: 'Palabra',
    fr: 'Mot',
    bg: 'Дума',
  }
  return labels[lang] ?? labels.en
}

/** Label for a type column; unknown `lang` falls back to English. */
export function typeColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Type',
    es: 'Tipo',
    fr: 'Type',
    bg: 'Тип',
  }
  return labels[lang] ?? labels.en
}

/** Label for an actions column; unknown `lang` falls back to English. */
export function actionsColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Actions',
    es: 'Acciones',
    fr: 'Actions',
    bg: 'Действия',
  }
  return labels[lang] ?? labels.en
}

/** Label for a normal, unoptimized audio file column. */
export function normalFileColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'File (Normal)',
    es: 'Archivo (normal)',
    fr: 'Fichier (normal)',
    bg: 'Файл (нормален)',
  }
  return labels[lang] ?? labels.en
}

/** Label for an optimized audio file column. */
export function optimizedFileColumnLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'File (Optimized)',
    es: 'Archivo (optimizado)',
    fr: 'Fichier (optimisé)',
    bg: 'Файл (оптимизиран)',
  }
  return labels[lang] ?? labels.en
}

/** Counted item label: plural form for >1, singular for exactly 1. */
export function itemCountLabel(lang: Language, count: number): string {
  const singular: Record<Language, string> = {
    en: 'item',
    es: 'elemento',
    fr: 'élément',
    bg: 'елемент',
  }
  const plural: Record<Language, string> = {
    en: 'items',
    es: 'elementos',
    fr: 'éléments',
    bg: 'елемента',
  }
  const word = count === 1 ? singular[lang] ?? singular.en : plural[lang] ?? plural.en
  return `${count} ${word}`
}

/** Search empty-state label for the category sidebar; unknown `lang` falls back to English. */
export function noCategoriesMatchLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No categories match',
    es: 'No hay categorías que coincidan',
    fr: 'Aucune catégorie ne correspond',
    bg: 'Няма съвпадащи категории',
  }
  return labels[lang] ?? labels.en
}

/** Search empty-state label for one expanded category; unknown `lang` falls back to English. */
export function noMatchesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No matches',
    es: 'Sin coincidencias',
    fr: 'Aucune correspondance',
    bg: 'Няма съвпадения',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category name" field; unknown `lang` falls back to English. */
export function categoryNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Name',
    es: 'Nombre de la categoría',
    fr: 'Nom de la catégorie',
    bg: 'Име на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category id" field; unknown `lang` falls back to English. */
export function categoryIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Id',
    es: 'Id de la categoría',
    fr: 'Id de la catégorie',
    bg: 'Идентификатор на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category icon" field; unknown `lang` falls back to English. */
export function categoryIconLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Icon',
    es: 'Icono de la categoría',
    fr: 'Icône de la catégorie',
    bg: 'Икона на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for uploading a category icon; unknown `lang` falls back to English. */
export function uploadCategoryIconLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Upload category icon',
    es: 'Subir icono de la categoría',
    fr: "Importer l'icône de la catégorie",
    bg: 'Качване на икона на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for deleting a category image; unknown `lang` falls back to English. */
export function deleteCategoryImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Delete category image',
    es: 'Eliminar imagen de la categoría',
    fr: "Supprimer l'image de la catégorie",
    bg: 'Изтриване на изображение на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Confirmation text for deleting a category image; unknown `lang` falls back to English. */
export function deleteCategoryImageConfirmLabel(lang: Language, category: string): string {
  const labels: Record<Language, string> = {
    en: `Are you sure you want to delete the image for category "${category}"?`,
    es: `¿Seguro que quieres eliminar la imagen de la categoría "${category}"?`,
    fr: `Voulez-vous vraiment supprimer l'image de la catégorie « ${category} » ?`,
    bg: `Сигурни ли сте, че искате да изтриете изображението за категория „${category}“?`,
  }
  return labels[lang] ?? labels.en
}

/** Modal title for uploading a voice file; unknown `lang` falls back to English. */
export function uploadVoiceFileLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Upload voice file',
    es: 'Subir archivo de voz',
    fr: 'Importer un fichier vocal',
    bg: 'Качване на гласов файл',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for deleting or resetting a voice; unknown `lang` falls back to English. */
export function deleteOrResetVoiceLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Delete or reset voice',
    es: 'Eliminar o restablecer voz',
    fr: 'Supprimer ou réinitialiser la voix',
    bg: 'Изтриване или нулиране на глас',
  }
  return labels[lang] ?? labels.en
}

/** Confirmation text for deleting or resetting a voice; unknown `lang` falls back to English. */
export function deleteOrResetVoiceConfirmLabel(lang: Language, voice: string, voiceLang: string): string {
  const languageCode = voiceLang.toUpperCase()
  const labels: Record<Language, string> = {
    en: `Are you sure you want to delete or reset the voice for "${voice}" (${languageCode})?`,
    es: `¿Seguro que quieres eliminar o restablecer la voz de "${voice}" (${languageCode})?`,
    fr: `Voulez-vous vraiment supprimer ou réinitialiser la voix pour « ${voice} » (${languageCode}) ?`,
    bg: `Сигурни ли сте, че искате да изтриете или нулирате гласа за „${voice}“ (${languageCode})?`,
  }
  return labels[lang] ?? labels.en
}

/** Help text for deleting or resetting a voice; unknown `lang` falls back to English. */
export function deleteOrResetVoiceHelpLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'This is intended to remove or clear the uploaded audio for that language. You can upload again afterward.',
    es: 'Esto eliminará o borrará el audio subido para ese idioma. Puedes volver a subirlo después.',
    fr: "Cela supprimera ou effacera l'audio importé pour cette langue. Vous pourrez l'importer à nouveau ensuite.",
    bg: 'Това ще премахне или изчисти каченото аудио за този език. Можете да го качите отново след това.',
  }
  return labels[lang] ?? labels.en
}

/** Label for the "Category voices" section; unknown `lang` falls back to English. */
export function categoryVoicesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category Voices',
    es: 'Voces de la categoría',
    fr: 'Voix de la catégorie',
    bg: 'Гласове на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for category rename; unknown `lang` falls back to English. */
export function renameCategoryLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Rename category',
    es: 'Cambiar nombre de la categoría',
    fr: 'Renommer la catégorie',
    bg: 'Преименуване на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder for category rename input; unknown `lang` falls back to English. */
export function enterCategoryNameLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Enter category name',
    es: 'Introduce el nombre de la categoría',
    fr: 'Saisir le nom de la catégorie',
    bg: 'Въведете име на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder for category id input; unknown `lang` falls back to English. */
export function enterCategoryIdLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Enter category id',
    es: 'Introduce el id de la categoría',
    fr: "Saisir l'id de la catégorie",
    bg: 'Въведете идентификатор на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Generic cancel button label; unknown `lang` falls back to English. */
export function cancelLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Cancel',
    es: 'Cancelar',
    fr: 'Annuler',
    bg: 'Отказ',
  }
  return labels[lang] ?? labels.en
}

/** Generic save button label; unknown `lang` falls back to English. */
export function saveLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Save',
    es: 'Guardar',
    fr: 'Enregistrer',
    bg: 'Запази',
  }
  return labels[lang] ?? labels.en
}

/** Generic create button label; unknown `lang` falls back to English. */
export function createLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Create',
    es: 'Crear',
    fr: 'Créer',
    bg: 'Създай',
  }
  return labels[lang] ?? labels.en
}

/** Generic delete button label; unknown `lang` falls back to English. */
export function deleteLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Delete',
    es: 'Eliminar',
    fr: 'Supprimer',
    bg: 'Изтрий',
  }
  return labels[lang] ?? labels.en
}

/** Delete/reset button label; unknown `lang` falls back to English. */
export function deleteResetLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Delete / reset',
    es: 'Eliminar / restablecer',
    fr: 'Supprimer / réinitialiser',
    bg: 'Изтрий / нулирай',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for editing one localized word; unknown `lang` falls back to English. */
export function editWordLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Edit word',
    es: 'Editar palabra',
    fr: 'Modifier le mot',
    bg: 'Редактиране на дума',
  }
  return labels[lang] ?? labels.en
}

/** Placeholder for a localized word input; unknown `lang` falls back to English. */
export function enterWordLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Enter word',
    es: 'Introduce la palabra',
    fr: 'Saisir le mot',
    bg: 'Въведете дума',
  }
  return labels[lang] ?? labels.en
}

/** Button label for adding a category; unknown `lang` falls back to English. */
export function addNewCategoryLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add New Category',
    es: 'Añadir categoría',
    fr: 'Ajouter une catégorie',
    bg: 'Добави категория',
  }
  return labels[lang] ?? labels.en
}

/** Modal title for adding a category; unknown `lang` falls back to English. */
export function addNewCategoryTitleLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Add new category',
    es: 'Añadir categoría',
    fr: 'Ajouter une catégorie',
    bg: 'Добавяне на категория',
  }
  return labels[lang] ?? labels.en
}

/** Label for an English category-name field; unknown `lang` falls back to English. */
export function categoryNameEnglishLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Category name (English)',
    es: 'Nombre de la categoría (inglés)',
    fr: 'Nom de la catégorie (anglais)',
    bg: 'Име на категорията (английски)',
  }
  return labels[lang] ?? labels.en
}

/** Validation hint for the category-name field; unknown `lang` falls back to English. */
export function categoryNameRulesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Use letters, numbers and spaces only. Spaces become underscores.',
    es: 'Usa solo letras, números y espacios. Los espacios se convierten en guiones bajos.',
    fr: 'Utilisez uniquement des lettres, des chiffres et des espaces. Les espaces deviennent des tirets bas.',
    bg: 'Използвайте само букви, цифри и интервали. Интервалите се превръщат в долни черти.',
  }
  return labels[lang] ?? labels.en
}

/** Warning that the typed category name is already taken; unknown `lang` falls back to English. */
export function categoryAlreadyExistsLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'A category with this name already exists.',
    es: 'Ya existe una categoría con este nombre.',
    fr: 'Une catégorie portant ce nom existe déjà.',
    bg: 'Вече съществува категория с това име.',
  }
  return labels[lang] ?? labels.en
}

/** Notice that category creation runs translation, TTS and icon generation. */
export function categoryGenerationNoticeLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Translations, audio for all languages and a category icon will be generated. This can take up to a minute.',
    es: 'Se generarán traducciones, audio para todos los idiomas y un icono de categoría. Puede tardar hasta un minuto.',
    fr: "Les traductions, l'audio pour toutes les langues et une icône de catégorie seront générés. Cela peut prendre jusqu'à une minute.",
    bg: 'Ще бъдат генерирани преводи, аудио за всички езици и икона на категорията. Това може да отнеме до минута.',
  }
  return labels[lang] ?? labels.en
}

/** Busy-state label while a category is being created; unknown `lang` falls back to English. */
export function creatingLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Creating…',
    es: 'Creando…',
    fr: 'Création…',
    bg: 'Създаване…',
  }
  return labels[lang] ?? labels.en
}

/** Button label and modal title for archiving a category; unknown `lang` falls back to English. */
export function archiveCategoryLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Archive category',
    es: 'Archivar categoría',
    fr: 'Archiver la catégorie',
    bg: 'Архивиране на категорията',
  }
  return labels[lang] ?? labels.en
}

/** Generic archive button label; unknown `lang` falls back to English. */
export function archiveLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Archive',
    es: 'Archivar',
    fr: 'Archiver',
    bg: 'Архивирай',
  }
  return labels[lang] ?? labels.en
}

/** Affirmative confirmation button label; unknown `lang` falls back to English. */
export function yesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Yes',
    es: 'Sí',
    fr: 'Oui',
    bg: 'Да',
  }
  return labels[lang] ?? labels.en
}

/** Negative confirmation button label; unknown `lang` falls back to English. */
export function noLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'No',
    es: 'No',
    fr: 'Non',
    bg: 'Не',
  }
  return labels[lang] ?? labels.en
}

/** Confirmation text for archiving a category; unknown `lang` falls back to English. */
export function archiveCategoryConfirmLabel(lang: Language, category: string): string {
  const labels: Record<Language, string> = {
    en: `This will move the category "${category}", its items, images and audio to the archive.`,
    es: `Esto moverá la categoría "${category}", sus elementos, imágenes y audio al archivo.`,
    fr: `Cela déplacera la catégorie « ${category} », ses éléments, images et audio vers l'archive.`,
    bg: `Това ще премести категория „${category}“, елементите, изображенията и аудиото ѝ в архива.`,
  }
  return labels[lang] ?? labels.en
}

/**
 * Warning shown before archiving. The backend deletes permanently when ARCHIVE_DIR is
 * unset and the frontend cannot tell in advance, so both outcomes are stated.
 */
export function archiveCategoryWarningLabel(lang: Language, itemCount: number): string {
  const labels: Record<Language, string> = {
    en: `If archiving is disabled on the server this permanently deletes them. This category contains ${itemCount} items.`,
    es: `Si el archivado está desactivado en el servidor, esto los elimina permanentemente. Esta categoría contiene ${itemCount} elementos.`,
    fr: `Si l'archivage est désactivé sur le serveur, cela les supprime définitivement. Cette catégorie contient ${itemCount} éléments.`,
    bg: `Ако архивирането е изключено на сървъра, това ги изтрива безвъзвратно. Тази категория съдържа ${itemCount} елемента.`,
  }
  return labels[lang] ?? labels.en
}

/** Typed-confirmation instruction for archiving; unknown `lang` falls back to English. */
export function archiveTypeToConfirmLabel(lang: Language, categoryId: string): string {
  const labels: Record<Language, string> = {
    en: `Type ${categoryId} to confirm`,
    es: `Escribe ${categoryId} para confirmar`,
    fr: `Saisissez ${categoryId} pour confirmer`,
    bg: `Въведете ${categoryId}, за да потвърдите`,
  }
  return labels[lang] ?? labels.en
}

/** Heading for the archived-categories section; unknown `lang` falls back to English. */
export function archivedCategoriesLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Archived Categories',
    es: 'Categorías archivadas',
    fr: 'Catégories archivées',
    bg: 'Архивирани категории',
  }
  return labels[lang] ?? labels.en
}

/** Generic restore button label; unknown `lang` falls back to English. */
export function restoreLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Restore',
    es: 'Restaurar',
    fr: 'Restaurer',
    bg: 'Възстанови',
  }
  return labels[lang] ?? labels.en
}

/** Relative "archived 3 days ago" label built from a unix-seconds timestamp. */
export function archivedAgoLabel(lang: Language, archivedAt: number): string {
  const prefix: Record<Language, string> = {
    en: 'archived',
    es: 'archivada',
    fr: 'archivée',
    bg: 'архивирана',
  }
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ]
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - archivedAt)
  const formatter = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' })
  const [unit, seconds] = units.find(([, size]) => elapsed >= size) ?? ['second', 1]
  return `${prefix[lang] ?? prefix.en} ${formatter.format(-Math.floor(elapsed / seconds), unit)}`
}

/** Label for an optional image-file input; unknown `lang` falls back to English. */
export function imageFileOptionalLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Image file (optional)',
    es: 'Archivo de imagen (opcional)',
    fr: 'Fichier image (facultatif)',
    bg: 'Файл с изображение (по избор)',
  }
  return labels[lang] ?? labels.en
}

/** Button label that clears a selected image; unknown `lang` falls back to English. */
export function removeImageLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Remove image',
    es: 'Quitar imagen',
    fr: "Retirer l'image",
    bg: 'Премахни изображението',
  }
  return labels[lang] ?? labels.en
}

/** Notice that item creation runs translation and, when a generator is active, TTS. */
export function itemGenerationNoticeLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Translations for all languages will be generated, and voices when a voice generator is active. This can take a while.',
    es: 'Se generarán traducciones para todos los idiomas y, si hay un generador de voz activo, también las voces. Puede tardar un poco.',
    fr: 'Les traductions pour toutes les langues seront générées, ainsi que les voix si un générateur vocal est actif. Cela peut prendre un moment.',
    bg: 'Ще бъдат генерирани преводи за всички езици, а при активен генератор на глас – и гласове. Това може да отнеме известно време.',
  }
  return labels[lang] ?? labels.en
}

/** Validation message for a duplicate item name; unknown `lang` falls back to English. */
export function itemAlreadyExistsLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'An item with this name already exists.',
    es: 'Ya existe un elemento con este nombre.',
    fr: 'Un élément portant ce nom existe déjà.',
    bg: 'Вече съществува елемент с това име.',
  }
  return labels[lang] ?? labels.en
}

/** Fallback error when item creation fails without a server message. */
export function createItemFailedLabel(lang: Language): string {
  const labels: Record<Language, string> = {
    en: 'Could not create the item. Please try again.',
    es: 'No se pudo crear el elemento. Inténtalo de nuevo.',
    fr: "Impossible de créer l'élément. Veuillez réessayer.",
    bg: 'Елементът не можа да бъде създаден. Моля, опитайте отново.',
  }
  return labels[lang] ?? labels.en
}

/** Success toast after an item was created; unknown `lang` falls back to English. */
export function itemCreatedLabel(lang: Language, word: string): string {
  const labels: Record<Language, string> = {
    en: `Item "${word}" created.`,
    es: `Elemento "${word}" creado.`,
    fr: `Élément « ${word} » créé.`,
    bg: `Елементът „${word}“ е създаден.`,
  }
  return labels[lang] ?? labels.en
}

/** Warning toast prefix when an item was created but some voices are missing. */
export function itemCreatedWithWarningsLabel(lang: Language, word: string): string {
  const labels: Record<Language, string> = {
    en: `Item "${word}" created, but some voices could not be generated:`,
    es: `Elemento "${word}" creado, pero no se pudieron generar algunas voces:`,
    fr: `Élément « ${word} » créé, mais certaines voix n'ont pas pu être générées :`,
    bg: `Елементът „${word}“ е създаден, но някои гласове не можаха да бъдат генерирани:`,
  }
  return labels[lang] ?? labels.en
}
