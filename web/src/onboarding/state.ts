export const ONBOARDING_STORAGE_KEY = 'bus-coming-soon:onboarding:v1';

/** Read the entry hash before App writes its default map view into the URL. */
export function shouldShowOnboarding(hash: string): boolean {
  // A view supplied in the URL belongs to the visitor, including list filters.
  if (hash.replace(/^#/, '').trim()) return false;
  try {
    return localStorage.getItem(ONBOARDING_STORAGE_KEY) !== 'seen';
  } catch {
    return true;
  }
}

export function markOnboardingSeen(): void {
  try {
    localStorage.setItem(ONBOARDING_STORAGE_KEY, 'seen');
  } catch {
    // Storage can be unavailable in private browsing; closing must still work.
  }
}
