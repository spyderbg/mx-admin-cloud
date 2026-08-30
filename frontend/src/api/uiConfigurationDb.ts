const DB_NAME = 'mx-admin'
const DB_VERSION = 1
const STORE_NAME = 'ui_configuration'

export type UiConfigurationValue = string | number | boolean | null

export interface UiConfigurationRecord {
  key: string
  value: UiConfigurationValue
  updatedAt: string
}

let dbPromise: Promise<IDBDatabase> | null = null

function openDatabase(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'key' })
      }
    }

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })

  return dbPromise
}

function getStore(db: IDBDatabase, mode: IDBTransactionMode): IDBObjectStore {
  return db.transaction(STORE_NAME, mode).objectStore(STORE_NAME)
}

export async function readUiConfiguration(
  key: string,
): Promise<UiConfigurationRecord | null> {
  const db = await openDatabase()

  return new Promise((resolve, reject) => {
    const request = getStore(db, 'readonly').get(key)

    request.onsuccess = () => {
      resolve((request.result as UiConfigurationRecord | undefined) ?? null)
    }
    request.onerror = () => reject(request.error)
  })
}

export async function writeUiConfiguration(
  key: string,
  value: UiConfigurationValue,
): Promise<void> {
  const db = await openDatabase()
  const record: UiConfigurationRecord = {
    key,
    value,
    updatedAt: new Date().toISOString(),
  }

  return new Promise((resolve, reject) => {
    const request = getStore(db, 'readwrite').put(record)

    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
  })
}
