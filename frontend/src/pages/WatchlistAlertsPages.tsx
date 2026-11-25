import React, { useEffect, useState } from 'react';
import { apiGet, apiSend, fmtPrice, ScanRow } from '../api/client';
import { BiasPill, ScoreCell, StatusBadge } from '../components/Badges';

export const WatchlistPage: React.FC<{
  onSelectCoin: (s: string) => void;
  onSyncWatchlist: (syms: string[]) => void;
}> = ({ onSelectCoin, onSyncWatchlist }) => {
  const [items, setItems] = useState<ScanRow[]>([]);
  const [input, setInput] = useState('');

  const load = async () => {
    const r = await apiGet<{ symbols: string[]; items: ScanRow[] }>('/api/watchlist');
    setItems(r.items);
    onSyncWatchlist(r.symbols);
  };

  useEffect(() => {
    load();
  }, []);

  const addSym = async (e: React.FormEvent) => {
    e.preventDefault();
    const s = input.trim().toUpperCase();
    if (!s) return;
    await apiSend('/api/watchlist', 'POST', { symbol: s.endsWith('USDT') ? s : `${s}USDT`, add: true });
    setInput('');
    load();
  };

  const removeSym = async (s: string) => {
    await apiSend('/api/watchlist', 'POST', { symbol: s, add: false });
    load();
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Watchlist</h1>
          <p className="muted">Track high-interest Binance USDT perpetual pairs</p>
        </div>
        <form onSubmit={addSym} className="inline-ctl">
          <input className="input" placeholder="Add symbol (e.g. BTCUSDT)" value={input} onChange={(e) => setInput(e.target.value)} />
          <button className="btn primary" type="submit">Add</button>
        </form>
      </div>

      <div className="panel">
        {items.length === 0 ? (
          <div className="empty">Your watchlist is empty. Star any coin in the Market Scanner or add one above.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Price</th>
                <th>1D</th>
                <th>4H</th>
                <th>1H</th>
                <th>Long Opp</th>
                <th>Short Opp</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.symbol} className="clickable" onClick={() => onSelectCoin(r.symbol)}>
                  <td className="sym">{r.symbol}</td>
                  <td className="mono">{fmtPrice(r.price)}</td>
                  <td><BiasPill bias={r.biases?.['1d']} /></td>
                  <td><BiasPill bias={r.biases?.['4h']} /></td>
                  <td><BiasPill bias={r.biases?.['1h']} /></td>
                  <td><ScoreCell value={r.long_opp_score ?? 0} side="long" /></td>
                  <td><ScoreCell value={r.short_opp_score ?? 0} side="short" /></td>
                  <td><StatusBadge status={r.status || 'UNSCANNED'} early={r.early_watch} /></td>
                  <td onClick={(e) => { e.stopPropagation(); removeSym(r.symbol); }}>
                    <button className="btn ghost small">Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

interface AlertItem {
  id: number;
  created_at: number;
  symbol: string;
  status: string;
  side: string;
  price: number;
  message: string;
  sent_telegram: boolean;
}

export const AlertsPage: React.FC<{ onSelectCoin: (s: string) => void }> = ({ onSelectCoin }) => {
  const [items, setItems] = useState<AlertItem[]>([]);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  const load = () => apiGet<{ items: AlertItem[] }>('/api/alerts').then((r) => setItems(r.items));
  useEffect(() => {
    load();
    const id = setInterval(load, 6000);
    return () => clearInterval(id);
  }, []);

  const testTelegram = async () => {
    const r = await apiSend<{ ok: boolean; detail: string }>('/api/alerts/test', 'POST');
    setTestMsg(r.ok ? 'Telegram test message delivered!' : `Telegram not sent: ${r.detail}`);
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Signal & Early-Watch Alerts</h1>
          <p className="muted">De-duplicated transition log (Watch → Developing → Confirmed → Invalidated) + optional Telegram push</p>
        </div>
        <button className="btn ghost" onClick={testTelegram}>Send Test Telegram</button>
      </div>
      {testMsg && <div className="banner">{testMsg}</div>}
      <div className="panel">
        {items.length === 0 ? (
          <div className="empty">No alerts emitted yet. Transitions are recorded automatically as setups form.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Transition</th>
                <th>Price</th>
                <th>Telegram</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="clickable" onClick={() => onSelectCoin(a.symbol)}>
                  <td className="mono muted small">{new Date(a.created_at).toLocaleString()}</td>
                  <td className="sym">{a.symbol}</td>
                  <td><StatusBadge status={a.status} /></td>
                  <td className="mono">{fmtPrice(a.price)}</td>
                  <td>{a.sent_telegram ? <span className="bull-txt">Sent</span> : <span className="muted">Local</span>}</td>
                  <td className="small muted" style={{ whiteSpace: 'pre-line' }}>{a.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
