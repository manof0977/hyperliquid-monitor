import React from 'react';
import { fmtPrice, fmtVol, ScannerStatus, ScanRow } from '../api/client';
import { BridgeState } from '../api/binanceBridge';
import { ScoreCell, StatusBadge } from '../components/Badges';

interface Props {
  status: ScannerStatus | null;
  bridge?: BridgeState | null;
  topRows: ScanRow[];
  onSelectCoin: (sym: string) => void;
  onTriggerScan: () => void;
}

export const DashboardPage: React.FC<Props> = ({ status, bridge, topRows, onSelectCoin, onTriggerScan }) => {
  const connected = status?.binance_connected || bridge?.active;
  const confirmed = topRows.filter((r) => r.status.includes('CONFIRMED'));
  const early = topRows.filter((r) => r.early_watch);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Market Intelligence Overview</h1>
          <p className="muted">Binance USDⓈ-M Perpetual Futures • Multi-Timeframe Structure, SMC & Opportunity Engine</p>
        </div>
        <button className="btn primary" onClick={onTriggerScan} disabled={status?.scanning || Boolean(bridge?.currentSymbol)}>
          {status?.scanning
            ? `Scanning (${status.scan_progress.done}/${status.scan_progress.total})…`
            : bridge?.currentSymbol
            ? `Syncing ${bridge.currentSymbol} (${status?.total_scanned ?? 0}/${bridge.targetSymbols || status?.eligible_contracts || 'ALL'})…`
            : 'Rescan All Coins'}
        </button>
      </div>

      {bridge?.active && !status?.direct_connected && (
        <div className="banner">
          <strong>Live Browser-Direct Binance Stream Active:</strong> Connected directly from your browser to{' '}
          <code>{bridge.endpoint}</code> (bypassing container firewall) and streaming real OHLCV & WebSocket updates into
          the Python SMC / Scoring Engine.
        </div>
      )}

      {status && !connected && (
        <div className="banner warn">
          <strong>Binance Feed Status — Connecting:</strong>{' '}
          {bridge?.error || 'Establishing live browser bridge to Binance public market-data endpoints…'}
        </div>
      )}

      <div className="kpi-grid">
        <div className="kpi-card">
          <span className="kpi-label">Coins Scanned</span>
          <span className="kpi-val">{status?.total_scanned ?? 0}</span>
          <span className="kpi-sub">{status?.eligible_contracts ?? 0} eligible USDT perps</span>
        </div>
        <div className="kpi-card bull-border">
          <span className="kpi-label">Long Candidates</span>
          <span className="kpi-val bull-txt">{status?.long_candidates ?? 0}</span>
          <span className="kpi-sub">Technical score ≥ developing threshold</span>
        </div>
        <div className="kpi-card bear-border">
          <span className="kpi-label">Short Candidates</span>
          <span className="kpi-val bear-txt">{status?.short_candidates ?? 0}</span>
          <span className="kpi-sub">Technical score ≥ developing threshold</span>
        </div>
        <div className="kpi-card accent-border">
          <span className="kpi-label">Confirmed Signals</span>
          <span className="kpi-val accent-txt">{status?.confirmed_signals ?? 0}</span>
          <span className="kpi-sub">Closed-bar BOS/CHoCH + zone + R:R</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Developing Setups</span>
          <span className="kpi-val">{status?.developing_setups ?? 0}</span>
          <span className="kpi-sub">{status?.early_watch_count ?? 0} in Early Watch</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Binance Connection</span>
          <span className={`kpi-val ${connected ? 'bull-txt' : 'bear-txt'}`}>
            {connected ? 'LIVE' : 'OFFLINE'}
          </span>
          <span className="kpi-sub">
            REST {connected ? 'OK' : '—'} • WS {status?.ws_connected ? 'STREAMING' : 'IDLE'}
          </span>
        </div>
      </div>

      <div className="two-col">
        <div className="panel">
          <div className="panel-head">
            <h3>Highest Opportunity Setups</h3>
            <span className="muted small">Click any symbol to open full SMC chart</span>
          </div>
          {topRows.length === 0 ? (
            <div className="empty">Waiting for live Binance klines to complete initial multi-timeframe scan…</div>
          ) : (
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Price</th>
                  <th>24H</th>
                  <th>Long Opp</th>
                  <th>Short Opp</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {topRows.slice(0, 10).map((r) => (
                  <tr key={r.symbol} onClick={() => onSelectCoin(r.symbol)} className="clickable">
                    <td className="sym">{r.symbol}</td>
                    <td className="mono">{fmtPrice(r.price)}</td>
                    <td className={r.change_24h >= 0 ? 'bull-txt mono' : 'bear-txt mono'}>
                      {r.change_24h >= 0 ? '+' : ''}
                      {r.change_24h.toFixed(2)}%
                    </td>
                    <td><ScoreCell value={r.long_opp_score} side="long" /></td>
                    <td><ScoreCell value={r.short_opp_score} side="short" /></td>
                    <td><StatusBadge status={r.status} early={r.early_watch} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="panel">
          <div className="panel-head">
            <h3>Early Watch Radar (Pre-Breakout)</h3>
            <span className="muted small">Approaching OB / liquidity / EMA expansion</span>
          </div>
          {early.length === 0 && confirmed.length === 0 ? (
            <div className="empty">No active Early Watch alerts at this moment.</div>
          ) : (
            <div className="radar-list">
              {early.slice(0, 8).map((r) => (
                <div key={r.symbol} className="radar-item" onClick={() => onSelectCoin(r.symbol)}>
                  <div className="radar-top">
                    <span className="sym">{r.symbol}</span>
                    <span className="mono">{fmtPrice(r.price)}</span>
                    <span className="muted small">{fmtVol(r.volume_24h)}</span>
                    <StatusBadge status={r.status} early={r.early_watch} />
                  </div>
                  <div className="radar-reasons">{r.early_reasons.join(' • ')}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
