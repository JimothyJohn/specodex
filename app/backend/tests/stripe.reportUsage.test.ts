/**
 * StripeService.reportUsage wire contract (PAYGATE.md follow-up,
 * 2026-09-13).
 *
 * The billing Lambda's `UsageRequest` is `{user_id, input_tokens,
 * output_tokens}` (stripe_py/src/billing/models.py). Until this fix the
 * client sent `{user_id, tokens}`; the Lambda's token fields default to
 * 0, so every report was accepted and recorded nothing. This pins the
 * exact body so the two sides can't drift apart silently again.
 *
 * config reads STRIPE_LAMBDA_URL at import, so the env is set before
 * the service module is required.
 */

const fetchMock = jest.fn();

// Module-level on purpose: the `require` below runs at file load, before
// any beforeAll hook, and config captures STRIPE_LAMBDA_URL at import.
process.env.STRIPE_LAMBDA_URL = 'https://billing.test';
(global as unknown as { fetch: typeof fetchMock }).fetch = fetchMock;

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { stripeService } = require('../src/services/stripe') as typeof import('../src/services/stripe');

beforeEach(() => {
  fetchMock.mockReset();
});

describe('StripeService.reportUsage', () => {
  it('POSTs the UsageRequest wire shape to /usage', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ total_tokens: 30, recorded: true }),
    });

    const res = await stripeService.reportUsage('user-1', { inputTokens: 10, outputTokens: 20 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string; body: string }];
    expect(url).toBe('https://billing.test/usage');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body).toEqual({ user_id: 'user-1', input_tokens: 10, output_tokens: 20 });
    expect(body).not.toHaveProperty('tokens'); // the old, silently-ignored field
    expect(res).toEqual({ total_tokens: 30, recorded: true });
  });

  it('returns null on a non-2xx and never throws', async () => {
    const errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    await expect(
      stripeService.reportUsage('user-1', { inputTokens: 1, outputTokens: 1 }),
    ).resolves.toBeNull();
    errorSpy.mockRestore();
  });

  it('returns null when fetch rejects (billing outage must not block the user)', async () => {
    const errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));
    await expect(
      stripeService.reportUsage('user-1', { inputTokens: 1, outputTokens: 1 }),
    ).resolves.toBeNull();
    errorSpy.mockRestore();
  });
});
