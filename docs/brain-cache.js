// The last copy of your GitHub brain on this device, so opening Cairn (a browser tab or the home-screen
// app) shows your notes at once instead of downloading the whole vault again. Live sync then fetches
// only the notes whose sha changed since. IndexedDB, not localStorage: a vault is often over 5 MB.
// Kept per GitHub account and wiped on sign-out, so a private vault never outlives your sign-in here.

const DB_NAME = "cairn-brains";
const TABLE = "brains";

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(TABLE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run(mode, action) {
  try {
    const db = await openDb();
    return await new Promise((resolve) => {
      const tx = db.transaction(TABLE, mode);
      const request = action(tx.objectStore(TABLE));
      tx.oncomplete = () => resolve(request.result ?? null);
      tx.onerror = () => resolve(null);
      tx.onabort = () => resolve(null);
    });
  } catch {
    return null; // private mode or storage full: Cairn just loads from GitHub as before
  }
}

export const brainKey = (login, fullName) => `${login}:${fullName}`.toLowerCase();

export const readBrain = (key) => run("readonly", (table) => table.get(key));

export const saveBrain = (key, snapshot) => run("readwrite", (table) => table.put(snapshot, key));

export const forgetBrains = () => run("readwrite", (table) => table.clear());
