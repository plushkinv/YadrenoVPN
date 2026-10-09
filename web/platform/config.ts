export interface PageDescriptor { id: string; title?: string; preview_parameter?: string; }
export interface ApplicationDescriptor {
  entry: string; styles: string[]; pages: PageDescriptor[];
  version: string; build_version: string; instance_id: string; asset_base: string;
  platform_version: string; frame_url: string; mode?: 'live' | 'preview';
}
export function readConfiguration(): ApplicationDescriptor {
  const element = document.getElementById('ui-application');
  if (!element?.textContent) throw new Error('Application descriptor is missing');
  return JSON.parse(element.textContent) as ApplicationDescriptor;
}
export const customization = readConfiguration();
