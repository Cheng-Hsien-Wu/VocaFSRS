import { STUDY_SUMMARY_SESSION_STORAGE_KEY } from '../domain';

const STORED_AT_KEY = `${STUDY_SUMMARY_SESSION_STORAGE_KEY}_stored_at`;
const STORED_SESSION_KEY = `${STUDY_SUMMARY_SESSION_STORAGE_KEY}_record`;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

interface StoredSession {
  id: string;
  storedAt: number;
  storage: Storage;
}

function readStoredSession(storage: Storage): StoredSession | null {
  try {
    const rawRecord = storage.getItem(STORED_SESSION_KEY);
    if (rawRecord) {
      try {
        const record = JSON.parse(rawRecord) as { id?: unknown; storedAt?: unknown };
        if (
          typeof record.id === 'string'
          && record.id
          && typeof record.storedAt === 'number'
          && Number.isFinite(record.storedAt)
        ) {
          return { id: record.id, storedAt: record.storedAt, storage };
        }
      } catch {
        // Fall through to the legacy two-key record.
      }
    }

    const id = storage.getItem(STUDY_SUMMARY_SESSION_STORAGE_KEY);
    if (!id) return null;

    const rawStoredAt = storage.getItem(STORED_AT_KEY);
    const parsedStoredAt = rawStoredAt ? Number(rawStoredAt) : Number.NaN;
    const storedAt = Number.isFinite(parsedStoredAt) ? parsedStoredAt : Date.now();
    if (!Number.isFinite(parsedStoredAt)) {
      try {
        storage.setItem(STORED_AT_KEY, String(storedAt));
      } catch {
        // A readable legacy entry is still usable when the store is read-only.
      }
    }
    return { id, storedAt, storage };
  } catch {
    return null;
  }
}

function browserStorages(): Storage[] {
  const storages: Storage[] = [];
  try {
    storages.push(window.sessionStorage);
  } catch {
    // Continue with localStorage when sessionStorage is unavailable.
  }
  try {
    storages.push(window.localStorage);
  } catch {
    // Continue with sessionStorage when localStorage is unavailable.
  }
  return storages;
}

function removeStoredSession(storage: Storage) {
  try {
    storage.removeItem(STUDY_SUMMARY_SESSION_STORAGE_KEY);
    storage.removeItem(STORED_AT_KEY);
    storage.removeItem(STORED_SESSION_KEY);
  } catch {
    // An unavailable store must not block recovery through another store.
  }
}

export function storeStudySummarySessionId(sessionId: string) {
  const record = JSON.stringify({ id: sessionId, storedAt: Date.now() });
  for (const storage of browserStorages()) {
    try {
      storage.setItem(STORED_SESSION_KEY, record);
    } catch {
      // Persist to whichever browser store remains available.
    }
  }
}

export function getStoredStudySummarySessionId(): string | null {
  const candidates = browserStorages().map(readStoredSession)
    .filter((candidate): candidate is StoredSession => candidate !== null)
    .sort((left, right) => right.storedAt - left.storedAt);

  const latest = candidates[0];
  if (!latest) return null;
  if (Date.now() - latest.storedAt <= MAX_AGE_MS) return latest.id;

  for (const candidate of candidates) {
    removeStoredSession(candidate.storage);
  }
  return null;
}
