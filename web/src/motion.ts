/** Read the current preference at the moment an interaction starts. */
export function motionDuration(milliseconds: number): number {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : milliseconds;
}
