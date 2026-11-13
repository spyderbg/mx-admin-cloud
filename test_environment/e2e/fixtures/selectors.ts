/**
 * Every CSS-class selector used by the suite, centralised so a frontend refactor that
 * renames a class is a one-file fix (see implementation plan risk #7 — the frontend has
 * exactly one data-testid in the whole codebase, so specs necessarily ride on the same
 * semantic classes a component author would rename freely).
 *
 * Verified directly against the source of frontend/src/{components,views,layouts}/*.vue
 * at the time this suite was written — not guessed from behaviour.
 */

import { expect, type Locator, type Page } from '@playwright/test'

// ---------------------------------------------------------------------------
// Login (views/LoginView.vue, components/LoginForm.vue)
// ---------------------------------------------------------------------------

export const login = {
  username: (page: Page): Locator => page.locator('input[type="text"].form-control'),
  password: (page: Page): Locator => page.locator('input[type="password"].form-control'),
  submit: (page: Page): Locator => page.locator('button.submit-btn'),
  error: (page: Page): Locator => page.locator('.error-msg'),
}

// ---------------------------------------------------------------------------
// Navbar (layouts/AppNavbar.vue)
// ---------------------------------------------------------------------------

export const navbar = {
  languageToggle: (page: Page): Locator => page.locator('.language-dropdown .dropdown-toggle'),
  languageOption: (page: Page, label: 'English' | 'Español' | 'Français' | 'Български'): Locator =>
    page.locator('.language-dropdown .dropdown-item', { hasText: label }),
  userToggle: (page: Page): Locator => page.locator('.user-dropdown .dropdown-toggle'),
  signOut: (page: Page): Locator => page.locator('.user-dropdown .dropdown-item', { hasText: 'Sign Out' }),
}

// ---------------------------------------------------------------------------
// Sidebar (layouts/AppSidebar.vue) — category tree
// ---------------------------------------------------------------------------

export const sidebar = {
  filterInput: (page: Page): Locator => page.locator('.cd-sidebar-filter-input'),
  categoryLinkByText: (page: Page, text: string): Locator =>
    page.locator('.cd-sidebar-tree-list > li.nav-item > a.nav-link', { hasText: text }),
  itemLinkByText: (page: Page, text: string): Locator =>
    page.locator('.cd-sidebar-tree-list .sub-menu a.nav-link', { hasText: text }),
}

// ---------------------------------------------------------------------------
// Dashboard (views/DashboardView.vue)
// ---------------------------------------------------------------------------

export const dashboard = {
  addCategoryButton: (page: Page): Locator => page.locator('.page-header button.btn-primary'),
  // components/CollectionCard.vue — the card's description line is the "N items" count.
  cards: (page: Page): Locator => page.locator('.collection-card'),
  cardByName: (page: Page, name: string): Locator =>
    page.locator('.collection-card', { has: page.locator('h5', { hasText: name }) }),
  cardIcons: (page: Page): Locator => page.locator('.collection-card img.collection-card-image'),
  cardItemCount: (card: Locator): Locator => card.locator('.collection-card-copy p'),
  archivedSection: (page: Page): Locator => page.locator('.archived-section'),
  archivedToggle: (page: Page): Locator => page.locator('.archived-toggle'),
  archivedRows: (page: Page): Locator => page.locator('.archived-row'),
  archivedRowByName: (page: Page, name: string): Locator =>
    page.locator('.archived-row', { has: page.locator('.archived-row-name', { hasText: name }) }),
  archivedRestore: (row: Locator): Locator => row.locator('.archived-action-restore'),
  archivedDelete: (row: Locator): Locator => row.locator('.archived-action-delete'),
}

// ---------------------------------------------------------------------------
// Generic cd-modal (Teleport to body — reused by every create/confirm dialog)
// ---------------------------------------------------------------------------

export const modal = {
  box: (page: Page): Locator => page.locator('.cd-modal-box'),
  title: (page: Page): Locator => page.locator('.cd-modal-title'),
  close: (page: Page): Locator => page.locator('.cd-modal-close'),
  nameInput: (page: Page): Locator => page.locator('.cd-modal-body input.form-control[type="text"]'),
  previewIdCode: (page: Page): Locator => page.locator('.cd-modal-body code'),
  fileInput: (page: Page): Locator => page.locator('.cd-modal-body input[type="file"]'),
  cancelButton: (page: Page): Locator => page.locator('.cd-modal-footer .btn-secondary'),
  primaryButton: (page: Page): Locator => page.locator('.cd-modal-footer .btn-primary'),
  dangerButton: (page: Page): Locator => page.locator('.cd-modal-footer .btn-danger'),
  errorText: (page: Page): Locator => page.locator('.cd-modal-body .text-danger'),
}

// ---------------------------------------------------------------------------
// AddCategoryModal / AddItemModal (components/AddCategoryModal.vue, AddItemModal.vue)
// ---------------------------------------------------------------------------

export const addItem = {
  openButton: (page: Page): Locator => page.locator('.category-items-panel button.btn-primary'),
  // Opt-in since 84bc38e: create_item generates no voices unless this is checked.
  generateVoicesCheckbox: (page: Page): Locator =>
    page.locator('.cd-modal-body .form-check-label', { hasText: 'Generate the voices' }).locator('input[type="checkbox"]'),
}

// ---------------------------------------------------------------------------
// CategoryDetail (components/CategoryDetail.vue)
// ---------------------------------------------------------------------------

export const categoryDetail = {
  idHeading: (page: Page): Locator => page.locator('.id-summary-panel .id-item-id'),
  icon: (page: Page): Locator => page.locator('.id-summary-image-preview .id-summary-image'),
  archiveButton: (page: Page): Locator => page.locator('.category-archive-btn'),
  description: (page: Page): Locator => page.locator('.id-description'),
}

// ---------------------------------------------------------------------------
// VoiceCardsGrid (components/VoiceCardsGrid.vue) — used by both CategoryDetail and
// ItemDetailsView's summary panel (NOT the item voices table, which has its own markup).
// ---------------------------------------------------------------------------

export const voiceCards = {
  all: (page: Page): Locator => page.locator('.voice-card'),
  byLanguageFlag: (page: Page, flagCode: string): Locator =>
    page.locator('.voice-card', { has: page.locator(`.flag-icon-${flagCode}`) }),
  playButton: (card: Locator): Locator => card.locator('.btn-play'),
  deleteButton: (card: Locator): Locator => card.locator('.btn-icon-delete'),
  name: (card: Locator): Locator => card.locator('.voice-card-name'),
}

// ---------------------------------------------------------------------------
// ItemGrid (components/ItemGrid.vue) — items grid under a category
// ---------------------------------------------------------------------------

export const itemGrid = {
  cards: (page: Page): Locator => page.locator('.item-grid .item-card'),
  cardByLabel: (page: Page, label: string): Locator =>
    page.locator('.item-grid .item-card', { has: page.locator('.item-card-name', { hasText: label }) }),
  fileCountBadge: (card: Locator): Locator => card.locator('.file-count-badge'),
}

// ---------------------------------------------------------------------------
// ItemDetailsView (views/ItemDetailsView.vue)
// ---------------------------------------------------------------------------

export const itemDetail = {
  idHeading: (page: Page): Locator => page.locator('.id-summary-panel .id-item-id'),
  currentImage: (page: Page): Locator => page.locator('.id-summary-copy .id-summary-image'),
  sectionTitle: (page: Page, label: 'Images' | 'Voices'): Locator =>
    page.locator('.id-section-title', { hasText: label }),

  imageCards: (page: Page): Locator => page.locator('.id-image-grid .id-image-card:not(.id-add-image-card)'),
  mainImageCard: (page: Page): Locator => page.locator('.id-image-card.id-image-card-main'),
  selectImageButton: (card: Locator): Locator => card.locator('.id-release-btn'),
  deleteImageButton: (card: Locator): Locator => card.locator('.file-actions .btn-icon-delete'),
  replaceImageButton: (card: Locator): Locator => card.locator('.file-actions .btn-icon-edit'),
  addImageTile: (page: Page): Locator => page.locator('.id-add-image-card'),
  emptyImagesState: (page: Page): Locator => page.locator('.file-empty'),
  /**
   * The two hidden `<input type=file>` elements in ItemDetailsView.vue (add image /
   * replace image) share no distinguishing attribute — ordinal position in the DOM is
   * the only handle. Fragile by nature; if a future edit reorders them, update the
   * indices here, not each spec.
   */
  hiddenFileInputs: (page: Page): Locator => page.locator('input[type="file"].d-none'),
  addImageInput: (page: Page): Locator => itemDetail.hiddenFileInputs(page).nth(0),
  replaceImageInput: (page: Page): Locator => itemDetail.hiddenFileInputs(page).nth(1),

  // The item's voices render as a VoiceCardsGrid in the summary panel: one card (and one
  // play button) per SUPPORTED_LANGUAGES entry.
  voiceCards: (page: Page): Locator => page.locator('.id-summary-voices-panel .voice-card'),
  voicePlayButtons: (page: Page): Locator => page.locator('.id-summary-voices-panel .voice-card .btn-play'),

  // The item actions (`itemActions`) render twice: inline buttons, shown only when the
  // item-details container is >= 64rem wide, and a MobileActionsMenu ("⋯") otherwise —
  // which is what Desktop Chrome's 1280px viewport gets once the sidebar is subtracted.
  // Neither carries a per-action class: release is the "success" tone, delete the only
  // danger action. Use clickItemAction() rather than these locators directly.
  releaseButton: (page: Page): Locator =>
    page.locator('.id-summary-panel .id-summary-buttons .id-summary-action-success'),
  deleteItemButton: (page: Page): Locator =>
    page.locator('.id-summary-panel .id-summary-buttons .btn-outline-danger'),
  itemActionsMenuTrigger: (page: Page): Locator =>
    page.locator('.id-summary-panel .id-summary-menu .mobile-actions-trigger'),
  itemActionsMenuItem: (page: Page, action: 'release' | 'delete'): Locator =>
    page.locator(`.id-summary-panel .id-summary-menu .mobile-actions-item-${action === 'delete' ? 'danger' : 'success'}`),
  /** Clicks an item action through whichever of the two renderings is visible. */
  clickItemAction: async (page: Page, action: 'release' | 'delete'): Promise<void> => {
    const trigger = itemDetail.itemActionsMenuTrigger(page)
    const inline = action === 'delete' ? itemDetail.deleteItemButton(page) : itemDetail.releaseButton(page)
    await expect(inline.or(trigger).filter({ visible: true })).toHaveCount(1)
    if (await inline.isVisible()) {
      await inline.click()
      return
    }
    await trigger.click()
    await itemDetail.itemActionsMenuItem(page, action).click()
  },

  imagePreviewOverlay: (page: Page): Locator => page.locator('.image-preview-overlay'),
}

// ---------------------------------------------------------------------------
// Native <dialog> confirmation (components/DeleteFileDialog.vue on VoiceDialog.vue) —
// used for image/voice file deletion, distinct from the cd-modal pattern above.
// ---------------------------------------------------------------------------

export const fileDialog = {
  box: (page: Page): Locator => page.locator('dialog.voice-dialog[open]'),
  title: (page: Page): Locator => page.locator('dialog.voice-dialog[open] .voice-dialog-header h2'),
  confirmButton: (page: Page): Locator => page.locator('dialog.voice-dialog[open] .delete-file-confirm'),
}

// ---------------------------------------------------------------------------
// Toasts (shared cd-toast pattern across views)
// ---------------------------------------------------------------------------

export const toast = {
  any: (page: Page): Locator => page.locator('.cd-toast'),
  success: (page: Page): Locator => page.locator('.cd-toast-success'),
  error: (page: Page): Locator => page.locator('.cd-toast-error'),
}
