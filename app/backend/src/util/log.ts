/**
 * Log helpers — sanitize user-controlled values before they hit
 * `console.log` / `console.warn` / `console.error`.
 *
 * `safeLog` is a thin formatter: it truncates absurdly long strings and
 * wraps the result in single quotes so it's visually obvious in
 * CloudWatch which fields came from the request. **It does not strip
 * CR/LF.** The CR/LF strip — the actual log-injection barrier — must
 * happen at the call site, inline:
 *
 *     console.log(`[req] path=${safeLog(req.path.replace(/\r|\n/g, ''))}`);
 *
 * The reason for the split is purely tooling: CodeQL's `js/log-injection`
 * sanitizer recognizes `String.prototype.replace` of `\r|\n` *only* when
 * it appears on the data-flow path to the sink. A `.replace` buried
 * inside this helper is not recognized as a barrier (we tried it in PR
 * #81 — alerts re-opened at the helper-wrapped sites in the next scan).
 * Keep the replace at the call site; let this helper do the rest.
 */

const MAX_LEN = 200;

export function safeLog(value: unknown): string {
  if (value === null || value === undefined) return String(value);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);

  let s: string;
  if (typeof value === 'string') {
    s = value;
  } else {
    try {
      s = JSON.stringify(value);
    } catch {
      s = '[unserializable]';
    }
  }

  const truncated = s.length > MAX_LEN ? s.slice(0, MAX_LEN) + '…' : s;
  return `'${truncated}'`;
}
