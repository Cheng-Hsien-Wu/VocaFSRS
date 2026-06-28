import { useState, type ChangeEvent } from 'react';
import { v4 as uuidv4 } from 'uuid';

import { api } from '../services/api';

export type ImportStep = 'upload' | 'mapping' | 'preview' | 'summary';

export interface ImportRowResult {
  row_index: number;
  original_row_data: Record<string, string>;
  classification: string;
  action: string;
  message: string;
}

interface ImportStats {
  total_rows: number;
  new_cards: number;
  skipped_duplicates: number;
  linked_existing_cards: number;
}

interface CommitSummary {
  new_cards: number;
  linked_existing_cards: number;
  skipped_duplicates: number;
}

export const IMPORT_STEP_ORDER: ImportStep[] = ['upload', 'mapping', 'preview', 'summary'];

export const IMPORT_STEP_LABELS: Record<ImportStep, string> = {
  upload: '上傳檔案',
  mapping: '欄位對應',
  preview: '預覽與核對',
  summary: '完成匯入',
};

export const DEFAULT_IMPORT_DECK_NAME = 'Imported Vocabulary';

export const IMPORT_DB_FIELDS = [
  { name: 'english', label: '英文單字 (English)*', required: true },
  { name: 'chinese_meaning', label: '中文釋義 (Chinese)*', required: true },
  { name: 'part_of_speech', label: '詞性 (Part of Speech)', required: false },
  { name: 'sense_hint', label: '義項提示 (Sense Hint)', required: false },
  { name: 'example_sentence', label: '例句 (Example Sentence)', required: false },
  { name: 'example_translation', label: '例句翻譯 (Example Translation)', required: false },
];

function requestHashFor(mapping: Record<string, string>, deckSelection: string): string {
  const payload = JSON.stringify({
    mapping,
    deckSelection: deckSelection.trim(),
  });
  let hash = 0;
  for (let i = 0; i < payload.length; i++) {
    hash = (hash << 5) - hash + payload.charCodeAt(i);
    hash |= 0;
  }
  return String(hash);
}

function cleanMapping(mapping: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(mapping).filter(([, value]) => Boolean(value)));
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function useImportWorkflow() {
  const [step, setStep] = useState<ImportStep>('upload');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState('');
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [deckSelection, setDeckSelection] = useState(DEFAULT_IMPORT_DECK_NAME);
  const [stats, setStats] = useState<ImportStats | null>(null);
  const [previewRows, setPreviewRows] = useState<ImportRowResult[]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [page, setPage] = useState(1);
  const [filterClass, setFilterClass] = useState('');
  const [commitSummary, setCommitSummary] = useState<CommitSummary | null>(null);

  async function handleFileUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setError(null);
    try {
      const data = await api.uploadImportFile(file);
      setJobId(data.import_job_id);
      setHeaders(data.headers);

      const initialMap: Record<string, string> = {};
      IMPORT_DB_FIELDS.forEach((field) => {
        initialMap[field.name] = data.suggested_mapping[field.name] || '';
      });
      setMapping(initialMap);
      setDeckSelection(data.deck_suggestion || DEFAULT_IMPORT_DECK_NAME);
      setStep('mapping');
    } catch (err: unknown) {
      setError(errorMessage(err, '檔案上傳失敗'));
    } finally {
      setLoading(false);
    }
  }

  async function fetchPreviewRows(id: string, pageNumber: number, classification?: string) {
    try {
      const data = await api.getImportRows(id, pageNumber, 10, classification);
      setPreviewRows(data.rows);
      setTotalRows(data.total);
      setPage(pageNumber);
    } catch (err: unknown) {
      setError(errorMessage(err, '無法載入預覽資料'));
    }
  }

  async function handleAnalyze() {
    if (!mapping.english || !mapping.chinese_meaning) {
      setError('請至少對應「英文單字」與「中文釋義」欄位。');
      return;
    }

    const finalDeckSelection = (deckSelection || DEFAULT_IMPORT_DECK_NAME).trim();

    setLoading(true);
    setError(null);
    try {
      const analysisStats = await api.analyzeImport(jobId, cleanMapping(mapping), finalDeckSelection);
      setStats(analysisStats);
      setDeckSelection(finalDeckSelection);
      setPage(1);
      await fetchPreviewRows(jobId, 1, filterClass);
      setStep('preview');
    } catch (err: unknown) {
      setError(errorMessage(err, '分析對應欄位失敗'));
    } finally {
      setLoading(false);
    }
  }

  async function handleFilterChange(classification: string) {
    setFilterClass(classification);
    setPage(1);
    await fetchPreviewRows(jobId, 1, classification);
  }

  async function handleCommit() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.commitImport(jobId, uuidv4(), requestHashFor(mapping, deckSelection));
      setCommitSummary(data);
      setStep('summary');
    } catch (err: unknown) {
      setError(errorMessage(err, '匯入提交失敗'));
    } finally {
      setLoading(false);
    }
  }

  return {
    step,
    setStep,
    loading,
    error,
    jobId,
    headers,
    mapping,
    setMapping,
    stats,
    previewRows,
    totalRows,
    page,
    filterClass,
    commitSummary,
    handleFileUpload,
    handleAnalyze,
    fetchPreviewRows,
    handleFilterChange,
    handleCommit,
  };
}
