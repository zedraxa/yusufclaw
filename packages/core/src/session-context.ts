import { AsyncLocalStorage } from 'node:async_hooks';

export interface ActiveSessionContext {
  session: string;
  requestId: string;
}

export const activeSessionContext = new AsyncLocalStorage<ActiveSessionContext>();
