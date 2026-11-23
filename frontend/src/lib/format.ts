/** Group a decimal string's integer part without converting to a float (money stays exact). */
export function formatAmount(value: string): string {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = (negative ? value.slice(1) : value).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = fraction.padEnd(2, "0");
  return `${negative ? "−" : ""}${grouped}.${cents}`;
}

/**
 * A calendar date (a period end, a due date) is shown exactly as written. A timestamp (when something was uploaded or
 * reviewed) is a moment, so it is shown in the reader's time zone; one stored without an offset is UTC.
 */
export function formatDate(iso: string, timeZone?: string): string {
  const options = { day: "numeric", month: "short", year: "numeric" } as const;
  if (iso.length > 10 && iso[10] === "T") {
    const utc = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/.test(iso) ? iso : `${iso}Z`;
    const moment = new Date(utc.replace(/(\.\d{3})\d+/, "$1"));
    if (!Number.isNaN(moment.getTime())) return moment.toLocaleDateString("en-GB", { ...options, timeZone });
  }
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", { ...options, timeZone: "UTC" });
}

export function shortHash(sha: string): string {
  return `${sha.slice(0, 8)}…${sha.slice(-4)}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
