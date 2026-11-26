import React, { useEffect, useState } from 'react';
import { apiGet, fmtPrice, ScanRow } from '../api/client';
import { BiasPill } from '../components/Badges';
import { PlaybookPanel } from '../components/PlaybookCard';

interface OppResponse {
  playbook_ready: ScanRow[];
  playbook_watch: ScanRow[];
  min_qualifying_score: number;
}

const GRADE_ORDER = ['A+', 'A', 'B'];

export const PlaybookPage: React.FC<{ onSelectCoin: (s: string, side?: 'LONG' | 'SHORT') => void }> = ({ onSelectCoin }) => {
  const [data, setData] = useState<OppResponse | null>(null);
  const [gradeFilter, setGradeFilter] = useState<'ALL' | 'A+' | 'A' | 'B'>('ALL');

  useEffect(() => {
    const load = () =>
      apiGet<OppResponse>('/api/opportunities')
        .then(setData)
        .catch(() => {});
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  const rows = [...(data?.playbook_ready ?? []), ...(data?.playbook_watch ?? [])]
    .filter((r) => (gradeFilter === 'ALL' ? true : r.playbook?.grade === gradeFilter))
    .sort((a, b) => {
      const ga = GRADE_ORDER.indexOf(a.playbook?.grade ?? 'C');
      const gb = GRADE_ORDER.indexOf(b.playbook?.grade ?? 'C');
      if (ga !== gb) return ga - gb;
      return (b.playbook?.score ?? 0) - (a.playbook?.score ?? 0);
    });

  const count = (g: string) => [...(data?.playbook_ready ?? []), ...(data?.playbook_watch ?? [])].filter((r) => r.playbook?.grade === g).length;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Institutional Setup Playbook</h1>
          <p className="muted">
            The A+ checklist top discretionary / prop traders actually run: HTF bias → liquidity sweep → displacement → POI at
            discount/premium → LTF shift → structural stop → R:R gates. Only A+ / A are tradeable.
          </p>
        </div>
        <div className="tf-bar">
          {(['ALL', 'A+', 'A', 'B'] as const).map((g) => (
            <button key={g} className={`tf-btn ${gradeFilter === g ? 'active' : ''}`} onClick={() => setGradeFilter(g)}>
              {g === 'ALL' ? `All (${rows.length})` : `${g} (${count(g)})`}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="panel empty">
          No A+/A-grade setups right now — that is the honest answer, and it is <b>most</b> of the time. The scanner keeps
          grading every contract; B-grade (watchlist) candidates appear here as soon as one gate or a confluence factor resolves.
        </div>
      ) : (
        <div className="playbook-list">
          {rows.map((r) => (
            <div key={r.symbol} className="panel playbook-row" onClick={() => onSelectCoin(r.symbol, (r.playbook?.side as 'LONG' | 'SHORT') || undefined)}>
              <div className="playbook-row-head">
                <div>
                  <div className="sym">{r.symbol}</div>
                  <div className="muted small">
                    <BiasPill bias={r.biases?.['1d']} /> <BiasPill bias={r.biases?.['4h']} /> <BiasPill bias={r.biases?.['1h']} />
                  </div>
                </div>
                <span className={`grade-badge ${(r.playbook?.grade ?? '').toLowerCase().replace('+', 'plus').replace('-', '')}`}>
                  {r.playbook?.grade} · {r.playbook?.score?.toFixed(0)}
                </span>
              </div>
              <div className="playbook-row-body">
                <div className="playbook-row-line">
                  <b className={r.playbook?.side === 'LONG' ? 'bull-txt' : 'bear-txt'}>{r.playbook?.label}</b>
                  <span className="muted small">
                    {r.playbook?.gates_passed}/{r.playbook?.gates_total} gates · {r.playbook?.setup_type}
                  </span>
                </div>
                <p className="muted small" style={{ margin: '4px 0' }}>{r.playbook?.summary}</p>
                {r.playbook?.entry ? (
                  <div className="mono small playbook-levels">
                    <span>Entry {fmtPrice(r.playbook.entry)}</span>
                    <span>Stop {fmtPrice(r.playbook.stop)}</span>
                    {(r.playbook.targets ?? []).slice(0, 2).map((t, i) => (
                      <span key={i}>T{i + 1} {fmtPrice(t)}</span>
                    ))}
                    <span className="accent">T1 {r.playbook.rr1}R</span>
                  </div>
                ) : null}
                {(r.playbook?.blockers ?? []).length > 0 && (
                  <div className="playbook-blockers">
                    {(r.playbook?.blockers ?? []).map((b, i) => (
                      <span key={i}>✕ {b}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <PlaybookPanel helpMode="full" />
    </div>
  );
};
