import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { db, type PlacementSession } from '../db/dexie';
import { ACTIVE_PLACEMENT_STATUSES } from '../domain';
import { api } from '../services/api';
import { forceSyncPlacementEvents, syncPlacementEvents } from '../services/placement-sync';
import { placementCheckpointSize } from '../services/placement-session-store';
import { MaterialSymbol } from '../components/MaterialSymbol';
import {
  cacheAuditQuestions,
  loadCachedAudit,
  mapCheckpointSession,
  queueAuditAnswer,
  type AuditQuestionsResponse,
  type UIAuditQuestion,
} from '../services/placement-audit-store';

interface SegmentStats {
  known: number;
  fuzzy: number;
  unknown: number;
  problematic: number;
  total: number;
}

type AuditPhase =
  | 'auditing'
  | 'second_sample'
  | 'results'
  | 'done'
  | 'loading'
  | 'sync_pending'
  | 'error';

function auditSummary(
  questions: UIAuditQuestion[],
  authoritativeErrorRate?: number,
) {
  const incorrect = questions.filter(question => question.isCorrect === false).length;
  const totalValid = questions.filter(
    question =>
      question.selectedOptionId !== 'problematic'
      && question.selectedOptionId !== null,
  ).length;
  return {
    incorrect,
    errorRate: authoritativeErrorRate
      ?? (totalValid > 0 ? incorrect / totalValid : 0),
  };
}

export default function PlacementCheckpointPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isComplete = searchParams.get('complete') === '1';

  const [session, setSession] = useState<PlacementSession | null>(null);
  const [checkpoint, setCheckpoint] = useState<number>(0);
  const [stats, setStats] = useState<SegmentStats>({ known: 0, fuzzy: 0, unknown: 0, problematic: 0, total: 0 });
  const [phase, setPhase] = useState<AuditPhase>('loading');
  const [questions, setQuestions] = useState<UIAuditQuestion[]>([]);
  const [currentQIndex, setCurrentQIndex] = useState<number>(0);
  const [errorRate, setErrorRate] = useState<number>(0);
  const [correctedCount, setCorrectedCount] = useState<number>(0);
  const [activationCounts, setActivationCounts] = useState<{ unknown: number; fuzzy: number; knownVerify: number } | null>(null);
  const [canStartStudy, setCanStartStudy] = useState(false);
  const [remainingPlacementCount, setRemainingPlacementCount] = useState<number | null>(null);
  const [auditError, setAuditError] = useState('');

  // Load active session and segment details
  const loadSession = useCallback(async () => {
    try {
      // 1. Fetch active session from Dexie or server
      let activeSession = await db.placement_sessions
        .where('status')
        .anyOf([...ACTIVE_PLACEMENT_STATUSES])
        .first();

      const serverSession = await api.getActivePlacementSession();
      let serverPosition: number | null = null;

      if (serverSession) {
        serverPosition = serverSession.current_position;
        activeSession = mapCheckpointSession(serverSession);
        await db.placement_sessions.put(activeSession);
        await db.placement_sessions
          .where('status')
          .anyOf([...ACTIVE_PLACEMENT_STATUSES])
          .and(localSession => localSession.id !== serverSession.id)
          .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
      } else if (!isComplete) {
        await db.placement_sessions
          .where('status')
          .anyOf([...ACTIVE_PLACEMENT_STATUSES])
          .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
        navigate('/');
        return;
      }

      if (!activeSession) {
        // If it's the final complete redirect, we might look for 'completed' session
        if (isComplete) {
          const completedSession = await db.placement_sessions
            .where('status')
            .equals('completed')
            .first();
          if (completedSession) {
            activeSession = completedSession;
          }
        }
      }

      if (!activeSession) {
        navigate('/');
        return;
      }

      setSession(activeSession);

      // 2. Compute Segment Stats from Dexie placement_items
      const items = await db.placement_items.where('sessionId').equals(activeSession.id).toArray();
      const activeItems = items.filter(item => !item.undone);
      const totalAnswered = serverPosition ?? activeItems.length;

      // 3. Resolve Checkpoint number
      const checkpointSize = placementCheckpointSize(activeSession);
      const cp = Math.floor(totalAnswered / checkpointSize) * checkpointSize;
      const finalCp = cp > 0 ? cp : checkpointSize;
      setCheckpoint(finalCp);

      const startPos = finalCp - checkpointSize;
      const endPos = finalCp - 1;
      const segmentItems = items.filter(
        item => item.position >= startPos && item.position <= endPos && !item.undone
      );

      setStats({
        known: segmentItems.filter(item => item.result === 'known').length,
        fuzzy: segmentItems.filter(item => item.result === 'fuzzy').length,
        unknown: segmentItems.filter(item => item.result === 'unknown').length,
        problematic: segmentItems.filter(item => item.result === 'problematic').length,
        total: segmentItems.length,
      });

      // 4. If session is complete, calculate total activation queues
      if (isComplete) {
        const activeItems = items.filter(item => !item.undone);
        setActivationCounts({
          unknown: activeItems.filter(item => item.result === 'unknown').length,
          fuzzy: activeItems.filter(
            item => item.result === 'fuzzy' || (item.result === 'known' && item.auditReclassified)
          ).length,
          knownVerify: activeItems.filter(
            item => item.result === 'known' && !item.auditReclassified
          ).length,
        });
        setPhase('done');
        return;
      }

      // 5. Load/Fetch Audit Questions
      await syncAndLoadAudit(activeSession.id, finalCp);
    } catch (err) {
      console.error('Failed to load session:', err);
      setAuditError('無法讀取盤點狀態。請確認後端服務正在執行，或回主畫面重新整理。');
      setPhase('error');
    }
  }, [navigate, isComplete]);

  async function syncAndLoadAudit(sessionId: string, cp: number) {
    try {
      setAuditError('');
      setPhase('loading');
      // Fetch audit questions from server
      const auditData = await api.getAuditQuestions(sessionId, cp) as AuditQuestionsResponse;

      if (auditData.status === 'skipped') {
        setPhase('done');
        return;
      }

      const uiQs = await cacheAuditQuestions(sessionId, cp, auditData);

      setQuestions(uiQs);

      // Determine phase and current question index
      if (auditData.status === 'completed') {
        const { incorrect, errorRate: completedErrorRate } = auditSummary(
          uiQs,
          auditData.error_rate,
        );
        setErrorRate(completedErrorRate);
        setCorrectedCount(incorrect);
        setPhase('results');
      } else {
        const unansweredIdx = uiQs.findIndex(q => !q.answered);
        if (unansweredIdx === -1) {
          // If all answered but status is active, wait for server sync
          setPhase('loading');
        } else {
          const firstUnanswered = uiQs[unansweredIdx];
          setPhase(firstUnanswered.sampleBatch === 2 ? 'second_sample' : 'auditing');
          setCurrentQIndex(unansweredIdx);
        }
      }
    } catch (err) {
      console.error('Failed to sync/load audit questions, fallback to Dexie:', err);
      // Fallback to offline Dexie cache
      const cached = await loadCachedAudit(sessionId, cp);
      if (cached) {
        const { audit: localAudit, questions: uiQs } = cached;

        setQuestions(uiQs);

        if (localAudit.status === 'completed') {
          const { incorrect, errorRate: completedErrorRate } = auditSummary(
            uiQs,
            localAudit.errorRate,
          );
          setErrorRate(completedErrorRate);
          setCorrectedCount(incorrect);
          setPhase('results');
        } else {
          const unansweredIdx = uiQs.findIndex(q => !q.answered);
          if (unansweredIdx === -1) {
            setAuditError('抽查答案已保存在此裝置。恢復連線後重新同步，伺服器才會決定是否需要二次抽查。');
            setPhase('sync_pending');
          } else {
            const firstUnanswered = uiQs[unansweredIdx];
            setPhase(firstUnanswered.sampleBatch === 2 ? 'second_sample' : 'auditing');
            setCurrentQIndex(unansweredIdx);
          }
        }
      } else {
        setAuditError('無法載入抽查題目。請回主畫面重新整理後再繼續盤點。');
        setPhase('error');
      }
    }
  }

  useEffect(() => {
    const id = window.setTimeout(() => {
      loadSession();
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadSession]);

  useEffect(() => {
    if (!isComplete) return;
    api.getStudyPlan()
      .then(plan => {
        setCanStartStudy(Boolean(plan.placement_status?.complete));
        setRemainingPlacementCount(plan.placement_status?.remaining_count ?? null);
      })
      .catch(() => {
        setCanStartStudy(false);
        setRemainingPlacementCount(null);
      });
  }, [isComplete]);

  function startNextPlacementBatch() {
    sessionStorage.setItem(
      'placement_count',
      String(session ? placementCheckpointSize(session) : checkpoint),
    );
    sessionStorage.removeItem('placement_resume');
    navigate('/placement');
  }

  const answerAuditQ = async (selectedId: string) => {
    if (!session || questions.length === 0) return;
    const q = questions[currentQIndex];
    if (!q) return;

    // 1. Update UI state immediately
    const updatedQs = [...questions];
    updatedQs[currentQIndex] = {
      ...q,
      answered: true,
      selectedOptionId: selectedId,
      isCorrect: selectedId === q.cardId,
    };
    setQuestions(updatedQs);

    await queueAuditAnswer({
      sessionId: session.id,
      checkpoint,
      question: q,
      selectedOptionId: selectedId,
    });

    // 3. Move to next question or check batch completeness
    const nextIdx = currentQIndex + 1;
    const currentBatch = q.sampleBatch;
    const batchQs = updatedQs.filter(item => item.sampleBatch === currentBatch);
    const batchAnswered = batchQs.every(item => item.answered);

    if (batchAnswered) {
      // Re-fetch questions/status from server to get second sample batch or completed status
      setPhase('loading');
      await forceSyncPlacementEvents();
      await syncAndLoadAudit(session.id, checkpoint);
    } else {
      syncPlacementEvents().catch(err => console.error('Immediate sync failed:', err));
      setCurrentQIndex(nextIdx);
    }
  };

  const retryAuditSync = async () => {
    if (!session) return;
    setPhase('loading');
    try {
      await forceSyncPlacementEvents();
      await syncAndLoadAudit(session.id, checkpoint);
    } catch (error) {
      console.error('Failed to retry audit sync:', error);
      setAuditError('仍然無法連線。答案已保存在此裝置，請稍後再試。');
      setPhase('sync_pending');
    }
  };

  const continueNext = async () => {
    if (!session) return;
    if (isComplete) {
      navigate('/');
      return;
    }
    navigate('/placement');
  };

  const abandonSession = async () => {
    if (!session) return;
    const confirm = window.confirm('確定要放棄本次盤點進度嗎？此動作不可撤銷。');
    if (!confirm) return;

    try {
      // Abandon on server
      await api.abandonPlacementSession(session.id);
      // Update local Dexie status
      await db.transaction('rw', db.placement_sessions, db.pending_events, async () => {
        await db.placement_sessions.update(session.id, {
          status: 'abandoned',
          updatedAt: new Date().toISOString(),
        });
        // Delete pending events for abandoned session — they will never be synced
        const keys = await db.pending_events
          .where('sessionId')
          .equals(session.id)
          .filter(e => e.type === 'placement' || e.type === 'audit')
          .primaryKeys();
        await db.pending_events.bulkDelete(keys);
      });
      navigate('/');
    } catch (err) {
      console.error('Failed to abandon session:', err);
      alert('放棄失敗，請確認網路連線。');
    }
  };

  // ─── Render ─────────────────────────────────────────────

  if (phase === 'loading' || !session) {
    return (
      <div className="full-screen checkpoint-loading">
        <div className="checkpoint-centered">
          <div className="spinner checkpoint-spinner" />
          <div role="status" aria-live="polite">載入中…</div>
        </div>
      </div>
    );
  }

  // Auditing wizard UI
  if (phase === 'auditing' || phase === 'second_sample') {
    const q = questions[currentQIndex];
    const isSec = phase === 'second_sample';
    return (
      <div className="full-screen">
        <div className="checkpoint-audit-header">
          <div className="checkpoint-audit-progress">
            {isSec ? '二次抽查' : '✓ 已知抽查'} — {currentQIndex + 1} / {questions.length}
          </div>
          <div className="checkpoint-audit-caption">
            {isSec ? '錯誤率較高，多抽一組確認' : '從你標記「知道」的單字中隨機抽查'}
          </div>
        </div>

        <div className="page checkpoint-audit-page">
          <div className="checkpoint-term-block">
            <div className="checkpoint-term-prompt">
              這個單字的中文意思是？
            </div>
            <div id="audit-term" className="display-term long-text checkpoint-term">
              {q?.english}
            </div>
            {q?.partOfSpeech && (
              <div className="checkpoint-term-pos">
                {q.partOfSpeech}
              </div>
            )}
          </div>

          <div className="checkpoint-options">
            {q?.options.map(opt => (
              <button
                key={opt.card_id}
                id={`audit-option-${opt.card_id}`}
                className="study-option long-text checkpoint-option"
                onClick={() => answerAuditQ(opt.card_id)}
              >
                {opt.chinese}
              </button>
            ))}
            <div className="checkpoint-audit-actions">
              <button
                id="audit-btn-unknown"
                className="btn btn-secondary btn-full checkpoint-audit-unknown"
                onClick={() => answerAuditQ('unknown')}
              >
                不知道 / 答錯
              </button>
              <button
                id="audit-btn-problematic"
                className="btn btn-ghost checkpoint-audit-problem"
                onClick={() => answerAuditQ('problematic')}
              >
                題目有問題
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className="full-screen">
        <div className="page">
          <div className="page-content page-content-spacious">
            <section className="card checkpoint-error-card">
              <h1 className="checkpoint-error-title">
                抽查載入失敗
              </h1>
              <p className="checkpoint-error-copy">
                {auditError || '目前無法載入抽查狀態。'}
              </p>
              <button
                className="btn btn-primary btn-full"
                onClick={() => navigate('/')}
              >
                回主畫面
              </button>
            </section>
          </div>
        </div>
      </div>
    );
  }

  if (phase === 'sync_pending') {
    return (
      <div className="full-screen">
        <div className="page">
          <div className="page-content page-content-spacious">
            <section className="card checkpoint-error-card">
              <h1 className="checkpoint-error-title">等待同步抽查結果</h1>
              <p className="checkpoint-error-copy">{auditError}</p>
              <button
                className="btn btn-primary btn-full"
                onClick={retryAuditSync}
              >
                重新同步
              </button>
              <button
                className="btn btn-secondary btn-full"
                onClick={() => navigate('/')}
              >
                稍後再處理
              </button>
            </section>
          </div>
        </div>
      </div>
    );
  }

  // Checkpoint summary UI
  const showAuditResults = phase === 'results' || phase === 'done';

  return (
    <div className="full-screen">
      <header className="nav-header">
        <div className="checkpoint-nav-copy">
          <div className="checkpoint-nav-title">
            {isComplete
              ? '盤點完成！'
              : `第 ${checkpoint / placementCheckpointSize(session)} 個盤點檢查點`}
          </div>
          <div className="checkpoint-nav-progress">
            進度：{isComplete ? session.requestedCount : checkpoint} / {session.requestedCount} 張
          </div>
        </div>
        {!isComplete && (
          <button
            className="btn btn-ghost btn-sm checkpoint-abandon"
            onClick={abandonSession}
          >
            放棄盤點
          </button>
        )}
      </header>

      <div className="page">
        <div className="page-content">

          {/* Stats grid */}
          <div className="metric-grid checkpoint-section">
            {[
              { label: '知道', value: stats.known },
              { label: '模糊', value: stats.fuzzy },
              { label: '不會', value: stats.unknown },
              { label: '有問題', value: stats.problematic },
            ].map(s => (
              <div key={s.label} className="card metric-card">
                <div className="metric-value tabular-nums">
                  {s.value}
                </div>
                <div className="metric-label">
                  {s.label}
                </div>
              </div>
            ))}
          </div>

          {/* Audit section */}
          {!isComplete && (
            <div className="card checkpoint-summary-card">
              <div className="checkpoint-summary-title">
                已知抽查
              </div>

              {showAuditResults && (
                <div>
                  <div className="checkpoint-audit-result">
                    <div className="checkpoint-audit-count">
                      共抽查 {questions.length} 題
                    </div>
                    {correctedCount > 0 ? (
                      <span className="checkpoint-badge checkpoint-badge-warning">
                        {correctedCount} 個改為模糊
                      </span>
                    ) : (
                      <span className="checkpoint-badge checkpoint-badge-success">
                        全部正確
                      </span>
                    )}
                  </div>
                  {errorRate >= 0.2 && (
                    <div className="checkpoint-warning">
                      錯誤率 <span className="tabular-nums">{Math.round(errorRate * 100)}%</span>，已自動調低對應單字熟練度。盤點結束後將優先安排複習。
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Activation summary (on complete) */}
          {isComplete && activationCounts && (
            <div className="card checkpoint-summary-card">
              <div className="checkpoint-summary-title">
                盤點統計與學習計畫
              </div>
              <div className="checkpoint-activation-list">
                <div className="checkpoint-activation-row checkpoint-activation-row-bordered">
                  <span>待學習（不會）</span>
                  <strong>{activationCounts.unknown} 字</strong>
                </div>
                <div className="checkpoint-activation-row checkpoint-activation-row-bordered">
                  <span>待加強（模糊 / 抽查錯誤）</span>
                  <strong className="text-warning-static">{activationCounts.fuzzy} 字</strong>
                </div>
                <div className="checkpoint-activation-row">
                  <span>安全字（知道）</span>
                  <strong className="text-success">{activationCounts.knownVerify} 字</strong>
                </div>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="stack stack-sm">
            {!isComplete && showAuditResults && (
              <button
                id="continue-next-100"
                className="btn btn-primary btn-full btn-lg"
                onClick={continueNext}
              >
                繼續盤點
                <MaterialSymbol name="arrow_forward" className="btn-inline-icon" />
              </button>
            )}
            {isComplete && (
              <button
                id="start-study-from-complete"
                className="btn btn-primary btn-full btn-lg"
                onClick={() => canStartStudy ? navigate('/study') : startNextPlacementBatch()}
              >
                {canStartStudy ? '開始正式複習' : `繼續盤點${remainingPlacementCount ? `（剩 ${remainingPlacementCount} 字）` : ''}`}
              </button>
            )}
            <button
              id="take-a-break"
              className="btn btn-ghost btn-full text-secondary"
              onClick={() => navigate('/')}
            >
              回首頁休息
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}
