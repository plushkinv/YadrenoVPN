import { ApiError, type Api } from './client';

export interface ModuleFile {
  file_id: string; filename: string; content_type: string; size: number; persistent: boolean; expires_at: number | null;
}
export interface ModuleCommand<T> { operation_id: string; result: T; }
export interface ModuleCommandStatus<T> {
  operation_id: string; state: 'pending' | 'completed' | 'result_expired'; result: T | null; error_code: string | null;
}

/** A single facade on direct pages, Mini Apps and the administrator's application frame. */
export function moduleApi(api: Api, moduleId: string) {
  const segment = (value: string) => {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(value)) throw new ApiError('invalid_request');
    return value;
  };
  const base = '/modules/' + segment(moduleId);
  return {
    async query<T>(name: string, inputs: Record<string, unknown> = {}): Promise<T> {
      const response = await api.request<{ result: T }>(base + '/queries/' + segment(name) + '?inputs=' + encodeURIComponent(JSON.stringify(inputs)));
      return response.result;
    },
    command<T>(name: string, inputs: Record<string, unknown>, key: string): Promise<ModuleCommand<T>> {
      return api.request(base + '/operations/' + segment(name), 'POST', inputs, key);
    },
    status<T>(operationId: string): Promise<ModuleCommandStatus<T>> {
      return api.request(base + '/operations/' + encodeURIComponent(operationId));
    },
    upload(file: File): Promise<ModuleFile> {
      const body = new FormData();
      body.append('file', file, file.name);
      return api.request(base + '/files', 'POST', body);
    },
    download(fileId: string): Promise<Blob> {
      if (!api.download) throw new ApiError('feature_unavailable');
      return api.download(base + '/files/' + encodeURIComponent(fileId));
    },
  };
}
