import React, { useEffect, useState } from 'react';
import { apiGet, fmtPrice, ScanRow } from '../api/client';
import { BiasPill, ScoreCell, StatusBadge } from '../components/Badges';

interface OppResponse {
  min_qualifying_score: number;
  longs: ScanRow[];
  shorts: ScanRow[];
  early_watch: ScanRow[];
}

export const TopOpportunitiesPage: React.FC<{ onSelectCoin: (s: string) => void }> = ({ onSelectCoin }) => {
  const [data, setData] = useState<OppResponse | null>(null);

  useEffect(() => {
    const load = () => apiGet<OppResponse>('/api/opportunities').then(setData).catch(() => {});
    load();
    const id = setInterval(load, 6000);
    return () => clearInterval(id);
  }, []);

  const renderSide = (title: string, side: 'long' | 'short', items: ScanRow[]) => (
    <div className="panel">
      <div className="panel-head">
        <h3 className={side === 'long' ? 'bull-txt' : 'bear-txt'}>{title}</h3>
        <span className="muted small">Top 20 qualifying (score ≥ {data?.min_qualifying_score ?? 60})</span>
      </div>
      {items.length === 0 ? (
        <div className="empty">No qualifying {side.toUpperCase()} setups meet the configured quality threshold.</div>
      ) : (
        <table className="data-table compact">
          <thead>
            <tr>
              <th>#</th>
              <th>Symbol</th>
              <th>Price</th>
              <th>HTF (1D/4H/1H)</th>
              <th>Tech</th>
              <th>Opp</th>
              <th>R:R</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r, i) => {
              const plan = side === 'long' ? r.long_plan : r.short_plan;
              return (
                <tr key={r.symbol} className="clickable" onClick={() => onSelectCoin(r.symbol)}>
                  <td className="muted">{i + 1}</td>
                  <td className="sym">{r.symbol}</td>
                  <td className="mono">{fmtPrice(r.price)}</td>
                  <td className="bias-triplet">
                    <BiasPill bias={r.biases?.['1d']} />
                    <BiasPill bias={r.biases?.['4h']} />
                    <BiasPill bias={r.biases?.['1h']} />
                  </td>
                  <td><ScoreCell value={side === 'long' ? r.long_tech_score : r.short_tech_score} side={side} /></td>
                  <td><ScoreCell value={side === 'long' ? r.long_opp_score : r.short_opp_score} side={side} /></td>
                  <td className="mono">{plan?.rr ? `${plan.rr}R` : '—'}</td>
                  <td><StatusBadge status={r.status} early={r.early_watch} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Top Qualifying Opportunities</h1>
          <p className="muted">Weak setups (&lt; threshold) are strictly excluded — never padded</p>
        </div>
      </div>
      <div className="two-col">
        {renderSide('LONG Rankings', 'long', data?.longs ?? [])}
        {renderSide('SHORT Rankings', 'short', data?.shorts ?? [])}
      </div>
    </div>
  );
};
