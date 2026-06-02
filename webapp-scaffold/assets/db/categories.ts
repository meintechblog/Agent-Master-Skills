/**
 * Category presentation for the browse-by-category navigation on the homepage.
 *
 * GENERIC by design (webapp-scaffold Homepage-Pattern v2): the homepage groups documents
 * by their `category` value and renders each group. Display is driven by two optional knobs:
 *
 *   - CATEGORY_LABELS: human label per category slug. Unknown slugs fall back to a generic
 *     prettifier (split on -/_ , title-case), so the UI never breaks on a new category.
 *   - CATEGORY_ORDER: preferred display order. Categories not listed sort alphabetically
 *     after the ordered ones.
 *
 * Both maps start empty → the prettifier + alpha order already produce a sensible browse
 * experience out of the box. Fill them in to tune labels/order for your domain, e.g.:
 *
 *   export const CATEGORY_LABELS = { "reverse-proxy": "Reverse-Proxy", tls: "TLS & Zertifikate" };
 *   export const CATEGORY_ORDER = ["exposure", "reverse-proxy", "tls"];
 */

export const CATEGORY_LABELS: Record<string, string> = {
  // "category-slug": "Human Label",
};

export const CATEGORY_ORDER: string[] = [
  // "category-slug-first", "category-slug-second",
];

export function categoryLabel(cat: string): string {
  return (
    CATEGORY_LABELS[cat] ??
    cat
      .split(/[-_]/)
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(" ")
  );
}

/** Sort category slugs by CATEGORY_ORDER, then alphabetically for anything not listed. */
export function sortCategories(cats: string[]): string[] {
  return [...cats].sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a);
    const ib = CATEGORY_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b, "de");
  });
}
