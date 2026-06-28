export type TtsMode = 'off' | 'again_hard' | 'every';
export type TtsStatus = 'locked' | 'ready' | 'speaking' | 'failed' | 'unsupported';

export const TTS_MODE_LABELS: Record<TtsMode, string> = {
  off: '關閉發音',
  again_hard: '錯題發音',
  every: '每題發音',
};

export function formatTime(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
