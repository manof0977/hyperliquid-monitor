import React, { useEffect, useState } from 'react';
import { apiGet, fmtPrice, PlaybookResult } from '../api/client';

interface Props {
  symbol?: string;
  playbook?: PlaybookResult | null;
  helpMode?: 'full' | 'short';
}

const GRADE_CLASS: Record<string, string> = {
  'A+': 'grade-aplus',
  A: 'grade-a',
  B: 'grade-b',
  C: 'grade-c',
  'NO-TRADE': 'grade-none',
};

export const GradeBadge: React.FC<{ grade?: string; score?: number }> = ({ grade, score }) => (
  <span className={`grade-badge ${GRADE_CLASS[grade ?? 'NO-TRADE'] ?? 'grade-none'}`}>
    {grade ?? 'NO-TRADE'}
    {score !== undefined && <em>{score.toFixed(0)}</em>}
  </span>
);

const GateRow: React.FC<{ g: { label: string; passed: boolean; detail: string } }> = ({ g }) => (
  <div className={`gate-row ${g.passed ? 'pass' : 'fail'}`}>
    <span className="gate-mark">{g.passed ? '✓' : '✕'}</span>
    <div>
      <div className="gate-label">{g.label}</div>
      <div className="muted small">{g.detail}</div>
    </div>
  </div>
);

const SideBlock: React.FC<{ side: string; s: NonNullable<PlaybookResult['sides']>[string] }> = ({ side, s }) => (
  <div className={`pb-side ${side.toLowerCase()}`}>
    <div className="pb-side-head">
      <b className={side === 'LONG' ? 'bull-txt' : 'bear-txt'}>{side}</b>
      <GradeBadge grade={s.grade} score={s.score} />
      <span className="muted small">{s.gates_passed}/{s.gates_total} gates · {s.setup_type}</span>
    </div>
    <div className="gate-list">
      {s.gates.map((g, i) => (
        <GateRow key={i} g={g} />
      ))}
    </div>
    {s.factors.length > 0 && (
      <div className="factor-list">
        {s.factors.map((f, i) => (
          <span key={i} className={f.points >= 0 ? 'factor-bonus' : 'factor-penalty'}>
            {f.points >= 0 ? '+' : ''}
            {f.points.toFixed(1)} · {f.label}
          </span>
        ))}
      </div>
    )}
    {s.entry && (
      <div className="pb-levels mono small">
        <span>Entry {fmtPrice(s.entry)}</span>
        <span>Stop {fmtPrice(s.stop)} ({s.risk_atr} ATR, {s.stop_anchor})</span>
        {s.targets.slice(0, 3).map((t, i) => (
          <span key={i}>T{i + 1} {fmtPrice(t)}</span>
        ))}
        <span className="accent">R:R {s.rr1}R / {s.rr2}R</span>
      </div>
    )}
    {s.blockers.length > 0 && (
      <div className="playbook-blockers">
        {s.blockers.map((b, i) => (
          <span key={i}>✕ {b}</span>
        ))}
      </div>
    )}
  </div>
);

export const PlaybookPanel: React.FC<Props> = ({ symbol, playbook, helpMode = 'full' }) => {
  const [pb, setPb] = useState<PlaybookResult | null>(playbook ?? null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (playbook) {
      setPb(playbook);
      return;
    }
    if (!symbol) return;
    let alive = true;
    setLoading(true);
    apiGet<{ playbook: PlaybookResult | null }>(`/api/playbook/${symbol}`)
      .then((r) => {
        if (alive) setPb(r.playbook);
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol, playbook]);

  if (!pb) {
    return (
      <div className="panel">
        <h3>Institutional Setup Playbook</h3>
        <div className="muted small">{loading ? 'Grading the checklist…' : 'Waiting for candles to grade this symbol.'}</div>
      </div>
    );
  }

  const best = pb.sides?.[pb.side] ?? null;

  return (
    <div className="panel playbook-panel">
      <div className="panel-head">
        <h3>Institutional Setup Playbook</h3>
        <span className="muted small">A+ checklist · closed-candle only · no predictions</span>
      </div>

      <div className={`pb-verdict ${(pb.grade ?? '').toLowerCase().replace('+', 'plus').replace('-', '')}`}>
        <GradeBadge grade={pb.grade} score={pb.score} />
        <div>
          <div className="pb-verdict-label">{pb.label}</div>
          <div className="muted small">{pb.summary}</div>
        </div>
      </div>

      {best && (
        <div className="pb-exec">
          <div><span className="muted">Action</span> {best.action}</div>
          {best.entry && (
            <div className="mono small">
              Entry <b>{fmtPrice(best.entry)}</b> · Stop <b>{fmtPrice(best.stop)}</b> ({best.risk_atr} ATR beyond {best.stop_anchor}) ·
              Targets {(best.targets ?? []).map((t, i) => `T${i + 1} ${fmtPrice(t)}`).join(' → ')} · <span className="accent">R:R {best.rr1}R / {best.rr2}R</span>
            </div>
          )}
          {best.partial_plan && <div className="muted small">{best.partial_plan}</div>}
          {best.management && <div className="muted small">{best.management}</div>}
          {best.session && (
            <div className="muted small">
              Session: <b>{best.session.name}</b> ({best.session.quality}) — {best.session.note}
            </div>
          )}
        </div>
      )}

      <div className="pb-sides">
        {(['LONG', 'SHORT'] as const).map((side) => {
          const s = pb.sides?.[side];
          if (!s) return null;
          return <SideBlock key={side} side={side} s={s} />;
        })}
      </div>

      {helpMode === 'full' && (
        <details className="pb-help">
          <summary>How this grade is built (and why it is not a prediction)</summary>
          <ol>
            <li><b>HTF bias</b> — weighted agreement across 1W/1D/4H/1H. Counter-trend setups are capped at B.</li>
            <li><b>Entry trigger</b> — either a liquidity sweep (reversal model) or an untested fresh POI in the direction of the trend (continuation model).</li>
            <li><b>Displacement</b> — the impulse leg must be ≥ 1.8 ATR with a confirmed BOS/CHoCH.</li>
            <li><b>POI at value</b> — unmitigated OB / unfilled FVG / protected swing, in discount (long) or premium (short), and ≤ 0.75 ATR from price. Wide zones are refined to their proximal 50%.</li>
            <li><b>LTF shift</b> — closed 15m/5m CHoCH, or a closed absorption/reversal candle at the POI.</li>
            <li><b>Structural stop</b> — beyond the POI, sweep wick or swing, 0.25–2.5 ATR. Never a fixed percentage.</li>
            <li><b>R:R gates</b> — T1 ≥ 2R and T2 ≥ 2.5R from real opposing structure or a measured-move extension.</li>
          </ol>
          <p className="muted small">
            Confluence factors (volume, funding crowding, PDH/PDL confluence, divergence, EMA squeeze, session liquidity) shift the
            score, never the gates. A/A+ requires <b>all</b> gates plus ≥ 75/100. Backtest it yourself on the Backtesting tab in
            “Institutional Playbook” mode — results are cost-inclusive and walk-forward.
          </p>
        </details>
      )}
    </div>
  );
};
