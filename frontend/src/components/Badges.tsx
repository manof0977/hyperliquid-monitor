import React from 'react';

export const BiasPill: React.FC<{ bias?: string }> = ({ bias }) => {
  const b = bias || 'NEUTRAL';
  const cls = b === 'BULLISH' ? 'pill bull' : b === 'BEARISH' ? 'pill bear' : 'pill neutral';
  const label = b === 'BULLISH' ? 'BULL' : b === 'BEARISH' ? 'BEAR' : 'NEUT';
  return <span className={cls}>{label}</span>;
};

export const StatusBadge: React.FC<{ status: string; early?: boolean }> = ({ status, early }) => {
  let cls = 'badge none';
  if (status.includes('CONFIRMED')) cls = status.startsWith('LONG') ? 'badge long-conf' : 'badge short-conf';
  else if (status.includes('DEVELOPING')) cls = status.startsWith('LONG') ? 'badge long-dev' : 'badge short-dev';
  else if (status.includes('WATCH')) cls = 'badge watch';
  else if (status === 'INVALIDATED') cls = 'badge inv';
  return (
    <span className="badge-wrap">
      <span className={cls}>{status}</span>
      {early && <span className="badge early">EARLY WATCH</span>}
    </span>
  );
};

export const ScoreCell: React.FC<{ value: number; side: 'long' | 'short' }> = ({ value, side }) => {
  const pct = Math.max(0, Math.min(100, value || 0));
  const tier = pct >= 85 ? 'hq' : pct >= 75 ? 'strong' : pct >= 60 ? 'dev' : pct >= 40 ? 'weak' : 'low';
  return (
    <div className={`score-cell ${side} ${tier}`}>
      <span>{pct.toFixed(1)}</span>
      <div className="score-bar"><div style={{ width: `${pct}%` }} /></div>
    </div>
  );
};

export const ReactionBadge: React.FC<{ state?: string; label?: string; title?: string }> = ({ state, label, title }) => {
  const st = state || 'MID_RANGE_WAIT';
  const txt = label || (st === 'MID_RANGE_WAIT' ? 'MID-RANGE' : st);
  let cls = 'badge rx-mid';
  if (st === 'BOUNCE_ZONE') cls = 'badge rx-bounce';
  else if (st === 'BULLISH_REVERSAL_ZONE') cls = 'badge rx-bull-rev';
  else if (st === 'BEARISH_REVERSAL_ZONE') cls = 'badge rx-bear-rev';
  else if (st === 'PULLBACK_DUE') cls = 'badge rx-pullback';
  else if (st === 'SCANNING') cls = 'badge none';
  return (
    <span className={cls} title={title || txt}>
      {txt}
    </span>
  );
};
