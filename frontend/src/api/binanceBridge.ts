/**
 * Browser-Direct Real Binance Market-Data Bridge
 *
 * When the backend container sits behind a sandbox/cloud firewall that blocks
 * outbound connections to `fapi.binance.com` (`direct_connected === false`),
 * the user's browser connects directly to real Binance public endpoints:
 *   1. Primary:  USDⓈ-M Perpetual Futures (`https://fapi.binance.com` + `wss://fstream.binance.com`)
 *   2. Fallback: Binance Vision public market-data mirror (`https://data-api.binance.vision` + `wss://data-stream.binance.vision:9443`)
 *                when the user's ISP/country geo-blocks `fapi.binance.com`.
 *
 * Zero synthetic or mock candles are ever created — 100% real Binance OHLCV & tickers
 * are streamed into the FastAPI SMC/structure/scoring/SQLite engine.
 */
import { apiGet, apiSend } from './client';

interface EndpointSpec {
  label: string;
  rest: string;
  ws: string;
  kind: 'futures' | 'vision-spot';
}

const ENDPOINTS: EndpointSpec[] = [
  {
    label: 'fapi.binance.com (USDⓈ-M Futures)',
    rest: 'https://fapi.binance.com/fapi/v1',
    ws: 'wss://fstream.binance.com',
    kind: 'futures',
  },
  {
    label: 'fstream-mm / fapi1.binance.com',
    rest: 'https://www.binance.com/fapi/v1',
    ws: 'wss://fstream.binance.com',
    kind: 'futures',
  },
  {
    label: 'data-api.binance.vision (Binance Public Mirror)',
    rest: 'https://data-api.binance.vision/api/v3',
    ws: 'wss://data-stream.binance.vision:9443',
    kind: 'vision-spot',
  },
];

export interface BridgeState {
  active: boolean;
  endpoint: string | null;
  syncedSymbols: number;
  targetSymbols: number;
  currentSymbol: string | null;
  error: string | null;
}

type Listener = (st: BridgeState) => void;

class BrowserBinanceBridge {
  private started = false;
  private stopped = false;
  private spec: EndpointSpec | null = null;
  private ws: WebSocket | null = null;
  private wsSymbolsKey = '';
  private state: BridgeState = {
    active: false,
    endpoint: null,
    syncedSymbols: 0,
    targetSymbols: 0,
    currentSymbol: null,
    error: null,
  };
  private listeners = new Set<Listener>();
  private forceBurst = false;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }

  private emit(patch: Partial<BridgeState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l(this.state));
  }

  triggerImmediateSync() {
    this.forceBurst = true;
  }

  async syncSingleSymbolNow(symbol: string): Promise<boolean> {
    if (!this.spec) {
      this.spec = await this.pickWorkingEndpoint();
    }
    if (!this.spec) return false;
    const tfs = ['5m', '15m', '1h', '4h', '1d', '1w'];
    const frames: Record<string, unknown[][]> = {};
    await Promise.all(
      tfs.map(async (tf) => {
        try {
          const r = await fetch(`${this.spec!.rest}/klines?symbol=${encodeURIComponent(symbol)}&interval=${tf}&limit=300`);
          if (r.ok) frames[tf] = await r.json();
        } catch {}
      })
    );
    if (Object.keys(frames).length > 0) {
      await apiSend('/api/bridge/klines', 'POST', {
        symbol,
        source: this.spec.label,
        frames,
      });
      return true;
    }
    return false;
  }

  start() {
    if (this.started) return;
    this.started = true;
    this.stopped = false;
    this.loop();
  }

  stop() {
    this.stopped = true;
    this.ws?.close();
  }

  private async pickWorkingEndpoint(): Promise<EndpointSpec | null> {
    for (const ep of ENDPOINTS) {
      try {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 6000);
        const r = await fetch(`${ep.rest}/ping`, { signal: ctl.signal });
        clearTimeout(timer);
        if (r.ok) return ep;
      } catch {}
    }
    return null;
  }

  private async bootstrapContractsAndTickers(ep: EndpointSpec) {
    const [infoRes, tkRes] = await Promise.all([
      fetch(`${ep.rest}/exchangeInfo`),
      fetch(`${ep.rest}/ticker/24hr`),
    ]);
    if (!infoRes.ok || !tkRes.ok) throw new Error(`Binance HTTP ${infoRes.status}/${tkRes.status}`);
    const info = await infoRes.json();
    const rawTickers: any[] = await tkRes.json();

    const symbols: any[] = [];
    for (const s of info.symbols || []) {
      const okFutures =
        ep.kind === 'futures' &&
        s.quoteAsset === 'USDT' &&
        s.contractType === 'PERPETUAL' &&
        s.status === 'TRADING';
      const okMirror =
        ep.kind === 'vision-spot' &&
        s.quoteAsset === 'USDT' &&
        s.status === 'TRADING' &&
        !/UPUSDT$|DOWNUSDT$|BULLUSDT$|BEARUSDT$/.test(s.symbol);
      if (okFutures || okMirror) {
        let tick = 0.01;
        for (const f of s.filters || []) {
          if (f.filterType === 'PRICE_FILTER') tick = parseFloat(f.tickSize || '0.01');
        }
        symbols.push({
          symbol: s.symbol,
          baseAsset: s.baseAsset || s.symbol.replace(/USDT$/, ''),
          pricePrecision: Number(s.pricePrecision ?? 4),
          tickSize: tick,
        });
      }
    }

    const symSet = new Set(symbols.map((x) => x.symbol));
    const tickers = rawTickers
      .filter((t) => symSet.has(t.symbol))
      .map((t) => ({
        symbol: t.symbol,
        price: parseFloat(t.lastPrice || '0'),
        change_24h: parseFloat(t.priceChangePercent || '0'),
        volume_24h: parseFloat(t.quoteVolume || '0'),
        high_24h: parseFloat(t.highPrice || '0'),
        low_24h: parseFloat(t.lowPrice || '0'),
      }));

    await apiSend('/api/bridge/bootstrap', 'POST', {
      source: ep.label,
      symbols,
      tickers,
    });
  }

  private ensureBrowserWs(ep: EndpointSpec, topSymbols: string[]) {
    const top = topSymbols.slice(0, 35);
    const key = `${ep.ws}:${top.join(',')}`;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN && this.wsSymbolsKey === key) return;
    if (this.ws) {
      try {
        this.ws.close();
      } catch {}
    }
    if (top.length === 0) return;

    this.wsSymbolsKey = key;
    const streams = ['!miniTicker@arr'];
    for (const s of top) {
      const sl = s.toLowerCase();
      streams.push(`${sl}@kline_5m`, `${sl}@kline_15m`, `${sl}@kline_1h`);
    }
    const url = `${ep.ws}/stream?streams=${streams.join('/')}`;

    let tickerBuf = new Map<string, any>();
    let klineBuf: any[] = [];
    let flushTimer: number | null = null;

    const flush = async () => {
      if (tickerBuf.size === 0 && klineBuf.length === 0) return;
      const tks = Array.from(tickerBuf.values());
      const kls = klineBuf;
      tickerBuf = new Map();
      klineBuf = [];
      try {
        await apiSend('/api/bridge/ws', 'POST', {
          ws_open: true,
          tickers: tks.length ? tks : null,
          klines: kls.length ? kls : null,
        });
      } catch {}
    };

    try {
      const ws = new WebSocket(url);
      this.ws = ws;
      ws.onopen = () => {
        flushTimer = window.setInterval(flush, 2500);
        apiSend('/api/bridge/ws', 'POST', { ws_open: true, tickers: null, klines: null }).catch(() => {});
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          const stream: string = msg.stream || '';
          const d = msg.data;
          if (stream === '!miniTicker@arr' && Array.isArray(d)) {
            for (const it of d) {
              if (it?.s) tickerBuf.set(it.s, it);
            }
          } else if (stream.includes('@kline_') && d?.k) {
            const k = d.k;
            // Forward closed candles immediately and throttle live intra-bar updates
            if (k.x || klineBuf.length < 40) {
              klineBuf.push({
                s: d.s || k.s,
                i: k.i,
                t: k.t,
                T: k.T,
                o: k.o,
                h: k.h,
                l: k.l,
                c: k.c,
                v: k.v,
                x: Boolean(k.x),
              });
            }
          }
        } catch {}
      };
      ws.onclose = () => {
        if (flushTimer) clearInterval(flushTimer);
        this.wsSymbolsKey = '';
      };
      ws.onerror = () => {
        ws.close();
      };
    } catch {}
  }

  private async loop() {
    let lastBootstrap = 0;
    while (!this.stopped) {
      try {
        const plan = await apiGet<{
          needed: boolean;
          jobs: { symbol: string; tfs: { tf: string; limit: number; start_time: number | null }[] }[];
          top_symbols: string[];
          total_target?: number;
        }>('/api/bridge/plan');

        if (!plan.needed) {
          // Backend has direct internet access to Binance — browser bridge stays idle!
          this.emit({ active: false, error: null, currentSymbol: null });
          await new Promise((r) => setTimeout(r, 15000));
          continue;
        }

        if (!this.spec) {
          this.spec = await this.pickWorkingEndpoint();
          if (!this.spec) {
            this.emit({
              active: false,
              error: 'Your browser could not reach fapi.binance.com or data-api.binance.vision (check network/VPN).',
            });
            await new Promise((r) => setTimeout(r, 8000));
            continue;
          }
        }

        this.emit({ active: true, endpoint: this.spec.label, error: null });

        // Refresh 24h tickers + contract discovery every 45s
        if (Date.now() - lastBootstrap > 45_000 || plan.top_symbols.length === 0) {
          await this.bootstrapContractsAndTickers(this.spec);
          lastBootstrap = Date.now();
          continue; // re-fetch plan now that symbols_meta & volume ranking exist
        }

        this.ensureBrowserWs(this.spec, plan.top_symbols);

        if (plan.jobs.length === 0) {
          this.emit({ currentSymbol: null, targetSymbols: plan.total_target ?? this.state.targetSymbols });
          for (let w = 0; w < 12 && !this.forceBurst && !this.stopped; w++) {
            await new Promise((r) => setTimeout(r, 1000));
          }
          this.forceBurst = false;
          continue;
        }

        // Process up to 4 symbols concurrently per batch across ALL Binance USDT perpetual contracts (safe inside rate limits)
        const ep = this.spec;
        const workerCount = 4;
        let idx = 0;
        const workers = Array.from({ length: Math.min(workerCount, plan.jobs.length) }, async () => {
          while (idx < plan.jobs.length && !this.stopped) {
            const job = plan.jobs[idx++];
            this.emit({ currentSymbol: job.symbol, targetSymbols: plan.total_target ?? this.state.targetSymbols });
            const frames: Record<string, unknown[][]> = {};
            await Promise.all(
              job.tfs.map(async (q) => {
                const params = new URLSearchParams({
                  symbol: job.symbol,
                  interval: q.tf,
                  limit: String(q.limit),
                });
                if (q.start_time) params.set('startTime', String(q.start_time));
                try {
                  const r = await fetch(`${ep.rest}/klines?${params.toString()}`);
                  if (r.ok) {
                    frames[q.tf] = await r.json();
                  }
                } catch {}
              })
            );
            if (Object.keys(frames).length > 0) {
              await apiSend('/api/bridge/klines', 'POST', {
                symbol: job.symbol,
                source: ep.label,
                frames,
              });
              this.emit({ syncedSymbols: this.state.syncedSymbols + 1 });
            }
            await new Promise((r) => setTimeout(r, 120));
          }
        });
        await Promise.all(workers);
      } catch (err: any) {
        this.spec = null;
        this.emit({ error: err?.message || 'Bridge sync error' });
        await new Promise((r) => setTimeout(r, 4000));
      }
    }
  }
}

export const binanceBridge = new BrowserBinanceBridge();
