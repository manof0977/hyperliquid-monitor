import React, { useEffect, useState } from 'react';
import { apiGet, CoinPayload, fmtPrice, fmtVol } from '../api/client';
import { binanceBridge } from '../api/binanceBridge';
import { BiasPill, ScoreCell, StatusBadge } from '../components/Badges';
import { SMCChart } from '../components/SMCChart';
import { PlainEnglishReport } from '../components/PlainEnglishReport';

const TFS = ['5m', '15m', '1h', '4h', '1d', '1w'];

interface Props {
  symbol: string;
  symbols: string[];
  onChangeSymbol: (s: string) => void;
  watchlist: string[];
  onToggleWatch: (s: string, add: boolean) => void;
}

export const CoinAnalysisPage: React.FC<Props> = ({
  symbol,
  symbols,
  onChangeSymbol,
  watchlist,
  onToggleWatch,
}) => {
  const [tf, setTf] = useState('1h');
  const [data, setData] = useState<CoinPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [showEMA, setShowEMA] = useState(true);
  const [showOB, setShowOB] = useState(true);
  const [showFVG, setShowFVG] = useState(true);
  const [showSR, setShowSR] = useState(true);
  const [showPlan, setShowPlan] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const load = async () => {
      try {
        let d = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
        if (alive && d.candles.length === 0) {
          const ok = await binanceBridge.syncSingleSymbolNow(symbol);
          if (ok && alive) {
            d = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
          }
        } else if (alive && !d.sentiment) {
          const ok = await binanceBridge.syncSymbolSentiment(symbol, tf);
          if (ok && alive) {
            d = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
          }
        }
        if (alive) setData(d);
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [symbol, tf]);

  const sum = data?.summary;
  const ov = data?.overlays;
  const starred = watchlist.includes(symbol);

  return (
    <div className="page">
      <div className="coin-topbar">
        <div className="coin-select-group">
          <select className="input sym-select" value={symbol} onChange={(e) => onChangeSymbol(e.target.value)}>
            {[symbol, ...symbols.filter((s) => s !== symbol)].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button className="btn ghost" onClick={() => onToggleWatch(symbol, !starred)}>
            {starred ? '★ Watchlisted' : '☆ Watchlist'}
          </button>
          {sum && (
            <>
              <span className="big-price mono">{fmtPrice(sum.price)}</span>
              <span className={sum.change_24h >= 0 ? 'bull-txt mono' : 'bear-txt mono'}>
                {sum.change_24h >= 0 ? '+' : ''}{sum.change_24h.toFixed(2)}%
              </span>
              <StatusBadge status={sum.status} early={sum.early_watch} />
            </>
          )}
        </div>

        <div className="tf-bar">
          {TFS.map((t) => (
            <button key={t} className={`tf-btn ${tf === t ? 'active' : ''}`} onClick={() => setTf(t)}>
              {t.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="overlay-toggles">
        <label><input type="checkbox" checked={showEMA} onChange={(e) => setShowEMA(e.target.checked)} /> EMA 20/50/100/200</label>
        <label><input type="checkbox" checked={showOB} onChange={(e) => setShowOB(e.target.checked)} /> Order Blocks</label>
        <label><input type="checkbox" checked={showFVG} onChange={(e) => setShowFVG(e.target.checked)} /> FVG Imbalances</label>
        <label><input type="checkbox" checked={showSR} onChange={(e) => setShowSR(e.target.checked)} /> Support / Resistance</label>
        <label><input type="checkbox" checked={showPlan} onChange={(e) => setShowPlan(e.target.checked)} /> Entry / SL / Targets</label>
      </div>

      <div className="analysis-layout">
        <div className="chart-col">
          <div className="chart-panel panel">
            {loading && !data ? (
              <div className="empty">Loading {symbol} {tf} candles from Binance…</div>
            ) : !data || data.candles.length === 0 ? (
              <div className="empty">
                No real historical candles stored yet for <strong>{symbol} ({tf})</strong>. Connect to Binance public
                endpoint <code>fapi.binance.com</code> to populate live OHLCV data.
              </div>
            ) : (
              <SMCChart data={data} showEMA={showEMA} showOB={showOB} showFVG={showFVG} showSR={showSR} showPlan={showPlan} />
            )}
          </div>

          <PlainEnglishReport report={data?.explanation} symbol={symbol} tf={tf} />
        </div>

        <div className="side-analysis">
          <div className="panel">
            <h3>Multi-Timeframe Bias</h3>
            <div className="mtf-grid">
              {['1w', '1d', '4h', '1h', '15m', '5m'].map((k) => (
                <div key={k} className="mtf-cell">
                  <span className="muted small">{k.toUpperCase()}</span>
                  <BiasPill bias={sum?.biases?.[k]} />
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h3>Dual Scoring Engine</h3>
            <div className="score-grid-2">
              <div>
                <div className="muted small">LONG Tech ({sum?.long_tier ?? '—'})</div>
                <ScoreCell value={sum?.long_tech_score ?? 0} side="long" />
                <div className="muted small" style={{ marginTop: 8 }}>LONG Opportunity</div>
                <ScoreCell value={sum?.long_opp_score ?? 0} side="long" />
              </div>
              <div>
                <div className="muted small">SHORT Tech ({sum?.short_tier ?? '—'})</div>
                <ScoreCell value={sum?.short_tech_score ?? 0} side="short" />
                <div className="muted small" style={{ marginTop: 8 }}>SHORT Opportunity</div>
                <ScoreCell value={sum?.short_opp_score ?? 0} side="short" />
              </div>
            </div>
          </div>

          <div className="panel">
            <h3>Structural Trade Levels ({sum?.active_side ?? '—'})</h3>
            <div className="kv-list">
              <div><span>Current Price</span><strong className="mono">{fmtPrice(sum?.price)}</strong></div>
              <div>
                <span>Entry Zone</span>
                <strong className="mono">
                  {sum?.entry_zone ? `${fmtPrice(sum.entry_zone[0])} – ${fmtPrice(sum.entry_zone[1])}` : '—'}
                </strong>
              </div>
              <div><span>Suggested Entry</span><strong className="mono accent-txt">{fmtPrice(sum?.entry)}</strong></div>
              <div><span>Invalidation</span><strong className="mono bear-txt">{fmtPrice(sum?.invalidation)}</strong></div>
              <div><span>Target 1</span><strong className="mono bull-txt">{fmtPrice(sum?.targets?.[0])}</strong></div>
              <div><span>Target 2</span><strong className="mono bull-txt">{fmtPrice(sum?.targets?.[1])}</strong></div>
              <div><span>Target 3</span><strong className="mono bull-txt">{fmtPrice(sum?.targets?.[2])}</strong></div>
              <div><span>Risk / Reward</span><strong className="mono">{sum?.rr ? `${sum.rr} R` : '—'}</strong></div>
              <div><span>Dealing Range</span><strong className="mono">{ov?.smc.zone?.toUpperCase() ?? '—'}</strong></div>
            </div>
          </div>

          {data?.sentiment && (
            <div className="panel">
              <h3>Futures Positioning & Squeeze Data</h3>
              <div className="kv-list">
                {data.sentiment.funding_rate_pct !== undefined && (
                  <div>
                    <span>Funding Rate (8H)</span>
                    <strong className={`mono ${data.sentiment.funding_rate_pct > 0.025 ? 'bear-txt' : data.sentiment.funding_rate_pct < -0.01 ? 'bull-txt' : ''}`}>
                      {data.sentiment.funding_rate_pct >= 0 ? '+' : ''}
                      {Number(data.sentiment.funding_rate_pct).toFixed(4)}%
                    </strong>
                  </div>
                )}
                {data.sentiment.basis_pct !== undefined && (
                  <div>
                    <span>Perp vs Spot Basis</span>
                    <strong className="mono">
                      {data.sentiment.basis_pct >= 0 ? '+' : ''}
                      {Number(data.sentiment.basis_pct).toFixed(3)}%
                    </strong>
                  </div>
                )}
                {data.sentiment.open_interest !== undefined && sum?.price ? (
                  <div>
                    <span>Open Interest</span>
                    <strong className="mono">{fmtVol(Number(data.sentiment.open_interest) * sum.price)}</strong>
                  </div>
                ) : null}
                {data.sentiment.retail_long_pct !== undefined && (
                  <div>
                    <span>Retail Accounts (L / S)</span>
                    <strong className="mono">
                      <span className="bull-txt">{data.sentiment.retail_long_pct}%</span> /{' '}
                      <span className="bear-txt">{(100 - Number(data.sentiment.retail_long_pct)).toFixed(1)}%</span>
                    </strong>
                  </div>
                )}
                {data.sentiment.top_trader_long_pct !== undefined && (
                  <div>
                    <span>Top Whales (L / S)</span>
                    <strong className="mono">
                      <span className="bull-txt">{data.sentiment.top_trader_long_pct}%</span> /{' '}
                      <span className="bear-txt">{(100 - Number(data.sentiment.top_trader_long_pct)).toFixed(1)}%</span>
                    </strong>
                  </div>
                )}
              </div>
            </div>
          )}

          {ov && (
            <div className="panel">
              <h3>{tf.toUpperCase()} Indicators & Key Levels</h3>
              <div className="kv-list">
                <div><span>Structure Trend</span><strong>{ov.structure.trend.toUpperCase()}</strong></div>
                <div><span>EMA Stack</span><strong>{String(ov.ind.ema_stack || '').toUpperCase()}</strong></div>
                <div><span>RSI (14)</span><strong className="mono">{Number(ov.ind.rsi14 || 0).toFixed(1)}</strong></div>
                <div><span>MACD Hist</span><strong className="mono">{Number(ov.ind.macd_hist || 0).toPrecision(3)}</strong></div>
                <div><span>Volume / SMA20</span><strong className="mono">{Number(ov.ind.vol_ratio || 0).toFixed(2)}x</strong></div>
                <div><span>PDH / PDL</span><strong className="mono">{fmtPrice(ov.sr.pdh)} / {fmtPrice(ov.sr.pdl)}</strong></div>
                <div><span>PWH / PWL</span><strong className="mono">{fmtPrice(ov.sr.pwh)} / {fmtPrice(ov.sr.pwl)}</strong></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
