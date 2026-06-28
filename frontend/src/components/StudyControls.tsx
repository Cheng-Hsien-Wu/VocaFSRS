import { MaterialSymbol } from './MaterialSymbol';
import {
  formatTime,
  TTS_MODE_LABELS,
  type TtsMode,
  type TtsStatus,
} from '../services/study-tts';

export function FeedbackHeader({
  answeredCount,
  totalCount,
  onExit,
  timerSeconds,
  ttsMode,
  onCycleTtsMode,
}: {
  answeredCount: number;
  totalCount: number;
  onExit: () => void;
  timerSeconds?: number;
  ttsMode?: TtsMode;
  onCycleTtsMode?: () => void;
}) {
  return (
    <div className="study-feedback-header">
      <button
        className="nav-back nav-close-button"
        onClick={onExit}
        aria-label="結束學習，回首頁"
      >
        <MaterialSymbol name="close" />
      </button>
      <span id="study-progress" className="study-feedback-progress">
        {answeredCount} / {totalCount}
      </span>
      {timerSeconds !== undefined && (
        <span
          id="timer-display"
          className={`study-timer ${timerSeconds < 30 ? 'study-timer-urgent' : ''}`}
        >
          {formatTime(timerSeconds)}
        </span>
      )}
      {onCycleTtsMode && ttsMode !== undefined && (
        <button
          id="tts-mode-btn"
          onClick={onCycleTtsMode}
          aria-label={`發音模式：${TTS_MODE_LABELS[ttsMode]}`}
          title={TTS_MODE_LABELS[ttsMode]}
          className={`study-tts-mode ${ttsMode === 'off' ? 'study-tts-mode-off' : ''}`}
        >
          {ttsMode === 'off'
            ? '發音: 關'
            : ttsMode === 'again_hard'
              ? '發音: 錯題'
              : '發音: 每題'}
        </button>
      )}
    </div>
  );
}

export function TTSButton({
  word,
  status,
  onPlay,
}: {
  word: string;
  status: TtsStatus;
  onPlay: (word: string) => void;
}) {
  return (
    <button
      id="tts-btn"
      onClick={() => onPlay(word)}
      aria-label={`播放發音：${word}`}
      title={status === 'locked' ? '點一下啟用發音' : `播放發音：${word}`}
      disabled={status === 'unsupported'}
      className={`study-compact-icon-button study-tts-${status}`}
    >
      <AudioIcon
        muted={status === 'locked' || status === 'unsupported'}
        active={status === 'speaking'}
      />
    </button>
  );
}

export function AudioIcon({
  muted,
  active,
}: {
  muted?: boolean;
  active?: boolean;
}) {
  return (
    <MaterialSymbol
      name={muted ? 'volume_off' : 'volume_up'}
      fill={active}
      className={`study-audio-icon ${active ? 'study-audio-icon-active' : ''}`}
    />
  );
}
