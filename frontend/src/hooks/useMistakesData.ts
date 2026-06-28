import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';

export interface MistakeItem {
  english: string;
  chinese_meaning: string;
  part_of_speech?: string | null;
  sense_hint?: string | null;
  again_count: number;
  hard_count: number;
  lapses: number;
  confused_word?: string | null;
  selected_wrong_meaning?: string | null;
  example_sentence?: string | null;
  example_translation?: string | null;
  last_review_time?: string | null;
}

export function useMistakesList() {
  const [mistakes, setMistakes] = useState<MistakeItem[]>([]);
  const [totalMistakes, setTotalMistakes] = useState(0);
  const [mistakesPage, setMistakesPage] = useState(1);
  const [days, setDays] = useState<number | null>(7);
  const [ratingFilter, setRatingFilter] = useState('all');
  const [repeatedLapses, setRepeatedLapses] = useState(false);
  const [expandedMistakeWord, setExpandedMistakeWord] = useState<string | null>(null);
  const [isLoadingMistakes, setIsLoadingMistakes] = useState(false);

  const loadMistakes = useCallback(async (reset = false, pageOverride?: number) => {
    setIsLoadingMistakes(true);
    const targetPage = pageOverride ?? 1;
    try {
      const data = await api.getMistakes({
        days,
        deckId: null,
        rating: ratingFilter,
        repeatedLapses: repeatedLapses || null,
        page: targetPage,
        limit: 15,
      });
      if (reset) {
        setMistakes(data.items);
      } else {
        setMistakes((prev) => [...prev, ...data.items]);
      }
      setMistakesPage(targetPage);
      setTotalMistakes(data.total);
    } catch (err) {
      console.error('Failed to load mistakes:', err);
    } finally {
      setIsLoadingMistakes(false);
    }
  }, [days, ratingFilter, repeatedLapses]);

  useEffect(() => {
    void Promise.resolve().then(() => loadMistakes(true));
  }, [loadMistakes]);

  return {
    mistakes,
    totalMistakes,
    mistakesPage,
    days,
    setDays,
    ratingFilter,
    setRatingFilter,
    repeatedLapses,
    setRepeatedLapses,
    expandedMistakeWord,
    setExpandedMistakeWord,
    isLoadingMistakes,
    loadMistakes,
  };
}
