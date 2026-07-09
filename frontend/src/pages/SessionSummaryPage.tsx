import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { api } from '../services/api';
import { formatTaipeiDateTime } from '../utils/datetime';

interface AdjudicationResult {
  id: string;
  card_id: string;
  english?: string;
  part_of_speech?: string | null;
  typed_answer: string;
  expected_answer: string;
  status: 'pending' | 'processing' | 'succeeded' | 'failed';
  verdict?: 'correct' | 'partial' | 'incorrect' | null;
  rating?: 'Good' | 'Hard' | 'Again' | null;
  reason?: string | null;
  confidence?: number | null;
  next_due?: string | null;
  error_message?: string | null;
}

interface AdjudicationStatus {
  pending: number;
  processing: number;
  succeeded: number;
  failed: number;
  total: number;
  results: AdjudicationResult[];
}

export default function SessionSummaryPage() {
  const navigate = useNavigate();
  const sessionId = sessionStorage.getItem('study_summary_typed_session_id');
  const [status, setStatus] = useState<AdjudicationStatus | null>(null);
  const [isLoading, setIsLoading] = useState(Boolean(sessionId));
  const [error, setError] = useState<string | null>(null);
  const [adjudicationStartedAt, setAdjudicationStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const adjudicationInFlightRef = useRef(false);
  const autoRunSessionRef = useRef<string | null>(null);

  const runAdjudication = useCallback(async (retry = false) => {
    if (!sessionId) return;
    if (adjudicationInFlightRef.current) return;
    adjudicationInFlightRef.current = true;
    setIsLoading(true);
    setAdjudicationStartedAt(Date.now());
    setElapsedSeconds(0);
    setError(null);
    try {
      const result = retry
        ? await api.retryStudyAdjudication(sessionId)
        : await api.adjudicateStudySession(sessionId);
      setStatus(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      try {
        setStatus(await api.getStudyAdjudicationStatus(sessionId));
      } catch {
        // Keep the original error visible.
      }
    } finally {
      setIsLoading(false);
      setAdjudicationStartedAt(null);
      adjudicationInFlightRef.current = false;
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId && autoRunSessionRef.current !== sessionId) {
      autoRunSessionRef.current = sessionId;
      runAdjudication();
    }
  }, [runAdjudication, sessionId]);

  useEffect(() => {
    if (!adjudicationStartedAt) return;
    const id = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - adjudicationStartedAt) / 1000)));
    }, 500);
    return () => window.clearInterval(id);
  }, [adjudicationStartedAt]);

  const stats = useMemo(() => {
    const results = status?.results ?? [];
    const again = results.filter(r => r.rating === 'Again').length;
    const hard = results.filter(r => r.rating === 'Hard').length;
    const good = results.filter(r => r.rating === 'Good').length;
    const total = results.length;
    const accuracy = total > 0 ? Math.round(((good + hard) / total) * 100) : 0;
    return { again, hard, good, total, accuracy };
  }, [status]);

  const allDone = Boolean(status && status.total > 0 && status.succeeded === status.total);
  const isStuck = Boolean(sessionId && !isLoading && !allDone);
  const adjudicationPromptSummary = [
    '每一題送出英文單字、詞性、標準中文意思、你的輸入。',
    'LLM 只判斷語意是否正確，不要求逐字相同。',
    '輸出只能是 JSON：correct→Good、partial→Hard、incorrect→Again，並附 confidence 和簡短 reason。',
    '整輪複習會 batch 成一次請求；完成後才套用 FSRS 下次複習時間。',
  ];

  return (
    <div className="full-screen">
      <header className="nav-header nav-header-leading">
        <button className="nav-back" onClick={() => navigate('/')} aria-label="回首頁">
          <MaterialSymbol name="arrow_back" />
        </button>
        <h1 className="nav-title">本輪批改</h1>
      </header>

      <div className="page">
        <div className="page-content page-content-spacious">
          {!sessionId && (
            <div className="empty-state">
              <div>沒有可批改的複習紀錄</div>
              <button className="btn btn-primary" onClick={() => navigate('/')}>回首頁</button>
            </div>
          )}

          {sessionId && (
            <>
              <section className="section-block section-block-accent summary-status-panel">
                <div className="summary-status-row">
                  {isLoading && <span className="loading-spinner" aria-hidden="true" />}
                  <div role="status" aria-live="polite" className="summary-status-copy">
                    {allDone ? 'LLM 批改完成' : isLoading ? `LLM 批改中，已送出 ${elapsedSeconds}s` : isStuck ? '批改中斷，請按下方重新批改' : '等待批改'}
                  </div>
                </div>
                <div className="summary-stat-grid">
                  <Stat label="總題" value={status?.total ?? 0} />
                  <Stat label="完成" value={status?.succeeded ?? 0} />
                  <Stat label="待批" value={(status?.pending ?? 0) + (status?.processing ?? 0)} />
                  <Stat label="失敗" value={status?.failed ?? 0} tone="error" />
                </div>
                {(isLoading || !allDone) && (
                  <div className="summary-status-detail">
                    {isLoading ? '正在等待模型回傳，完成後會立刻更新 FSRS 下次複習時間。' : '批改完成前不會更新 FSRS 下次複習時間。'}
                  </div>
                )}
                {error && (
                  <div role="alert" className="summary-error">
                    {error}
                  </div>
                )}
                {isStuck && (
                  <button className="btn btn-primary btn-full summary-retry" onClick={() => runAdjudication(true)}>
                    重新批改
                  </button>
                )}
              </section>

              {allDone && (
                <section className="section-block summary-score-panel">
                  <div className={`summary-score ${accuracyTone(stats.accuracy)}`}>
                    {stats.accuracy}%
                  </div>
                  <div className="summary-score-breakdown">
                    Good {stats.good} · Hard {stats.hard} · Again {stats.again}
                  </div>
                </section>
              )}

              <details className="section-block section-block-muted summary-prompt">
                <summary className="summary-prompt-toggle">
                  目前 LLM 批改 prompt 摘要
                </summary>
                <ul className="summary-prompt-list">
                  {adjudicationPromptSummary.map((line) => <li key={line}>{line}</li>)}
                </ul>
	              </details>

	              <div className="stack stack-md">
	                {(status?.results ?? []).map((result, index) => (
		                  <div key={result.id} className="card content-auto summary-result-card">
		                    <div className="summary-result-header">
		                      <span className="tabular-nums summary-result-index">第 {index + 1} 題</span>
		                      <span className={`summary-result-rating ${ratingTone(result.rating)}`}>
	                        {result.rating ?? result.status}
	                      </span>
                    </div>
                    <div className="long-text summary-result-term">
                      {result.english ?? result.card_id}
                    </div>
                    {result.part_of_speech && (
                      <div className="summary-result-pos">
                        {result.part_of_speech}
                      </div>
                    )}
                    <div className="summary-result-answer">
                      你的答案：{result.typed_answer || '不知道'}
                    </div>
                    <div className="summary-result-answer">
                      標準答案：{result.expected_answer}
                    </div>
                    {result.reason && (
                      <div className="summary-result-reason">
                        {result.reason}
                      </div>
                    )}
                    {result.next_due && (
                      <div className="summary-result-due">
                        下次：{formatTaipeiDateTime(result.next_due)}
                      </div>
                    )}
                    {result.error_message && (
                      <div className="summary-result-error">
                        {result.error_message}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="stack stack-md summary-actions">
                <button
                  id="continue-next-round"
                  className="btn btn-primary btn-full btn-lg"
                  onClick={() => navigate('/study')}
                  disabled={!allDone || isLoading}
                >
                  {allDone ? '繼續下一輪' : '等待批改完成'}
                </button>
                <button id="view-mistakes" className="btn btn-secondary btn-full" onClick={() => navigate('/mistakes')}>
                  查看錯題
                </button>
                <button id="stop-today" className="btn btn-ghost btn-full summary-stop" onClick={() => navigate('/')}>
                  今天先到這裡
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'error' }) {
  return (
    <div>
      <div className={`tabular-nums summary-stat-value ${tone === 'error' ? 'text-error-static' : ''}`}>{value}</div>
      <div className="summary-stat-label">{label}</div>
    </div>
  );
}

function ratingTone(rating?: string | null) {
  if (rating === 'Good') return 'summary-rating-good';
  if (rating === 'Hard') return 'summary-rating-hard';
  if (rating === 'Again') return 'summary-rating-again';
  return 'summary-rating-pending';
}

function accuracyTone(accuracy: number) {
  if (accuracy >= 80) return 'summary-score-good';
  if (accuracy >= 60) return 'summary-score-hard';
  return 'summary-score-neutral';
}
