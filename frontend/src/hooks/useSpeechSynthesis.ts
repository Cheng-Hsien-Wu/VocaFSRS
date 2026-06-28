import { useCallback, useEffect, useRef } from 'react';

export type SpeechStatus = 'ready' | 'speaking' | 'failed' | 'unsupported';

function selectEnglishVoice(voices: SpeechSynthesisVoice[]) {
  return voices.find(voice =>
    voice.lang.startsWith('en') && !voice.lang.startsWith('ja')
  );
}

export function useSpeechSynthesis() {
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.speechSynthesis) return;

    const loadVoices = () => {
      voicesRef.current = window.speechSynthesis.getVoices();
    };

    loadVoices();
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices);
    return () => {
      window.speechSynthesis.removeEventListener('voiceschanged', loadVoices);
    };
  }, []);

  const speak = useCallback((
    word: string,
    onStatus?: (status: SpeechStatus) => void,
  ) => {
    if (!word || typeof window === 'undefined' || !window.speechSynthesis) {
      onStatus?.('unsupported');
      return false;
    }

    try {
      window.speechSynthesis.cancel();
      window.speechSynthesis.resume();
      const voices = voicesRef.current.length > 0
        ? voicesRef.current
        : window.speechSynthesis.getVoices();
      const utterance = new SpeechSynthesisUtterance(word);
      utterance.lang = 'en-US';
      utterance.voice = selectEnglishVoice(voices) ?? null;
      utterance.onstart = () => onStatus?.('speaking');
      utterance.onend = () => onStatus?.('ready');
      utterance.onerror = () => onStatus?.('failed');
      window.speechSynthesis.speak(utterance);
      return true;
    } catch {
      onStatus?.('failed');
      return false;
    }
  }, []);

  return { speak };
}
