/**
 * Small status badge for compatibility results. Two states only — fits-partial
 * mode means the API never returns 'fail'. Per-field detail rides in the
 * tooltip on hover/focus.
 */
import { CheckStatus } from '../types/compat';
import Tooltip from './ui/Tooltip';

interface CompatBadgeProps {
  status: CheckStatus;
  label?: string;
  detail?: string;
}

const STATUS_LABEL: Record<CheckStatus, string> = {
  ok: 'OK',
  partial: 'Check',
};

export default function CompatBadge({ status, label, detail }: CompatBadgeProps) {
  const text = label ?? STATUS_LABEL[status];
  return (
    <Tooltip content={detail || text}>
      <span
        className={`compat-badge compat-badge-${status}`}
        aria-label={detail ? `${text}: ${detail}` : text}
        tabIndex={0}
      >
        {text}
      </span>
    </Tooltip>
  );
}
