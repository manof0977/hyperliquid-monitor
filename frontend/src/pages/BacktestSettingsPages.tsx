import React, { useEffect, useState } from 'react';
import { apiGet, apiSend, fmtPrice } from '../api/client';
import { binanceBridge } from '../api/binanceBridge';

interface SubMetric {
  trades: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
}

interface BacktestReport {
  symbol: string;
  tf: string;
  note: string | null;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  by_side: Record<string, SubMetric>;
  by_timeframe: Record<string, SubMetric>;
  by_score: Record<string, SubMetric>;
  trades: {
    symbol: string;
    tf: string;
    side: string;
    status: string;
    score: number;
    entry_time: number;
    entry: number;
    exit: number;
    sl: number;
    tp: number;
    r_multiple: number;
    outcome: string;
  }[];
}

export const BacktestingPage: React.FC = () => {
  const [symbolsText, setSymbolsText] = useState('BTCUSDT, ETHUSDT, SOLUSDT');
  const [selectedTfs, setSelectedTfs] = useState<string[]>(['1h', '4h']);
  const [minScore, setMinScore] = useState(60);
  const [feePct, setFeePct] = useState(0.05);
  const [slipPct, setSlipPct] = useState(0.02);
  const [allowDev, setAllowDev] = useState(true);
  const [running, setRunning] = useState(false);
  const [rep, setRep] = useState<BacktestReport | null>(null);

  const toggleTf = (t: string) =>
    setSelectedTfs((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setRunning(true);
    try {
      const syms = symbolsText.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      const tfs = selectedTfs.length ? selectedTfs : ['1h'];
      let r = await apiSend<BacktestReport>('/api/backtest', 'POST', {
        symbols: syms,
        timeframes: tfs,
        min_tech_score: minScore,
        allow_developing: allowDev,
        fee_pct: feePct,
        slippage_pct: slipPct,
      });
      if (r.total_trades === 0 && r.note && r.note.includes('queued for live Binance sync')) {
        await Promise.all(syms.slice(0, 5).map((s) => binanceBridge.syncSingleSymbolNow(s)));
        r = await apiSend<BacktestReport>('/api/backtest', 'POST', {
          symbols: syms,
          timeframes: tfs,
          min_tech_score: minScore,
          allow_developing: allowDev,
          fee_pct: feePct,
          slippage_pct: slipPct,
        });
      }
      setRep(r);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Causal Walk-Forward Backtesting</h1>
          <p className="muted">
            Zero look-ahead bias • Bar-by-bar closed-candle simulation • Next-bar open fills with adverse slippage & fees
          </p>
        </div>
      </div>

      <form onSubmit={run} className="panel form-grid">
        <label>
          <span>Symbols (comma-separated)</span>
          <input className="input" value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} />
        </label>
        <label>
          <span>Timeframes</span>
          <div className="tf-bar">
            {['15m', '1h', '4h', '1d'].map((t) => (
              <button type="button" key={t} className={`tf-btn ${selectedTfs.includes(t) ? 'active' : ''}`} onClick={() => toggleTf(t)}>
                {t.toUpperCase()}
              </button>
            ))}
          </div>
        </label>
        <label>
          <span>Min Technical Score</span>
          <input className="input" type="number" value={minScore} min={35} max={90} onChange={(e) => setMinScore(Number(e.target.value))} />
        </label>
        <label>
          <span>Fee per side (%)</span>
          <input className="input" type="number" step="0.01" value={feePct} onChange={(e) => setFeePct(Number(e.target.value))} />
        </label>
        <label>
          <span>Slippage (%)</span>
          <input className="input" type="number" step="0.01" value={slipPct} onChange={(e) => setSlipPct(Number(e.target.value))} />
        </label>
        <label className="inline-ctl" style={{ alignSelf: 'end' }}>
          <input type="checkbox" checked={allowDev} onChange={(e) => setAllowDev(e.target.checked)} />
          <span>Include DEVELOPING setups</span>
        </label>
        <div style={{ alignSelf: 'end' }}>
          <button className="btn primary" type="submit" disabled={running}>
            {running ? 'Running Causal Simulation…' : 'Run Backtest'}
          </button>
        </div>
      </form>

      {rep && (
        <>
          {rep.note && <div className="banner warn">{rep.note}</div>}
          <div className="kpi-grid">
            <div className="kpi-card"><span className="kpi-label">Total Trades</span><span className="kpi-val">{rep.total_trades}</span></div>
            <div className="kpi-card"><span className="kpi-label">Win Rate</span><span className="kpi-val">{rep.win_rate}%</span></div>
            <div className="kpi-card"><span className="kpi-label">Profit Factor</span><span className="kpi-val">{rep.profit_factor}</span></div>
            <div className="kpi-card"><span className="kpi-label">Average R Multiple</span><span className={`kpi-val ${rep.avg_r >= 0 ? 'bull-txt' : 'bear-txt'}`}>{rep.avg_r}R</span></div>
            <div className="kpi-card"><span className="kpi-label">Net Cumulative R</span><span className={`kpi-val ${rep.total_r >= 0 ? 'bull-txt' : 'bear-txt'}`}>{rep.total_r}R</span></div>
            <div className="kpi-card"><span className="kpi-label">Max Drawdown</span><span className="kpi-val bear-txt">-{rep.max_drawdown_r}R</span></div>
          </div>

          <div className="three-col">
            <div className="panel">
              <h3>Long vs Short</h3>
              <BreakdownTable map={rep.by_side} />
            </div>
            <div className="panel">
              <h3>By Timeframe</h3>
              <BreakdownTable map={rep.by_timeframe} />
            </div>
            <div className="panel">
              <h3>By Signal Score Tier</h3>
              <BreakdownTable map={rep.by_score} />
            </div>
          </div>

          {rep.trades.length > 0 && (
            <div className="panel">
              <h3>Executed Trade Log (Latest {rep.trades.length})</h3>
              <div className="table-scroll">
                <table className="data-table compact">
                  <thead>
                    <tr>
                      <th>Time</th><th>Symbol</th><th>TF</th><th>Side</th><th>Score</th>
                      <th>Entry</th><th>SL</th><th>TP1</th><th>Exit</th><th>Net R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rep.trades.map((t, i) => (
                      <tr key={i}>
                        <td className="mono small muted">{new Date(t.entry_time).toLocaleString()}</td>
                        <td className="sym">{t.symbol}</td>
                        <td>{t.tf}</td>
                        <td className={t.side === 'LONG' ? 'bull-txt' : 'bear-txt'}>{t.side}</td>
                        <td className="mono">{t.score}</td>
                        <td className="mono">{fmtPrice(t.entry)}</td>
                        <td className="mono">{fmtPrice(t.sl)}</td>
                        <td className="mono">{fmtPrice(t.tp)}</td>
                        <td className="mono">{fmtPrice(t.exit)}</td>
                        <td className={`mono ${t.r_multiple > 0 ? 'bull-txt' : 'bear-txt'}`}>
                          {t.r_multiple > 0 ? '+' : ''}{t.r_multiple}R
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

const BreakdownTable: React.FC<{ map: Record<string, SubMetric> }> = ({ map }) => (
  <table className="data-table compact">
    <thead>
      <tr><th>Bucket</th><th>Trades</th><th>Win%</th><th>Avg R</th><th>Net R</th></tr>
    </thead>
    <tbody>
      {Object.entries(map).map(([k, v]) => (
        <tr key={k}>
          <td className="sym">{k}</td>
          <td className="mono">{v.trades}</td>
          <td className="mono">{v.win_rate}%</td>
          <td className={`mono ${v.avg_r >= 0 ? 'bull-txt' : 'bear-txt'}`}>{v.avg_r}R</td>
          <td className={`mono ${v.total_r >= 0 ? 'bull-txt' : 'bear-txt'}`}>{v.total_r}R</td>
        </tr>
      ))}
    </tbody>
  </table>
);

export const SettingsPage: React.FC = () => {
  const [cfg, setCfg] = useState<any>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    apiGet('/api/settings').then(setCfg);
  }, []);

  if (!cfg) return <div className="page"><div className="empty">Loading settings…</div></div>;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const updated = await apiSend('/api/settings', 'PUT', cfg);
    setCfg(updated);
    setSavedMsg('Settings saved & persisted to SQLite.');
    setTimeout(() => setSavedMsg(null), 3500);
  };

  const setTh = (k: string, val: number) =>
    setCfg({ ...cfg, thresholds: { ...cfg.thresholds, [k]: val } });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Scanner, Scoring Thresholds & Telegram Configuration</h1>
          <p className="muted">All score gates, SMC tolerances, scan limits & optional Telegram notifications</p>
        </div>
      </div>
      {savedMsg && <div className="banner">{savedMsg}</div>}
      <form onSubmit={save} className="two-col">
        <div className="panel form-stack">
          <h3>Score Tier & Confirmation Gates</h3>
          <label><span>High-Quality Candidate (default 85)</span>
            <input className="input" type="number" value={cfg.thresholds.high_quality} onChange={(e) => setTh('high_quality', Number(e.target.value))} />
          </label>
          <label><span>Strong Candidate (default 75)</span>
            <input className="input" type="number" value={cfg.thresholds.strong} onChange={(e) => setTh('strong', Number(e.target.value))} />
          </label>
          <label><span>Developing Setup (default 60)</span>
            <input className="input" type="number" value={cfg.thresholds.developing} onChange={(e) => setTh('developing', Number(e.target.value))} />
          </label>
          <label><span>Weak Threshold (default 40)</span>
            <input className="input" type="number" value={cfg.thresholds.weak} onChange={(e) => setTh('weak', Number(e.target.value))} />
          </label>
          <label><span>Min Opportunity Score for CONFIRMED (default 70)</span>
            <input className="input" type="number" value={cfg.thresholds.confirmed_min} onChange={(e) => setTh('confirmed_min', Number(e.target.value))} />
          </label>
        </div>

        <div className="panel form-stack">
          <h3>Engine & Optional Telegram Alerts</h3>
          <label><span>Symbols per Full MTF Scan Cycle (0 = Scan ALL Binance USDT Perpetual Coins)</span>
            <input className="input" type="number" min={0} max={1000} value={cfg.scan_limit} onChange={(e) => setCfg({ ...cfg, scan_limit: Number(e.target.value) })} />
          </label>
          <label><span>Scan Interval (seconds)</span>
            <input className="input" type="number" min={20} max={600} value={cfg.scan_interval_sec} onChange={(e) => setCfg({ ...cfg, scan_interval_sec: Number(e.target.value) })} />
          </label>
          <label><span>Equal High/Low Tolerance (ATR multiple)</span>
            <input className="input" type="number" step="0.05" value={cfg.equal_tolerance_atr} onChange={(e) => setCfg({ ...cfg, equal_tolerance_atr: Number(e.target.value) })} />
          </label>
          <label className="inline-ctl">
            <input type="checkbox" checked={cfg.telegram_enabled} onChange={(e) => setCfg({ ...cfg, telegram_enabled: e.target.checked })} />
            <span>Enable Telegram Push Notifications (or set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in Replit Secrets)</span>
          </label>
          <label><span>Telegram Bot Token (optional)</span>
            <input className="input" type="password" placeholder="Uses Replit Secret if blank" value={cfg.telegram_bot_token} onChange={(e) => setCfg({ ...cfg, telegram_bot_token: e.target.value })} />
          </label>
          <label><span>Telegram Chat ID (optional)</span>
            <input className="input" value={cfg.telegram_chat_id} onChange={(e) => setCfg({ ...cfg, telegram_chat_id: e.target.value })} />
          </label>
          <button className="btn primary" type="submit">Save Configuration</button>
        </div>
      </form>
    </div>
  );
};
