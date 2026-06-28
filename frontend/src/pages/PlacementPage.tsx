import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePlacementSession } from '../hooks/usePlacementSession';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { useModalFocus } from '../hooks/useModalFocus';
import { useSpeechSynthesis } from '../hooks/useSpeechSynthesis';

const PROBLEMATIC_REASONS = [
  { value: 'ambiguous_meaning', label: '多義不明確' },
  { value: 'wrong_translation', label: '翻譯不正確' },
  { value: 'missing_data', label: '資料缺失' },
  { value: 'other', label: '其他' },
] as const;

function TTSButton({ word, onSpeak }: { word: string; onSpeak: (word: string) => void }) {
  return (
    <button
      id="tts-btn"
      className="placement-tts-button"
      onClick={() => onSpeak(word)}
      aria-label={`發音：${word}`}
    >
      <MaterialSymbol name="volume_up" className="study-audio-icon" />
    </button>
  );
}

export default function PlacementPage() {
  const navigate = useNavigate();
  const { speak } = useSpeechSynthesis();
  const [sessionCount, setSessionCount] = useState(() =>
    parseInt(sessionStorage.getItem('placement_count') ?? '20')
  );
  const {
    session,
    currentPosition,
    uiState,
    currentCardData: currentCard,
    dispatch,
    errorState,
    clearError,
    retryCreateSession
  } = usePlacementSession(sessionCount);

  const { ui, flashMeaning, flashCard } = uiState;
  const displayCard = ui === 'fuzzy_flash' ? flashCard : currentCard;

  // Navigate to checkpoint page
  useEffect(() => {
    if (ui === 'checkpoint') {
      navigate('/placement/checkpoint');
    }
  }, [ui, navigate]);

  // Navigate to complete
  useEffect(() => {
    if (ui === 'complete') {
      navigate('/placement/checkpoint?complete=1');
    }
  }, [ui, navigate]);

  if (errorState) {
    if (errorState.availableCount === 0) {
      const needsDeckScope = errorState.errorType === 'deck_scope_required';
      return (
        <div className="full-screen placement-status-screen">
          <div className="placement-status-panel">
            <h1 className="placement-status-title">
              {needsDeckScope ? '請先整理複習來源' : '可盤點字數不足'}
            </h1>
            <p className="placement-status-copy placement-status-copy-large">
              {needsDeckScope
                ? '目前複習來源不明確，請回首頁重新整理或重新匯入單字。'
                : '目前沒有符合條件的卡片。請回首頁重新整理或重新匯入單字。'}
            </p>
            <button
              id="btn-insufficient-home"
              className="btn btn-primary btn-full"
              onClick={() => {
                sessionStorage.removeItem('placement_count');
                navigate('/');
              }}
            >
              返回首頁
            </button>
          </div>
        </div>
      );
    } else {
      return (
        <div className="full-screen placement-status-screen">
          <div id="insufficient-modal" className="placement-clamp-dialog">
            <h2 id="insufficient-title" className="placement-clamp-title">
              可盤點字數不足
            </h2>
            <p id="insufficient-desc" className="placement-clamp-copy">
              目前僅有 {errorState.availableCount} 張符合條件的卡片，是否使用此數量開始盤點？
            </p>
            <div className="placement-button-stack">
              <button
                id="btn-insufficient-confirm"
                className="btn btn-primary btn-full"
                onClick={() => {
                  sessionStorage.setItem('placement_count', String(errorState.availableCount));
                  setSessionCount(errorState.availableCount);
                  retryCreateSession(errorState.availableCount);
                }}
              >
                確認 (使用 {errorState.availableCount} 張)
              </button>
              <button
                id="btn-insufficient-cancel"
                className="btn btn-secondary btn-full"
                onClick={() => {
                  sessionStorage.removeItem('placement_count');
                  clearError();
                  navigate('/');
                }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      );
    }
  }

  if (ui === 'loading' || !session) {
    return <div className="full-screen placement-status-screen" role="status" aria-live="polite">載入中…</div>;
  }

  const progress = currentPosition;
  const progressPct = (progress / session.requestedCount) * 100;
  const milestoneNumber = Math.floor(currentPosition / 50);

  if (ui === 'paused') {
    return (
      <div className="full-screen placement-status-screen">
        <div className="placement-status-panel placement-status-panel-wide">
          <h1 className="placement-status-title">
            已暫停
          </h1>
          <p className="placement-status-copy">
            第 {progress} / {session.requestedCount} 張
          </p>
          <div className="placement-status-actions">
            <button className="btn btn-primary btn-lg btn-full" onClick={() => dispatch({ type: 'RESUME' })}>
              繼續盤點
            </button>
            <button className="btn btn-secondary btn-lg btn-full" onClick={() => navigate('/')}>
              回首頁
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="full-screen">
      {/* Top bar: back + progress */}
      <div className="placement-progress-header">
        <button
          className="nav-back nav-close-button"
          onClick={() => dispatch({ type: 'PAUSE' })}
          aria-label="暫停並回首頁"
        >
          <MaterialSymbol name="close" />
        </button>
        <div className="placement-progress-track">
          <div className="progress-bar-track">
            <div
              className="progress-bar-fill"
              style={{ transform: `scaleX(${progressPct / 100})` }}
            />
          </div>
        </div>
        <span id="placement-progress" className="placement-progress-label">
          {progress} ·
        </span>
      </div>

      {/* Milestone banner */}
      {ui === 'milestone' && (
        <div className="milestone-banner placement-milestone">
          <MaterialSymbol name="check" className="placement-milestone-icon" />
          <div className="placement-milestone-copy">
            <div className="placement-milestone-title">
              第 {milestoneNumber} 個里程碑
            </div>
            <div className="placement-milestone-detail">
              完成 {milestoneNumber * 50} 張
            </div>
          </div>
          <button
            id="dismiss-milestone"
            className="btn btn-ghost btn-sm placement-milestone-dismiss"
            onClick={() => dispatch({ type: 'DISMISS_MILESTONE' })}
          >
            繼續
          </button>
        </div>
      )}

      {/* Main card area */}
      <div className="page placement-card-stage">
        {displayCard && (
          <>
            {/* English term */}
            <div
              id="placement-term"
              className="display-term long-text placement-term"
            >
              {displayCard.english}
            </div>

            {/* Part of speech */}
            {displayCard.partOfSpeech && (
              <div className="placement-part-of-speech">
                {displayCard.partOfSpeech}
              </div>
            )}

            <div className="placement-tts-row">
              <TTSButton word={displayCard.english} onSpeak={speak} />
            </div>

            {/* Meaning reveal — shown for both fuzzy and unknown answers */}
            {ui === 'fuzzy_flash' && flashMeaning && (
              <div
                id="placement-answer-reveal"
                className="placement-answer-reveal"
              >
                <div className="placement-answer-label">正確意思</div>
                <span id="placement-flash-meaning" className="long-text placement-answer-meaning">
                  {flashMeaning}
                </span>
              </div>
            )}

            {/* Undo control */}
            {currentPosition > 0 && (ui === 'card' || ui === 'milestone') && (
              <div className="placement-undo-row">
                <button
                  id="placement-undo"
                  className="btn btn-ghost btn-sm placement-undo"
                  onClick={() => dispatch({ type: 'UNDO' })}
                >
                  撤銷上一題
                </button>
              </div>
            )}
          </>
        )}

        {/* Empty state */}
        {!displayCard && ui === 'card' && (
          <div className="empty-state">
            <div>沒有更多卡片了</div>
          </div>
        )}
      </div>

      {/* Action buttons — during flash, show a Continue button for rapid progression */}
      {ui === 'fuzzy_flash' ? (
        <div className="placement-actions">
          <button
            id="btn-flash-continue"
            className="placement-btn placement-btn-known placement-flash-continue"
            onClick={() => dispatch({ type: 'FLASH_DONE' })}
          >
            繼續
          </button>
        </div>
      ) : (
        <div className="placement-actions">
          <button
            id="btn-known"
            className="placement-btn placement-btn-known"
            onClick={() => dispatch({ type: 'ANSWER', result: 'known' })}
          >
            知道
          </button>
          <button
            id="btn-fuzzy"
            className="placement-btn placement-btn-fuzzy"
            onClick={() => dispatch({ type: 'ANSWER', result: 'fuzzy' })}
          >
            模糊
          </button>
          <button
            id="btn-unknown"
            className="placement-btn placement-btn-unknown"
            onClick={() => dispatch({ type: 'ANSWER', result: 'unknown' })}
          >
            不會
          </button>
          <div className="placement-problematic-row">
            <button
              id="btn-problematic"
              className="btn btn-ghost placement-problematic-button"
              onClick={() => dispatch({ type: 'ANSWER', result: 'problematic' })}
            >
              題目有問題
            </button>
          </div>
        </div>
      )}

      {/* Problematic reason sheet */}
      {ui === 'problematic_sheet' && (
        <ProblematicSheet
          onSelect={(reason) => dispatch({ type: 'PROBLEMATIC_REASON', reason })}
          onCancel={() => dispatch({ type: 'ANSWER', result: 'unknown' })}
        />
      )}
    </div>
  );
}

function ProblematicSheet({
  onSelect,
  onCancel,
}: {
  onSelect: (reason: string) => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const sheetRef = useRef<HTMLDivElement | null>(null);

  useModalFocus({
    active: true,
    containerRef: sheetRef,
    initialFocusRef: cancelRef,
    onClose: onCancel,
  });

  return (
    <>
      <button className="sheet-backdrop" type="button" onClick={onCancel} aria-label="關閉問題回報" />
      <div
        id="problematic-sheet"
        ref={sheetRef}
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="problematic-sheet-title"
        aria-describedby="problematic-sheet-description"
      >
        <div className="sheet-handle" />
        <div className="problematic-sheet-title" id="problematic-sheet-title">
          這題有什麼問題？
        </div>
        <div className="problematic-sheet-description" id="problematic-sheet-description">
          此卡將排入資料清理清單，暫時跳過。
        </div>
        <div className="placement-button-stack">
          {PROBLEMATIC_REASONS.map(r => (
            <button
              key={r.value}
              id={`reason-${r.value}`}
              className="btn btn-secondary btn-full problematic-reason-button"
              onClick={() => onSelect(r.value)}
            >
              {r.label}
            </button>
          ))}
        </div>
        <button
          id="reason-cancel"
          ref={cancelRef}
          className="btn btn-ghost btn-full problematic-cancel"
          onClick={onCancel}
        >
          取消
        </button>
      </div>
    </>
  );
}
