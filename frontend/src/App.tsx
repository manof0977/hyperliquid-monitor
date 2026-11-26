import React, { useEffect, useState } from 'react';
import { apiGet, apiSend, ScannerStatus, ScanRow } from './api/client';
import { binanceBridge, BridgeState } from './api/binanceBridge';
import { DashboardPage } from './pages/DashboardPage';
import { MarketScannerPage } from './pages/MarketScannerPage';
import { TopOpportunitiesPage } from './pages/TopOpportunitiesPage';
import { PlaybookPage } from './pages/PlaybookPage';
import { CoinAnalysisPage } from './pages/CoinAnalysisPage';
import { AlertsPage, WatchlistPage } from './pages/WatchlistAlertsPages';
import { BacktestingPage, SettingsPage } from './pages/BacktestSettingsPages';

type Tab =
  | 'dashboard'
  | 'scanner'
  | 'opportunities'
  | 'playbook'
  | 'analysis'
  | 'watchlist'
  | 'alerts'
  | 'backtesting'
  | 'settings';

const NAV: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'scanner', label: 'Market Scanner' },
  { id: 'opportunities', label: 'Top Opportunities' },
  { id: 'playbook', label: 'Setups (A+ Playbook)' },
  { id: 'analysis', label: 'Coin Analysis' },
  { id: 'watchlist', label: 'Watchlist' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'backtesting', label: 'Backtesting' },
  { id: 'settings', label: 'Settings' },
];

export const App: React.FC = () => {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [selectedCoin, setSelectedCoin] = useState('BTCUSDT');
  const [preferredSide, setPreferredSide] = useState<'LONG' | 'SHORT' | null>(null);
  const [status, setStatus] = useState<ScannerStatus | null>(null);
  const [topRows, setTopRows] = useState<ScanRow[]>([]);
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']);
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [bridge, setBridge] = useState<BridgeState | null>(null);

  useEffect(() => {
    const refreshSymbols = () => {
      apiGet<{ symbols: string[] }>('/api/symbols')
        .then((r) => {
          if (r.symbols && r.symbols.length > 0) {
            setSymbols(r.symbols);
          }
        })
        .catch(() => {});
    };

    binanceBridge.start();
    const unsub = binanceBridge.subscribe((st) => {
      setBridge(st);
      if (st.targetSymbols > 0 || (st.syncedSymbols > 0 && (st.syncedSymbols <= 3 || st.syncedSymbols % 8 === 0))) {
        refreshSymbols();
      }
    });

    refreshSymbols();
    const symPoll = window.setInterval(refreshSymbols, 6000);

    apiGet<{ symbols: string[] }>('/api/watchlist')
      .then((r) => setWatchlist(r.symbols))
      .catch(() => {});
    apiGet<{ items: ScanRow[]; status: ScannerStatus }>('/api/scanner?sort_by=best_opp')
      .then((r) => {
        setStatus(r.status);
        setTopRows(r.items.slice(0, 25));
      })
      .catch(() => {});

    // Relative WebSocket (works under Replit / E2B HTTPS preview proxy)
    let ws: WebSocket | null = null;
    let retryTimer: number | undefined;
    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/api/ws`);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.status) setStatus(msg.status);
          if (msg.top) setTopRows(msg.top);
        } catch {}
      };
      ws.onclose = () => {
        retryTimer = window.setTimeout(connect, 4000);
      };
    };
    connect();
    return () => {
      unsub();
      clearInterval(symPoll);
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);

  const openCoin = (sym: string, side?: 'LONG' | 'SHORT') => {
    const clean = sym.trim().toUpperCase();
    if (!clean) return;
    setSymbols((prev) => (prev.includes(clean) ? prev : [...prev, clean].sort()));
    setSelectedCoin(clean);
    setPreferredSide(side ?? null);
    setTab('analysis');
  };

  const toggleWatch = async (sym: string, add: boolean) => {
    const r = await apiSend<{ symbols: string[] }>('/api/watchlist', 'POST', { symbol: sym, add });
    setWatchlist(r.symbols);
  };

  const triggerScan = async () => {
    binanceBridge.triggerImmediateSync();
    await apiSend('/api/scan/trigger', 'POST');
    const st = await apiGet<ScannerStatus>('/api/status');
    setStatus(st);
  };

  return (
    <div className="terminal-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-dot" />
          <div>
            <div className="brand-title">BINANCE INTEL</div>
            <div className="brand-sub">USDT-M PERP SCANNER</div>
          </div>
        </div>

        <nav className="nav-list">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={`nav-btn ${tab === n.id ? 'active' : ''}`}
              onClick={() => setTab(n.id)}
            >
              {n.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="conn-row">
            <span className={`dot ${status?.binance_connected ? 'live' : 'off'}`} />
            <span>
              Binance: {status?.binance_connected
                ? (status.bridge_connected && !status.direct_connected ? 'Live (Browser)' : 'Connected')
                : 'Connecting…'}
            </span>
          </div>
          <div className="conn-row">
            <span className={`dot ${status?.ws_connected ? 'live' : 'off'}`} />
            <span>Binance WS: {status?.ws_connected ? 'Streaming' : 'Idle'}</span>
          </div>
          {bridge?.currentSymbol && (
            <div className="muted small" style={{ marginTop: 4 }}>
              Syncing {bridge.currentSymbol}…
            </div>
          )}
          <div className="muted small" style={{ marginTop: 6 }}>
            Scanned: {status?.total_scanned ?? 0} / {status?.eligible_contracts ?? 0}
          </div>
        </div>
      </aside>

      <main className="main-area">
        {tab === 'dashboard' && (
          <DashboardPage
            status={status}
            bridge={bridge}
            topRows={topRows}
            onSelectCoin={openCoin}
            onTriggerScan={triggerScan}
          />
        )}
        {tab === 'scanner' && (
          <MarketScannerPage onSelectCoin={openCoin} watchlist={watchlist} onToggleWatch={toggleWatch} />
        )}
        {tab === 'opportunities' && <TopOpportunitiesPage onSelectCoin={openCoin} />}
        {tab === 'playbook' && <PlaybookPage onSelectCoin={openCoin} />}
        {tab === 'analysis' && (
          <CoinAnalysisPage
            symbol={selectedCoin}
            preferredSide={preferredSide}
            symbols={symbols}
            onChangeSymbol={(s) => {
              setSelectedCoin(s);
              setPreferredSide(null);
            }}
            watchlist={watchlist}
            onToggleWatch={toggleWatch}
          />
        )}
        {tab === 'watchlist' && <WatchlistPage onSelectCoin={openCoin} onSyncWatchlist={setWatchlist} />}
        {tab === 'alerts' && <AlertsPage onSelectCoin={openCoin} />}
        {tab === 'backtesting' && <BacktestingPage />}
        {tab === 'settings' && <SettingsPage />}
      </main>
    </div>
  );
};
