import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStudySession } from '../hooks/useStudySession';
import { formatTaipeiDateTime } from '../utils/datetime';
import { MaterialSymbol } from '../components/MaterialSymbol';
import {
  AudioIcon,
  FeedbackHeader,
  TTSButton,
} from '../components/StudyControls';
import {
  formatTime,
  TTS_MODE_LABELS,
  type TtsMode,
  type TtsStatus,
} from '../services/study-tts';
import { useSpeechSynthesis } from '../hooks/useSpeechSynthesis';
import { storeStudySummarySessionId } from '../services/study-summary-storage';

const TIME_LIMIT = 180; // 3 minutes
const SKIPPED_TYPED_ANSWER = '不知道';

const TTS_MODE_CYCLE: TtsMode[] = ['off', 'again_hard', 'every'];

export default function StudyPage() {
  const navigate = useNavigate();
  const { speak } = useSpeechSynthesis();

  const count = parseInt(sessionStorage.getItem('study_count') ?? '25');
  const mode = sessionStorage.getItem('study_mode') === 'timed' ? 'timed' : 'fixed';
  const isResume = sessionStorage.getItem('study_resume') === '1';

  // TTS mode persisted in localStorage
  const [ttsMode, setTtsMode] = useState<TtsMode>(() => {
    const saved = localStorage.getItem('tts_mode');
    return (saved === 'off' || saved === 'again_hard' || saved === 'every') ? saved : 'every';
  });
  const ttsModeRef = useRef<TtsMode>(ttsMode);

  useEffect(() => {
    ttsModeRef.current = ttsMode;
  }, [ttsMode]);

  function cycleTtsMode() {
    setTtsMode(prev => {
      const idx = TTS_MODE_CYCLE.indexOf(prev);
      const next = TTS_MODE_CYCLE[(idx + 1) % TTS_MODE_CYCLE.length];
      localStorage.setItem('tts_mode', next);
      return next;
    });
  }

  const {
    session,
    currentPosition,
    uiState,
    errorState,
    lifecycleError,
    loadedCards,
    retryCreateSession,
    submitTypedAnswer,
    finishSession,
    abandonSession,
    clearLifecycleError,
  } = useStudySession(count, mode, isResume);

  const [timerSeconds, setTimerSeconds] = useState(TIME_LIMIT);
  const [timerStarted, setTimerStarted] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [typedAnswer, setTypedAnswer] = useState('');
  const [revealedAnswer, setRevealedAnswer] = useState<{ typed: string; expected: string } | null>(null);
  const [isSubmittingTyped, setIsSubmittingTyped] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [abandonArmed, setAbandonArmed] = useState(false);
  const [isAnswerFocused, setIsAnswerFocused] = useState(false);
  const [hasEnteredCompactMode, setHasEnteredCompactMode] = useState(false);
  const [ttsUnlocked, setTtsUnlocked] = useState(() => localStorage.getItem('tts_unlocked') === '1');
  const [ttsStatus, setTtsStatus] = useState<TtsStatus>(() => {
    if (typeof window !== 'undefined' && !window.speechSynthesis) return 'unsupported';
    return localStorage.getItem('tts_unlocked') === '1' ? 'ready' : 'locked';
  });
  const typedAnswerRef = useRef<HTMLInputElement | null>(null);
  const skipRevealGuardUntilRef = useRef(0);
  const summaryFinishStartedRef = useRef(false);
  const typedSubmitInFlightRef = useRef(false);
  const abandonTimeoutRef = useRef<number | null>(null);

  const playCurrentWord = useCallback((word: string) => {
    if (!word) return;
    setTtsUnlocked(true);
    localStorage.setItem('tts_unlocked', '1');
    setTtsStatus('ready');
    speak(word, setTtsStatus);
  }, [speak]);

  // Auto-play TTS when question changes in 'every' mode
  const prevCardIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (ttsModeRef.current !== 'every') return;
    if (!ttsUnlocked) {
      const id = window.setTimeout(() => {
        setTtsStatus(prev => prev === 'unsupported' ? 'unsupported' : 'locked');
      }, 0);
      return () => window.clearTimeout(id);
    }
    if (uiState.ui !== 'question') return;
    const manifestItem = session?.manifest[currentPosition];
    if (!manifestItem) return;
    const cardId = manifestItem.cardId;
    if (cardId === prevCardIdRef.current) return;
    prevCardIdRef.current = cardId;
    const card = loadedCards[cardId];
    if (card?.english) speak(card.english, setTtsStatus);
  }, [currentPosition, uiState.ui, session, loadedCards, ttsUnlocked, speak]);

  useEffect(() => {
    if (uiState.ui !== 'question' || revealedAnswer) return;
    // Intentional mobile exception: the study flow is optimized for uninterrupted
    // typed recall, so each new question returns focus without scrolling the page.
    const id = window.setTimeout(() => {
      typedAnswerRef.current?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(id);
  }, [currentPosition, uiState.ui, revealedAnswer]);

  useEffect(() => {
    return () => {
      if (abandonTimeoutRef.current !== null) {
        window.clearTimeout(abandonTimeoutRef.current);
      }
    };
  }, []);

  // Start timer in timed mode when first question is interactive
  useEffect(() => {
    if (mode === 'timed' && uiState.ui === 'question' && session && !timerStarted) {
      const id = window.setTimeout(() => setTimerStarted(true), 0);
      return () => window.clearTimeout(id);
    }
  }, [mode, uiState.ui, session, timerStarted]);

  // Countdown logic with background tab pause support
  useEffect(() => {
    if (!timerStarted || isPaused || mode !== 'timed' || uiState.ui === 'complete' || uiState.ui === 'summary') return;

    const tick = () => {
      if (!document.hidden) {
        setTimerSeconds(prev => {
          if (prev <= 1) {
            return 0;
          }
          return prev - 1;
        });
      }
    };

    const intervalId = setInterval(tick, 1000);
    return () => clearInterval(intervalId);
  }, [timerStarted, isPaused, mode, uiState.ui]);

  // Handle auto-finish or transition when summary phase reached in hook
  useEffect(() => {
    if (uiState.ui === 'summary' && !summaryFinishStartedRef.current) {
      summaryFinishStartedRef.current = true;
      const handleSummaryFinish = async () => {
        try {
          await finishSession();
        } catch (err) {
          console.error('Failed to finish study summary:', err);
        }
      };
      handleSummaryFinish();
    }
  }, [uiState.ui, finishSession]);

  // Navigate to summary page; LLM adjudication happens there.
  useEffect(() => {
    if (uiState.ui === 'complete' && session) {
      storeStudySummarySessionId(session.id);
      navigate('/study/summary');
    }
  }, [uiState.ui, session, navigate]);

  const handleTypedReveal = useCallback(() => {
    const manifestItem = session?.manifest[currentPosition];
    if (!manifestItem) return;
    const currentCard = loadedCards[manifestItem.cardId];
    if (!currentCard) return;
    setRevealedAnswer({
      typed: typedAnswer.trim(),
      expected: currentCard.chineseMeaning,
    });
  }, [session, currentPosition, loadedCards, typedAnswer]);

  const handleTypedNext = useCallback(async () => {
    if (typedSubmitInFlightRef.current || isSubmittingTyped) return;
    if (Date.now() < skipRevealGuardUntilRef.current) return;
    const isTimedMode = mode === 'timed';
    const hasExpired = isTimedMode && timerSeconds <= 0;
    typedSubmitInFlightRef.current = true;
    setIsSubmittingTyped(true);
    setSubmitError(null);
    try {
      await submitTypedAnswer(revealedAnswer?.typed ?? typedAnswer.trim());
      setTypedAnswer('');
      setRevealedAnswer(null);
      if (hasExpired) {
        await finishSession();
      }
    } catch (error) {
      console.error('Failed to submit typed answer:', error);
      setSubmitError('答案尚未儲存，請確認連線後再按一次「下一題」。');
    } finally {
      setIsSubmittingTyped(false);
      typedSubmitInFlightRef.current = false;
    }
  }, [isSubmittingTyped, mode, timerSeconds, submitTypedAnswer, finishSession, revealedAnswer, typedAnswer]);

  const handleSkipTyped = useCallback(async () => {
    const manifestItem = session?.manifest[currentPosition];
    const card = manifestItem ? loadedCards[manifestItem.cardId] : null;
    skipRevealGuardUntilRef.current = Date.now() + 350;
    setTypedAnswer(SKIPPED_TYPED_ANSWER);
    setRevealedAnswer({ typed: SKIPPED_TYPED_ANSWER, expected: card?.chineseMeaning ?? '' });
  }, [session, currentPosition, loadedCards]);

  const handleTypedFormSubmit = useCallback((event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (revealedAnswer) {
      if (!isSubmittingTyped) {
        handleTypedNext();
      }
      return;
    }
    if (typedAnswer.trim()) {
      handleTypedReveal();
    }
  }, [revealedAnswer, isSubmittingTyped, handleTypedNext, typedAnswer, handleTypedReveal]);

  // Exit study session
  const handleExit = useCallback(async () => {
    try {
      await abandonSession();
      navigate('/');
    } catch (error) {
      console.error('Failed to abandon study session:', error);
    }
  }, [abandonSession, navigate]);

  const handleAbandonClick = useCallback(() => {
    if (abandonArmed) {
      handleExit();
      return;
    }
    setAbandonArmed(true);
    if (abandonTimeoutRef.current !== null) {
      window.clearTimeout(abandonTimeoutRef.current);
    }
    abandonTimeoutRef.current = window.setTimeout(() => {
      setAbandonArmed(false);
      abandonTimeoutRef.current = null;
    }, 3000);
  }, [abandonArmed, handleExit]);

  // Render Loading Screen
  if (uiState.ui === 'loading') {
    return (
      <div className="full-screen study-status-screen">
        <div role="status" aria-live="polite" className="study-loading-copy">載入中…</div>
      </div>
    );
  }

  if (lifecycleError) {
    const isFinishFailure = lifecycleError === 'finish';
    const retryTransition = async () => {
      try {
        if (isFinishFailure) {
          await finishSession();
        } else {
          await abandonSession();
          navigate('/');
        }
      } catch (error) {
        console.error('Failed to retry study session transition:', error);
      }
    };
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <h1 className="study-status-title">
            {isFinishFailure ? '尚未完成本輪' : '尚未放棄本輪'}
          </h1>
          <p className="study-status-copy">
            無法連線到伺服器，本機狀態尚未變更。請確認連線後重試。
          </p>
          <div className="study-status-actions">
            <button className="btn btn-primary btn-full" onClick={retryTransition}>
              {isFinishFailure ? '重試完成本輪' : '重試放棄本輪'}
            </button>
            <button
              className="btn btn-secondary btn-full"
              onClick={() => {
                clearLifecycleError();
                if (isFinishFailure) navigate('/');
              }}
            >
              {isFinishFailure ? '回首頁' : '繼續本輪'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render Paused Screen
  if (isPaused) {
    return (
      <div className="full-screen study-status-screen">
        <div className="study-pause-panel">
          <h1 className="study-status-title">
            已暫停
          </h1>
          <p className="study-status-copy study-pause-copy">
            第 {session ? session.cardsAnswered : 0} / {session ? session.requestedSize : 0} 題
          </p>
          <div className="study-status-actions">
            <button className="btn btn-primary btn-lg btn-full" onClick={() => setIsPaused(false)}>
              繼續學習
            </button>
            <button className="btn btn-secondary btn-lg btn-full" onClick={() => navigate('/')}>
              回首頁
            </button>
            <button
              id="abandon-session-btn"
              className="btn btn-ghost btn-full text-error-static"
              onClick={handleAbandonClick}
              aria-live="polite"
            >
              {abandonArmed ? '再次點擊放棄' : '放棄本輪'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render Clamping / Insufficient Cards Error
  if (errorState?.errorType === 'deck_scope_required') {
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <div className="study-status-title">
            請先整理複習來源
          </div>
          <p className="study-status-copy">
            目前複習來源不明確，請回首頁重新整理或重新匯入單字。
          </p>
          <button className="btn btn-primary btn-full" onClick={() => navigate('/')}>
            回首頁
          </button>
        </div>
      </div>
    );
  }

  if (errorState?.errorType === 'placement_required') {
    const remaining = errorState.placementStatus?.remaining_count;
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <div className="study-status-title">
            正式複習需要先完成盤點
          </div>
          <p className="study-status-copy">
            {typeof remaining === 'number' && remaining > 0
              ? `還有 ${remaining} 個單字尚未盤點。全部盤點完後，系統才會開放 FSRS 複習。`
              : '請先回首頁繼續盤點。'}
          </p>
          <button className="btn btn-primary btn-full" onClick={() => navigate('/')}>
            回首頁盤點
          </button>
        </div>
      </div>
    );
  }

  if (errorState?.errorType === 'no_due_cards' || errorState?.errorType === 'pending_adjudication') {
    const nextDue = errorState.nextDue ? new Date(errorState.nextDue) : null;
    const nextDueLabel = nextDue && !Number.isNaN(nextDue.getTime())
      ? formatTaipeiDateTime(errorState.nextDue!)
      : '尚未排定';
    const hasScheduledReview = Boolean(nextDue);
    const hasPendingAdjudication = (errorState.pendingAdjudicationCount ?? 0) > 0;
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <div className="study-status-title">
            {hasPendingAdjudication ? '有批改尚未完成' : hasScheduledReview ? '目前沒有到期複習' : '還沒有可複習的單字'}
          </div>
          <p className="study-status-copy">
            {hasPendingAdjudication
              ? '請先回到本輪批改結果，完成 LLM 批改後才會更新 FSRS 複習時間。'
              : hasScheduledReview
              ? `下一次到期：${nextDueLabel}`
              : '正式複習需要先有盤點結果。請先完成一輪盤點，系統才會把不熟或需確認的字加入複習。'}
          </p>
          <div className="study-status-actions">
            <button className="btn btn-primary btn-full" onClick={() => navigate('/')}>
              {hasScheduledReview || hasPendingAdjudication ? '回首頁' : '回首頁盤點'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (errorState?.errorType === 'insufficient_cards') {
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <h2 className="study-status-title">
            剩餘可複習單字不足
          </h2>
          <p className="study-status-copy">
            你請求了 {count} 題，目前只有 {errorState.availableCount} 題可用。
          </p>
          <div className="study-status-actions study-status-actions-row">
            <button
              id="confirm-available-btn"
              className="btn btn-primary study-status-action"
              onClick={() => retryCreateSession(errorState.availableCount)}
            >
              使用 {errorState.availableCount} 個單字開始
            </button>
            <button
              className="btn btn-secondary study-status-action"
              onClick={() => navigate('/')}
            >
              取消
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (uiState.ui === 'empty') {
    return (
      <div className="full-screen study-status-screen study-status-screen-padded">
        <div className="empty-state empty-state-panel study-status-panel">
          <div className="study-status-title">
            這輪沒有可用題目
          </div>
          <p className="study-status-copy">
            可能是還沒盤點、目前沒有到期字，或上一輪資料已被清空。
          </p>
          <div className="study-status-actions">
            <button className="btn btn-primary btn-full" onClick={() => navigate('/')}>
              回首頁
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Render question UI
  const manifestItem = session?.manifest[currentPosition];
  const currentCard = manifestItem ? loadedCards[manifestItem.cardId] : null;

  if (!manifestItem || !currentCard) {
    return (
      <div className="full-screen study-status-screen">
        <div className="empty-state empty-state-panel">
          <div className="study-status-title">
            沒有可顯示的題目
          </div>
          <p className="study-status-copy">
            如果你還沒盤點，請先回首頁建立複習來源。
          </p>
          <button className="btn btn-primary" onClick={() => navigate('/')}>回首頁</button>
        </div>
      </div>
    );
  }

  const answeredCount = session ? session.cardsAnswered : 0;
  const totalCount = session ? session.requestedSize : 0;
  const progressPct = totalCount > 0 ? (answeredCount / totalCount) * 100 : 0;
  const isTimeExpired = mode === 'timed' && timerSeconds <= 0;
  const canReveal = typedAnswer.trim().length > 0 && !revealedAnswer;
  const isKeyboardMode = isAnswerFocused || Boolean(revealedAnswer) || hasEnteredCompactMode;

  return (
    <div className={`full-screen ${isKeyboardMode ? 'study-keyboard-mode' : ''}`}>
      {/* Header */}
      {isKeyboardMode ? (
        <div className="study-compact-header">
          <button className="nav-back nav-close-button" onClick={() => setIsPaused(true)} aria-label="結束學習，回首頁">
            <MaterialSymbol name="close" />
          </button>
          <span>{answeredCount} / {totalCount}</span>
          <button
            type="button"
            className={`study-compact-icon-button study-tts-${ttsStatus}`}
            onClick={() => playCurrentWord(currentCard.english)}
            aria-label={`播放發音：${currentCard.english}`}
            title={ttsStatus === 'locked' ? '點一下啟用發音' : `播放發音：${currentCard.english}`}
            disabled={ttsStatus === 'unsupported'}
          >
            <AudioIcon muted={ttsMode === 'off' || ttsStatus === 'locked' || ttsStatus === 'unsupported'} active={ttsStatus === 'speaking'} />
          </button>
          <button
            type="button"
            className={`study-compact-sound ${ttsMode === 'off' ? 'study-compact-sound-off' : ''}`}
            onClick={cycleTtsMode}
            aria-label={`發音模式：${TTS_MODE_LABELS[ttsMode]}`}
            title={TTS_MODE_LABELS[ttsMode]}
          >
            {ttsMode === 'off' ? '關' : ttsMode === 'again_hard' ? '錯題' : '每題'}
          </button>
          {mode === 'timed' && <span id="timer-display">{formatTime(timerSeconds)}</span>}
        </div>
      ) : (
        <FeedbackHeader
          answeredCount={answeredCount}
          totalCount={totalCount}
          onExit={() => setIsPaused(true)}
          timerSeconds={mode === 'timed' ? timerSeconds : undefined}
          ttsMode={ttsMode}
          onCycleTtsMode={cycleTtsMode}
        />
      )}

      {/* Progress track */}
      <div className="progress-bar-track study-progress-track">
        <div className="progress-bar-fill" style={{ transform: `scaleX(${progressPct / 100})` }} />
      </div>

      {/* Time expired notice */}
      {isTimeExpired && (
        <div className="study-time-expired">
          時間到，完成這題後結束
        </div>
      )}

      {/* Main Question Display */}
      <div className="page study-page">
        <div className="study-term-panel">
          <div
            id="study-term"
            className="display-term long-text study-term"
          >
            {currentCard.english}
          </div>
          {currentCard.partOfSpeech && !isKeyboardMode && (
            <div className="study-part-of-speech">
              {currentCard.partOfSpeech}
            </div>
          )}
          {!isKeyboardMode && <TTSButton word={currentCard.english} status={ttsStatus} onPlay={playCurrentWord} />}
        </div>

        <form
          className={`section-block study-answer-form ${revealedAnswer ? 'section-block-muted' : ''}`}
          onSubmit={handleTypedFormSubmit}
        >
          <label htmlFor="typed-answer" className="study-answer-label">
            請輸入中文意思
          </label>
          <input
            id="typed-answer"
            name="typedAnswer"
            ref={typedAnswerRef}
            type="text"
            value={typedAnswer}
            onChange={(event) => setTypedAnswer(event.target.value)}
            onFocus={() => {
              setIsAnswerFocused(true);
              setHasEnteredCompactMode(true);
            }}
            onBlur={() => window.setTimeout(() => {
              if (document.activeElement !== typedAnswerRef.current) {
                setIsAnswerFocused(false);
              }
            }, 80)}
            readOnly={Boolean(revealedAnswer)}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            enterKeyHint={revealedAnswer ? 'next' : 'done'}
            inputMode="text"
            placeholder="例如：安排、預約、約定…"
            className="study-answer-input"
          />
          {!revealedAnswer ? (
            <div className="study-answer-actions">
              <button
                id="typed-submit"
                type="submit"
                className="btn btn-primary btn-full btn-lg"
                disabled={!canReveal}
              >
                查看標準答案
              </button>
              <button
                id="typed-skip"
                type="button"
                className="btn btn-ghost btn-full study-skip-button"
                onPointerDown={(event) => event.preventDefault()}
                onClick={handleSkipTyped}
              >
                不知道
              </button>
            </div>
          ) : (
            <div className="study-reveal-panel">
              <div>
                <div className="study-reveal-label">標準答案</div>
                <div id="typed-expected-answer" className="long-text study-reveal-answer">
                  {revealedAnswer.expected}
                </div>
              </div>
              <button
                id="typed-next"
                type="button"
                className="btn btn-primary btn-full btn-lg"
                disabled={isSubmittingTyped}
                onClick={handleTypedNext}
              >
                <span aria-live="polite">{isSubmittingTyped ? '儲存中…' : '下一題'}</span>
              </button>
              {submitError && (
                <p role="alert" className="study-status-copy text-error-static">
                  {submitError}
                </p>
              )}
            </div>
          )}
        </form>
      </div>

    </div>
  );
}
