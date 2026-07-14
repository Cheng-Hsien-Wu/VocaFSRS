import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../services/api';

const PODCAST_EXPORT_FORMAT = 'notebooklm';

export function useMistakesExport() {
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportFilterType, setExportFilterType] = useState('recent_7_days');
  const [exportLimitType, setExportLimitType] = useState('all');
  const [exportCustomLimit, setExportCustomLimit] = useState('10');
  const [minimumAgainType, setMinimumAgainType] = useState('2');
  const [customMinimumAgain, setCustomMinimumAgain] = useState('2');
  const [exportPreview, setExportPreview] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState('');
  const exportRequestIdRef = useRef(0);

  const triggerExportPreview = useCallback(async () => {
    const requestId = exportRequestIdRef.current + 1;
    exportRequestIdRef.current = requestId;
    setIsExporting(true);
    try {
      const res = await api.exportData({
        filterType: exportFilterType,
        deckId: null,
        limit: exportLimit(exportLimitType, exportCustomLimit),
        minimumAgainCount: minimumAgainCount(minimumAgainType, customMinimumAgain),
        format: PODCAST_EXPORT_FORMAT,
      });
      if (requestId === exportRequestIdRef.current) {
        setExportPreview(res.content);
      }
    } catch {
      if (requestId === exportRequestIdRef.current) {
        setExportPreview('預覽生成失敗，請確認有可用的錯題或混淆資料。');
      }
    } finally {
      if (requestId === exportRequestIdRef.current) {
        setIsExporting(false);
      }
    }
  }, [customMinimumAgain, exportCustomLimit, exportFilterType, exportLimitType, minimumAgainType]);

  useEffect(() => {
    if (!showExportModal) return;
    const id = window.setTimeout(() => {
      triggerExportPreview();
    }, 0);
    return () => window.clearTimeout(id);
  }, [showExportModal, triggerExportPreview]);

  async function handleCopyToClipboard() {
    const text = exportPreview.trim();
    if (!text || isExporting) return;
    try {
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        copyWithHiddenTextarea(text);
      }
      showCopyFeedback('已複製', 2000);
    } catch {
      showCopyFeedback('複製失敗，請長按預覽文字選取', 3000);
    }
  }

  function handleDownloadFile() {
    const blob = new Blob([exportPreview], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `vocab_export_${exportFilterType}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function openTodayExport() {
    setExportFilterType('today');
    setShowExportModal(true);
  }

  function showCopyFeedback(message: string, timeoutMs: number) {
    setCopyFeedback(message);
    window.setTimeout(() => setCopyFeedback(''), timeoutMs);
  }

  return {
    showExportModal,
    setShowExportModal,
    exportFilterType,
    setExportFilterType,
    exportLimitType,
    setExportLimitType,
    exportCustomLimit,
    setExportCustomLimit,
    minimumAgainType,
    setMinimumAgainType,
    customMinimumAgain,
    setCustomMinimumAgain,
    exportPreview,
    isExporting,
    copyFeedback,
    handleCopyToClipboard,
    handleDownloadFile,
    openTodayExport,
  };
}

function exportLimit(limitType: string, customLimit: string): number | null {
  if (limitType === '8') return 8;
  if (limitType === '12') return 12;
  if (limitType === '15') return 15;
  if (limitType === 'custom') return parseInt(customLimit, 10) || 10;
  return null;
}

function minimumAgainCount(type: string, customValue: string): number {
  if (type === 'custom') {
    return Math.min(1000, Math.max(1, parseInt(customValue, 10) || 2));
  }
  return parseInt(type, 10) || 2;
}

function copyWithHiddenTextarea(text: string) {
  const node = document.createElement('textarea');
  node.value = text;
  node.readOnly = true;
  node.style.position = 'fixed';
  node.style.top = '-1000px';
  node.style.left = '-1000px';
  node.style.width = '1px';
  node.style.height = '1px';
  node.style.opacity = '0';
  node.style.fontSize = '16px';
  node.style.transform = 'translate3d(-1000px, -1000px, 0)';
  node.setAttribute('aria-hidden', 'true');
  document.body.appendChild(node);
  try {
    node.focus({ preventScroll: true });
    node.setSelectionRange(0, node.value.length);
    document.execCommand('copy');
  } finally {
    document.body.removeChild(node);
  }
}
