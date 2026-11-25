import React from 'react';
import { ExplanationReport } from '../api/client';

interface Props {
  report: ExplanationReport | null | undefined;
  symbol: string;
  tf: string;
}

const renderBulletText = (text: string) => {
  // Highlight [PASS] and [WAIT] tokens inside confirmation checklists
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

  return (
    <div className="panel narrative-wrap">
      <div className="panel-head">
        <h3>Full Plain-English Technical Breakdown — {report.symbol} ({report.tf.toUpperCase()})</h3>
        <span className="muted small">100% rule-based mathematical reading • Zero guesswork</span>
      </div>

      <div className={`narrative-headline ${report.tone}`}>
        {report.headline}
      </div>

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
