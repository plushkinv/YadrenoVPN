declare module 'virtual:yadreno-customization' {
  const value: {
    version: string;
    build_version: string;
    asset_base: string;
    instance_id: string;
    components: Record<string, import('./runtime/overrides').Replacement>;
    pages: (Omit<import('./runtime/pages').PageDefinition, 'title'> & { title?: string; module_id?: string })[];
    modules: { id: string; version: string; api_version: number }[];
    navigation?: { route: string; label: string }[];
  };
  export default value;
}
