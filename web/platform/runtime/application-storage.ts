import type { ApplicationStorage } from './contract';

let storage: ApplicationStorage;
export function bindApplicationStorage(value: ApplicationStorage) { storage = value; }
export function stored<T>(key: string, fallback: T): T { return storage.get(key, fallback); }
export function remember(key: string, value: unknown) { storage.set(key, value); }
