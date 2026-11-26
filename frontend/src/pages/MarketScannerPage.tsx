import React, { useEffect, useState } from 'react';
import { apiGet, fmtPrice, fmtVol, ScanRow } from '../api/client';
import { BiasPill, ReactionBadge, ScoreCell, StatusBadge } from '../components/Badges';

interface Props {
  onSelectCoin: (sym: string, side?: 'LONG' | 'SHORT') => void;
  watchlist: string[];
  onToggleWatch: (sym: string, add: boolean) => void;
}

const REACTIONS: { value: string; label: string }[] = [
  { value: 'ALL', label: 'All Pre-Indicator States' },
  { value: 'ACTIONABLE_NOW', label: 'Any Active Reaction (Bounce / Reversal / Pullback)' },
  { value: 'BOUNCE_ZONE', label: 'Bounce Zones (Demand tap)' },
  { value: 'PULLBACK_DUE', label: 'Pullback / Snapback Due (ATR stretch)' },
  { value: 'REVERSAL_ANY', label: 'Reversal Zones (Liquidity sweep)' },
  { value: 'BULLISH_REVERSAL_ZONE', label: 'Bottom Reversal Zones' },
  { value: 'BEARISH_REVERSAL_ZONE', label: 'Top Reversal Zones' },
  { value: 'MID_RANGE_WAIT', label: 'Mid-Range Wait (no edge)' },
];

const STATUSES = [
  'ALL',
  'LONG CONFIRMED',
  'SHORT CONFIRMED',
  'LONG DEVELOPING',
  'SHORT DEVELOPING',
  'LONG WATCH',
  'SHORT WATCH',
  'INVALIDATED',
  'NO SETUP',
];

export const MarketScannerPage: React.FC<Props> = ({ onSelectCoin, watchlist, onToggleWatch }) => {
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [reactionFilter, setReactionFilter] = useState('ALL');
  const [sortBy, setSortBy] = useState('best_opp');
  const [minScore, setMinScore] = useState(0);
  const [earlyOnly, setEarlyOnly] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const q = new URLSearchParams({
        search,
        status: statusFilter,
        reaction: reactionFilter,
        sort_by: sortBy,
        min_score: String(minScore),
        early_only: String(earlyOnly),
      });
      const res = await apiGet<{ items: ScanRow[] }>(`/api/scanner?${q.toString()}`);
      setRows(res.items);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [search, statusFilter, reactionFilter, sortBy, minScore, earlyOnly]);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>USDT Perpetual Market Scanner</h1>
          <p className="muted">All 6 timeframe biases, independent Long & Short Technical + Opportunity scores</p>
        </div>
      </div>

      <div className="filter-bar">
        <input
          className="input"
          placeholder="Search symbol (e.g. BTC, SOL, ETH)…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s === 'ALL' ? 'All Signal States' : s}</option>
          ))}
        </select>
        <select className="input" value={reactionFilter} onChange={(e) => setReactionFilter(e.target.value)}>
          {REACTIONS.map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>
        <select className="input" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="best_opp">Sort: Highest Opportunity Score</option>
          <option value="long_tech">Sort: Highest Long Technical</option>
          <option value="short_tech">Sort: Highest Short Technical</option>
          <option value="long_opp">Sort: Highest Long Opportunity</option>
          <option value="short_opp">Sort: Highest Short Opportunity</option>
          <option value="volume">Sort: 24H Volume</option>
          <option value="change">Sort: 24H % Change</option>
        </select>
        <label className="inline-ctl">
          <span>Min Tech: {minScore}</span>
          <input type="range" min={0} max={85} step={5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
        </label>
        <label className="inline-ctl">
          <input type="checkbox" checked={earlyOnly} onChange={(e) => setEarlyOnly(e.target.checked)} />
          <span>Early Watch Only</span>
        </label>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>★</th>
              <th>Symbol</th>
              <th>Live Price</th>
              <th>24H Chg</th>
              <th>24H Vol</th>
              <th>1W</th>
              <th>1D</th>
              <th>4H</th>
              <th>1H</th>
              <th>15M</th>
              <th>5M</th>
              <th className="clickable" onClick={() => setSortBy('long_tech')}>Long Tech ▾</th>
              <th className="clickable" onClick={() => setSortBy('short_tech')}>Short Tech ▾</th>
              <th className="clickable" onClick={() => setSortBy('long_opp')}>Long Opp ▾</th>
              <th className="clickable" onClick={() => setSortBy('short_opp')}>Short Opp ▾</th>
              <th>Signal Status</th>
              <th>Pre-Indicator Reaction</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={17} className="empty">
                  {loading ? 'Loading live contracts…' : 'No symbols match current filters (or awaiting Binance connection).'}
                </td>
              </tr>
            ) : (
              rows.map((r) => {
                const starred = watchlist.includes(r.symbol);
                const rowSide: 'LONG' | 'SHORT' = r.status.startsWith('SHORT')
                  ? 'SHORT'
                  : r.status.startsWith('LONG')
                  ? 'LONG'
                  : r.active_side === 'SHORT'
                  ? 'SHORT'
                  : 'LONG';
                return (
                  <tr key={r.symbol} className="clickable" onClick={() => onSelectCoin(r.symbol, rowSide)}>
                    <td
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleWatch(r.symbol, !starred);
                      }}
                      className={starred ? 'star active' : 'star'}
                      title="Toggle Watchlist"
                    >
                      {starred ? '★' : '☆'}
                    </td>
                    <td className="sym">{r.symbol}</td>
                    <td className="mono">{fmtPrice(r.price)}</td>
                    <td className={r.change_24h >= 0 ? 'bull-txt mono' : 'bear-txt mono'}>
                      {r.change_24h >= 0 ? '+' : ''}{r.change_24h.toFixed(2)}%
                    </td>
                    <td className="mono muted">{fmtVol(r.volume_24h)}</td>
                    <td><BiasPill bias={r.biases?.['1w']} /></td>
                    <td><BiasPill bias={r.biases?.['1d']} /></td>
                    <td><BiasPill bias={r.biases?.['4h']} /></td>
                    <td><BiasPill bias={r.biases?.['1h']} /></td>
                    <td><BiasPill bias={r.biases?.['15m']} /></td>
                    <td><BiasPill bias={r.biases?.['5m']} /></td>
                    <td><ScoreCell value={r.long_tech_score} side="long" /></td>
                    <td><ScoreCell value={r.short_tech_score} side="short" /></td>
                    <td><ScoreCell value={r.long_opp_score} side="long" /></td>
                    <td><ScoreCell value={r.short_opp_score} side="short" /></td>
                    <td><StatusBadge status={r.status} early={r.early_watch} /></td>
                    <td>
                      <ReactionBadge
                        state={r.reaction_state}
                        label={r.reaction_label}
                        title={r.reaction_summary || 'Pre-indicator price-action reaction state'}
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
