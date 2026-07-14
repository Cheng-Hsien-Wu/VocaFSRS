import type { RefObject } from 'react';

import { MaterialSymbol } from './MaterialSymbol';

const LIMIT_OPTIONS = [
  { id: 'top_8', value: '8', label: '前 8 筆' },
  { id: 'top_12', value: '12', label: '前 12 筆' },
  { id: 'top_15', value: '15', label: '前 15 筆' },
  { id: 'all', value: 'all', label: '全部' },
  { id: 'custom', value: 'custom', label: '自訂' },
];

const AGAIN_OPTIONS = [
  { value: '2', label: '至少 2 次' },
  { value: '3', label: '至少 3 次' },
  { value: '5', label: '至少 5 次' },
  { value: 'custom', label: '自訂' },
];

interface MistakesExportDialogProps {
  dialogRef: RefObject<HTMLDivElement | null>;
  closeRef: RefObject<HTMLButtonElement | null>;
  filterType: string;
  setFilterType: (value: string) => void;
  limitType: string;
  setLimitType: (value: string) => void;
  customLimit: string;
  setCustomLimit: (value: string) => void;
  minimumAgainType: string;
  setMinimumAgainType: (value: string) => void;
  customMinimumAgain: string;
  setCustomMinimumAgain: (value: string) => void;
  preview: string;
  isExporting: boolean;
  copyFeedback: string;
  onClose: () => void;
  onCopy: () => void;
  onDownload: () => void;
}

export function MistakesExportDialog({
  dialogRef,
  closeRef,
  filterType,
  setFilterType,
  limitType,
  setLimitType,
  customLimit,
  setCustomLimit,
  minimumAgainType,
  setMinimumAgainType,
  customMinimumAgain,
  setCustomMinimumAgain,
  preview,
  isExporting,
  copyFeedback,
  onClose,
  onCopy,
  onDownload,
}: MistakesExportDialogProps) {
  return (
    <div className="full-screen export-modal-backdrop">
      <button
        type="button"
        className="export-modal-dismiss"
        onClick={onClose}
        aria-label="關閉 Podcast 匯出視窗"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-modal-title"
        className="export-modal"
      >
        <div className="export-modal-header">
          <h2 id="export-modal-title" className="export-modal-title">
            Podcast 複習素材
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="icon-button"
            aria-label="關閉"
          >
            <MaterialSymbol name="close" />
          </button>
        </div>

        <div className="export-modal-content">
          <div>
            <label htmlFor="export-filter-type" className="form-label">
              匯出內容範圍
            </label>
            <select
              id="export-filter-type"
              name="exportFilterType"
              value={filterType}
              onChange={event => setFilterType(event.target.value)}
              className="form-control"
            >
              <option value="today">今日反覆答錯</option>
              <option value="recent_7_days">近 7 天反覆答錯</option>
              <option value="recent_30_days">近 30 天反覆答錯</option>
            </select>
          </div>

          <div>
            <div className="form-label">反覆答錯門檻</div>
            <div
              role="group"
              aria-label="Podcast 最低 Again 次數"
              className="export-limit-options"
            >
              {AGAIN_OPTIONS.map(option => (
                <button
                  key={option.value}
                  id={`btn-export-again-${option.value}`}
                  type="button"
                  aria-pressed={minimumAgainType === option.value}
                  onClick={() => setMinimumAgainType(option.value)}
                  className="export-limit-option"
                >
                  {option.label}
                </button>
              ))}
            </div>
            {minimumAgainType === 'custom' && (
              <div>
                <label htmlFor="export-custom-again-input" className="sr-only">
                  最低 Again 次數
                </label>
                <input
                  type="number"
                  id="export-custom-again-input"
                  name="exportCustomAgain"
                  value={customMinimumAgain}
                  onChange={event => setCustomMinimumAgain(event.target.value)}
                  autoComplete="off"
                  inputMode="numeric"
                  min={1}
                  max={1000}
                  className="form-control export-custom-limit"
                />
              </div>
            )}
            <div className="export-filter-help">只匯出在所選期間內 Again 達門檻的單字，即使後來已答對也會保留。</div>
          </div>

          <div>
            <div className="form-label">輸出數量限制</div>
            <div
              role="group"
              aria-label="Podcast 輸出數量限制"
              className="export-limit-options"
            >
              {LIMIT_OPTIONS.map(option => (
                <button
                  key={option.id}
                  id={`btn-export-limit-${option.id}`}
                  type="button"
                  aria-pressed={limitType === option.value}
                  onClick={() => setLimitType(option.value)}
                  className="export-limit-option"
                >
                  {option.label}
                </button>
              ))}
            </div>
            {limitType === 'custom' && (
              <div>
                <label htmlFor="export-custom-limit-input" className="sr-only">
                  自訂輸出數量
                </label>
                <input
                  type="number"
                  id="export-custom-limit-input"
                  name="exportCustomLimit"
                  value={customLimit}
                  onChange={event => setCustomLimit(event.target.value)}
                  autoComplete="off"
                  inputMode="numeric"
                  min={1}
                  className="form-control export-custom-limit"
                />
              </div>
            )}
          </div>

          <div>
            <label htmlFor="export-preview-area" className="form-label">
              內容預覽
            </label>
            <div className="sr-only" role="status" aria-live="polite">
              {isExporting ? '預覽載入中…' : copyFeedback}
            </div>
            <textarea
              id="export-preview-area"
              name="exportPreview"
              readOnly
              value={isExporting ? '預覽載入中…' : preview}
              className="form-control export-preview"
            />
          </div>
        </div>

        <div className="export-modal-footer">
          <button
            id="btn-export-copy"
            onClick={onCopy}
            disabled={isExporting || !preview.trim()}
            className="btn btn-secondary"
          >
            <span aria-live="polite">{copyFeedback || '複製到剪貼簿'}</span>
          </button>
          <button
            id="btn-export-download"
            onClick={onDownload}
            disabled={isExporting || !preview.trim()}
            className="btn btn-primary"
          >
            下載檔案
          </button>
        </div>
      </div>
    </div>
  );
}
