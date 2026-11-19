import copy from './locales/vi.json';

export { copy };

export function formatCopy(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) => String(values[name] ?? placeholder));
}
