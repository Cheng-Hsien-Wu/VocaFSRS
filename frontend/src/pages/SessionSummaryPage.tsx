import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { api, type AdjudicationStatusDto } from '../services/api';
import { getStoredStudySummarySessionId } from '../services/study-summary-storage';
import { formatTaipeiDateTime } from '../utils/datetime';

const ADJUDICATION_POLL_INTERVAL_MS = 1500;
const ADJUDICATION_RECLAIM_PROBE_INTERVAL_MS = 60 * 1000;

function adjudicationIsActive(status: AdjudicationStatusDto) {
  return status.pending + status.processing > 0;
}

export default function SessionSummaryPage() {
  const navigate = useNavigate();
  const [sessionId] = useState(getStoredStudySummarySessionId);
  const [status, setStatus] = useState<AdjudicationStatusDto | null>(null);
  const [isLoading, setIsLoading] = useState(Boolean(sessionId));
  const [error, setError] = useState<string | null>(null);
  const [adjudicationStartedAt, setAdjudicationStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const statusRef = useRef<AdjudicationStatusDto | null>(null);
  const requestSequenceRef = useRef(0);
  const appliedSequenceRef = useRef(0);
  const adjudicationRequestRef = useRef<{ controller: AbortController } | null>(null);
  const pollNowRef = useRef<(() => void) | null>(null);
  const processingProbeNeededRef = useRef(true);
  const lastProcessingProbeAtRef = useRef<number | null>(null);

  const applyStatus = useCallback((next: AdjudicationStatusDto, sequence: number) => {
    const previous = statusRef.current;
    const terminalPrevious = previous && previous.total > 0 && previous.succeeded === previous.total;
    const terminalNext = next.total > 0 && next.succeeded === next.total;
    if (
      sequence < appliedSequenceRef.current
      || (terminalPrevious && !terminalNext)
      || (previous && next.succeeded < previous.succeeded)
    ) {
      return false;
    }

    appliedSequenceRef.current = sequence;
    statusRef.current = next;
    setStatus(next);
    setError(null);

    const active = adjudicationIsActive(next);
    setIsLoading(active);
    if (active) {
      setAdjudicationStartedAt(current => current ?? Date.now());
    } else {
      setAdjudicationStartedAt(null);
    }

    if (next.processing === 0) {
      processingProbeNeededRef.current = true;
      lastProcessingProbeAtRef.current = null;
    }
    return true;
  }, []);

  const runAdjudication = useCallback(async (retry = false) => {
    if (!sessionId) return;
    const existing = adjudicationRequestRef.current;
    if (existing) return;
    processingProbeNeededRef.current = false;
    lastProcessingProbeAtRef.current = Date.now();

    const controller = new AbortController();
    const request = { controller };
    adjudicationRequestRef.current = request;
    const sequence = ++requestSequenceRef.current;
    setIsLoading(true);
    setAdjudicationStartedAt(current => current ?? Date.now());
    setError(null);
    try {
      const result = retry
        ? await api.retryStudyAdjudication(sessionId, controller.signal)
        : await api.adjudicateStudySession(sessionId, controller.signal);
      applyStatus(result, sequence);
      if (retry && adjudicationIsActive(result)) pollNowRef.current?.();
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        setError(err instanceof Error ? err.message : String(err));
      }
      const currentStatus = statusRef.current;
      if (retry && (!currentStatus || !adjudicationIsActive(currentStatus))) {
        setIsLoading(false);
        setAdjudicationStartedAt(null);
      }
      if (retry) pollNowRef.current?.();
    } finally {
      if (adjudicationRequestRef.current === request) {
        adjudicationRequestRef.current = null;
      }
    }
  }, [applyStatus, sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let timerId: number | null = null;
    let statusRequestController: AbortController | null = null;
    let pollInFlight = false;
    let pollAgainRequested = false;

    const schedulePoll = () => {
      if (cancelled) return;
      if (timerId !== null) window.clearTimeout(timerId);
      timerId = window.setTimeout(poll, ADJUDICATION_POLL_INTERVAL_MS);
    };

    const pollNow = () => {
      if (cancelled) return;
      if (pollInFlight) {
        pollAgainRequested = true;
        return;
      }
      if (timerId !== null) window.clearTimeout(timerId);
      timerId = null;
      void poll();
    };
    pollNowRef.current = pollNow;

    const poll = async () => {
      if (cancelled || pollInFlight) return;
      pollInFlight = true;
      statusRequestController = new AbortController();
      const sequence = ++requestSequenceRef.current;
      let shouldContinuePolling = true;
      try {
        const current = await api.getStudyAdjudicationStatus(sessionId, statusRequestController.signal);
        if (cancelled) return;
        const applied = applyStatus(current, sequence);
        if (!applied) {
          shouldContinuePolling = statusRef.current === null || adjudicationIsActive(statusRef.current);
          return;
        }
        shouldContinuePolling = adjudicationIsActive(current);

        if (current.pending > 0) {
          void runAdjudication();
        } else if (
          current.processing > 0
          && (
            processingProbeNeededRef.current
            || lastProcessingProbeAtRef.current === null
            || Date.now() - lastProcessingProbeAtRef.current >= ADJUDICATION_RECLAIM_PROBE_INTERVAL_MS
          )
        ) {
          processingProbeNeededRef.current = false;
          lastProcessingProbeAtRef.current = Date.now();
          void runAdjudication();
        }
      } catch (err) {
        if (!cancelled && !(err instanceof DOMException && err.name === 'AbortError')) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        pollInFlight = false;
        statusRequestController = null;
        if (pollAgainRequested) {
          pollAgainRequested = false;
          timerId = window.setTimeout(poll, 0);
        } else if (shouldContinuePolling || adjudicationRequestRef.current !== null) {
          schedulePoll();
        }
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState !== 'visible') return;
      processingProbeNeededRef.current = true;
      pollNow();
    };

    document.addEventListener('visibilitychange', handleVisibility);
    void poll();
    return () => {
      cancelled = true;
      if (timerId !== null) window.clearTimeout(timerId);
      if (pollNowRef.current === pollNow) pollNowRef.current = null;
      statusRequestController?.abort();
      adjudicationRequestRef.current?.controller.abort();
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [applyStatus, runAdjudication, sessionId]);

  const retryAdjudication = useCallback(() => {
    void runAdjudication(true);
  }, [runAdjudication]);

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
    '「不知道」直接判為 Again；其他答案分批送出，並以答案 ID 彙整。',
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
                  <button className="btn btn-primary btn-full summary-retry" onClick={retryAdjudication}>
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
