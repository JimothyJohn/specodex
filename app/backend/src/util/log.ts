/**
 * Log helpers — sanitize user-controlled values before they hit
 * `console.log` / `console.warn` / `console.error`.
 *
 * `safeLog` strips CR/LF (the log-injection vector — an attacker who
 * controls the value can otherwise forge fake log lines) and truncates
 * absurdly long values so a single bad input can't pollute the log
 * stream. Use it on every interpolation that originates from
 * `req.params`, `req.query`, `req.body`, or `req.path`.
 *
 * The output is wrapped in single quotes to make it visually obvious
 * in logs that the value was user-supplied.
 */

const MAX_LEN = 200;

export function safeLog(value: unknown): string {
  if (value === null || value === undefined) return String(value);
  let s: string;
  if (typeof value === 'string') {
    s = value;
  } else if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  } else {
    try {
      s = JSON.stringify(value);
    } catch {
      s = '[unserializable]';
    }
  }
  s = s.replace(/[\r\n\t]/g, ' ');
  if (s.length > MAX_LEN) s = s.slice(0, MAX_LEN) + '…';
  return `'${s}'`;
}
