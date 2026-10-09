export class ApiError extends Error {
  constructor(public code: string, public retryable = false, public status = 0,
    public details: Record<string, unknown> = {}, public operationId?: string) { super(code); }
}
export interface Api {
  request<T>(path: string, method?: 'GET' | 'POST', body?: unknown, idempotencyKey?: string): Promise<T>;
}


export function mutationKey(): string { return crypto.randomUUID(); }
