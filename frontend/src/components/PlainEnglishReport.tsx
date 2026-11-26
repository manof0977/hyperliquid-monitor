import React from 'react';
import { ExplanationReport, fmtPrice, fmtVol } from '../api/client';
import { ReactionBadge } from './Badges';

interface Props {
  report: ExplanationReport | null | undefined;
  symbol: string;
  tf: string;
}

const renderBulletText = (text: string) => {
  if (!text.includes('[PASS]') && !text.includes('[WAIT]')) {
    return text;
  }
  const parts = text.split(/(\[PASS\]|\[WAIT\])/g);
  return parts.map((part, i) => {
    if (part === '[PASS]') {
      return (
        <span key={i} className="pill bull" style={{ marginRight: 4 }}>
          PASS
        </span>
      );
    }
    if (part === '[WAIT]') {
      return (
        <span key={i} className="pill neutral" style={{ marginRight: 4 }}>
          WAIT
        </span>
      );
    }
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
};

export const PlainEnglishReport: React.FC<Props> = ({ report, symbol, tf }) => {
  if (!report) {
    return (
      <div className="panel narrative-wrap">
        <h3>Full Plain-English Technical Breakdown ({symbol} • {tf.toUpperCase()})</h3>
        <div className="muted">Waiting for live {symbol} candles to generate plain-English breakdown…</div>
      </div>
    );
  }

  const nm = report.next_move_map;
  const pm = nm?.positioning_metrics;
  const rx = report.pre_indicator_radar;

  return (
    <div className="panel narrative-wrap">
      <div className="panel-head">
        <h3>Full Plain-English Technical Breakdown — {report.symbol} ({report.tf.toUpperCase()})</h3>
        <span className="muted small">100% rule-based mathematical reading • Zero guesswork</span>
      </div>

      <div className={`narrative-headline ${report.tone}`}>
        {report.headline}
      </div>

      {rx && (
        <div className="rx-radar-box">
          <div className="rx-radar-head">
            <div>
              <div className="scenario-kicker">PRE-INDICATOR PRICE-ACTION REACTION RADAR</div>
              <div className="scenario-lean">
                State: <ReactionBadge state={rx.state} label={rx.badge_label} />{' '}
                <span className="muted small">
                  Range position {rx.range_position_pct.toFixed(0)}% • EMA20 stretch {rx.ema20_stretch_atr >= 0 ? '+' : ''}
                  {rx.ema20_stretch_atr.toFixed(2)} ATR
                </span>
              </div>
            </div>
            <span className="muted small">Fires BEFORE EMA/MACD/RSI flip — measured on closed candles only</span>
          </div>

          <p className="narrative-summary" style={{ marginBottom: 8 }}>{rx.summary}</p>

          <div className="rx-zones-grid">
            <div className="rx-zone-card bounce">
              <div className="scenario-title">Demand / Bounce Wall Below</div>
              {rx.bounce_zone ? (
                <>
                  <p>
                    <strong>{rx.bounce_zone.label}</strong> · {fmtPrice(rx.bounce_zone.low)} – {fmtPrice(rx.bounce_zone.high)}
                    {' '}({rx.bounce_zone.distance_atr.toFixed(2)} ATR){rx.bounce_zone.in_zone ? ' — PRICE IS IN ZONE' : ''}
                  </p>
                  <p className="muted small">
                    Hard invalidation: {fmtPrice(rx.bounce_zone.invalidation)} (0.25 ATR under zone) • Reaction target:{' '}
                    {fmtPrice(rx.bounce_zone.target_1)}
                  </p>
                </>
              ) : (
                <p className="muted small">No clean demand wall within 6 ATR below price.</p>
              )}
            </div>
            <div className="rx-zone-card reject">
              <div className="scenario-title">Supply / Rejection Wall Above</div>
              {rx.rejection_zone ? (
                <>
                  <p>
                    <strong>{rx.rejection_zone.label}</strong> · {fmtPrice(rx.rejection_zone.low)} – {fmtPrice(rx.rejection_zone.high)}
                    {' '}({rx.rejection_zone.distance_atr.toFixed(2)} ATR){rx.rejection_zone.in_zone ? ' — PRICE IS IN ZONE' : ''}
                  </p>
                  <p className="muted small">
                    Hard invalidation: {fmtPrice(rx.rejection_zone.invalidation)} (0.25 ATR above zone) • Reaction target:{' '}
                    {fmtPrice(rx.rejection_zone.target_1)}
                  </p>
                </>
              ) : (
                <p className="muted small">No clean supply wall within 6 ATR above price.</p>
              )}
            </div>
          </div>

          {rx.why_before_indicators.length > 0 && (
            <ul className="narrative-bullets">
              {rx.why_before_indicators.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          )}

          <div className="rx-exec-strip">
            <span>{rx.aggressive_trigger}</span>
            <span>{rx.conservative_trigger}</span>
            <span className="muted small">
              This radar is NOT a trade signal — it never upgrades a setup to CONFIRMED. Candle-close confirmation,
              structure breaks, and your existing risk rules still gate every entry.
            </span>
          </div>
        </div>
      )}

      {nm && (
        <div className="scenario-map-box">
          <div className="scenario-map-top">
            <div>
              <div className="scenario-kicker">WHAT&apos;S LIKELY NEXT — EXPERT SCENARIO &amp; LIQUIDITY MAGNET MAP</div>
              <div className="scenario-lean">{nm.lean_label}</div>
            </div>
            <div className="magnet-chip">
              <span className="muted small">#1 Price Magnet:</span>{' '}
              <strong className="mono accent-txt">{nm.primary_magnet}</strong>
            </div>
          </div>

          <div className="weight-split-bar" title="Normalized Confluence Weight Split (Long vs Short)">
            <div className="bull-fill" style={{ width: `${nm.bull_weight_pct}%` }}>
              Bullish {nm.bull_weight_pct}%
            </div>
            <div className="bear-fill" style={{ width: `${nm.bear_weight_pct}%` }}>
              Bearish {nm.bear_weight_pct}%
            </div>
          </div>

          <div className="scenario-pair">
            <div className="scenario-card bull-card">
              <div className="scenario-title bull-txt">▲ Scenario A — Bullish Path (If Support Holds)</div>
              <p>{nm.bull_scenario}</p>
            </div>
            <div className="scenario-card bear-card">
              <div className="scenario-title bear-txt">▼ Scenario B — Bearish Path (If Resistance Caps / Support Breaks)</div>
              <p>{nm.bear_scenario}</p>
            </div>
          </div>

          {nm.positioning_read && (
            <div className="positioning-strip">
              <div>
                <strong style={{ color: '#fcd34d' }}>Futures Positioning &amp; Squeeze Read: </strong>
                <span>{nm.positioning_read}</span>
              </div>
              {pm && (
                <div className="pos-pills mono">
                  {pm.funding_rate_pct !== undefined && (
                    <span>Funding: {pm.funding_rate_pct >= 0 ? '+' : ''}{pm.funding_rate_pct.toFixed(4)}%</span>
                  )}
                  {pm.open_interest_usd ? <span>OI: {fmtVol(pm.open_interest_usd)}</span> : null}
                  {pm.retail_long_pct !== undefined && <span>Retail L/S: {pm.retail_long_pct}% / {pm.retail_short_pct}%</span>}
                  {pm.top_trader_long_pct !== undefined && <span>Whales L/S: {pm.top_trader_long_pct}% / {pm.top_trader_short_pct}%</span>}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="narrative-grid">
        {report.sections.map((sec, idx) => (
          <div key={idx} className="narrative-card">
            <h4>{sec.title}</h4>
            <p className="narrative-summary">{sec.summary}</p>
            {sec.bullets.length > 0 && (
              <ul className="narrative-bullets">
                {sec.bullets.map((b, bi) => (
                  <li key={bi}>{renderBulletText(b)}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
