export const inches = (value: number): number => value;

export function normalizeColor(value: string): string {
  const color = value.replace(/^#/, "").toUpperCase();
  if (!/^[0-9A-F]{6}$/.test(color)) throw new Error("invalid color");
  return color;
}
