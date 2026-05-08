import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import Dropdown from './Dropdown';
import Tooltip from './ui/Tooltip';
import './AdminPanel.css';

type Stage = 'dev' | 'staging' | 'prod';
type ProductType = 'motor' | 'drive' | 'gearhead' | 'robot_arm';

const STAGES: Stage[] = ['dev', 'staging', 'prod'];
const PRODUCT_TYPES: ProductType[] = ['motor', 'drive', 'gearhead', 'robot_arm'];

// Context holding the current manufacturer list sourced from the live dev
// table. Fetched once at panel mount; forms consume this via ManufacturerSelect
// rather than free-text inputs so the admin picks from real values.
interface ManufacturerCtx {
  manufacturers: string[];
  loading: boolean;
  reload: () => void;
}
const ManufacturerContext = createContext<ManufacturerCtx>({
  manufacturers: [],
  loading: false,
  reload: () => {},
});

interface ManufacturerSelectProps {
  value: string;
  onChange: (v: string) => void;
  /** If true, add a leading "(any)" option mapping to empty string. */
  allowAny?: boolean;
  id?: string;
}

function ManufacturerSelect({
  value,
  onChange,
  allowAny = true,
  id,
}: ManufacturerSelectProps) {
  const { manufacturers, loading } = useContext(ManufacturerContext);
  const options: { value: string; label: string; disabled?: boolean }[] = [];
  if (allowAny) {
    options.push({ value: '', label: '(any)' });
  } else if (!value) {
    options.push({ value: '', label: 'Select manufacturer…', disabled: true });
  }
  for (const m of manufacturers) {
    options.push({ value: m, label: m });
  }
  return (
    <Dropdown<string>
      id={id}
      value={value}
      onChange={onChange}
      disabled={loading && manufacturers.length === 0}
      ariaLabel="Manufacturer"
      placeholder={allowAny ? '(any)' : 'Select manufacturer…'}
      fullWidth
      options={options}
    />
  );
}

interface DiffData {
  product_type: ProductType;
  source_stage: Stage;
  target_stage: Stage;
  only_in_source: string[];
  only_in_target: string[];
  in_both_count: number;
}

interface PromoteData {
  product_type: ProductType;
  considered: number;
  blocked_by_blacklist: number;
  blocked_manufacturers: string[];
  promoted_products: number;
  promoted_manufacturers: number;
  applied: boolean;
}

interface PurgeData {
  stage: Stage;
  product_type: ProductType | null;
  manufacturer: string | null;
  matched: number;
  deleted: number;
  applied: boolean;
  expected_confirm: string;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  const json = await res.json();
  if (!res.ok || json?.success === false) {
    throw new Error(json?.error || `Request failed: ${res.status}`);
  }
  return json.data as T;
}

// ── Blacklist section ──────────────────────────────────────────────

function BlacklistSection() {
  const [names, setNames] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api<{ banned_manufacturers: string[] }>('/api/admin/blacklist');
      setNames(data.banned_manufacturers);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load blacklist');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const add = async () => {
    const name = input.trim();
    if (!name) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api<{ banned_manufacturers: string[] }>('/api/admin/blacklist', {
        method: 'POST',
        body: JSON.stringify({ manufacturer: name }),
      });
      setNames(data.banned_manufacturers);
      setInput('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Add failed');
    } finally {
      setLoading(false);
    }
  };

  const remove = async (name: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<{ banned_manufacturers: string[] }>(
        `/api/admin/blacklist/${encodeURIComponent(name)}`,
        { method: 'DELETE' }
      );
      setNames(data.banned_manufacturers);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Remove failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="admin-section">
      <h3>Manufacturer Blacklist</h3>
      <p className="section-help">
        Manufacturers on this list are skipped when promoting dev → prod. Add one
        to block its products from ever reaching production. Removes existing
        entries; changes persist to <code>admin/blacklist.json</code>.
      </p>

      <div className="blacklist-list">
        {names.length === 0 ? (
          <span className="blacklist-empty">(blacklist is empty)</span>
        ) : (
          names.map((n) => (
            <span key={n} className="blacklist-chip">
              {n}
              <button
                type="button"
                aria-label={`Remove ${n}`}
                onClick={() => remove(n)}
                disabled={loading}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>

      <div className="admin-form" style={{ gridTemplateColumns: '2fr auto' }}>
        <div className="form-group">
          <label htmlFor="bl-add">Add manufacturer</label>
          <input
            id="bl-add"
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Exact manufacturer name"
            onKeyDown={(e) => e.key === 'Enter' && add()}
          />
        </div>
        <div className="form-group">
          <label>&nbsp;</label>
          <button
            type="button"
            className="btn-apply danger"
            onClick={add}
            disabled={loading || !input.trim()}
          >
            Add to blacklist
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
    </section>
  );
}

// ── Diff section ───────────────────────────────────────────────────

function DiffSection() {
  const [source, setSource] = useState<Stage>('dev');
  const [target, setTarget] = useState<Stage>('prod');
  const [type, setType] = useState<ProductType>('drive');
  const [manufacturer, setManufacturer] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DiffData | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await api<DiffData>('/api/admin/diff', {
        method: 'POST',
        body: JSON.stringify({
          source,
          target,
          type,
          manufacturer: manufacturer.trim() || undefined,
        }),
      });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Diff failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="admin-section">
      <h3>Diff</h3>
      <p className="section-help">
        Compare two stages by <code>product_id</code>. Shows what's unique to
        each side and how many are shared. Read-only.
      </p>

      <div className="admin-form">
        <div className="form-group">
          <label>Source</label>
          <Dropdown<Stage>
            value={source}
            onChange={setSource}
            ariaLabel="Source stage"
            fullWidth
            options={STAGES.map((s) => ({ value: s, label: s }))}
          />
        </div>
        <div className="form-group">
          <label>Target</label>
          <Dropdown<Stage>
            value={target}
            onChange={setTarget}
            ariaLabel="Target stage"
            fullWidth
            options={STAGES.map((s) => ({ value: s, label: s }))}
          />
        </div>
        <div className="form-group">
          <label>Product type</label>
          <Dropdown<ProductType>
            value={type}
            onChange={setType}
            ariaLabel="Product type"
            fullWidth
            options={PRODUCT_TYPES.map((t) => ({ value: t, label: t }))}
          />
        </div>
        <div className="form-group">
          <label>Manufacturer (optional)</label>
          <ManufacturerSelect value={manufacturer} onChange={setManufacturer} />
        </div>
      </div>

      <div className="admin-actions">
        <button type="button" className="btn-dry-run" onClick={run} disabled={loading}>
          {loading ? 'Running…' : 'Run diff'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {data && (
        <div className="result-card">
          <div className="result-row">
            <span>only in {data.source_stage}</span>
            <strong>{data.only_in_source.length}</strong>
          </div>
          <div className="result-row">
            <span>only in {data.target_stage}</span>
            <strong>{data.only_in_target.length}</strong>
          </div>
          <div className="result-row">
            <span>in both</span>
            <strong>{data.in_both_count}</strong>
          </div>
          {data.only_in_source.length > 0 && (
            <>
              <div style={{ marginTop: '0.75rem', fontWeight: 600 }}>
                Candidates to promote ({data.source_stage}):
              </div>
              <div className="id-list">
                {data.only_in_source.map((id) => (
                  <div key={id} className="id-row">
                    {id}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

// ── Promote / Demote shared form ───────────────────────────────────

interface PromoteFormProps {
  kind: 'promote' | 'demote';
  defaultSource: Stage;
  defaultTarget: Stage;
  applyLabel: string;
  description: string;
}

function PromoteForm({
  kind,
  defaultSource,
  defaultTarget,
  applyLabel,
  description,
}: PromoteFormProps) {
  const [source, setSource] = useState<Stage>(defaultSource);
  const [target, setTarget] = useState<Stage>(defaultTarget);
  const [type, setType] = useState<ProductType>('drive');
  const [manufacturer, setManufacturer] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState<PromoteData | null>(null);
  // Snapshot of the form values that produced the current dry run, so we can
  // disable Apply if the user edits anything after previewing.
  const [dryRunKey, setDryRunKey] = useState<string | null>(null);

  const currentKey = `${kind}|${source}|${target}|${type}|${manufacturer.trim()}`;
  const canApply = dryRun !== null && !dryRun.applied && dryRunKey === currentKey;

  const invalidate = () => {
    setDryRun(null);
    setDryRunKey(null);
  };

  const submit = async (apply: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<PromoteData>(`/api/admin/${kind}`, {
        method: 'POST',
        body: JSON.stringify({
          source,
          target,
          type,
          manufacturer: manufacturer.trim() || undefined,
          apply,
        }),
      });
      setDryRun(result);
      setDryRunKey(currentKey);
    } catch (e) {
      setError(e instanceof Error ? e.message : `${kind} failed`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="admin-section">
      <h3>{kind === 'promote' ? 'Promote' : 'Demote'}</h3>
      <p className="section-help">{description}</p>

      <div className="admin-form">
        <div className="form-group">
          <label>Source</label>
          <Dropdown<Stage>
            value={source}
            onChange={(v) => {
              setSource(v);
              invalidate();
            }}
            ariaLabel="Source stage"
            fullWidth
            options={STAGES.map((s) => ({ value: s, label: s }))}
          />
        </div>
        <div className="form-group">
          <label>Target</label>
          <Dropdown<Stage>
            value={target}
            onChange={(v) => {
              setTarget(v);
              invalidate();
            }}
            ariaLabel="Target stage"
            fullWidth
            options={STAGES.map((s) => ({ value: s, label: s }))}
          />
        </div>
        <div className="form-group">
          <label>Product type</label>
          <Dropdown<ProductType>
            value={type}
            onChange={(v) => {
              setType(v);
              invalidate();
            }}
            ariaLabel="Product type"
            fullWidth
            options={PRODUCT_TYPES.map((t) => ({ value: t, label: t }))}
          />
        </div>
        <div className="form-group">
          <label>Manufacturer (optional)</label>
          <ManufacturerSelect
            value={manufacturer}
            onChange={(v) => {
              setManufacturer(v);
              invalidate();
            }}
          />
        </div>
      </div>

      <div className="admin-actions">
        <button
          type="button"
          className="btn-dry-run"
          onClick={() => submit(false)}
          disabled={loading}
        >
          {loading ? 'Running…' : 'Dry run'}
        </button>
        <Tooltip content={canApply ? 'Apply the migration' : 'Run a dry run first with the current form values'}>
          <button
            type="button"
            className="btn-apply"
            onClick={() => submit(true)}
            disabled={loading || !canApply}
          >
            {applyLabel}
          </button>
        </Tooltip>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {dryRun && (
        <div className="result-card">
          <div>
            <span className={`result-mode ${dryRun.applied ? 'applied' : 'dry'}`}>
              {dryRun.applied ? 'Applied' : 'Dry run'}
            </span>
            <strong>{dryRun.product_type}</strong>
          </div>
          <div className="result-row">
            <span>considered</span>
            <strong>{dryRun.considered}</strong>
          </div>
          <div className="result-row">
            <span>blocked by blacklist</span>
            <strong>{dryRun.blocked_by_blacklist}</strong>
          </div>
          <div className="result-row">
            <span>products {dryRun.applied ? 'written' : 'to write'}</span>
            <strong>{dryRun.promoted_products}</strong>
          </div>
          {dryRun.blocked_manufacturers.length > 0 && (
            <div style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}>
              Blocked: {dryRun.blocked_manufacturers.join(', ')}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ── Purge section ──────────────────────────────────────────────────

function PurgeSection() {
  const [stage, setStage] = useState<Stage>('dev');
  const [type, setType] = useState<ProductType | ''>('');
  const [manufacturer, setManufacturer] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState<PurgeData | null>(null);
  const [dryRunKey, setDryRunKey] = useState<string | null>(null);

  const currentKey = `purge|${stage}|${type}|${manufacturer.trim()}`;
  const canApply =
    dryRun !== null &&
    !dryRun.applied &&
    dryRunKey === currentKey &&
    dryRun.expected_confirm === confirm;

  const invalidate = () => {
    setDryRun(null);
    setDryRunKey(null);
  };

  const submit = async (apply: boolean) => {
    if (!type && !manufacturer.trim()) {
      setError('purge requires type and/or manufacturer');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api<PurgeData>('/api/admin/purge', {
        method: 'POST',
        body: JSON.stringify({
          stage,
          type: type || undefined,
          manufacturer: manufacturer.trim() || undefined,
          apply,
          confirm: apply ? confirm : undefined,
        }),
      });
      setDryRun(result);
      setDryRunKey(currentKey);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Purge failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="admin-section">
      <h3>Purge</h3>
      <p className="section-help">
        Delete products in one stage matching a type and/or manufacturer.
        Datasheets are never touched. Irreversible — you will be asked to type a
        confirmation string exactly.
      </p>

      <div className="danger-notice">
        Purge bypasses the blacklist. Use it to clean up prod after adding a
        manufacturer to the blacklist, or to wipe a stale product type.
      </div>

      <div className="admin-form">
        <div className="form-group">
          <label>Stage</label>
          <Dropdown<Stage>
            value={stage}
            onChange={(v) => {
              setStage(v);
              invalidate();
            }}
            ariaLabel="Stage"
            fullWidth
            options={STAGES.map((s) => ({ value: s, label: s }))}
          />
        </div>
        <div className="form-group">
          <label>Product type</label>
          <Dropdown<ProductType | ''>
            value={type}
            onChange={(v) => {
              setType(v);
              invalidate();
            }}
            ariaLabel="Product type"
            fullWidth
            options={[
              { value: '', label: '(any)' },
              ...PRODUCT_TYPES.map((t) => ({ value: t, label: t })),
            ]}
          />
        </div>
        <div className="form-group">
          <label>Manufacturer</label>
          <ManufacturerSelect
            value={manufacturer}
            onChange={(v) => {
              setManufacturer(v);
              invalidate();
            }}
          />
        </div>
      </div>

      <div className="admin-actions">
        <button
          type="button"
          className="btn-dry-run"
          onClick={() => submit(false)}
          disabled={loading || (!type && !manufacturer.trim())}
        >
          {loading ? 'Running…' : 'Dry run'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {dryRun && (
        <div className="result-card">
          <div>
            <span className={`result-mode ${dryRun.applied ? 'applied' : 'dry'}`}>
              {dryRun.applied ? 'Applied' : 'Dry run'}
            </span>
            <strong>{stage}</strong>
          </div>
          <div className="result-row">
            <span>matched</span>
            <strong>{dryRun.matched}</strong>
          </div>
          <div className="result-row">
            <span>deleted</span>
            <strong>{dryRun.deleted}</strong>
          </div>

          {!dryRun.applied && dryRun.matched > 0 && (
            <>
              <div style={{ marginTop: '1rem' }}>
                To delete these {dryRun.matched} items, type the confirmation
                string below exactly:
              </div>
              <code className="confirm-hint">{dryRun.expected_confirm}</code>
              <div className="admin-form" style={{ marginTop: '0.75rem' }}>
                <div className="form-group" style={{ gridColumn: '1 / span 2' }}>
                  <label>Confirm</label>
                  <input
                    type="text"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    placeholder={dryRun.expected_confirm}
                  />
                </div>
              </div>
              <div className="admin-actions">
                <button
                  type="button"
                  className="btn-apply danger"
                  onClick={() => submit(true)}
                  disabled={loading || !canApply}
                >
                  Apply purge
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

// ── Top-level panel ────────────────────────────────────────────────

export default function AdminPanel() {
  const [manufacturers, setManufacturers] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/products/manufacturers');
      const json = await res.json();
      if (json?.success && Array.isArray(json.data)) {
        setManufacturers(json.data);
      }
    } catch {
      // Non-fatal — the select will just be empty; the user can still edit
      // other fields and this panel is dev-only so failures here are soft.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <ManufacturerContext.Provider value={{ manufacturers, loading, reload }}>
      <div className="admin-panel">
        <h2>Admin</h2>
        <p className="page-intro">
          Manage the manufacturer blacklist and move data between{' '}
          <code>dev</code> and <code>prod</code> DynamoDB tables. Every
          destructive action is dry-run first; <strong>Apply</strong> only
          unlocks after a preview with the same parameters.
        </p>

        <BlacklistSection />
        <DiffSection />
        <PromoteForm
          kind="promote"
          defaultSource="dev"
          defaultTarget="prod"
          applyLabel="Apply promotion"
          description="Copy products from dev → prod, skipping any whose manufacturer is on the blacklist."
        />
        <PromoteForm
          kind="demote"
          defaultSource="prod"
          defaultTarget="dev"
          applyLabel="Apply demotion"
          description="Copy products prod → dev with no blacklist check. Rollback / escape hatch."
        />
        <PurgeSection />
      </div>
    </ManufacturerContext.Provider>
  );
}
