import { createContext, useContext, type ComponentType, type ReactNode } from 'react';

export type Replacement = ComponentType<{ Base: ComponentType<any>; [name: string]: any }>;
export const UiOverrides = createContext<Record<string, Replacement>>({});
/** Narrow structural ownership; the original component remains explicitly usable. */
export function replaceable<P extends object>(id: string, Base: ComponentType<P>) {
  return function Replaceable(props: P) {
    const replacements = useContext(UiOverrides);
    const target = (props as Record<string, unknown>)['data-ui'];
    const Component = (typeof target === 'string' && replacements[target]) || replacements[id];
    return Component ? <Component {...props} Base={Base} /> : <Base {...props} />;
  };
}
export function UiOverrideProvider({ components, children }: { components: Record<string, Replacement>; children: ReactNode }) {
  return <UiOverrides.Provider value={components}>{children}</UiOverrides.Provider>;
}
